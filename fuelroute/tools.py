"""후보·상세·휴게소 조회를 Agent 없이 실행하는 정규화 Tool 계층."""

from datetime import datetime, timedelta, timezone
from typing import Literal

import httpx
from langchain_core.tools import StructuredTool
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from pyproj.exceptions import ProjError

from fuelroute.config import Settings
from fuelroute.contracts import ToolResult
from fuelroute.coordinates import geodesic_distance_m, katec_to_wgs84, wgs84_to_katec
from fuelroute.probes import (
    Coordinate,
    OpinetSearch,
    ProbeFailure,
    probe_opinet,
    probe_rest_area,
    run_probe,
)

FuelType = Literal["gasoline", "diesel"]
PRODUCT_CODES = {"gasoline": "B027", "diesel": "D047"}


class CandidateSearch(Coordinate):
    """한 경로 검색점의 WGS84 좌표·유종·반경 계약."""

    fuel_type: FuelType = Field(description="gasoline=휘발유, diesel=경유")
    radius_m: int = Field(default=5000, ge=1, le=5000, description="검색점 반경(m), 1~5000")


class StationLookup(BaseModel):
    """후보 조회에서 받은 ID로만 상세를 조회하는 독립 입력."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    station_id: str = Field(min_length=1, max_length=30, description="Opinet 후보의 UNI_ID")
    fuel_type: FuelType = Field(description="상세 가격에서 선택할 유종: gasoline/diesel")


class RestAreaSearch(Coordinate):
    """전체 휴게소 중 반경·선택 노선/방향으로 참고 정보를 찾는 입력."""

    radius_m: int = Field(default=5000, ge=1, le=50000, description="직선 검색거리(m), 최대 50000")
    route_name: str | None = Field(
        default=None, max_length=100, description="노선명 정확 일치 필터"
    )
    direction: str | None = Field(default=None, max_length=100, description="방향 정확 일치 필터")


class StationCandidate(Coordinate):
    """출발지 도달 판정 전의 후보. center_distance_m은 검색점 기준 거리다."""

    station_id: str
    name: str
    fuel_type: FuelType
    price_krw_per_l: float | None = Field(ge=0, allow_inf_nan=False)
    center_distance_m: float = Field(ge=0, allow_inf_nan=False)


class StationDetails(Coordinate):
    """가격 시각과 원본에서 확인한 주소·브랜드·시설만 포함하는 상세 정보."""

    station_id: str
    name: str
    fuel_type: FuelType
    price_krw_per_l: float | None = Field(ge=0, allow_inf_nan=False)
    price_observed_at: datetime | None
    address: str | None
    brand: str | None
    facilities: dict[str, bool | None]


class RestAreaInfo(Coordinate):
    """주유소와 동일 시설임을 입증하지 않은 주변 휴게소 참고 정보."""

    name: str
    route_name: str | None
    direction: str | None
    distance_m: float = Field(ge=0, allow_inf_nan=False)
    facilities: dict[str, bool | None]
    reference_date: str | None
    match_status: Literal["unverified"] = "unverified"
    source: str = "전국휴게소정보표준데이터"


def _unwrap(result: ToolResult[dict]) -> dict:
    """성공한 하위 probe의 데이터를 반환하고 실패는 같은 오류 플래그로 전달한다.

    입력은 API 검증 공통 응답이다. 실패를 빈 목록으로 바꾸지 않으며 외부 호출·저장은
    없다. 예: AUTH_FAILED는 상위 Tool에서도 재계획 불가인 동일 오류가 된다.
    """
    if not result.ok:
        error = result.error
        raise ProbeFailure(
            error.code,
            error.message,
            retryable=error.retryable,
            replan_allowed=error.replan_allowed,
        )
    return result.data


def _price(value: object) -> float | None:
    """제공사 가격을 원/L로 해석하되 미제공·0은 None으로 반환한다.

    양의 유한값만 유효 가격이다. 숫자가 아닌 값·음수·무한값은 ValueError로
    잘못된 응답임을 알린다. 가격을 생성하지 않으며 외부 호출·부작용은 없다.
    """
    if value in (None, "", 0, "0"):
        return None
    price = float(value)
    if not 0 < price < float("inf"):
        raise ValueError("유효하지 않은 가격")
    return price


def _flag(value: object) -> bool | None:
    """시설 Y/N을 True/False로, 누락·그 외 값은 미확인(None)으로 반환한다.

    외부 호출·저장은 없으며 None을 ‘시설 없음’으로 해석하지 않는다. 예: '' → None.
    """
    return {"Y": True, "N": False}.get(value)


class FuelTools:
    """호출자 소유 HTTP 클라이언트로 조회 Tool을 제공한다. Agent/LLM 상태는 요구하지 않는다."""

    def __init__(self, settings: Settings, client: httpx.AsyncClient) -> None:
        """제공사 설정과 열린 비동기 HTTP 클라이언트를 보관한다.

        이 생성자는 호출·저장·키 출력·인증 검증을 하지 않는다. 호출자가 client를
        닫아야 한다. 예: async with httpx.AsyncClient() as c: tools=FuelTools(s,c).
        """
        self.settings = settings
        self.client = client

    async def search_gas_stations(
        self, longitude: float, latitude: float, fuel_type: FuelType, radius_m: int = 5000
    ) -> ToolResult[dict]:
        """안전 주행 구간에서 선택한 한 WGS84 검색점 주변의 주유소 후보를 조회한다.

        경도·위도는 도, 반경은 1~5000m, 유종은 gasoline/diesel이다. 기본 경로·안전
        구간 검증은 호출자가 선행한다. 좌표를 KATEC로 변환해 Opinet을 1회 요청한다.
        data.items는 주유소 ID·이름·원/L 가격(미제공 null)·WGS84 위치·검색점 거리다.
        ID로 중복 제거 후 가격순으로 정렬하며 ID는 get_station_details에 전달한다.
        0건은 EMPTY_RESULT, 잘못된 입력은 INVALID_INPUT, 필수 필드/좌표 오류는
        INVALID_RESPONSE다. 외부 인증·타임아웃 실패도 공통 계약을 유지한다.
        DB 저장·LLM·자체 재계획은 없다. 검색점 거리로 실제 도달 가능성을 판정하지 않는다.
        예: search_gas_stations(127.035,37.494,'gasoline',3000).
        """

        async def operation() -> dict:
            """입력 검증→KATEC 검색→정규화만 수행한다. HTTP는 probe 한 번이며 저장은 없다."""
            query = CandidateSearch(
                longitude=longitude, latitude=latitude, fuel_type=fuel_type, radius_m=radius_m
            )
            x, y = wgs84_to_katec(query.longitude, query.latitude)
            raw = _unwrap(
                await probe_opinet(
                    self.settings,
                    self.client,
                    search=OpinetSearch(
                        x=x, y=y, radius_m=query.radius_m, fuel_type=query.fuel_type
                    ),
                )
            )
            candidates = {}
            try:
                for row in raw["response"]["RESULT"]["OIL"]:
                    point = katec_to_wgs84(float(row["GIS_X_COOR"]), float(row["GIS_Y_COOR"]))
                    candidate = StationCandidate(
                        **point.model_dump(),
                        station_id=row["UNI_ID"],
                        name=row["OS_NM"],
                        fuel_type=query.fuel_type,
                        price_krw_per_l=_price(row.get("PRICE")),
                        center_distance_m=row["DISTANCE"],
                    )
                    candidates[candidate.station_id] = candidate
            except (ValueError, TypeError, KeyError, ProjError) as exc:
                raise ProbeFailure(
                    "INVALID_RESPONSE", "주유소 후보의 필수 필드·좌표가 잘못됐습니다."
                ) from exc
            ordered = sorted(
                candidates.values(),
                key=lambda item: (
                    item.price_krw_per_l is None,
                    item.price_krw_per_l or 0,
                    item.center_distance_m,
                ),
            )
            return {"items": [item.model_dump(mode="json") for item in ordered]}

        return await run_probe(self.settings, "opinet", "search_gas_stations", operation)

    async def get_station_details(self, station_id: str, fuel_type: FuelType) -> ToolResult[dict]:
        """후보의 Opinet ID를 받아 상세정보를 한 번 조회한다. 후보 검색을 다시 실행하지 않는다.

        station_id는 search_gas_stations의 station_id, 유종은 gasoline/diesel이다.
        data는 이름·주소·브랜드·WGS84 위치·선택 유종 가격(원/L)·가격 기준시각(KST)
        및 확인 가능한 시설이다. 가격/시각 누락은 null, 시설 누락은 null이다.
        잘못된 입력은 INVALID_INPUT, 없는 ID는 NOT_FOUND, 잘못된 필드는 INVALID_RESPONSE다.
        HTTP 1회 외 저장·LLM·재시도·재계획 부작용은 없다. 이 결과로 가격을 평가하되
        실제 도달 가능성은 별도 경로 조회로 확인한다.
        예: get_station_details('A0010207','gasoline').
        """

        async def operation() -> dict:
            """ID·유종 검증 후 원본 상세에서 선택 가격과 기준시각을 정규화한다. 저장은 없다."""
            query = StationLookup(station_id=station_id, fuel_type=fuel_type)
            raw = _unwrap(
                await probe_opinet(self.settings, self.client, station_id=query.station_id)
            )
            try:
                row = raw["response"]["RESULT"]["OIL"][0]
                if row["UNI_ID"] != query.station_id:
                    raise ValueError("반환된 ID가 요청과 다름")
                prices = row.get("OIL_PRICE", [])
                if isinstance(prices, dict):
                    prices = [prices]
                selected = next(
                    (
                        item
                        for item in prices
                        if item.get("PRODCD") == PRODUCT_CODES[query.fuel_type]
                    ),
                    {},
                )
                observed = None
                if selected.get("TRADE_DT") and selected.get("TRADE_TM"):
                    observed = datetime.strptime(
                        selected["TRADE_DT"] + selected["TRADE_TM"], "%Y%m%d%H%M%S"
                    ).replace(tzinfo=timezone(timedelta(hours=9)))
                point = katec_to_wgs84(float(row["GIS_X_COOR"]), float(row["GIS_Y_COOR"]))
                detail = StationDetails(
                    **point.model_dump(),
                    station_id=row["UNI_ID"],
                    name=row["OS_NM"],
                    fuel_type=query.fuel_type,
                    price_krw_per_l=_price(selected.get("PRICE")),
                    price_observed_at=observed,
                    address=row.get("NEW_ADR") or row.get("VAN_ADR") or None,
                    brand=row.get("POLL_DIV_CD") or row.get("POLL_DIV_CO") or None,
                    facilities={
                        key: _flag(row.get(key)) for key in ("MAINT_YN", "CAR_WASH_YN", "CVS_YN")
                    },
                )
            except (ValueError, TypeError, KeyError, AttributeError, ProjError) as exc:
                raise ProbeFailure(
                    "INVALID_RESPONSE", "주유소 상세 필드·가격 시각을 확인하세요."
                ) from exc
            return detail.model_dump(mode="json")

        return await run_probe(self.settings, "opinet", "get_station_details", operation)

    async def search_rest_area_info(
        self,
        longitude: float,
        latitude: float,
        radius_m: int = 5000,
        route_name: str | None = None,
        direction: str | None = None,
    ) -> ToolResult[dict]:
        """WGS84 좌표 주변 휴게소 보조정보를 전체 페이지에서 검색한다. 주유소 매칭은 미확인이다.

        경도·위도(도), 반경 1~50000m, 선택 노선명·방향은 원본 필드와 정확 일치한다.
        페이지당 1000건, 최대 10페이지로 조회하고 거리순 정렬한 data.items와 처리 건수를
        반환한다. 시설은 Y/N/미확인을 True/False/null로, 기준일은 원본값으로 유지한다.
        거리 계산은 반경 필터용이며 경로 접근 가능성·같은 주유소임을 보장하지 않는다.
        match_status는 항상 unverified다. 0건은 EMPTY_RESULT, 전체 페이지를 받지 못하면
        실패이며 부분 결과를 완전 결과로 위장하지 않는다. 보조 Tool 실패만으로 추천을
        중단하지 않는 것은 호출자 책임이다. HTTP 외 저장·LLM·재계획은 없다.
        예: search_rest_area_info(128.806648,37.758206,3000,direction='하행').
        """

        async def operation() -> dict:
            """페이지 누락 없이 수집 후 명시적 거리·노선·방향으로 필터한다. DB·캐시는 없다."""
            query = RestAreaSearch(
                longitude=longitude,
                latitude=latitude,
                radius_m=radius_m,
                route_name=route_name,
                direction=direction,
            )
            catalog = []
            total = None
            for page in range(1, 11):
                raw = _unwrap(
                    await probe_rest_area(self.settings, self.client, page=page, rows=1000)
                )
                if total is None:
                    total = raw["total_count"]
                elif total != raw["total_count"]:
                    raise ProbeFailure(
                        "INVALID_RESPONSE", "휴게소 페이지 조회 중 전체 개수가 달라졌습니다."
                    )
                root = raw["response"].get("response", raw["response"])
                items = root["body"]["items"]
                if isinstance(items, dict):
                    items = items["item"]
                if isinstance(items, dict):
                    items = [items]
                catalog.extend(items)
                if len(catalog) >= total:
                    break
            else:
                raise ProbeFailure(
                    "INVALID_RESPONSE", "휴게소 전체 페이지가 조회 상한을 초과했습니다."
                )
            matches = []
            skipped = 0
            for row in catalog:
                try:
                    point = Coordinate(longitude=row["longitude"], latitude=row["latitude"])
                    if not row.get("entrpsNm"):
                        raise ValueError("휴게소명 없음")
                    distance = geodesic_distance_m(query, point)
                    if distance > query.radius_m:
                        continue
                    if query.route_name is not None and row.get("roadRouteNm") != query.route_name:
                        continue
                    if query.direction is not None and row.get("roadRouteDrc") != query.direction:
                        continue
                    matches.append(
                        RestAreaInfo(
                            **point.model_dump(),
                            name=row["entrpsNm"],
                            route_name=row.get("roadRouteNm") or None,
                            direction=row.get("roadRouteDrc") or None,
                            distance_m=distance,
                            reference_date=row.get("referenceDate") or None,
                            facilities={
                                key: _flag(row.get(key))
                                for key in ("oltYn", "toiletYn", "rstrtYn", "shopYn", "nrsgYn")
                            },
                        )
                    )
                except (ValidationError, ValueError, KeyError, TypeError):
                    skipped += 1
            if not matches:
                if skipped == len(catalog):
                    raise ProbeFailure(
                        "INVALID_RESPONSE", "유효한 위치·이름을 가진 휴게소가 없습니다."
                    )
                raise ProbeFailure("EMPTY_RESULT", "해당 반경·노선·방향의 휴게소 정보가 없습니다.")
            matches.sort(key=lambda item: item.distance_m)
            return {
                "items": [item.model_dump(mode="json") for item in matches],
                "catalog_count": len(catalog),
                "skipped_invalid_count": skipped,
            }

        return await run_probe(self.settings, "rest_area", "search_rest_area_info", operation)

    def registered_tools(self) -> list[StructuredTool]:
        """세 비동기 조회 함수를 설명·필드 스키마와 함께 LangChain Tool로 등록한다.

        통합 ToolRegistry에서 세 조회 Tool만 선택한다. 등록만으로 HTTP·DB를 실행하지
        않으며 같은 설정/클라이언트를 사용한다. ainvoke(dict)는 입력 오류도 포함한
        공통 JSON 객체를 반환한다. 직접 Python 메서드의 ToolResult 반환은 유지한다.
        """
        from fuelroute.tool_registry import ToolRegistry

        names = {"search_gas_stations", "get_station_details", "search_rest_area_info"}
        return [
            tool
            for tool in ToolRegistry(self.settings, self.client).registered_tools()
            if tool.name in names
        ]
