"""공통 입출력·실패 계약과 직렬화 가능한 설명을 갖춘 실행용 Tool 레지스트리."""

from dataclasses import dataclass
from typing import Any

import httpx
from langchain_core.tools import StructuredTool
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from fuelroute.config import Settings
from fuelroute.contracts import ToolResult
from fuelroute.probes import Coordinate, ProbeFailure, probe_route, run_probe
from fuelroute.recommendation import RecommendationRequest, RecommendationResult, rank_candidates
from fuelroute.route_contracts import DrivingRoute
from fuelroute.route_spike import assess_candidate_routes
from fuelroute.routing import RoutePath, build_route_path
from fuelroute.services import RangeConditions, calculate_safe_range
from fuelroute.tools import (
    CandidateSearch,
    FuelTools,
    RestAreaInfo,
    RestAreaSearch,
    StationCandidate,
    StationDetails,
    StationLookup,
)


class DrivingRouteRequest(BaseModel):
    """기본·접근 또는 단일 주유소 경유 경로를 독립 조회하는 입력."""

    model_config = ConfigDict(extra="forbid")
    origin: Coordinate = Field(description="출발 WGS84 경도·위도(도)")
    destination: Coordinate = Field(description="목적 WGS84 경도·위도(도)")
    via: Coordinate | None = Field(
        default=None, description="단일 주유소 경유 좌표, 기본 경로는 null"
    )


class SafeRangeOutput(BaseModel):
    """안전 잔량을 남길 수 있는 입력 기반 추정 주행거리."""

    safe_range_km: float = Field(gt=0, allow_inf_nan=False, description="안전 주행거리(km)")


class CandidatesOutput(BaseModel):
    """Opinet 주유소 ID와 다음 상세·경로 조회에 전달할 정규화 후보."""

    items: list[StationCandidate]


class RestAreasOutput(BaseModel):
    """주유소와 동일 시설이라고 단정하지 않는 휴게소 참고 정보."""

    items: list[RestAreaInfo]
    catalog_count: int = Field(ge=0)
    skipped_invalid_count: int = Field(ge=0)


class AssessmentRequest(BaseModel):
    """명시적 실제 자동차 비용 세 종류와 안전·우회 상한의 평가 입력."""

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    base: DrivingRoute = Field(description="출발→목적지 기본 경로 비용")
    approach: DrivingRoute = Field(description="출발→주유소 접근 경로 비용")
    via: DrivingRoute = Field(description="출발→주유소→목적지 경로 비용, 두 구간 필수")
    safe_range_km: float = Field(gt=0, description="안전 주행거리(km)")
    max_detour_min: float | None = Field(default=None, ge=0, description="허용 추가 시간(분)")
    max_detour_km: float | None = Field(default=None, ge=0, description="허용 추가 거리(km)")

    @model_validator(mode="after")
    def single_waypoint(self) -> "AssessmentRequest":
        """경유 경로가 두 구간인지 HTTP 없이 확인한다. 틀린 입력은 ValueError다."""
        if len(self.via.legs) != 2:
            raise ValueError("단일 경유 경로의 구간은 두 개여야 합니다.")
        return self


class AssessmentOutput(BaseModel):
    """비용 판정·제외 이유와 교통 변동으로 발생할 수 있는 원래 차이."""

    eligible: bool
    reason_codes: list[str]
    reach_distance_m: float = Field(ge=0, allow_inf_nan=False)
    detour_distance_m: float = Field(ge=0, allow_inf_nan=False)
    detour_duration_s: float = Field(ge=0, allow_inf_nan=False)
    raw_detour_distance_m: float = Field(allow_inf_nan=False)
    raw_detour_duration_s: float = Field(allow_inf_nan=False)
    negative_delta_observed: bool


@dataclass(frozen=True)
class _Definition:
    """사용 가능한 고정 Tool의 제공사·스키마·다음 단계 전달 필드."""

    name: str
    provider: str
    input_model: type[BaseModel]
    output_model: type[BaseModel]
    next_fields: tuple[str, ...]
    description: str


