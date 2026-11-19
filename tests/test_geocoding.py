"""지명·주소의 실제 요청 형식과 모호성·실패·분석 노드 연결을 모의 전송으로 검증한다."""

import asyncio
from datetime import date

import httpx
import pytest

from fuelroute.agent import analyze_request
from fuelroute.config import Settings
from fuelroute.geocoding import resolve_location
from fuelroute.request_analysis import ExtractedRequest

USER = "대구에서 서울로 가는데 휘발유이고 현재 연료15L, 안전 잔량3L, 연비12km/L로 주유 추천해줘."


def settings() -> Settings:
    """환경 파일을 읽지 않는 Kakao Local 테스트 설정을 반환한다. 실제 키가 아니다."""
    return Settings(_env_file=None, kakao_rest_api_key="test-local-key")


def address(name: str = "서울", x: str = "126.978", y: str = "37.5665") -> dict:
    """단일 지역 검색 문서를 합성한다. 실제 제공사 조회 결과로 해석하지 않는다."""
    return {"address_name": name, "address_type": "REGION", "x": x, "y": y, "road_address": None}


def body(documents: list, total: int | None = None) -> dict:
    """첫 페이지에 없는 결과도 포함하는 전체 개수를 명시한 합성 응답을 만든다."""
    count = len(documents) if total is None else total
    return {
        "meta": {"total_count": count, "pageable_count": count, "is_end": count <= len(documents)},
        "documents": documents,
    }


@pytest.mark.parametrize(
    "mode", ["unique", "street", "ambiguous", "truncated", "keyword", "empty", "invalid"]
)
def test_address_keyword_fallback_and_ambiguity(mode: str) -> None:
    """유일한 결과만 선택하고 모호성·빈 결과·응답 오류와 최대 2회 호출을 검증한다."""
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        """카카오 요청의 인증·검색점 제한을 확인하고 정해진 응답만 합성한다."""
        calls.append(request.url.path)
        assert request.headers["Authorization"] == "KakaoAK test-local-key"
        assert request.url.params["query"] == "서울" and request.url.params["size"] == "5"
        if request.url.path.endswith("address.json"):
            assert request.url.params["analyze_type"] == "exact"
            if mode in {"keyword", "empty"}:
                return httpx.Response(200, json=body([]))
            if mode == "ambiguous":
                return httpx.Response(200, json=body([address("위치 A"), address("위치 B")]))
            if mode == "truncated":
                return httpx.Response(200, json=body([address()], total=100))
            if mode == "invalid":
                return httpx.Response(200, json=body([address(x="not-a-number")]))
            if mode == "street":
                row = address("서울 합성로 1")
                row.update(address_type="ROAD_ADDR", road_address={"address_name": "서울 합성로 1"})
                return httpx.Response(200, json=body([row]))
            return httpx.Response(200, json=body([address()]))
        assert request.url.path.endswith("keyword.json")
        if mode == "empty":
            return httpx.Response(200, json=body([]))
        return httpx.Response(
            200,
            json=body(
                [
                    {
                        "id": "test-place",
                        "place_name": "합성 장소",
                        "address_name": "서울 합성 주소",
                        "road_address_name": "",
                        "x": "127",
                        "y": "37",
                        "place_url": "http://place.map.kakao.com/test",
                    }
                ]
            ),
        )

    async def scenario() -> None:
        """MockTransport로만 독립 위치 검색을 실행하고 공통 응답과 호출 수를 확인한다."""
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await resolve_location("서울", settings(), client)
        assert len(calls) == (2 if mode in {"keyword", "empty"} else 1)
        assert "test-local-key" not in result.model_dump_json()
        if mode in {"invalid", "empty"}:
            assert not result.ok and result.error.code == (
                "INVALID_RESPONSE" if mode == "invalid" else "EMPTY_RESULT"
            )
            return
        assert result.ok
        if mode in {"ambiguous", "truncated"}:
            assert result.data["status"] == "ambiguous" and result.data["selected"] is None
        else:
            assert result.data["status"] == "resolved"
            selected = result.data["selected"]
            assert selected["match_level"] == (
                "place" if mode == "keyword" else "address" if mode == "street" else "area"
            )
            assert selected["source"] == ("kakao_keyword" if mode == "keyword" else "kakao_address")

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("status", "payload", "code"),
    [
        (403, {}, "AUTH_FAILED"),
        (429, {}, "RATE_LIMITED"),
        (400, {"code": -10}, "RATE_LIMITED"),
        (500, {}, "UPSTREAM_ERROR"),
        ("timeout", {}, "TIMEOUT"),
    ],
)
def test_provider_errors_stop_without_keyword_fallback(
    status: int | str, payload: dict, code: str
) -> None:
    """권한·쿼터·서버 오류를 빈 검색 결과로 취급하지 않고 한 번의 호출로 종료한다."""
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        """API 오류를 합성하고 재시도나 키워드 대체 호출 여부를 기록한다."""
        calls.append(request.url.path)
        if status == "timeout":
            raise httpx.ReadTimeout("합성 타임아웃", request=request)
        return httpx.Response(status, json=payload)

    async def scenario() -> None:
        """외부 오류 코드와 한 번의 호출 상한을 확인한다. 실제 네트워크는 없다."""
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await resolve_location("서울", settings(), client)
        assert not result.ok and result.error.code == code and len(calls) == 1

    asyncio.run(scenario())


