"""호출량을 제한한 경로→주유소→실제 도달·우회비용 연결 실험. 전체 Agent는 실행하지 않는다."""

from collections import Counter
from typing import Literal

import httpx
from pydantic import BaseModel, Field

from fuelroute.config import Settings
from fuelroute.contracts import ToolResult
from fuelroute.probes import Coordinate, ProbeFailure, probe_route, run_probe
from fuelroute.routing import build_route_path, generate_search_points
from fuelroute.services import FuelConditions, calculate_safe_range
from fuelroute.tools import FuelTools


class RouteSpikeRequest(FuelConditions):
    """실험의 명시적 사용자 조건. 주소 해석·LLM 추출 없이 WGS84 좌표와 연료값을 받는다."""

    origin: Coordinate = Field(description="출발 WGS84 좌표")
    destination: Coordinate = Field(description="목적 WGS84 좌표")
    fuel_type: Literal["gasoline", "diesel"] = Field(description="검증할 유종")
    radius_m: int = Field(default=5000, ge=1, le=5000, description="검색점 반경(m)")
    max_search_points: int = Field(default=3, ge=1, le=3, description="최대 검색점 수")
    max_candidates: int = Field(default=3, ge=1, le=10, description="상세·경로 평가할 후보 상한")
    max_detour_min: float | None = Field(
        default=None, ge=0, allow_inf_nan=False, description="허용 추가 시간(분), null은 미지정"
    )
    max_detour_km: float | None = Field(
        default=None, ge=0, allow_inf_nan=False, description="허용 추가 거리(km), null은 미지정"
    )


class _Constraints(BaseModel):
    """경로 평가에 쓰는 안전거리와 선택 우회 상한의 유한값 계약."""

    safe_range_km: float = Field(gt=0, allow_inf_nan=False)
    max_detour_min: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    max_detour_km: float | None = Field(default=None, ge=0, allow_inf_nan=False)


class _Cost(BaseModel):
    """실제 경로 비용의 단위·타입 계약. 현재 위치의 주유소 접근은 0m/0초도 가능하다."""

    distance_m: int = Field(ge=0, strict=True)
    duration_s: int = Field(ge=0, strict=True)


def _checked_cost(data: dict) -> tuple[_Cost, list[_Cost]]:
    """probe 성공 data의 비용과 구간 합계를 검증한다. 검색용 좌표 보간은 하지 않는다.

    거리·시간은 0 이상 정수 m/s이고 원본 요약과 같아야 한다. 0m 접근 경로는 유효하다.
    잘못된 구조·타입·구간 거리 합계는 ValueError이며 외부 호출·저장은 없다.
    반환 비용과 구간 목록은 안전거리·우회 상한 계산에만 사용한다.
    """
    try:
        route = data["response"]["routes"][0]
        summary = route["summary"]
        cost = _Cost.model_validate(data)
        sections = [
            _Cost(distance_m=part["distance"], duration_s=part["duration"])
            for part in route["sections"]
        ]
        if (
            route.get("result_code") != 0
            or summary["distance"] != cost.distance_m
            or summary["duration"] != cost.duration_s
            or not sections
            or sum(part.distance_m for part in sections) != cost.distance_m
        ):
            raise ValueError("경로 비용과 구간 합계 불일치")
        return cost, sections
    except (KeyError, IndexError, TypeError, AttributeError) as exc:
        raise ValueError("경로 비용의 필수 구조를 확인하세요.") from exc


