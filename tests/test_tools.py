"""실제 API 형식에 기반한 정규화·페이지·좌표·실패 계약 회귀 검증. HTTP는 모의 전송이다."""

import asyncio

import httpx
import pytest
from langchain_core.utils.function_calling import convert_to_openai_tool

from fuelroute.config import Settings
from fuelroute.coordinates import katec_to_wgs84, wgs84_to_katec
from fuelroute.probes import probe_rest_area
from fuelroute.tools import FuelTools


def settings() -> Settings:
    """실제 .env를 읽지 않는 테스트 키를 반환한다. 인증·외부 저장은 하지 않는다."""
    return Settings(
        _env_file=None, opinet_api_key="test-opinet", data_go_kr_service_key="test-data"
    )


@pytest.mark.parametrize(
    ("longitude", "latitude", "x", "y"),
    [
        (126.98217734415019, 37.56468648536046, 310278.5350755865, 551869.0243206669),
        (129.07579349764512, 35.17883196265564, 498181.0591427401, 287188.0233151837),
        (127.38454163183215, 36.35127257501252, 344955.1840998584, 416909.51311973063),
    ],
)
def test_tm128_against_official_tmap_examples(
    longitude: float, latitude: float, x: float, y: float
) -> None:
    """공식 문서의 독립된 세 기준점을 5m 이내로 대조해 잘못된 CRS·축 순서를 잡는다."""
    actual_x, actual_y = wgs84_to_katec(longitude, latitude)
    assert ((actual_x - x) ** 2 + (actual_y - y) ** 2) ** 0.5 < 5
    point = katec_to_wgs84(actual_x, actual_y)
    assert point.longitude == pytest.approx(longitude, abs=1e-7)
    assert point.latitude == pytest.approx(latitude, abs=1e-7)


def test_candidates_detail_and_schema_are_independent() -> None:
    """WGS84 후보→ID 상세 연결·중복 제거·가격 누락·시각·실제 Tool 스키마를 검증한다."""
    paths = []

    def handler(request: httpx.Request) -> httpx.Response:
        """반경과 상세 요청에 다른 합성 응답을 제공하고 올바른 키 형식을 검사한다."""
        paths.append(request.url.path)
        assert request.url.params["code"] == "test-opinet"
        row = {
            "UNI_ID": "A0010207",
            "OS_NM": "예시",
            "GIS_X_COOR": 314871.8,
            "GIS_Y_COOR": 544012,
            "DISTANCE": 100,
            "PRICE": 1700,
        }
        if request.url.path.endswith("detailById.do"):
            row.update(
                OIL_PRICE=[
                    {"PRODCD": "D047", "PRICE": 0, "TRADE_DT": "20261118", "TRADE_TM": "120000"}
                ],
                CVS_YN="Y",
            )
            rows = [row]
        else:
            rows = [row, row]
        return httpx.Response(200, json={"RESULT": {"OIL": rows}})

    async def scenario() -> None:
        """두 메서드를 명시적 입력으로 별도 호출하며 전체 Agent는 실행하지 않는다."""
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            tools = FuelTools(settings(), client)
            registered = tools.registered_tools()
            for tool in registered:
                schema = convert_to_openai_tool(tool)["function"]
                assert len(schema["description"]) > 100
                assert all(
                    field.get("description")
                    for field in schema["parameters"]["properties"].values()
                )
            near = await tools.search_gas_stations(127.03509, 37.49434, "gasoline")
            assert near.ok and len(near.data["items"]) == 1
            detail = await tools.get_station_details(near.data["items"][0]["station_id"], "diesel")
            assert detail.ok and detail.data["price_krw_per_l"] is None
            assert detail.data["price_observed_at"].endswith("+09:00")
            assert detail.data["facilities"]["CVS_YN"] is True
            assert detail.data["facilities"]["MAINT_YN"] is None
            assert len(paths) == 2

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("kind", "code"),
    [
        ("empty", "EMPTY_RESULT"),
        ("auth", "AUTH_FAILED"),
        ("timeout", "TIMEOUT"),
        ("malformed", "INVALID_RESPONSE"),
    ],
)
def test_candidate_failures_preserve_common_contract(kind: str, code: str) -> None:
    """외부 실패를 성공으로 바꾸지 않고 공통 응답과 재시도 플래그를 유지하는지 검증한다."""

    def handler(request: httpx.Request) -> httpx.Response:
        """인증·타임아웃·빈 결과·잘못된 좌표를 합성하고 실제 API는 호출하지 않는다."""
        if kind == "auth":
            return httpx.Response(401)
        if kind == "timeout":
            raise httpx.ReadTimeout("test", request=request)
        rows = [] if kind == "empty" else [{"UNI_ID": "A1", "OS_NM": "예시"}]
        return httpx.Response(200, json={"RESULT": {"OIL": rows}})

    async def scenario() -> None:
        """후보 조회 실패에서 data=null과 오류 분류·메타가 유지되는지 확인한다."""
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await FuelTools(settings(), client).search_gas_stations(127, 37, "gasoline")
        assert not result.ok and result.data is None and result.error.code == code
        assert result.error.retryable is (kind == "timeout")

    asyncio.run(scenario())