def test_query_and_key_are_checked_before_network() -> None:
    """빈 지명·과도한 길이·키 누락은 HTTP 전에 공통 실패로 반환한다."""

    def handler(request: httpx.Request) -> httpx.Response:
        """사전 검증 실패에서 네트워크를 호출하면 테스트를 실패시킨다."""
        raise AssertionError("HTTP 호출 금지")

    async def scenario() -> None:
        """입력/설정 오류를 각각 검증하며 예외 원문·키를 출력하지 않는다."""
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            for query in (" ", "x" * 201):
                invalid = await resolve_location(query, settings(), client)
                assert not invalid.ok and invalid.error.code == "INVALID_INPUT"
            missing = await resolve_location("서울", Settings(_env_file=None), client)
            assert not missing.ok and missing.error.code == "CONFIG_ERROR"

    asyncio.run(scenario())


def extracted(**updates) -> ExtractedRequest:
    """좌표 없이 지명·유종·연료만 있는 요청의 모델 대역 출력을 만든다."""
    values = {
        "intent": "recommend",
        "reason": "주유 추천 요청",
        "origin_label": {"value": "대구", "quote": "대구"},
        "destination_label": {"value": "서울", "quote": "서울"},
        "fuel_type": {"value": "gasoline", "quote": "휘발유"},
        "current_fuel_l": {"value": 15, "quote": "현재 연료15L"},
        "reserve_fuel_l": {"value": 3, "quote": "안전 잔량3L"},
        "avg_efficiency_kmpl": {"value": 12, "quote": "연비12km/L"},
    }
    return ExtractedRequest.model_validate({**values, **updates})


