"""실제 도달·우회·유종·가격을 확인해 평가한 후보 안에서 결정적으로 추천한다."""

from fuelroute.schemas.recommendation import (
    CandidateEvaluation,
    RecommendationRequest,
    RecommendationResult,
)


def rank_candidates(request: RecommendationRequest) -> RecommendationResult:
    """유종·실제 접근·우회 상한·가격을 다시 검사한 뒤 평가한 후보 안에서 한 곳을 선택한다.

    입력은 상세·경로 비용이 있는 후보와 명시적 안전거리(km), 선택 우회 상한(분/km)이다.
    기존 eligible 플래그를 신뢰하지 않으며, 알려진 제약 판정 사유는 현재 수치로 다시
    계산한다. 제공사 실패 사유는 유지한다. 상세/비용/가격 누락·ID 불일치도 제외한다.
    기본 정렬은 가격→추가 시간→추가 거리→ID, 시간 우선이면 추가 시간→추가 거리→가격→ID다.
    가중치·가상 주유량·추가 연료비를 만들지 않는다. 상한과 같은 값은 허용하며,
    후보가 없으면 recommended=None이다. 반환에는 모든 제외 사유와 정렬 기준이 있다.
    입력 검증 실패는 ValidationError다. HTTP·LLM·DB·재계획 부작용은 없다.
    예: 싼 후보의 실제 접근이 145km이고 안전거리가 144km면 가격과 관계없이 제외한다.
    """
    ranked = []
    excluded = []
    constraints = {"UNREACHABLE", "DETOUR_TIME_EXCEEDED", "DETOUR_DISTANCE_EXCEEDED"}
    for candidate in request.candidates:
        item = candidate.model_copy(deep=True)
        reasons = [reason for reason in item.reason_codes if reason not in constraints]
        station = item.station
        if station is None:
            if not reasons:
                reasons.append("MISSING_DETAILS")
        else:
            if station.station_id != item.station_id:
                reasons.append("STATION_ID_MISMATCH")
            if station.fuel_type != request.fuel_type:
                reasons.append("FUEL_TYPE_MISMATCH")
        if (
            item.reach_distance_m is not None
            and item.reach_distance_m > request.safe_range_km * 1000
        ):
            reasons.append("UNREACHABLE")
        if (
            request.max_detour_min is not None
            and item.detour_duration_s is not None
            and item.detour_duration_s > request.max_detour_min * 60
        ):
            reasons.append("DETOUR_TIME_EXCEEDED")
        if (
            request.max_detour_km is not None
            and item.detour_distance_m is not None
            and item.detour_distance_m > request.max_detour_km * 1000
        ):
            reasons.append("DETOUR_DISTANCE_EXCEEDED")
        costs = (item.reach_distance_m, item.detour_duration_s, item.detour_distance_m)
        if any(value is None for value in costs) and not reasons:
            reasons.append("MISSING_ROUTE_COST")
        if station is not None and (
            station.price_krw_per_l is None or station.price_krw_per_l <= 0
        ):
            reasons.append("MISSING_PRICE")
        item.reason_codes = list(dict.fromkeys(reasons))
        item.eligible = not item.reason_codes
        (ranked if item.eligible else excluded).append(item)
    fields = (
        ["price_krw_per_l", "detour_duration_s", "detour_distance_m", "station_id"]
        if request.sort_by == "price"
        else ["detour_duration_s", "detour_distance_m", "price_krw_per_l", "station_id"]
    )

    def ordering(item: CandidateEvaluation) -> tuple:
        """필수 비용을 검증한 후보의 사전식 정렬 키만 만든다. 입력 수정·외부 호출은 없다."""
        values = {
            "price_krw_per_l": item.station.price_krw_per_l,
            "detour_duration_s": item.detour_duration_s,
            "detour_distance_m": item.detour_distance_m,
            "station_id": item.station_id,
        }
        return tuple(values[field] for field in fields)

    ranked.sort(key=ordering)
    return RecommendationResult(
        recommended=ranked[0] if ranked else None,
        ranked_candidates=ranked,
        excluded_candidates=excluded,
        sort_by=request.sort_by,
        ranking_fields=fields,
    )