def test_actual_rest_envelope_pagination_filters_and_unknown() -> None:
    """실제 header/body/items.item 형식에서 전체 페이지·방향 필터·시설 미확인을 검증한다."""
    pages = []

    def handler(request: httpx.Request) -> httpx.Response:
        """첫 페이지를 짧게 반환해 두 번째 페이지까지 실제 수집하게 하는 합성 응답이다."""
        page = int(request.url.params["pageNo"])
        pages.append(page)
        row = {
            "entrpsNm": f"휴게소{page}",
            "longitude": "128.806648",
            "latitude": "37.758206",
            "roadRouteNm": "영동선",
            "roadRouteDrc": "하행" if page == 2 else "상행",
            "toiletYn": "Y",
            "rstrtYn": "",
            "referenceDate": "2026-09-17",
        }
        return httpx.Response(
            200,
            json={
                "header": {"resultCode": "00"},
                "body": {"totalCount": 2, "items": {"item": [row]}},
            },
        )

    async def scenario() -> None:
        """전체 두 페이지에서 방향 일치 한 건만 반환하고 동일 주유소로 단정하지 않는다."""
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            tools = FuelTools(settings(), client)
            result = await tools.search_rest_area_info(128.806648, 37.758206, direction="하행")
        assert result.ok and result.data["catalog_count"] == 2
        item = result.data["items"][0]
        assert item["direction"] == "하행" and item["match_status"] == "unverified"
        assert item["facilities"]["rstrtYn"] is None
        assert item["reference_date"] == "2026-09-17"

    asyncio.run(scenario())
    assert pages == [1, 2]


def test_rest_singleton_and_empty_filter() -> None:
    """단일 item 객체 정규화와 반경 결과 없음이 핵심 추천 성공/실패를 대신하지 않음을 확인한다."""
    payload = {
        "header": {"resultCode": "00"},
        "body": {
            "totalCount": 1,
            "items": {
                "item": {"entrpsNm": "강릉", "longitude": "128.806648", "latitude": "37.758206"}
            },
        },
    }

    async def scenario() -> None:
        """단일 행 응답은 정상 페이지, 다른 위치의 반경 검색은 EMPTY_RESULT로 구분한다."""
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(lambda request: httpx.Response(200, json=payload))
        ) as client:
            assert (await probe_rest_area(settings(), client)).ok
            result = await FuelTools(settings(), client).search_rest_area_info(127, 37, 100)
        assert result.error.code == "EMPTY_RESULT" and not result.error.replan_allowed

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "tool_name", ["search_gas_stations", "get_station_details", "search_rest_area_info"]
)
@pytest.mark.parametrize("failure", ["auth", "timeout", "empty", "malformed"])
def test_all_query_tools_preserve_failure_contract(tool_name: str, failure: str) -> None:
    """각 독립 조회 Tool의 인증·타임아웃·0건·잘못된 응답이 같은 공통 계약인지 검증한다."""

    def handler(request: httpx.Request) -> httpx.Response:
        """선택한 제공사의 정상 전송 대신 오류나 빈 응답을 반환한다. 실제 HTTP는 없다."""
        if failure == "auth":
            return httpx.Response(401)
        if failure == "timeout":
            raise httpx.ReadTimeout("fixture", request=request)
        if failure == "malformed":
            return httpx.Response(200, json={"unexpected": True})
        if tool_name == "search_rest_area_info":
            return httpx.Response(
                200, json={"header": {"resultCode": "00"}, "body": {"totalCount": 0, "items": ""}}
            )
        return httpx.Response(200, json={"RESULT": {"OIL": []}})

    async def scenario() -> None:
        """Tool별 필수 입력을 전달하고 오류가 data=null과 올바른 플래그를 유지하는지 확인한다."""
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            tools = FuelTools(settings(), client)
            if tool_name == "search_gas_stations":
                result = await tools.search_gas_stations(127, 37, "gasoline")
            elif tool_name == "get_station_details":
                result = await tools.get_station_details("A0010207", "gasoline")
            else:
                result = await tools.search_rest_area_info(127, 37)
        expected = {
            "auth": "AUTH_FAILED",
            "timeout": "TIMEOUT",
            "malformed": "INVALID_RESPONSE",
            "empty": "NOT_FOUND" if tool_name == "get_station_details" else "EMPTY_RESULT",
        }
        assert not result.ok and result.data is None
        assert result.error.code == expected[failure]
        assert result.error.retryable is (failure == "timeout")
        if failure == "auth":
            assert not result.error.replan_allowed

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "tool_name", ["search_gas_stations", "get_station_details", "search_rest_area_info"]
)
def test_invalid_tool_input_is_rejected_before_network(tool_name: str) -> None:
    """각 조회 Tool의 범위 초과·빈 ID가 실제 요청 전에 거절되는지 확인한다."""

    def handler(request: httpx.Request) -> httpx.Response:
        """잘못된 입력에서 호출되면 테스트를 실패시키며 외부 전송은 하지 않는다."""
        raise AssertionError("invalid input reached HTTP")

    async def scenario() -> None:
        """유효하지 않은 명시적 입력마다 INVALID_INPUT과 공통 메타가 반환되는지 확인한다."""
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            tools = FuelTools(settings(), client)
            if tool_name == "search_gas_stations":
                result = await tools.search_gas_stations(127, 37, "gasoline", 5001)
            elif tool_name == "get_station_details":
                result = await tools.get_station_details(" ", "gasoline")
            else:
                result = await tools.search_rest_area_info(181, 37)
        assert result.error.code == "INVALID_INPUT" and result.data is None

    asyncio.run(scenario())
