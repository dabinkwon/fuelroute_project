"""명시적 입력의 후보·검색점을 재사용하는 유한 재계획 실행기. 그래프 연결은 후속 작업이다."""

from collections import Counter

import httpx

from fuelroute.calculations.detour import assess_candidate_routes
from fuelroute.calculations.driving_range import calculate_safe_range
from fuelroute.calculations.ranking import rank_candidates
from fuelroute.calculations.route_points import RoutePath, build_route_path, generate_search_points
from fuelroute.clients.kakao_routes import probe_route
from fuelroute.core.config import Settings
from fuelroute.core.contracts import ToolResult
from fuelroute.core.errors import FATAL_CODES, ProbeFailure
from fuelroute.core.results import run_probe
from fuelroute.schemas.coordinates import Coordinate
from fuelroute.schemas.recommendation import RecommendationRequest, RecommendationResult
from fuelroute.schemas.requests import PlanningRequest
from fuelroute.schemas.routes import RoutePoint
from fuelroute.tools.fuel_tools import FuelTools


def replan_search_points(
    path: RoutePath, safe_range_km: float, radius_m: int, initial: list[RoutePoint]
) -> list[RoutePoint]:
    """안전 구간의 앞쪽에 추가할 검색점 후보를 만들고 기존 점과 중복을 제거한다.

    path·반경·안전거리는 검증한 최초 요청값이다. 한계에서 반경의 1/5/10/15/20배
    앞을 고려하고 0m로 제한한다. initial에 사용한 누적거리는 제외한다. 재계획 실행은
    이 목록에서 회차당 최대 한 점만 사용한다. 외부 호출·상한 완화·기존 요청 재시도는
    없으며, 예: 144km/반경5km의 최초 세 점 이후에는 69/44km를 반환한다.
    """
    # 공개 입력의 동일한 범위 검증은 최초 검색점 생성 함수에 위임한다.
    generate_search_points(path, safe_range_km, radius_m, 1)
    used = {point.route_distance_m for point in initial}
    end = min(path.distance_m, safe_range_km * 1000)
    distances = dict.fromkeys(max(0.0, end - factor * radius_m) for factor in (1, 5, 10, 15, 20))
    return [path.point_at_distance(distance) for distance in distances if distance not in used]


def _require_data(result: ToolResult[dict]) -> dict:
    """하위 실패를 같은 코드와 플래그로 상위 공통 실패에 전달한다. 추가 호출·저장은 없다."""
    if result.error:
        error = result.error
        raise ProbeFailure(
            error.code,
            error.message,
            retryable=error.retryable,
            replan_allowed=error.replan_allowed,
        )
    return result.data


