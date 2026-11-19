"""공식 요청 형식으로 API 연결을 검증한다. 추천용 Tool의 구현과 구분한다."""

import json
from collections.abc import Awaitable, Callable
from time import perf_counter
from typing import Any, Literal
from urllib.parse import quote, quote_plus
from uuid import uuid4
from xml.etree import ElementTree

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from fuelroute.config import Settings
from fuelroute.contracts import ToolError, ToolMeta, ToolResult, utc_now


class ProbeFailure(Exception):
    """예상 가능한 외부 호출 오류만 공통 실패 응답으로 변환하기 위한 예외."""

    def __init__(
        self, code: str, message: str, *, retryable: bool = False, replan_allowed: bool = False
    ) -> None:
        """비밀값 없는 오류 코드·안내와 재시도/재계획 플래그를 보관한다.

        호출자는 제공사 원문 대신 내부 메시지를 전달한다. ToolError를 생성하며
        외부 호출·저장 부작용은 없다. 예: ProbeFailure('AUTH_FAILED', '인증 확인 필요').
        """
        super().__init__(message)
        self.error = ToolError(
            code=code, message=message, retryable=retryable, replan_allowed=replan_allowed
        )


class Coordinate(BaseModel):
    """경도·위도 순서의 유한한 WGS84 좌표(도). KATEC 입력과 혼용 금지."""

    model_config = ConfigDict(allow_inf_nan=False, extra="forbid")
    longitude: float = Field(ge=-180, le=180, description="WGS84 경도(도)")
    latitude: float = Field(ge=-90, le=90, description="WGS84 위도(도)")

    def as_query(self) -> str:
        """Kakao 요청용 '경도,위도' 문자열을 반환한다. 외부 호출·저장은 없다.

        검증된 자기 좌표를 사용한다. 예: longitude=127, latitude=37 → '127.0,37.0'.
        좌표계 변환이나 주소 해석은 하지 않는다.
        """
        return f"{self.longitude},{self.latitude}"


class OpinetSearch(BaseModel):
    """좌표 변환 검증 전 인증 확인용 KATEC 직접 입력. WGS84 Tool 입력이 아니다."""

    model_config = ConfigDict(allow_inf_nan=False, extra="forbid")
    x: float = Field(gt=0, description="공식 예시 등에서 확보한 KATEC X(m)")
    y: float = Field(gt=0, description="공식 예시 등에서 확보한 KATEC Y(m)")
    radius_m: int = Field(default=3000, ge=1, le=5000, description="검색 반경(m), 최대 5000")
    fuel_type: Literal["gasoline", "diesel"] = Field(
        default="gasoline", description="휘발유 gasoline 또는 경유 diesel"
    )


def redact(value: Any, secrets: list[str]) -> Any:
    """JSON 값의 인증 필드와 설정된 키(원문·URL 인코딩)를 재귀적으로 마스킹한다.

    입력은 응답 JSON 또는 공개할 요약이다. dict/list/str를 복사해 정제하며 다른
    스칼라는 그대로 반환한다. 네트워크·저장은 없다. 예: {'certkey': 'x'} → [REDACTED].
    키 자체가 포함된 필드명도 정제하며, 외부 예외 문자열은 애초에 이 함수에 넘기지 않는다.
    """
    sensitive = {"certkey", "servicekey", "api_key", "authorization", "access_token"}
    if isinstance(value, dict):
        return {
            redact(str(key), secrets): (
                "[REDACTED]" if str(key).lower() in sensitive else redact(item, secrets)
            )
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact(item, secrets) for item in value]
    if isinstance(value, str):
        for secret in secrets:
            for token in {secret, quote(secret, safe=""), quote_plus(secret)}:
                if token:
                    value = value.replace(token, "[REDACTED]")
        return value
    return value


async def run_probe(
    settings: Settings, provider: str, name: str, operation: Callable[[], Awaitable[dict]]
) -> ToolResult[dict]:
    """설정 확인 후 검증 작업을 실행해 성공·예상 가능한 실패를 같은 계약으로 반환한다.

    provider는 설정 제공사명, name은 고정 검증명, operation은 지연 실행할 비동기 작업이다.
    설정 부족은 HTTP 요청 전 CONFIG_ERROR로 반환하고 성공 데이터의 키는 마스킹한다.
    ProbeFailure/입력 ValidationError만 변환하며 프로그래밍 오류를 감추지 않는다.
    부작용은 operation의 외부 호출뿐이고 자동 재시도·DB 저장·재계획은 하지 않는다.
    """
    started = perf_counter()
    error = None
    data = None
    try:
        missing = settings.missing(provider)
        if missing:
            raise ProbeFailure("CONFIG_ERROR", "미설정 환경변수: " + ", ".join(missing))
        data = redact(await operation(), settings.secret_values())
    except ProbeFailure as exc:
        error = exc.error
    except ValidationError:
        error = ToolError(code="INVALID_INPUT", message="입력 타입·범위·필수값을 확인하세요.")
    return ToolResult(
        ok=error is None,
        tool_name=name,
        data=data,
        error=error,
        meta=ToolMeta(
            provider=provider,
            request_id=str(uuid4()),
            elapsed_ms=round((perf_counter() - started) * 1000, 3),
            fetched_at=utc_now(),
        ),
    )


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