@pytest.mark.parametrize(
    "mode",
    [
        "unique",
        "ambiguous",
        "empty",
        "auth",
        "duplicate",
        "explicit",
        "greeting",
        "invalid_label",
        "invalid_coordinate",
        "missing_fuel",
    ],
)
def test_analysis_location_node_preserves_user_values_and_reuses_results(mode: str) -> None:
    """실제 분석 그래프와 검색 함수를 연결해 자동 변환·입력 보완·조회 생략을 검증한다."""
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        """주소 검색 결과를 합성하고 같은 지명 및 전역 장애의 중복 요청을 감지한다."""
        calls.append(request.url.params["query"])
        if mode == "auth":
            return httpx.Response(403, json={})
        if mode == "empty":
            return httpx.Response(200, json=body([]))
        if mode == "ambiguous" and request.url.params["query"] == "대구":
            return httpx.Response(200, json=body([address("후보 A"), address("후보 B")]))
        return httpx.Response(200, json=body([address(request.url.params["query"])]))

    async def model(user: str) -> ExtractedRequest:
        """테스트별 조건만 바꾸며 GMS를 호출하지 않고 모델 출력을 주입한다."""
        if mode == "greeting":
            return ExtractedRequest(intent="greeting", reason="인사 요청")
        if mode == "duplicate":
            return extracted(destination_label={"value": "대구", "quote": "대구"})
        if mode == "explicit":
            return extracted(
                origin={"value": {"longitude": 128, "latitude": 35}, "quote": "출발좌표128,35"}
            )
        if mode == "invalid_label":
            return extracted(origin_label={"value": "대구" * 101, "quote": "대구" * 101})
        if mode == "invalid_coordinate":
            return extracted(
                origin={"value": {"longitude": 181, "latitude": 95}, "quote": "출발좌표181,95"}
            )
        if mode == "missing_fuel":
            return extracted(
                fuel_type=None, current_fuel_l=None, reserve_fuel_l=None, avg_efficiency_kmpl=None
            )
        return extracted()

    async def scenario() -> None:
        """MockTransport 검색을 resolver로 주입해 분석·검색 후 누락 상태를 확인한다."""
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:

            async def lookup(label: str):
                """같은 클라이언트의 모의 검색을 분석 그래프에서 호출하도록 주입한다."""
                return await resolve_location(label, settings(), client)

            result = await analyze_request(
                USER
                + " 출발좌표128,35 출발좌표181,95 "
                + ("대구" * 101 if mode == "invalid_label" else ""),
                settings(),
                analyzer=model,
                reference_date=date(2026, 11, 19),
                location_resolver=lookup,
            )
        assert result.ok
        analysis = result.data["analysis"]
        if mode in {"ambiguous", "empty", "auth", "invalid_label", "invalid_coordinate"}:
            assert analysis["needs_user_input"] and not analysis["ready_for_planning"]
            assert analysis["conditions"]["origin"] is None
            assert not any("좌표" in question for question in analysis["questions"])
            if mode == "auth":
                assert len(calls) == 1
            elif mode == "invalid_label":
                assert calls == ["서울"]
                assert analysis["location_results"]["origin"]["error"]["code"] == "INVALID_INPUT"
            elif mode == "invalid_coordinate":
                assert calls == ["서울"] and "origin" in analysis["invalid_fields"]
                assert "origin" not in analysis["location_results"]
            elif mode == "empty":
                assert len(calls) == 4
            else:
                assert len(calls) == 2
                assert len(analysis["location_results"]["origin"]["data"]["candidates"]) == 2
        elif mode == "greeting":
            assert calls == [] and not analysis["needs_user_input"]
        elif mode == "missing_fuel":
            assert len(calls) == 2 and analysis["needs_user_input"]
            assert set(analysis["missing_fields"]) == {
                "fuel_type",
                "remaining_range_km",
                "reserve_range_km",
            }
            assert (
                analysis["conditions"]["origin"] is not None
                and analysis["conditions"]["destination"] is not None
            )
            assert not any("좌표" in question for question in analysis["questions"])
        else:
            assert analysis["ready_for_planning"] and not analysis["missing_fields"]
            assert analysis["coordinate_sources"]["destination"] == "kakao_address"
            assert analysis["warnings"]
            if mode == "explicit":
                assert calls == ["서울"] and analysis["conditions"]["origin"]["longitude"] == 128
                assert analysis["coordinate_sources"]["origin"] == "user"
            else:
                assert len(calls) == (1 if mode == "duplicate" else 2)
                if mode == "duplicate":
                    async with httpx.AsyncClient(
                        transport=httpx.MockTransport(handler)
                    ) as second_client:

                        async def repeated_lookup(label: str):
                            """새 실행에서 이전 실행 캐시를 쓰지 않는지 모의 검색으로 확인한다."""
                            return await resolve_location(label, settings(), second_client)

                        repeated = await analyze_request(
                            USER,
                            settings(),
                            analyzer=model,
                            reference_date=date(2026, 11, 19),
                            location_resolver=repeated_lookup,
                        )
                    assert repeated.ok and len(calls) == 2

    asyncio.run(scenario())