async def recommend_route(
    request: PlanningRequest, settings: Settings, client: httpx.AsyncClient
) -> ToolResult[dict]:
    """현재 조건을 유지한 채 후보 평가와 최대 두 번의 재계획으로 한 곳을 추천한다.

    입력 좌표·주행 가능 거리/안전 여유(km) 또는 기존 연료·우회 상한은 완화하지 않는다.
    km 입력에는 연비가 필요 없고 안전거리는 주행 가능-안전 여유다. 최초 검색 후 회차별 최대
    max_candidates개, 총 max_evaluations개만 평가한다. 추천이 없으면 미평가 캐시 후보를
    먼저 사용하고, 없을 때만 새로운 앞쪽 검색점을 한 곳 추가한다. 최초 실행에
    max_replans(0~2)를 더한 회차가 상한이다. 상한이면 부분 결과와 종료 사유를 반환한다.
    동일 실행의 기본/접근/경유 경로·상세·검색 결과는 같은 요청 인자로 캐시한다.
    인증·설정·한도 오류는 즉시 중단하고 실패한 HTTP를 같은 인자로 재시도하지 않는다.
    기본 최대 호출은 Opinet 14회/Kakao 19회이며 실제 수·캐시 사용·회차별 근거를 반환한다.
    client 소유권은 호출자에게 있다. 캐시는 함수 호출이 끝나면 폐기하며 사용자 간
    공유·DB·LLM·시간 기반 자동 재시도는 없다. 평가한 후보 집합 안의 추천이며 전역
    최저가나 전체 경로 커버리지를 보장하지 않는다. 예: max_replans=0은 최초 회차만 실행한다.
    """

    async def operation() -> dict:
        """실행마다 독립 캐시·카운터를 만들고 검색·평가·재계획 상태를 제한된 순서로 전이한다."""
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
        tools = FuelTools(settings, client)
        calls = []
        route_cache = {}
        detail_cache = {}
        search_cache = {}
        cache_hits = Counter()

        def record(result: ToolResult[dict], inputs: dict) -> None:
            """공개 입력·메타·실패를 추가한다. 키·전체 경로 저장이나 추가 호출은 없다."""
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
            """출발지와 고정 옵션이 같은 경로는 재사용한다. 캐시 미스만 HTTP 1회를 발생시킨다."""
            key = (
                destination.longitude,
                destination.latitude,
                via.longitude if via else None,
                via.latitude if via else None,
            )
            if key in route_cache:
                cache_hits["route"] += 1
                return route_cache[key]
            result = await probe_route(
                settings, client, origin=request.origin, destination=destination, via=via
            )
            record(
                result,
                {"destination": destination.model_dump(), "via": via.model_dump() if via else None},
            )
            route_cache[key] = result
            return result

        async def detail(station_id: str) -> ToolResult[dict]:
            """같은 ID·고정 유종의 상세 응답을 재사용하며 최초 조회만 실제 호출로 기록한다."""
            if station_id in detail_cache:
                cache_hits["detail"] += 1
                return detail_cache[station_id]
            result = await tools.get_station_details(station_id, request.fuel_type)
            record(result, {"station_id": station_id, "fuel_type": request.fuel_type})
            detail_cache[station_id] = result
            return result

        base = _require_data(await route(request.destination))
        try:
            path = build_route_path(base)
        except ValueError as exc:
            raise ProbeFailure("INVALID_RESPONSE", "기본 상세 경로를 확인하세요.") from exc
        initial = generate_search_points(
            path, safe_range, request.radius_m, request.max_search_points
        )
        additional = replan_search_points(path, safe_range, request.radius_m, initial)
        candidates = {}
        evaluated = {}
        rounds = []
        hits = 0
        recommendation: RecommendationResult | None = None
        stop_reason = "replan_limit_reached"

        async def evaluate(candidate: dict) -> dict:
            """후보 하나의 상세·접근·경유를 평가한다. 전역 오류는 중단하고 개별 오류는 보존한다."""
            station_id = candidate["station_id"]
            item = {"station_id": station_id, "eligible": False, "reason_codes": []}
            detail_result = await detail(station_id)
            if detail_result.error:
                if detail_result.error.code in FATAL_CODES:
                    _require_data(detail_result)
                return {**item, "reason_codes": [detail_result.error.code]}
            station = _require_data(detail_result)
            item["station"] = station
            if station["price_krw_per_l"] is None:
                return {**item, "reason_codes": ["MISSING_PRICE"]}
            point = Coordinate(longitude=station["longitude"], latitude=station["latitude"])
            approach_result = await route(point)
            if approach_result.error:
                if approach_result.error.code in FATAL_CODES:
                    _require_data(approach_result)
                return {**item, "reason_codes": [approach_result.error.code]}
            approach = _require_data(approach_result)
            if approach["distance_m"] > safe_range * 1000:
                return {
                    **item,
                    "reach_distance_m": approach["distance_m"],
                    "reason_codes": ["UNREACHABLE"],
                }
            via_result = await route(request.destination, point)
            if via_result.error:
                if via_result.error.code in FATAL_CODES:
                    _require_data(via_result)
                return {**item, "reason_codes": [via_result.error.code]}
            try:
                item.update(
                    assess_candidate_routes(
                        base,
                        approach,
                        _require_data(via_result),
                        safe_range,
                        request.max_detour_min,
                        request.max_detour_km,
                    )
                )
            except ValueError:
                item["reason_codes"] = ["INVALID_RESPONSE"]
            return item

        for round_index in range(request.max_replans + 1):
            pending = [item for key, item in candidates.items() if key not in evaluated]
            new_points = initial if round_index == 0 else ([] if pending else additional[:1])
            if round_index and new_points:
                additional = additional[1:]
            trigger = (
                "initial"
                if round_index == 0
                else ("cached_candidates" if pending else "earlier_search_point")
            )
            for point in new_points:
                key = (point.longitude, point.latitude, request.radius_m)
                if key in search_cache:
                    cache_hits["search"] += 1
                    result = search_cache[key]
                else:
                    result = await tools.search_gas_stations(
                        point.longitude, point.latitude, request.fuel_type, request.radius_m
                    )
                    record(
                        result,
                        {
                            **point.model_dump(),
                            "radius_m": request.radius_m,
                            "fuel_type": request.fuel_type,
                        },
                    )
                    search_cache[key] = result
                if result.error and result.error.code == "EMPTY_RESULT":
                    continue
                for candidate in _require_data(result)["items"]:
                    hits += 1
                    candidates.setdefault(candidate["station_id"], candidate)
            pending = sorted(
                (item for key, item in candidates.items() if key not in evaluated),
                key=lambda item: (
                    item["price_krw_per_l"] is None,
                    item["price_krw_per_l"] or 0,
                    item["station_id"],
                ),
            )
            budget = min(request.max_candidates, request.max_evaluations - len(evaluated))
            selected = pending[:budget]
            for candidate in selected:
                evaluated[candidate["station_id"]] = await evaluate(candidate)
            recommendation = rank_candidates(
                RecommendationRequest(
                    candidates=list(evaluated.values()),
                    fuel_type=request.fuel_type,
                    safe_range_km=safe_range,
                    max_detour_min=request.max_detour_min,
                    max_detour_km=request.max_detour_km,
                    sort_by=request.sort_by,
                )
            )
            rounds.append(
                {
                    "round": round_index,
                    "trigger": trigger,
                    "new_search_points": [point.model_dump() for point in new_points],
                    "evaluated_station_ids": [item["station_id"] for item in selected],
                    "eligible_count": len(recommendation.ranked_candidates),
                }
            )
            if recommendation.recommended is not None:
                stop_reason = "recommendation_found"
                break
            if len(evaluated) >= request.max_evaluations:
                stop_reason = "evaluation_budget_exhausted"
                break
            if not additional and all(key in evaluated for key in candidates):
                stop_reason = "search_space_exhausted"
                break
        return {
            "status": "recommended" if recommendation.recommended else "no_recommendation",
            "recommendation": recommendation.model_dump(mode="json"),
            "safe_range_km": safe_range,
            "base_distance_m": path.distance_m,
            "base_duration_s": path.duration_s,
            "replan_count": len(rounds) - 1,
            "stop_reason": stop_reason,
            "rounds": rounds,
            "unique_candidate_count": len(candidates),
            "duplicate_hits_removed": hits - len(candidates),
            "unevaluated_candidate_count": len(candidates) - len(evaluated),
            "api_call_counts": dict(Counter(call["meta"]["provider"] for call in calls)),
            "cache_hits": dict(cache_hits),
            "calls": calls,
        }

    result = await run_probe(settings, "kakao", "recommend_route", operation)
    result.meta.provider = "kakao+opinet"
    return result
