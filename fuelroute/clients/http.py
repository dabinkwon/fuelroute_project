"""제공사 공통 비동기 GET·JSON 검사와 비밀값 없는 외부 통신 오류 변환."""

import json
from xml.etree import ElementTree

import httpx

from fuelroute.core.errors import ProbeFailure


async def get_json(
    client: httpx.AsyncClient,
    url: str,
    *,
    params: dict,
    headers: dict | None = None,
    timeout: float = 20,
    rate_limit_codes: tuple[int, ...] = (),
) -> dict:
    """고정 제공사 URL에 GET을 1회 보내 JSON 객체를 받는다.

    params/headers는 인증정보를 포함할 수 있어 기록하지 않는다. timeout은 초다.
    HTTP 인증·한도·서버 오류와 타임아웃을 ProbeFailure로 변환한다. XML 게이트웨이
    오류도 구분하며 비정상 JSON은 INVALID_RESPONSE다. rate_limit_codes가 있으면 HTTP
    오류 본문의 code도 비교한다. 예: Kakao Local의 HTTP 400/code=-10은 RATE_LIMITED다.
    리다이렉트·자동 재시도는 없다. 오류 본문·인증정보는 반환하지 않는다.
    """
    try:
        response = await client.get(
            url, params=params, headers=headers, timeout=timeout, follow_redirects=False
        )
    except httpx.TimeoutException as exc:
        raise ProbeFailure(
            "TIMEOUT", "외부 API 응답 시간이 초과되었습니다.", retryable=True
        ) from exc
    except httpx.RequestError as exc:
        raise ProbeFailure(
            "UPSTREAM_ERROR", "외부 API에 연결할 수 없습니다.", retryable=True
        ) from exc
    if rate_limit_codes and response.status_code >= 400:
        try:
            error_body = response.json()
        except ValueError:
            error_body = None
        if isinstance(error_body, dict) and error_body.get("code") in rate_limit_codes:
            raise ProbeFailure("RATE_LIMITED", "호출 한도를 확인하세요.", retryable=True)
    if response.status_code in (401, 403):
        raise ProbeFailure("AUTH_FAILED", "인증키와 API 사용 권한을 확인하세요.")
    if response.status_code == 429:
        raise ProbeFailure("RATE_LIMITED", "호출 한도를 확인한 뒤 재시도하세요.", retryable=True)
    if response.status_code >= 500:
        raise ProbeFailure("UPSTREAM_ERROR", "제공사 서버 오류입니다.", retryable=True)
    if not response.is_success:
        raise ProbeFailure("UPSTREAM_ERROR", f"외부 API HTTP 오류: {response.status_code}")
    try:
        payload = response.json()
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        # 공공데이터 게이트웨이는 JSON 요청에도 XML 인증 오류를 반환할 수 있다.
        try:
            root = ElementTree.fromstring(response.content)
        except ElementTree.ParseError:
            raise ProbeFailure("INVALID_RESPONSE", "JSON 응답을 해석할 수 없습니다.") from exc
        code = root.findtext(".//returnReasonCode")
        if code == "22":
            raise ProbeFailure("RATE_LIMITED", "공공데이터 호출 한도를 확인하세요.", retryable=True)
        if code in {"20", "21", "30", "31", "32"}:
            raise ProbeFailure("AUTH_FAILED", "공공데이터 인증키·사용 권한을 확인하세요.") from exc
        raise ProbeFailure(
            "INVALID_RESPONSE", "제공사가 XML 오류 또는 예상 밖 응답을 반환했습니다."
        )
    if not isinstance(payload, dict):
        raise ProbeFailure("INVALID_RESPONSE", "JSON 최상위 객체가 필요합니다.")
    return payload
