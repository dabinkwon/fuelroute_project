"""호출량을 제한한 경로→후보→실제 접근·우회 연결 진단. 순수 계산은 calculations에 둔다."""

from collections import Counter

import httpx

from fuelroute.calculations.detour import assess_candidate_routes
from fuelroute.calculations.driving_range import calculate_safe_range
from fuelroute.calculations.route_points import build_route_path, generate_search_points
from fuelroute.clients.kakao_routes import probe_route
from fuelroute.core.config import Settings
from fuelroute.core.contracts import ToolResult
from fuelroute.core.errors import ProbeFailure
from fuelroute.core.results import run_probe
from fuelroute.schemas.coordinates import Coordinate
from fuelroute.schemas.requests import RouteSpikeRequest
from fuelroute.tools.fuel_tools import FuelTools


def _required_data(result: ToolResult[dict]) -> dict:
    """실패를 숨기지 않고 상위 실험에 동일 코드·재시도/재계획 플래그로 전달한다.

    성공이면 data를 반환한다. 하위 호출 결과만 해석하며 추가 HTTP·저장은 없다.
    예: AUTH_FAILED를 빈 후보 목록으로 바꾸지 않고 ProbeFailure로 전달한다.
    """
    if result.error:
        error = result.error
        raise ProbeFailure(
            error.code,
            error.message,
            retryable=error.retryable,
            replan_allowed=error.replan_allowed,
        )
    return result.data


