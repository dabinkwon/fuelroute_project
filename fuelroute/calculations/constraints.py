"""F3008 실제 경로·상세·입력 제약을 재판정한다. HTTP·LLM 없이 공유 실행 모델만 사용한다."""

from datetime import datetime, time, timedelta, timezone
from math import isfinite

from fuelroute.calculations.detour import assess_candidate_routes
from fuelroute.calculations.driving_range import calculate_safe_range
from fuelroute.calculations.ranking import rank_candidates
from fuelroute.core.errors import ProbeFailure
from fuelroute.schemas.analysis import FIELD_LABELS, RequestAnalysis, TripConditions
from fuelroute.schemas.driving import RangeConditions, SafeRangeOutput
from fuelroute.schemas.execution import PlanExecution
from fuelroute.schemas.judgement import (
    CandidateJudgement,
    ConstraintCheck,
    TimeConstraint,
    TripJudgement,
)
from fuelroute.schemas.planning import WorkPlan
from fuelroute.schemas.recommendation import (
    CandidateEvaluation,
    RecommendationRequest,
)

KST = timezone(timedelta(hours=9))


def _clock_seconds(value: time) -> float:
    """날짜 없는 로컬 시각을 자정 기준 초로 바꾼다. 날짜·시간대·외부 호출을 생성하지 않는다.

    F3005가 검증한 HH:MM time을 받는다. 예:15:00은54000초다. 초/마이크로초도 보존하며
    반환은 하루 안의 초다. 입력 수정·DB·재시도·예외는 없다.
    """
    return value.hour * 3600 + value.minute * 60 + value.second + value.microsecond / 1_000_000


def time_constraint(conditions: TripConditions) -> TimeConstraint:
    """출발·도착 시각/날짜와 사용자 정차시간으로 비교 가능한 시간 예산을 만든다.

    도착 시각이 없으면 시간 제약을 만들지 않는다. 도착 날짜만 있으면 시각을 추가로
    묻고23:59로 채우지 않는다. 도착 상한이 있으면 출발시각·주유/정차 예상 분이 필요하다.
    날짜 둘이 명시되면 KST datetime 차이를 사용한다. 도착 날짜 미언급은 같은 날의
    시각차만 비교하며 시각이 역전되면 익일을 추측하지 않고 날짜를 묻는다. 명시된
    도착 날짜만 있으면 출발 날짜도 요구한다. 날짜가 없어도 정상 순서의 시각차는
    same_day_clock으로 비교하며 사용자 조건에 날짜를 써넣지 않는다. 음수/비유한 정차는
    INVALID_INPUT이다. 예:15시→20시·정차20분은예산18000초/정차1200초다. 외부/저장은 없다.
    """
    result = TimeConstraint()
    if conditions.arrival_deadline is None:
        if conditions.arrival_date is not None:
            result.status = "needs_user_input"
            result.missing_fields = ["arrival_deadline"] + [
                name
                for name in ["trip_date", "departure_time", "refuel_stop_min"]
                if getattr(conditions, name) is None
            ]
        return result
    result.status = "ready"
    if conditions.departure_time is None:
        result.missing_fields.append("departure_time")
    if conditions.refuel_stop_min is None:
        result.missing_fields.append("refuel_stop_min")
    else:
        stop_s = conditions.refuel_stop_min * 60
        if not isfinite(stop_s) or stop_s < 0:
            raise ProbeFailure(
                "INVALID_INPUT", "주유·정차 예상 시간은 계산 가능한 0 이상 분이어야 합니다."
            )
        result.stop_s = stop_s
    if conditions.arrival_date is not None and conditions.trip_date is None:
        result.missing_fields.append("trip_date")
    if conditions.departure_time is not None:
        start = _clock_seconds(conditions.departure_time)
        end = _clock_seconds(conditions.arrival_deadline)
        result.departure_clock_s = start
        if conditions.trip_date is not None:
            result.departure_at = datetime.combine(
                conditions.trip_date, conditions.departure_time, KST
            )
        if conditions.trip_date is not None and conditions.arrival_date is not None:
            result.basis = "explicit_dates"
            result.deadline_at = datetime.combine(
                conditions.arrival_date, conditions.arrival_deadline, KST
            )
            result.available_s = (result.deadline_at - result.departure_at).total_seconds()
            if result.available_s < 0:
                result.missing_fields.append("arrival_date")
                result.available_s = None
        elif conditions.arrival_date is None:
            result.basis = "same_day_clock"
            if end < start:
                result.missing_fields += ["trip_date", "arrival_date"]
            else:
                result.available_s = end - start
                if result.departure_at is not None:
                    result.deadline_at = datetime.combine(
                        conditions.trip_date, conditions.arrival_deadline, KST
                    )
    result.missing_fields = list(
        dict.fromkeys(
            field
            for field in result.missing_fields
            if field != "trip_date" or conditions.trip_date is None
        )
    )
    if result.missing_fields:
        result.status = "needs_user_input"
    return result


