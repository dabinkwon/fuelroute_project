"""문서 형식의 합성 응답으로 HTTP 요청·실패 계약을 검증한다. 실호출 증거가 아니다."""

import asyncio
from urllib.parse import quote

import httpx
import pytest

from fuelroute.config import Settings
from fuelroute.probes import (
    Coordinate,
    OpinetSearch,
    probe_opinet,
    probe_rest_area,
    probe_route,
    redact,
)


def settings() -> Settings:
    """실제 인증에 사용하지 않는 테스트 전용 키 설정을 반환한다. .env는 읽지 않는다."""
    return Settings(
        _env_file=None,
        opinet_api_key="test-opinet-key",
        kakao_rest_api_key="test-kakao-key",
        data_go_kr_service_key="a+b/c=",
    )


def test_opinet_search_then_independent_detail() -> None:
    """KATEC/유종 쿼리와 검색 ID→상세 연결을 모의 전송으로 검증한다. 좌표 변환은 없다."""
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        """요청만 기록하고 합성 주유소 1건을 돌려준다. 실제 네트워크를 대체한다."""
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "RESULT": {
                    "OIL": [
                        {
                            "UNI_ID": "example",
                            "OS_NM": "예시",
                            "PRICE": 1700,
                            "certkey": "test-opinet-key",
                        }
                    ]
                }
            },
        )

    async def scenario() -> None:
        """클라이언트를 공유하되 두 검증 함수를 각각 호출하고 키 마스킹을 확인한다."""
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            nearby = await probe_opinet(settings(), client, search=OpinetSearch(x=314681, y=544837))
            assert nearby.ok
            station_id = nearby.data["response"]["RESULT"]["OIL"][0]["UNI_ID"]
            detail = await probe_opinet(settings(), client, station_id=station_id)
            assert detail.ok
            assert "test-opinet-key" not in detail.model_dump_json()
            assert detail.meta.request_id != nearby.meta.request_id

    asyncio.run(scenario())
    assert len(requests) == 2
    assert requests[0].url.params["prodcd"] == "B027"
    assert requests[0].url.params["code"] == "test-opinet-key"
    assert "certkey" not in requests[0].url.params
    assert requests[0].url.params["x"] == "314681.0"
    assert requests[1].url.path.endswith("detailById.do")
    assert requests[1].url.params["id"] == "example"


@pytest.mark.parametrize(
    ("status", "code", "retryable"),
    [
        (401, "AUTH_FAILED", False),
        (403, "AUTH_FAILED", False),
        (429, "RATE_LIMITED", True),
        (503, "UPSTREAM_ERROR", True),
        (302, "UPSTREAM_ERROR", False),
    ],
)
def test_http_errors_are_not_success(status: int, code: str, retryable: bool) -> None:
    """인증/한도/서버/리다이렉트 실패를 구분하고 인증 포함 원문을 숨긴다."""

    def handler(request: httpx.Request) -> httpx.Response:
        """키가 포함된 URL을 오류 본문에 넣어도 출력되지 않는지 확인할 합성 응답이다."""
        return httpx.Response(
            status, text=str(request.url), headers={"Location": "https://evil.test"}
        )

    async def scenario() -> None:
        """재시도 없이 외부 오류가 공통 실패로 변환되는지 확인한다."""
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await probe_opinet(settings(), client, station_id="example")
        assert not result.ok and result.data is None
        assert result.error.code == code and result.error.retryable is retryable
        assert not result.error.replan_allowed
        assert "test-opinet-key" not in result.model_dump_json()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        ({"RESULT": {"OIL": []}}, "NOT_FOUND"),
        ({"RESULT": {"OIL": [{"OS_NM": "ID 없음"}]}}, "INVALID_RESPONSE"),
        ({"error": "인증 여부 미확인"}, "INVALID_RESPONSE"),
    ],
)
def test_opinet_empty_or_malformed(payload: dict, expected: str) -> None:
    """빈 결과·필수 필드 누락·예상 밖 제공사 오류를 성공으로 처리하지 않는다."""

    async def scenario() -> None:
        """주입한 JSON을 1회 처리하고 오류 코드와 시간대 있는 메타를 확인한다."""
        transport = httpx.MockTransport(lambda request: httpx.Response(200, json=payload))
        async with httpx.AsyncClient(transport=transport) as client:
            result = await probe_opinet(settings(), client, station_id="example")
        assert result.error.code == expected
        assert result.meta.fetched_at.utcoffset() is not None

    asyncio.run(scenario())