async def run_route_spike(
    request: RouteSpikeRequest, settings: Settings, client: httpx.AsyncClient
) -> ToolResult[dict]:
    """연료 조건→안전 구간 검색점→후보 중복 제거→상세·실제 경로 평가를 순서대로 실행한다.

    request는 검증된 좌표·km 범위 또는 기존 연료·유종·상한이다. 설정은 Kakao/Opinet 키만 필요하며
    client의 종료 책임은 호출자에게 있다. 기본 경로 1회, 검색 최대 3회, 후보당 상세·
    접근·경유 각 최대 1회다. 기본값이면 Opinet 최대 6회/Kakao 최대 7회다.
    검색점은 안전 구간 안에 두며 후보는 ID로 합친 뒤 가격순 일부만 평가한다.
    상세 누락/후보 경로 실패는 개별 제외로 남기고 인증·설정·한도 오류는 즉시 중단한다.
    결과에 실제 호출 수·정제된 추적·검색점·평가/미평가 건수·제외 사유를 반환한다.
    실행 완료 시 후보가 없거나 모두 제외돼도 status로 명시한다. 이는 조회 Tool의
    EMPTY_RESULT를 성공으로 바꾼 것이 아니라 실험 실행 결과다. 전체 탐색·전역 최저가
    추천은 보장하지 않는다. LLM·DB·자동 재시도·추가 재계획은 없다.
    예: 주행 가능180km/안전 여유30km이면150km 구간을 탐색한다. 기존 L 입력도 지원한다.
    """

    async def operation() -> dict:
        """키 사전 확인 후 유한한 검색·평가를 수행한다. 인증정보 없는 호출 요약만 남긴다."""
        missing = settings.missing("opinet")
        if missing:
            raise ProbeFailure("CONFIG_ERROR", "미설정 환경변수: " + ", ".join(missing))
        try:
            safe_range = calculate_safe_range(
                request.current_fuel_l,
                request.reserve_fuel_l,
                request.avg_efficiency_kmpl,
                remaining_range_km=request.remaining_range_km,
                reserve_range_km=request.reserve_range_km,
            )
        except ValueError as exc:
            raise ProbeFailure(
                "INVALID_INPUT", "주행 가능 거리·안전 여유 입력을 확인하세요."
            ) from exc
        calls = []

        def record(result: ToolResult[dict], inputs: dict) -> None:
            """공개 입력·메타·오류만 추적한다. 원본 경로·키 저장과 추가 호출은 없다."""
            calls.append(
                {
                    "tool_name": result.tool_name,
                    "input": inputs,
                    "ok": result.ok,
                    "error": result.error.model_dump() if result.error else None,
                    "meta": result.meta.model_dump(mode="json"),
                }
            )

        async def route(destination: Coordinate, via: Coordinate | None = None) -> ToolResult[dict]:
            """동일 출발지·옵션의 자동차 경로 한 번을 요청하고 호출 요약에 추가한다."""
            result = await probe_route(
                settings, client, origin=request.origin, destination=destination, via=via
            )
            record(
                result,
                {
                    "origin": request.origin.model_dump(),
                    "destination": destination.model_dump(),
                    "via": via.model_dump() if via else None,
                },
            )
            return result

        base = _required_data(await route(request.destination))
        try:
            path = build_route_path(base)
        except ValueError as exc:
            raise ProbeFailure(
                "INVALID_RESPONSE", "기본 경로 거리·상세 좌표를 확인하세요."
            ) from exc
        points = generate_search_points(
            path, safe_range, request.radius_m, request.max_search_points
        )
        tools = FuelTools(settings, client)
        candidates = {}
        hits = 0
        for point in points:
            result = await tools.search_gas_stations(
                point.longitude, point.latitude, request.fuel_type, request.radius_m
            )
            record(result, {**point.model_dump(), "fuel_type": request.fuel_type})
            if result.error and result.error.code == "EMPTY_RESULT":
                continue
            for candidate in _required_data(result)["items"]:
                hits += 1
                candidates.setdefault(candidate["station_id"], candidate)
        selected = sorted(
            candidates.values(),
            key=lambda item: (
                item["price_krw_per_l"] is None,
                item["price_krw_per_l"] or 0,
                item["station_id"],
            ),
        )[: request.max_candidates]
        evaluated = []
        fatal_codes = {"AUTH_FAILED", "CONFIG_ERROR", "RATE_LIMITED"}
        for candidate in selected:
            station_id = candidate["station_id"]
            item = {"station_id": station_id, "eligible": False, "reason_codes": []}
            detail_result = await tools.get_station_details(station_id, request.fuel_type)
            record(detail_result, {"station_id": station_id, "fuel_type": request.fuel_type})
            if detail_result.error:
                if detail_result.error.code in fatal_codes:
                    _required_data(detail_result)
                item["reason_codes"] = [detail_result.error.code]
                evaluated.append(item)
                continue
            detail = _required_data(detail_result)
            item["station"] = detail
            if detail["price_krw_per_l"] is None:
                item["reason_codes"] = ["MISSING_PRICE"]
                evaluated.append(item)
                continue
            point = Coordinate(longitude=detail["longitude"], latitude=detail["latitude"])
            approach_result = await route(point)
            if approach_result.error:
                if approach_result.error.code in fatal_codes:
                    _required_data(approach_result)
                item["reason_codes"] = [approach_result.error.code]
                evaluated.append(item)
                continue
            approach = _required_data(approach_result)
            if approach["distance_m"] > safe_range * 1000:
                item.update(reason_codes=["UNREACHABLE"], reach_distance_m=approach["distance_m"])
                evaluated.append(item)
                continue
            via_result = await route(request.destination, point)
            if via_result.error:
                if via_result.error.code in fatal_codes:
                    _required_data(via_result)
                item["reason_codes"] = [via_result.error.code]
                evaluated.append(item)
                continue
            try:
                item.update(
                    assess_candidate_routes(
                        base,
                        approach,
                        _required_data(via_result),
                        safe_range,
                        request.max_detour_min,
                        request.max_detour_km,
                    )
                )
            except ValueError:
                item["reason_codes"] = ["INVALID_RESPONSE"]
            evaluated.append(item)
        eligible = sorted(
            (item for item in evaluated if item["eligible"]),
            key=lambda item: (
                item["station"]["price_krw_per_l"],
                item["detour_duration_s"],
                item["station_id"],
            ),
        )
        return {
            "status": "eligible_found" if eligible else "no_eligible_candidates",
            "safe_range_km": safe_range,
            "base_distance_m": path.distance_m,
            "base_duration_s": path.duration_s,
            "search_points": [point.model_dump() for point in points],
            "candidate_hits": hits,
            "unique_candidate_count": len(candidates),
            "duplicate_hits_removed": hits - len(candidates),
            "evaluated_candidates": evaluated,
            "unevaluated_candidate_count": len(candidates) - len(selected),
            "eligible_station_ids": [item["station_id"] for item in eligible],
            "coverage": "sampled_points_and_limited_candidates",
            "api_call_counts": dict(Counter(call["meta"]["provider"] for call in calls)),
            "calls": calls,
        }

    result = await run_probe(settings, "kakao", "run_route_spike", operation)
    result.meta.provider = "kakao+opinet"
    return result