class ToolRegistry:
    """HTTP 소유권은 호출자에게 두고 로컬 계산과 외부 조회 일곱 종류를 등록한다."""

    def __init__(self, settings: Settings, client: httpx.AsyncClient) -> None:
        """설정·열린 클라이언트·고정 계약을 보관한다. 생성·등록은 API를 호출하지 않는다.

        등록된 문자열 이름만 실행할 수 있다. 입력은 Pydantic으로, 성공 data는 출력 모델로
        검사하며 도구 설명·스키마에 인증정보를 포함하지 않는다. 클라이언트 종료는 호출자 책임이다.
        """
        self.settings = settings
        self.client = client
        self.lookups = FuelTools(settings, client)
        self.definitions = {
            item.name: item
            for item in [
                _Definition(
                    "calculate_safe_range",
                    "local",
                    RangeConditions,
                    SafeRangeOutput,
                    ("data.safe_range_km",),
                    calculate_safe_range.__doc__,
                ),
                _Definition(
                    "get_driving_route",
                    "kakao",
                    DrivingRouteRequest,
                    DrivingRoute,
                    ("data.distance_m", "data.duration_s", "data.legs"),
                    self._driving_route.__doc__,
                ),
                _Definition(
                    "search_gas_stations",
                    "opinet",
                    CandidateSearch,
                    CandidatesOutput,
                    ("data.items[].station_id", "data.items[].longitude", "data.items[].latitude"),
                    self.lookups.search_gas_stations.__doc__,
                ),
                _Definition(
                    "get_station_details",
                    "opinet",
                    StationLookup,
                    StationDetails,
                    ("data.station_id", "data.price_krw_per_l", "data.price_observed_at"),
                    self.lookups.get_station_details.__doc__,
                ),
                _Definition(
                    "search_rest_area_info",
                    "rest_area",
                    RestAreaSearch,
                    RestAreasOutput,
                    ("data.items[].match_status", "data.items[].reference_date"),
                    self.lookups.search_rest_area_info.__doc__,
                ),
                _Definition(
                    "assess_candidate_routes",
                    "local",
                    AssessmentRequest,
                    AssessmentOutput,
                    ("data.eligible", "data.reason_codes", "data.reach_distance_m"),
                    assess_candidate_routes.__doc__,
                ),
                _Definition(
                    "rank_candidates",
                    "local",
                    RecommendationRequest,
                    RecommendationResult,
                    ("data.recommended", "data.excluded_candidates", "data.scope"),
                    rank_candidates.__doc__,
                ),
            ]
        }

    async def _driving_route(self, query: DrivingRouteRequest) -> ToolResult[dict]:
        """기본·접근·단일 경유 자동차 비용을 독립 조회하고 원본 구조를 제거한다.

        WGS84 출발·목적 좌표와 선택 경유 좌표가 필요하다. Kakao HTTP는 1회이며 재시도·
        후보 검색·저장은 없다. 거리 m·시간 초·순서가 보존된 legs를 반환한다. 결과는
        assess_candidate_routes의 base/approach/via에 전달한다. 원본 상세 좌표는 반환하지 않는다.
        설정·인증·타임아웃·경로 없음은 공통 실패, 잘못된 비용은 INVALID_RESPONSE다.
        예: via=null은 기본/접근 경로, via=주유소 좌표는 경유 비용이다.
        """
        result, _ = await self.route_context(query)
        return result

    async def route_context(
        self, query: DrivingRouteRequest, *, include_path: bool = False
    ) -> tuple[ToolResult[dict], RoutePath | None]:
        """경로를 한 번 조회해 공통 비용 결과와 선택적인 실행기 전용 상세 경로를 반환한다.

        query는 WGS84 출발/목적·선택 경유 좌표다. include_path=True는 경유 없는 기본
        경로에만 사용하며 도로 거리·상세 좌표로 RoutePath를 만든다. 첫 반환값 data는 항상
        DrivingRoute의 m/초/구간 비용뿐이고 두 번째 경로는 모델·로그·공개 JSON에 넣지 않는다.
        실패하면 같은 공통 오류와 None이다. 입력/설정은 HTTP 전에 검사하고 인증·0경로·
        타임아웃·파싱 실패를 정제한다. 호출 최대1회·재시도0회이며 DB·캐시·파일 쓰기는 없다.
        예: result,path=await registry.route_context(query,include_path=True) 뒤 path로 검색점을
        만든다. 반환 경로와 HTTP 클라이언트의 수명은 요청 실행기에 한정한다.
        """
        path: RoutePath | None = None

        async def operation() -> dict:
            """입력을 재검증해 원본 경로를 비용과 내부 기하로 분리한다. 원본은 반환하지 않는다."""
            nonlocal path
            checked = DrivingRouteRequest.model_validate(query.model_dump())
            if include_path and checked.via is not None:
                raise ProbeFailure(
                    "INVALID_INPUT", "검색점 생성에는 경유 없는 기본 경로가 필요합니다."
                )
            raw = await probe_route(
                self.settings,
                self.client,
                origin=checked.origin,
                destination=checked.destination,
                via=checked.via,
            )
            if raw.error:
                error = raw.error
                raise ProbeFailure(
                    error.code,
                    error.message,
                    retryable=error.retryable,
                    replan_allowed=error.replan_allowed,
                )
            try:
                normalized = DrivingRoute.from_probe_data(raw.data)
                if include_path:
                    path = build_route_path(raw.data)
            except ValueError as exc:
                raise ProbeFailure(
                    "INVALID_RESPONSE", "경로 비용·구간·상세 좌표를 확인하세요."
                ) from exc
            return normalized.model_dump(mode="json")

        result = await run_probe(self.settings, "kakao", "get_driving_route", operation)
        return result, path if result.ok else None

    async def invoke(self, name: str, arguments: dict) -> ToolResult[dict]:
        """고정 이름과 JSON 객체로 Tool 하나만 실행해 성공·실패를 같은 구조로 반환한다.

        입력 모델 검증은 INVALID_INPUT, 성공 data 검증 실패는 INVALID_RESPONSE다.
        키는 해당 제공사에만 필요하고 로컬 계산에는 필요 없다. 미등록 이름도 실행하지
        않고 공통 실패를 반환한다. 부작용·재시도는 선택 Tool의 계약을 따른다.
        예: invoke('calculate_safe_range', {'current_fuel_l':15,...})의 data.safe_range_km은 144다.
        """
        definition = self.definitions.get(name)

        async def operation() -> dict:
            """입력 검증 후 고정 구현을 실행하고 출력 모델로 성공 payload를 확인한다."""
            if definition is None:
                raise ProbeFailure("INVALID_INPUT", "등록되지 않은 Tool입니다.")
            query = definition.input_model.model_validate(arguments)
            if name == "calculate_safe_range":
                try:
                    data = {"safe_range_km": calculate_safe_range(**query.model_dump())}
                except ValueError as exc:
                    raise ProbeFailure("INVALID_INPUT", "연료 계산 범위를 확인하세요.") from exc
            elif name == "assess_candidate_routes":
                data = assess_candidate_routes(**query.model_dump())
            elif name == "rank_candidates":
                data = rank_candidates(query).model_dump(mode="json")
            else:
                if name == "get_driving_route":
                    result = await self._driving_route(query)
                else:
                    result = await getattr(self.lookups, name)(**query.model_dump())
                if result.error:
                    error = result.error
                    raise ProbeFailure(
                        error.code,
                        error.message,
                        retryable=error.retryable,
                        replan_allowed=error.replan_allowed,
                    )
                data = result.data
            try:
                return definition.output_model.model_validate(data).model_dump(mode="json")
            except ValidationError as exc:
                raise ProbeFailure("INVALID_RESPONSE", "Tool의 출력 계약을 확인하세요.") from exc

        return await run_probe(
            self.settings,
            definition.provider if definition else "local",
            name if definition else "unknown_tool",
            operation,
        )

    def _registered_tool(self, definition: _Definition) -> StructuredTool:
        """JSON schema를 모델에 제공하고 실행 시 같은 모델로 재검증하는 Tool 하나를 만든다.

        객체형 입력의 누락·추가 필드도 invoke에서 공통 오류로 반환한다. LangChain이
        wrapper 전에 Pydantic 예외로 중단하지 않도록 JSON schema 형식을 사용한다.
        프로토콜 입력은 JSON 객체이며 문자열 입력은 지원하지 않는다. API 호출은 등록 시 없다.
        """

        async def call(**arguments: Any) -> dict:
            """이 정의의 Tool만 실행해 JSON 직렬화 가능한 공통 응답 객체를 반환한다."""
            return (await self.invoke(definition.name, arguments)).model_dump(mode="json")

        return StructuredTool.from_function(
            coroutine=call,
            name=definition.name,
            description=self._description(definition),
            args_schema=definition.input_model.model_json_schema(),
        )

    def _description(self, definition: _Definition) -> str:
        """원 함수 설명에 실제 Tool 포장·실패·다음 필드 계약을 붙인다. 호출·저장은 없다.

        원 함수가 float나 모델을 반환해도 등록 Tool은 공통 JSON의 data에 넣는다는 점을
        명시한다. 직렬화된 description과 정적 명세가 같은 내용을 사용한다.
        """
        return (
            definition.description + "\n\n등록 Tool의 반환은 공통 JSON 객체다: "
            "ok/tool_name/data/error/meta. 원 함수의 반환은 출력 스키마에 맞춰 data에 넣는다. "
            "입력 오류는 INVALID_INPUT, 출력 계약 오류는 INVALID_RESPONSE다. "
            "예외 원문은 노출하지 않는다. "
            "다음 단계 필드: " + ", ".join(definition.next_fields)
        )

    def registered_tools(self) -> list[StructuredTool]:
        """일곱 Tool을 중복 이름 없이 반환한다. 등록만으로 HTTP·LLM·DB를 실행하지 않는다.

        ainvoke(dict)는 유효/무효 필드 모두 공통 JSON 객체를 반환한다. LangGraph 연결과
        직렬화 검사에 사용한다. 예: tools[0].ainvoke({...})의 결과는 ok/data/error/meta 객체다.
        """
        return [self._registered_tool(item) for item in self.definitions.values()]

    def contracts(self) -> dict:
        """입력·공통 출력 JSON schema, 가능한 실패 코드와 다음 필드를 반환한다.

        설정값·키·클라이언트를 포함하지 않는다. 호출·파일 쓰기 부작용은 없으며
        tool-schemas CLI와 문서 검증에서 같은 명세를 사용한다. 실패 코드 목록은
        가능한 분류이며 실제 재시도·재계획 여부는 응답 error의 플래그를 확인한다.
        """
        external_errors = [
            "CONFIG_ERROR",
            "AUTH_FAILED",
            "TIMEOUT",
            "RATE_LIMITED",
            "UPSTREAM_ERROR",
        ]
        lookup_errors = {
            "get_driving_route": ["NO_ROUTE"],
            "search_gas_stations": ["EMPTY_RESULT"],
            "get_station_details": ["NOT_FOUND"],
            "search_rest_area_info": ["EMPTY_RESULT"],
        }
        return {
            item.name: {
                "provider": item.provider,
                "description": self._description(item),
                "input_schema": item.input_model.model_json_schema(),
                "output_schema": ToolResult[item.output_model].model_json_schema(),
                "error_codes": ["INVALID_INPUT", "INVALID_RESPONSE"]
                + (external_errors + lookup_errors[item.name] if item.provider != "local" else []),
                "next_fields": list(item.next_fields),
            }
            for item in self.definitions.values()
        }
