"""휴게소 표준데이터의 한 페이지를 독립 조회하고 인증·건수·필수 구조를 검사한다."""

import httpx

from fuelroute.clients.http import get_json
from fuelroute.core.config import Settings
from fuelroute.core.contracts import ToolResult
from fuelroute.core.errors import ProbeFailure
from fuelroute.core.results import run_probe


async def probe_rest_area(
    settings: Settings, client: httpx.AsyncClient, *, page: int = 1, rows: int = 10
) -> ToolResult[dict]:
    """휴게소 표준데이터 API의 인증·한 페이지 응답을 검증한다.

    page는 1 이상, rows는 1~1000이다. decoded serviceKey를 httpx로 한 번 인코딩해
    전송하고 resultCode·items·totalCount를 검사한다. 전체 페이지 수집·반경 검색·
    주유소 매칭은 후속 구현이다. 0건은 EMPTY_RESULT이며 기준일은 원본 필드에 남긴다.
    HTTP 1회 외 부작용은 없다. 예: page=1, rows=10으로 최소 응답을 확인한다.
    """

    async def operation() -> dict:
        """페이지 범위와 공공데이터 표준 응답을 확인해 실제 필드 검토용 JSON을 반환한다.

        설정된 키를 쿼리에 보내되 출력에서 제거한다. 인증/한도 오류와 0건을 구분하며
        제공사 메시지를 그대로 노출하지 않는다.
        """
        if type(page) is not int or type(rows) is not int or page < 1 or not 1 <= rows <= 1000:
            raise ProbeFailure("INVALID_INPUT", "page>=1, rows=1~1000 범위를 사용하세요.")
        payload = await get_json(
            client,
            "https://api.data.go.kr/openapi/tn_pubr_public_rest_area_api",
            params={
                "serviceKey": settings.data_go_kr_service_key.get_secret_value(),
                "pageNo": page,
                "numOfRows": rows,
                "type": "json",
            },
            timeout=settings.http_timeout_seconds,
        )
        response = payload.get("response", payload)
        if not isinstance(response, dict) or not isinstance(response.get("header"), dict):
            raise ProbeFailure("INVALID_RESPONSE", "공공데이터 response.header를 확인하세요.")
        code = str(response["header"].get("resultCode", ""))
        if code == "22":
            raise ProbeFailure("RATE_LIMITED", "공공데이터 호출 한도를 확인하세요.", retryable=True)
        if code in {"20", "21", "30", "31", "32"}:
            raise ProbeFailure("AUTH_FAILED", "공공데이터 인증키·권한을 확인하세요.")
        if code == "03":
            raise ProbeFailure("EMPTY_RESULT", "휴게소 결과가 없습니다.")
        if code != "00":
            raise ProbeFailure("UPSTREAM_ERROR", "공공데이터 응답 코드를 확인하세요.")
        body = response.get("body")
        if not isinstance(body, dict) or "totalCount" not in body:
            raise ProbeFailure("INVALID_RESPONSE", "공공데이터 본문·전체 개수가 없습니다.")
        try:
            total = int(body["totalCount"])
        except (ValueError, TypeError) as exc:
            raise ProbeFailure("INVALID_RESPONSE", "전체 개수가 숫자가 아닙니다.") from exc
        if total < 0:
            raise ProbeFailure("INVALID_RESPONSE", "전체 개수는 음수일 수 없습니다.")
        items = body.get("items")
        if isinstance(items, dict):
            items = items.get("item")
        if isinstance(items, dict):
            items = [items]
        if total == 0 or items in (None, "", []):
            raise ProbeFailure("EMPTY_RESULT", "해당 페이지에 휴게소 결과가 없습니다.")
        if not isinstance(items, list) or any(not isinstance(item, dict) for item in items):
            raise ProbeFailure("INVALID_RESPONSE", "휴게소 items 배열을 확인하세요.")
        return {"page": page, "total_count": total, "count": len(items), "response": payload}

    return await run_probe(settings, "rest_area", "probe_rest_area", operation)
