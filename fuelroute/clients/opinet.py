"""Opinet KATEC 반경 검색·ID 상세를 호출하고 원본 응답 계약을 검증한다."""

import httpx

from fuelroute.clients.http import get_json
from fuelroute.core.config import Settings
from fuelroute.core.contracts import ToolResult
from fuelroute.core.errors import ProbeFailure
from fuelroute.core.results import run_probe
from fuelroute.schemas.stations import OpinetSearch


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
