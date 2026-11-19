"""Kakao 자동차 기본·단일 경유 경로를 동일 옵션으로 조회하고 원본 거리·시간을 검사한다."""

import httpx

from fuelroute.clients.http import get_json
from fuelroute.core.config import Settings
from fuelroute.core.contracts import ToolResult
from fuelroute.core.errors import ProbeFailure
from fuelroute.core.results import run_probe
from fuelroute.schemas.coordinates import Coordinate


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