def _bound_check(
    code: str,
    actual: float | None,
    limit: float | None,
    unit: str,
    sources: list[str],
    *,
    required: bool = False,
) -> ConstraintCheck:
    """상한 비교를 passed/failed/미확인/미지정으로 구분한다. 상한과 같으면 통과다.

    actual/limit는 같은 단위의 유한 수 또는None이다. required는 안전거리 같은 필수
    항목에 사용한다. 미확인 값을0으로 바꾸지 않는다. 예:150km<=150km는passed,
    비용None·상한15분은unverified다. 외부 호출·변경·보정·DB는 없다.
    """
    status = (
        "not_requested"
        if limit is None and not required
        else (
            "unverified"
            if actual is None or limit is None
            else ("passed" if actual <= limit else "failed")
        )
    )
    return ConstraintCheck(
        code=code,
        status=status,
        actual=actual,
        limit=limit,
        unit=unit,
        sources=sources,
        message={
            "passed": "상한 이내입니다.",
            "failed": "상한을 초과했습니다.",
            "unverified": "비교할 값이 충분하지 않습니다.",
            "not_requested": "지정한 상한이 없습니다.",
        }[status],
    )


def _candidate(
    source: CandidateEvaluation,
    execution: PlanExecution,
    plan: WorkPlan,
    analysis: RequestAnalysis,
    timing: TimeConstraint,
    safe_range_km: float,
) -> CandidateJudgement:
    """상세/경로의 실제 값으로 후보를 재구성하며 과거 eligible·계산된 비용만 신뢰하지 않는다.

    요청은 검증된 km/유종/우회 상한이고 source는 평가한 ID/조회 실패 사유다. 상세 목록과
    접근/경유 경로로 도달거리·우회를 다시 계산한다. 경유 첫 구간과 독립 접근 중 큰 m를
    사용하고 음수 우회는 원래 값 보존 후 상한에0을 적용한다. 알려진 조회 실패는 유지하며
    불충족/미확인을 구분한다. 시간은 실제 주행 예상초+명시 정차초를 예산과 비교한다.
    반환에는 km 여유·선택 연비의 추정L·근거와 호출ID를 담는다. 연비가 없으면L는None이다.
    잘못된 구간은 후보 미확인이며 프로그래밍 오류는 숨기지 않는다. HTTP·LLM·저장·재시도는 없다.
    """
    request = plan.request
    station_id = source.station_id
    station = next(
        (item for item in execution.results.station_details if item.station_id == station_id), None
    )
    item = CandidateEvaluation(station_id=station_id, station=station)
    known = {
        "UNREACHABLE",
        "DETOUR_TIME_EXCEEDED",
        "DETOUR_DISTANCE_EXCEEDED",
        "MISSING_PRICE",
        "MISSING_DETAILS",
        "MISSING_ROUTE_COST",
        "FUEL_TYPE_MISMATCH",
        "STATION_ID_MISMATCH",
    }
    provider_errors = [code for code in source.reason_codes if code not in known]
    checks = [
        ConstraintCheck(
            code="DETAILS",
            status="passed" if station else "unverified",
            message="상세 조회 확인" if station else "상세를 확인하지 못했습니다.",
            sources=["results.station_details"],
        )
    ]
    if provider_errors:
        checks.append(
            ConstraintCheck(
                code="LOOKUP_ERROR",
                status="unverified",
                actual=",".join(provider_errors),
                message="조회 실패 사유를 보존합니다.",
                sources=["execution.calls"],
            )
        )
    if station:
        checks.append(
            ConstraintCheck(
                code="FUEL_TYPE_MISMATCH",
                status="passed" if station.fuel_type == request.fuel_type else "failed",
                actual=station.fuel_type,
                limit=request.fuel_type,
                message="요청 유종과 비교합니다.",
                sources=["results.station_details", "plan.request.fuel_type"],
            )
        )
        checks.append(
            ConstraintCheck(
                code="MISSING_PRICE",
                status="passed"
                if station.price_krw_per_l and station.price_krw_per_l > 0
                else "unverified",
                actual=station.price_krw_per_l,
                unit="KRW/L",
                message="실제 상세 조회 가격을 사용합니다.",
                sources=["results.station_details.price_krw_per_l"],
            )
        )
    pair = execution.results.candidate_routes.get(station_id)
    valid_route = False
    if pair and pair.approach:
        item.reach_distance_m = pair.approach.distance_m
        if pair.via and execution.results.base_route:
            try:
                assessed = assess_candidate_routes(
                    execution.results.base_route.model_dump(),
                    pair.approach.model_dump(),
                    pair.via.model_dump(),
                    safe_range_km,
                    request.max_detour_min,
                    request.max_detour_km,
                )
                item = CandidateEvaluation.model_validate({**item.model_dump(), **assessed})
                valid_route = True
            except ValueError:
                pass
    checks.append(
        ConstraintCheck(
            code="ROUTE_COST",
            status="passed" if valid_route else "unverified",
            message="접근/경유 실제 비용 확인"
            if valid_route
            else "완전한 실제 경로 비용이 없습니다.",
            sources=["results.base_route", f"results.candidate_routes[{station_id}]"],
        )
    )
    reach_km = item.reach_distance_m / 1000 if item.reach_distance_m is not None else None
    checks += [
        _bound_check(
            "UNREACHABLE",
            reach_km,
            safe_range_km,
            "km",
            ["plan.request", f"results.candidate_routes[{station_id}]"],
            required=True,
        ),
        _bound_check(
            "DETOUR_DISTANCE_EXCEEDED",
            item.detour_distance_m / 1000 if item.detour_distance_m is not None else None,
            request.max_detour_km,
            "km",
            [
                "results.base_route",
                f"results.candidate_routes[{station_id}]",
                "plan.request.max_detour_km",
            ],
        ),
        _bound_check(
            "DETOUR_TIME_EXCEEDED",
            item.detour_duration_s / 60 if item.detour_duration_s is not None else None,
            request.max_detour_min,
            "min",
            [
                "results.base_route",
                f"results.candidate_routes[{station_id}]",
                "plan.request.max_detour_min",
            ],
        ),
    ]
    arrival_at = None
    arrival_clock = None
    day_offset = None
    if timing.status != "not_requested":
        duration = pair.via.duration_s if valid_route else None
        total_s = (
            duration + timing.stop_s if duration is not None and timing.stop_s is not None else None
        )
        time_check = _bound_check(
            "ARRIVAL_DEADLINE_EXCEEDED",
            total_s,
            timing.available_s,
            "s",
            [
                f"results.candidate_routes[{station_id}].via.duration_s",
                "analysis.conditions.refuel_stop_min",
                "analysis.conditions.arrival_deadline",
            ],
            required=True,
        )
        if (
            duration is not None
            and timing.available_s is not None
            and duration > timing.available_s
        ):
            time_check.status = "failed"
            time_check.actual = float(duration)
            time_check.message = "주행 예상시간만으로도 도착 상한을 초과합니다."
        elif timing.status != "ready":
            time_check.status = "unverified"
            time_check.message = "출발/도착 날짜·시각 또는 정차시간을 확인해야 합니다."
        checks.append(time_check)
        if total_s is not None and timing.departure_clock_s is not None:
            clock = timing.departure_clock_s + total_s
            day_offset = int(clock // 86400)
            seconds = int(clock % 86400)
            arrival_clock = f"{seconds // 3600:02}:{seconds % 3600 // 60:02}:{seconds % 60:02}"
            if timing.departure_at is not None:
                try:
                    arrival_at = timing.departure_at + timedelta(seconds=total_s)
                except OverflowError:
                    arrival_at = None
    failed = [check.code for check in checks if check.status == "failed"]
    unverified = ["UNVERIFIED_" + check.code for check in checks if check.status == "unverified"]
    item.reason_codes = list(dict.fromkeys(provider_errors + failed + unverified))
    item.eligible = not item.reason_codes
    fuel_l = (
        reach_km / request.avg_efficiency_kmpl
        if reach_km is not None and request.avg_efficiency_kmpl
        else None
    )
    if fuel_l is not None and not isfinite(fuel_l):
        fuel_l = None
    ids = [call.result.meta.request_id for call in execution.calls if call.station_id == station_id]
    if pair:
        ids += [key for key in [pair.approach_request_id, pair.via_request_id] if key]
    return CandidateJudgement(
        station_id=station_id,
        status="excluded"
        if failed
        else ("unverified" if unverified or provider_errors else "eligible"),
        evaluation=item,
        checks=checks,
        range_margin_km=safe_range_km - reach_km if reach_km is not None else None,
        estimated_fuel_to_station_l=fuel_l,
        estimated_arrival_at=arrival_at,
        estimated_arrival_clock=arrival_clock,
        arrival_day_offset=day_offset,
        tool_request_ids=list(dict.fromkeys(ids)),
    )


def judge_execution(
    analysis: RequestAnalysis, plan: WorkPlan, execution: PlanExecution
) -> TripJudgement:
    """F3007 결과를 F3008 제약으로 재판정해 근거·확정 추천·입력 보완 상태를 반환한다.

    분석과 계획의 위치·유종·주행 조건이 같아야 한다. 실제 상세/경로를 재사용하며 새 API·
    LLM·DB·재시도는 없다. 안전거리와 비용을 다시 계산해 과거 eligible와 캐시된 판정값을
    신뢰하지 않는다. 실패/미확인 후보는 확정 추천에서 제외하고 사용자 정렬을 유지한다.
    날짜 없는 시각 비교·명시된 익일 날짜·정차 미확인을 구분하고 누락값을 새로 만들지 않는다.
    plan 미준비/비추천은 같은 상태, 핵심 조회 실패는unavailable, 데이터 모순은ProbeFailure다.
    예: 가장 싼 후보의 주행+정차가 도착 예산을 초과하면 다음 충족 후보를 선택한다.
    반환 가격/도착시각은 조회값·추정치이며 전체 경로 최저가/미래 도착 보장이 아니다.
    """
    if plan.status == "not_required":
        return TripJudgement(status="not_required", warnings=list(execution.warnings))
    if plan.status != "ready":
        return TripJudgement(
            status="needs_user_input",
            missing_fields=list(analysis.missing_fields),
            questions=list(analysis.questions),
            warnings=list(execution.warnings),
        )
    if execution.error:
        return TripJudgement(
            status="unavailable", warnings=[*execution.warnings, execution.error.message]
        )
    request = plan.request
    if request is None or execution.results.base_route is None:
        raise ProbeFailure("INVALID_RESPONSE", "판정에 필요한 실행 요청·기본 경로가 없습니다.")
    for name in [
        "origin",
        "destination",
        "fuel_type",
        *RangeConditions.model_fields,
        "max_detour_min",
        "max_detour_km",
        "sort_by",
    ]:
        value = getattr(analysis.conditions, name)
        if name == "sort_by" and value is None:
            value = "price"
        if value != getattr(request, name):
            raise ProbeFailure(
                "INVALID_INPUT", "분석 조건과 실행 요청이 달라 재조회 여부를 확인해야 합니다."
            )
    base_call = next((call for call in execution.calls if call.action == "base_route"), None)
    if base_call is not None:
        if base_call.inputs.get("origin") != request.origin.model_dump(
            mode="json"
        ) or base_call.inputs.get("destination") != request.destination.model_dump(mode="json"):
            raise ProbeFailure("INVALID_INPUT", "기본 경로의 출발·목적과 현재 요청이 다릅니다.")
    try:
        safe_range = calculate_safe_range(
            **request.model_dump(include=set(RangeConditions.model_fields))
        )
    except ValueError as exc:
        raise ProbeFailure("INVALID_INPUT", "안전 주행거리 계산 범위를 확인하세요.") from exc
    if safe_range <= 0:
        raise ProbeFailure("INVALID_INPUT", "양수 안전 주행거리가 필요합니다.")
    timing = time_constraint(analysis.conditions)
    ids = [item.station_id for item in execution.results.evaluate_constraints]
    detail_ids = [item.station_id for item in execution.results.station_details]
    if len(ids) != len(set(ids)) or len(detail_ids) != len(set(detail_ids)):
        raise ProbeFailure("INVALID_RESPONSE", "판정 결과의 주유소 ID 중복을 확인하세요.")
    candidates = [
        _candidate(item, execution, plan, analysis, timing, safe_range)
        for item in execution.results.evaluate_constraints
    ]
    recommendation = rank_candidates(
        RecommendationRequest(
            candidates=[item.evaluation for item in candidates],
            fuel_type=request.fuel_type,
            safe_range_km=safe_range,
            max_detour_min=request.max_detour_min,
            max_detour_km=request.max_detour_km,
            sort_by=request.sort_by,
        )
    )
    status = (
        "recommended"
        if recommendation.recommended
        else (
            "needs_user_input"
            if timing.missing_fields
            else (
                "unavailable"
                if candidates and all(item.status == "unverified" for item in candidates)
                else "no_recommendation"
            )
        )
    )
    warnings = [
        value
        for value in execution.warnings
        if "시간 적합성은 아직 판정하지 않았습니다" not in value
    ]
    warnings.append("주행 가능 거리와 소요시간은 입력·조회 응답에 따른 추정치입니다.")
    if timing.status != "not_requested":
        warnings.append(
            "도착 예상은 조회 주행시간과 입력 정차시간 기준입니다. "
            "미래 교통·대기시간을 보장하지 않습니다."
        )
    return TripJudgement(
        status=status,
        safe_range_km=safe_range,
        candidates=candidates,
        recommendation=recommendation,
        time_constraint=timing,
        missing_fields=timing.missing_fields,
        questions=[FIELD_LABELS[name] + "을 확인해 알려주세요." for name in timing.missing_fields],
        warnings=warnings,
        replan_needed=recommendation.recommended is None and not timing.missing_fields,
    )


def apply_judgement(execution: PlanExecution, judgement: TripJudgement) -> PlanExecution:
    """판정한 비용/추천을 실행 결과 복사본에 반영하고 다른 후보의 보조정보를 분리한다.

    입력은 수정하지 않는다. 상태·재계획 필요·경고와 평가/정렬 결과를 교체한다. 핵심
    실행 오류는 유지한다. 추천이 바뀌면 기존 휴게소 결과를 재사용하지 않고 지워 후속
    보조 조회에 넘긴다. 외부 호출·LLM·DB·예외는 없다. 예:시간 제약으로A→B면A의 휴게소를
    B 정보로 표시하지 않는다. 실제 보조 조회는 별도 실행기 책임이다.
    """
    result = execution.model_copy(deep=True)
    if result.error:
        return result
    if judgement.safe_range_km is not None:
        result.results.safe_range = SafeRangeOutput(safe_range_km=judgement.safe_range_km)
    old = result.results.rank_candidates.recommended if result.results.rank_candidates else None
    new = judgement.recommendation.recommended if judgement.recommendation else None
    if old is None or new is None or old.station_id != new.station_id:
        result.results.rest_area_info = None
    if not result.error:
        result.status = (
            judgement.status
            if judgement.status in {"recommended", "needs_user_input", "not_required"}
            else "no_recommendation"
        )
    result.results.evaluate_constraints = [item.evaluation for item in judgement.candidates]
    result.results.rank_candidates = judgement.recommendation
    result.replan_needed = judgement.replan_needed
    result.warnings = list(judgement.warnings)
    return result
