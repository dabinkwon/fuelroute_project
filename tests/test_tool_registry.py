"""통합 Tool의 실제 직렬화 schema·입출력·실패 payload를 검증한다. HTTP는 합성이다."""

import asyncio
import json

import httpx
import pytest
from langchain_core.utils.function_calling import convert_to_openai_tool

from fuelroute.config import Settings
from fuelroute.contracts import ToolResult
from fuelroute.coordinates import wgs84_to_katec
from fuelroute.tool_registry import ToolRegistry

NAMES = {
    "calculate_safe_range",
    "get_driving_route",
    "search_gas_stations",
    "get_station_details",
    "search_rest_area_info",
    "assess_candidate_routes",
    "rank_candidates",
}


def settings() -> Settings:
    """환경 파일을 읽지 않고 모의 요청에만 사용할 세 제공사 키를 반환한다."""
    return Settings(
        _env_file=None,
        kakao_rest_api_key="test",
        opinet_api_key="test",
        data_go_kr_service_key="test",
    )


def cost(distance: int, duration: int, legs: list[int] | None = None) -> dict:
    """명세의 m/s 단위를 사용하는 정규화 합성 비용을 만든다. 실제 도로 데이터가 아니다."""
    lengths = legs or [distance]
    return {
        "distance_m": distance,
        "duration_s": duration,
        "legs": [
            {"distance_m": length, "duration_s": duration // len(lengths)} for length in lengths
        ],
    }


def test_serialized_schemas_and_tool_call_payloads() -> None:
    """일곱 이름·필드 설명·공통 출력 schema와 LangChain ToolCall의 JSON 응답을 확인한다."""

    async def scenario() -> None:
        """모델에 보낼 schema와 로컬 계산의 실제 ToolMessage만 검사한다. API 호출은 없다."""
        async with httpx.AsyncClient() as client:
            registry = ToolRegistry(Settings(_env_file=None), client)
            tools = {tool.name: tool for tool in registry.registered_tools()}
            assert set(tools) == NAMES
            contracts = registry.contracts()
            for name, tool in tools.items():
                schema = convert_to_openai_tool(tool)["function"]
                assert len(schema["description"]) > 100
                assert all(
                    field.get("description")
                    for field in schema["parameters"]["properties"].values()
                )
                assert set(contracts[name]["output_schema"]["properties"]) == {
                    "ok",
                    "tool_name",
                    "data",
                    "error",
                    "meta",
                }
                assert contracts[name]["next_fields"]
                assert "INVALID_INPUT" in contracts[name]["error_codes"]
                assert ("AUTH_FAILED" in contracts[name]["error_codes"]) == (
                    contracts[name]["provider"] != "local"
                )
            payload = {
                "name": "calculate_safe_range",
                "id": "call-1",
                "type": "tool_call",
                "args": {"current_fuel_l": 15, "reserve_fuel_l": 3, "avg_efficiency_kmpl": 12},
            }
            response = await tools["calculate_safe_range"].ainvoke(payload)
            body = json.loads(response.content)
            assert body["ok"] and body["data"] == {"safe_range_km": 144}
            bad = await tools["calculate_safe_range"].ainvoke({**payload, "args": {}})
            assert json.loads(bad.content)["error"]["code"] == "INVALID_INPUT"

    asyncio.run(scenario())


@pytest.mark.parametrize("name", sorted(NAMES))
def test_invalid_registered_arguments_have_common_failure(name: str) -> None:
    """직렬화된 Tool도 누락·추가 필드를 Pydantic 예외 대신 공통 INVALID_INPUT으로 반환한다."""

    def handler(request: httpx.Request) -> httpx.Response:
        """입력 오류에서 HTTP가 실행되면 실패시킨다. 외부 네트워크는 열지 않는다."""
        raise AssertionError("입력 오류에서 HTTP 호출")

    async def scenario() -> None:
        """설정은 합성 값으로 충족시켜 실제 입력 검증 경로를 검사한다."""
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            registry = ToolRegistry(settings(), client)
            tool = next(item for item in registry.registered_tools() if item.name == name)
            result = await tool.ainvoke({"unexpected": True})
            assert not result["ok"] and result["data"] is None
            assert result["error"]["code"] == "INVALID_INPUT"
            assert result["meta"]["fetched_at"]

    asyncio.run(scenario())


def test_external_lookup_route_assessment_and_ranking_are_chainable() -> None:
    """실제 요청 형식의 합성 응답을 후보→상세→경로 비용→평가→추천으로 연결한다."""
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        """제공사별로 다른 원본 구조를 합성한다. 실제 인증키·실시간 데이터를 사용하지 않는다."""
        calls.append(request.url.host)
        if request.url.host == "www.opinet.co.kr":
            assert request.url.params["code"] == "test"
            x, y = wgs84_to_katec(127.1, 37)
            row = {
                "UNI_ID": "A1",
                "OS_NM": "합성 주유소",
                "GIS_X_COOR": x,
                "GIS_Y_COOR": y,
                "PRICE": 1800,
                "DISTANCE": 100,
                "OIL_PRICE": [{"PRODCD": "B027", "PRICE": 1800}],
            }
            return httpx.Response(200, json={"RESULT": {"OIL": [row]}})
        if request.url.host == "api.data.go.kr":
            return httpx.Response(
                200,
                json={
                    "header": {"resultCode": "00"},
                    "body": {
                        "totalCount": 1,
                        "items": {
                            "item": [
                                {
                                    "entrpsNm": "합성 휴게소",
                                    "longitude": 127.1,
                                    "latitude": 37,
                                    "referenceDate": "2026-11-18",
                                    "toiletYn": "Y",
                                }
                            ]
                        },
                    },
                },
            )
        assert request.headers["Authorization"] == "KakaoAK test"
        return httpx.Response(
            200,
            json={
                "routes": [
                    {
                        "result_code": 0,
                        "summary": {"distance": 20000, "duration": 2000},
                        "sections": [{"distance": 20000, "duration": 2000}],
                    }
                ]
            },
        )

    async def scenario() -> None:
        """정규화 필드를 다음 함수에 전달하며 네 번의 조회 이외 HTTP가 없는지 확인한다."""
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            registry = ToolRegistry(settings(), client)
            nearby = await registry.invoke(
                "search_gas_stations", {"longitude": 127.1, "latitude": 37, "fuel_type": "gasoline"}
            )
            assert nearby.ok
            station_id = nearby.data["items"][0]["station_id"]
            detail = await registry.invoke(
                "get_station_details", {"station_id": station_id, "fuel_type": "gasoline"}
            )
            route = await registry.invoke(
                "get_driving_route",
                {
                    "origin": {"longitude": 127, "latitude": 37},
                    "destination": {"longitude": 129, "latitude": 37},
                },
            )
            assert route.ok and set(route.data) == {"distance_m", "duration_s", "legs"}
            assessment = await registry.invoke(
                "assess_candidate_routes",
                {
                    "base": route.data,
                    "approach": cost(10000, 1000),
                    "via": cost(22000, 2100, [10000, 12000]),
                    "safe_range_km": 144,
                    "max_detour_min": 5,
                },
            )
            assert assessment.ok and assessment.data["eligible"]
            ranked = await registry.invoke(
                "rank_candidates",
                {
                    "fuel_type": "gasoline",
                    "safe_range_km": 144,
                    "candidates": [
                        {"station_id": station_id, "station": detail.data, **assessment.data}
                    ],
                },
            )
            assert ranked.ok and ranked.data["recommended"]["station_id"] == station_id
            rest = await registry.invoke(
                "search_rest_area_info", {"longitude": 127.1, "latitude": 37}
            )
            assert rest.ok and rest.data["items"][0]["match_status"] == "unverified"
            unknown = await registry.invoke("does_not_exist", {})
            assert not unknown.ok and unknown.error.code == "INVALID_INPUT"
        assert len(calls) == 4

    asyncio.run(scenario())


def test_route_response_and_output_contract_failures_are_not_input_errors(monkeypatch) -> None:
    """제공사 비용 합계 오류와 조회 Tool의 잘못된 출력은 INVALID_RESPONSE로 구분한다."""

    def handler(request: httpx.Request) -> httpx.Response:
        """요약 100m에 구간 99m를 주어 파싱 이후 출력 검증을 실패시킨다."""
        return httpx.Response(
            200,
            json={
                "routes": [
                    {
                        "result_code": 0,
                        "summary": {"distance": 100, "duration": 60},
                        "sections": [{"distance": 99, "duration": 60}],
                    }
                ]
            },
        )

    async def scenario() -> None:
        """외부 응답 검증 실패와 조회 성공 payload의 계약 위반을 각각 확인한다."""
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            registry = ToolRegistry(settings(), client)
            result = await registry.invoke(
                "get_driving_route",
                {
                    "origin": {"longitude": 127, "latitude": 37},
                    "destination": {"longitude": 128, "latitude": 37},
                },
            )
            assert not result.ok and result.error.code == "INVALID_RESPONSE"

            async def invalid_detail(**arguments) -> ToolResult:
                """상세 성공을 가장한 필드 누락 응답을 만들어 출력 계약 검사를 확인한다."""
                return ToolResult(
                    ok=True,
                    tool_name="get_station_details",
                    data={"station_id": "A0000001"},
                    error=None,
                    meta=result.meta,
                )

            monkeypatch.setattr(registry.lookups, "get_station_details", invalid_detail)
            invalid = await registry.invoke(
                "get_station_details", {"station_id": "A0000001", "fuel_type": "gasoline"}
            )
            assert not invalid.ok and invalid.error.code == "INVALID_RESPONSE"

    asyncio.run(scenario())