def assess_candidate_routes(
    base: dict,
    approach: dict,
    via: dict,
    safe_range_km: float,
    max_detour_min: float | None = None,
    max_detour_km: float | None = None,
) -> dict:
    """동일 옵션의 기본·출발→주유소·단일 경유 경로로 안전성과 우회 상한을 판정한다.

    세 dict는 각각 probe_route의 성공 data다. 단일 경유 응답은 구간이 정확히 두 개여야
    한다. 도달거리는 독립 접근 경로와 경유 첫 구간 거리 중 큰 값(m)을 사용한다.
    안전거리는 양수 km, 선택 상한은 0 이상 유한 분/km다. 경계값과 같으면 통과한다.
    우회비용은 경유-기본의 차이며 교통 변동으로 음수일 때 실제 차이는 raw 필드에
    보존하고 상한 판정에는 0을 쓴다. 도달 불가·시간/거리 초과 사유를 함께 반환한다.
    가격 정렬·재계획·HTTP·저장은 없다. 잘못된 입력/응답은 ValueError다.
    예: 안전거리 10km, 실제 접근 11km는 낮은 가격과 무관하게 eligible=False다.
    """
    limits = _Constraints(
        safe_range_km=safe_range_km,
        max_detour_min=max_detour_min,
        max_detour_km=max_detour_km,
    )
    base_path, _ = _checked_cost(base)
    approach_path, _ = _checked_cost(approach)
    via_path, sections = _checked_cost(via)
    if len(sections) != 2:
        raise ValueError("단일 주유소 경유 경로는 구간이 두 개여야 합니다.")
    reach = max(approach_path.distance_m, sections[0].distance_m)
    raw_distance = via_path.distance_m - base_path.distance_m
    raw_duration = via_path.duration_s - base_path.duration_s
    distance, duration = max(0.0, raw_distance), max(0, raw_duration)
    reasons = []
    if reach > limits.safe_range_km * 1000:
        reasons.append("UNREACHABLE")
    if limits.max_detour_min is not None and duration > limits.max_detour_min * 60:
        reasons.append("DETOUR_TIME_EXCEEDED")
    if limits.max_detour_km is not None and distance > limits.max_detour_km * 1000:
        reasons.append("DETOUR_DISTANCE_EXCEEDED")
    return {
        "eligible": not reasons,
        "reason_codes": reasons,
        "reach_distance_m": reach,
        "detour_distance_m": distance,
        "detour_duration_s": duration,
        "raw_detour_distance_m": raw_distance,
        "raw_detour_duration_s": raw_duration,
        "negative_delta_observed": raw_distance < 0 or raw_duration < 0,
    }


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

    request는 검증된 좌표·연료·유종·상한이다. 설정은 Kakao/Opinet 키만 필요하며
    client의 종료 책임은 호출자에게 있다. 기본 경로 1회, 검색 최대 3회, 후보당 상세·
    접근·경유 각 최대 1회다. 기본값이면 Opinet 최대 6회/Kakao 최대 7회다.
    검색점은 안전 구간 안에 두며 후보는 ID로 합친 뒤 가격순 일부만 평가한다.
    상세 누락/후보 경로 실패는 개별 제외로 남기고 인증·설정·한도 오류는 즉시 중단한다.
    결과에 실제 호출 수·정제된 추적·검색점·평가/미평가 건수·제외 사유를 반환한다.
    실행 완료 시 후보가 없거나 모두 제외돼도 status로 명시한다. 이는 조회 Tool의
    EMPTY_RESULT를 성공으로 바꾼 것이 아니라 실험 실행 결과다. 전체 탐색·전역 최저가
    추천은 보장하지 않는다. LLM·DB·자동 재시도·추가 재계획은 없다.
    예: CLI route-spike로 15L/3L/12km/L, 144km 안전 구간을 검증한다.
    """

    async def operation() -> dict:
        """키 사전 확인 후 유한한 검색·평가를 수행한다. 인증정보 없는 호출 요약만 남긴다."""
        missing = settings.missing("opinet")
        if missing:
            raise ProbeFailure("CONFIG_ERROR", "미설정 환경변수: " + ", ".join(missing))
        try:
            safe_range = calculate_safe_range(
                request.current_fuel_l, request.reserve_fuel_l, request.avg_efficiency_kmpl
            )
        except ValueError as exc:
            raise ProbeFailure("INVALID_INPUT", "연료 조건의 계산 범위를 확인하세요.") from exc
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