def test_timeout_and_missing_key_preflight() -> None:
    """타임아웃은 재시도 가능 실패, 키 누락은 전송 전 실패이며 무한 재시도하지 않는다."""
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        """실제 연결 대신 타임아웃을 발생시키고 요청 횟수를 기록한다."""
        requests.append(request)
        raise httpx.ReadTimeout("sensitive URL", request=request)

    async def scenario() -> None:
        """키가 없는 요청은 차단하고 정상 설정의 타임아웃 호출은 한 번만 수행한다."""
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            missing = await probe_opinet(Settings(_env_file=None), client, station_id="example")
            assert missing.error.code == "CONFIG_ERROR"
            assert not requests
            timeout = await probe_opinet(settings(), client, station_id="example")
            assert timeout.error.code == "TIMEOUT" and timeout.error.retryable

    asyncio.run(scenario())
    assert len(requests) == 1


def test_route_waypoint_units_and_no_route() -> None:
    """경유 좌표·동일 옵션·m/s 단위를 확인하고 경로 실패를 재계획 가능으로 분류한다."""
    requests = []
    payload = {
        "routes": [
            {"result_code": 0, "summary": {"distance": 1000, "duration": 60}, "sections": []}
        ]
    }

    def handler(request: httpx.Request) -> httpx.Response:
        """거리·시간을 가진 모의 Kakao 응답을 반환하고 인증 헤더를 검사한다."""
        requests.append(request)
        assert request.headers["Authorization"] == "KakaoAK test-kakao-key"
        return httpx.Response(200, json=payload)

    async def scenario() -> None:
        """동일 좌표 입력으로 정상과 NO_ROUTE 응답을 각각 검증한다."""
        point = Coordinate(longitude=127, latitude=37)
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await probe_route(
                settings(), client, origin=point, destination=point, via=point
            )
            assert result.data["distance_m"] == 1000 and result.data["duration_s"] == 60
            payload["routes"][0]["result_code"] = 104
            failure = await probe_route(settings(), client, origin=point, destination=point)
            assert failure.error.code == "NO_ROUTE" and failure.error.replan_allowed

    asyncio.run(scenario())
    assert requests[0].url.params["waypoints"] == "127.0,37.0"
    assert requests[0].url.params["summary"] == "false"


def test_rest_area_page_and_decoded_key() -> None:
    """휴게소 한 페이지 요청·서비스키 단일 인코딩·데이터 기준일 보존을 검증한다."""

    def handler(request: httpx.Request) -> httpx.Response:
        """요청의 키·페이지를 확인하고 합성 휴게소 1건을 반환한다."""
        assert request.url.params["serviceKey"] == "a+b/c="
        assert request.url.params["pageNo"] == "2"
        return httpx.Response(
            200,
            json={
                "response": {
                    "header": {"resultCode": "00"},
                    "body": {
                        "totalCount": "2",
                        "items": [{"entrpsNm": "예시 휴게소", "referenceDate": "2026-11-01"}],
                    },
                }
            },
        )

    async def scenario() -> None:
        """조회 시각과 원본 기준일을 혼동하지 않고 결과를 보존한다."""
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await probe_rest_area(settings(), client, page=2, rows=1)
        assert result.ok and result.data["total_count"] == 2
        assert (
            result.data["response"]["response"]["body"]["items"][0]["referenceDate"] == "2026-11-01"
        )

    asyncio.run(scenario())


def test_xml_gateway_auth_and_redaction() -> None:
    """JSON 요청에 반환된 XML 인증 오류와 원문/인코딩된 키의 마스킹을 확인한다."""

    async def scenario() -> None:
        """XML 게이트웨이 오류를 AUTH_FAILED로 변환한다. 실호출은 없다."""
        transport = httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                text="<OpenAPI_ServiceResponse><cmmMsgHeader><returnReasonCode>30"
                "</returnReasonCode></cmmMsgHeader></OpenAPI_ServiceResponse>",
            )
        )
        async with httpx.AsyncClient(transport=transport) as client:
            result = await probe_rest_area(settings(), client)
        assert result.error.code == "AUTH_FAILED"

    asyncio.run(scenario())
    key = "a+b/c="
    assert redact([key, quote(key, safe=""), {"authorization": "Bearer other"}], [key]) == [
        "[REDACTED]",
        "[REDACTED]",
        {"authorization": "[REDACTED]"},
    ]