async def probe_opinet(
    settings: Settings,
    client: httpx.AsyncClient,
    *,
    search: OpinetSearch | None = None,
    station_id: str | None = None,
) -> ToolResult[dict]:
    """Opinet 반경 검색 또는 상세 조회를 네이티브 입력으로 독립 검증한다.

    search는 검증용 KATEC 입력이고 station_id는 검색 응답의 UNI_ID다. 둘 중 하나만
    제공한다. JSON RESULT.OIL 배열과 식별자·이름을 확인해 원본 필드를 반환한다.
    좌표 변환·추천·DB 저장은 하지 않는다. 0건은 EMPTY_RESULT/NOT_FOUND이며
    원문 인증키는 출력하지 않는다. 예: station_id='A0010207'은 공식 예시 ID다.
    """

    async def operation() -> dict:
        """선택한 Opinet 요청을 1회 보내 RESULT.OIL 필수 구조를 검사한다.

        외부 settings/client/입력을 사용하며 검색·상세를 중첩 호출하지 않는다.
        오류 원문은 저장하지 않고 계약 불일치를 INVALID_RESPONSE로 반환한다.
        """
        if (search is None) == (station_id is None):
            raise ProbeFailure(
                "INVALID_INPUT", "KATEC 검색 입력 또는 주유소 ID 중 하나가 필요합니다."
            )
        # 실제 인증 호출 비교에서 certkey는 빈 결과, code는 정상 후보·상세를 반환했다.
        params = {"code": settings.opinet_api_key.get_secret_value(), "out": "json"}
        if search is not None:
            path = "aroundAll.do"
            params.update(
                x=search.x,
                y=search.y,
                radius=search.radius_m,
                prodcd={"gasoline": "B027", "diesel": "D047"}[search.fuel_type],
                sort=1,
            )
        else:
            if not station_id or not station_id.strip():
                raise ProbeFailure("INVALID_INPUT", "주유소 ID는 빈 값일 수 없습니다.")
            path = "detailById.do"
            params["id"] = station_id.strip()
        payload = await get_json(
            client,
            f"https://www.opinet.co.kr/api/{path}",
            params=params,
            timeout=settings.http_timeout_seconds,
        )
        result = payload.get("RESULT")
        if not isinstance(result, dict) or not isinstance(result.get("OIL"), list):
            raise ProbeFailure("INVALID_RESPONSE", "Opinet RESULT.OIL 구조를 확인하세요.")
        rows = result["OIL"]
        if not rows:
            raise ProbeFailure(
                "EMPTY_RESULT" if search else "NOT_FOUND",
                "조회 결과가 없습니다.",
                replan_allowed=True,
            )
        if any(
            not isinstance(row, dict) or not row.get("UNI_ID") or not row.get("OS_NM")
            for row in rows
        ):
            raise ProbeFailure("INVALID_RESPONSE", "주유소 ID 또는 이름이 누락되었습니다.")
        return {"count": len(rows), "response": payload, "coordinate_system": "KATEC"}

    return await run_probe(
        settings, "opinet", "probe_opinet_nearby" if search else "probe_opinet_detail", operation
    )


async def probe_route(
    settings: Settings,
    client: httpx.AsyncClient,
    *,
    origin: Coordinate,
    destination: Coordinate,
    via: Coordinate | None = None,
) -> ToolResult[dict]:
    """Kakao 기본/단일 경유 경로를 같은 옵션으로 조회해 거리·시간·상세 좌표를 검증한다.

    입력은 검증된 WGS84 좌표(경도·위도/도)다. summary=false로 요청하고 첫 경로의
    성공 코드·거리(m)·시간(s)·sections를 확인한다. 반환은 원본 응답과 단위이며
    우회 계산·주유 도달 판정은 하지 않는다. 경로 없음은 NO_ROUTE로 반환한다.
    HTTP 1회 외 부작용·재시도·저장은 없고 예: via를 주면 경유 요청 검증이다.
    """

    async def operation() -> dict:
        """경로 입력을 공식 쿼리로 변환하고 첫 경로의 필수 응답을 검증한다.

        API 오류는 ProbeFailure이며 원문 키·예외 URL을 노출하지 않는다.
        대안 경로·좌표 보간·재계획은 수행하지 않는다.
        """
        params = {
            "origin": origin.as_query(),
            "destination": destination.as_query(),
            "summary": "false",
            "alternatives": "false",
            "priority": "RECOMMEND",
        }
        if via is not None:
            params["waypoints"] = via.as_query()
        payload = await get_json(
            client,
            "https://apis-navi.kakaomobility.com/v1/directions",
            params=params,
            headers={"Authorization": f"KakaoAK {settings.kakao_rest_api_key.get_secret_value()}"},
            timeout=settings.http_timeout_seconds,
        )
        routes = payload.get("routes")
        if not isinstance(routes, list) or not routes or not isinstance(routes[0], dict):
            raise ProbeFailure("INVALID_RESPONSE", "Kakao routes 배열을 확인하세요.")
        route = routes[0]
        if "result_code" not in route:
            raise ProbeFailure("INVALID_RESPONSE", "경로 결과 코드가 없습니다.")
        if route["result_code"] != 0:
            raise ProbeFailure(
                "NO_ROUTE", "요청 조건에서 경로를 찾지 못했습니다.", replan_allowed=True
            )
        summary = route.get("summary")
        if not isinstance(summary, dict) or not isinstance(route.get("sections"), list):
            raise ProbeFailure("INVALID_RESPONSE", "상세 경로·요약 정보가 없습니다.")
        for field in ("distance", "duration"):
            if type(summary.get(field)) is not int or summary[field] < 0:
                raise ProbeFailure("INVALID_RESPONSE", "거리·시간 필드가 유효하지 않습니다.")
        return {
            "distance_m": summary["distance"],
            "duration_s": summary["duration"],
            "response": payload,
        }

    return await run_probe(settings, "kakao", "probe_route", operation)


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
