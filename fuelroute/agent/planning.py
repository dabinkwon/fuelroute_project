"""F3006 작업 순서·의존성·고정 실행 정책을 검증한다. 후보 실행은 execution/replanning이 맡는다."""

from pydantic import ValidationError

from fuelroute.agent.analysis import refresh_analysis
from fuelroute.core.errors import ProbeFailure
from fuelroute.schemas.analysis import RequestAnalysis
from fuelroute.schemas.planning import Action, PlanProposal, PlanStep, WorkPlan
from fuelroute.schemas.requests import PlanningRequest

# 모델은 아래 작업의 순서만 제안한다. Tool 이름·의존성·입력 출처·실패 정책은 코드가 정한다.
STEP_SPECS: dict[Action, dict] = {
    "base_route": {
        "kind": "external_tool",
        "tool_name": "get_driving_route",
        "depends_on": [],
        "inputs_from": ["request.origin", "request.destination"],
        "run_when": "once",
        "failure_policy": "stop",
    },
    "safe_range": {
        "kind": "local",
        "tool_name": None,
        "depends_on": [],
        "inputs_from": [
            "request.remaining_range_km",
            "request.reserve_range_km",
        ],
        "run_when": "once",
        "failure_policy": "stop",
    },
    "search_points": {
        "kind": "local",
        "tool_name": None,
        "depends_on": ["base_route", "safe_range"],
        "inputs_from": [
            "base_route",
            "safe_range",
            "request.radius_m",
            "request.max_search_points",
        ],
        "run_when": "once",
        "failure_policy": "stop",
    },
    "search_candidates": {
        "kind": "external_tool",
        "tool_name": "search_gas_stations",
        "depends_on": ["search_points"],
        "inputs_from": ["search_points", "request.fuel_type", "request.radius_m"],
        "run_when": "per_search_point",
        "failure_policy": "replan_if_empty",
    },
    "station_details": {
        "kind": "external_tool",
        "tool_name": "get_station_details",
        "depends_on": ["search_candidates"],
        "inputs_from": [
            "search_candidates.station_id",
            "request.fuel_type",
            "request.max_candidates",
        ],
        "run_when": "per_candidate",
        "failure_policy": "exclude_candidate",
    },
    "candidate_routes": {
        "kind": "external_tool",
        "tool_name": "get_driving_route",
        "depends_on": ["station_details"],
        "inputs_from": ["request.origin", "station_details", "request.destination"],
        "run_when": "per_priced_candidate",
        "failure_policy": "exclude_candidate",
    },
    "evaluate_constraints": {
        "kind": "local",
        "tool_name": None,
        "depends_on": ["base_route", "safe_range", "candidate_routes"],
        "inputs_from": ["base_route", "safe_range", "candidate_routes", "request"],
        "run_when": "per_priced_candidate",
        "failure_policy": "exclude_candidate",
    },
    "rank_candidates": {
        "kind": "local",
        "tool_name": None,
        "depends_on": ["evaluate_constraints"],
        "inputs_from": ["station_details", "evaluate_constraints", "request.sort_by"],
        "run_when": "once",
        "failure_policy": "stop",
    },
    "rest_area_info": {
        "kind": "external_tool",
        "tool_name": "search_rest_area_info",
        "depends_on": ["rank_candidates"],
        "inputs_from": ["rank_candidates.recommended", "request.radius_m"],
        "run_when": "if_recommended",
        "failure_policy": "optional",
    },
}


EXTERNAL_TOOL_NAMES = frozenset(
    item["tool_name"] for item in STEP_SPECS.values() if item["tool_name"] is not None
)


def plan_step_specs(request: PlanningRequest) -> dict[Action, dict]:
    """검증된 요청의 입력 방식에 맞게 안전거리 작업의 입력 출처만 선택한다.

    km 방식은 주행 가능/안전 여유 두 km 필드를, 기존 연료 방식은 L/L/km/L 세 필드를
    참조한다. 사양 dict를 복사해 반환하므로 요청 간 전역 사양을 변경하지 않는다.
    수치·순서·예산·Tool 이름·실패 정책 변경이나 계산·외부 호출·DB·예외는 없다.
    예:180km/30km 요청은 remaining_range_km,reserve_range_km를 다음 계산에 전달한다.
    """
    specs = {action: dict(spec) for action, spec in STEP_SPECS.items()}
    if request.remaining_range_km is None:
        specs["safe_range"]["inputs_from"] = [
            "request.current_fuel_l",
            "request.reserve_fuel_l",
            "request.avg_efficiency_kmpl",
        ]
    return specs


def prepare_plan(analysis: RequestAnalysis) -> WorkPlan | PlanningRequest:
    """입력 준비 상태를 재확인해 계획 생략 결과 또는 모델에 전달할 검증 요청을 만든다.

    F3005 위치 해석 뒤 호출하며 analysis는 변경하지 않는다. 추천 외 의도는not_required,
    누락·오류·미확인 값은needs_user_input의 빈 WorkPlan으로 반환한다. 준비된 추천이면
    PlanningRequest로 좌표(도)·주행 가능/안전 여유(km)·우회(분/km)를 재검증한다. km 입력은
    연비가 필요 없고 기존 L/연비 방식도 지원한다. 검색 반경
    5000m·검색점3개·후보3개/회·재계획2회·전체 평가9개·가격 우선은 기존 실행 기본값이며
    사용자 발언으로 취급하지 않는다. 서로 모순된 준비 상태는ProbeFailure(INVALID_INPUT)다.
    외부 호출·수치 계산·DB·재시도·시간 적합성 판정은 없다. 예: 연료 누락은 WorkPlan,
    준비된 요청은 PlanningRequest로 반환해 호출자가 계획 모델 호출 여부를 구분한다.
    """
    checked = refresh_analysis(analysis)
    plan = WorkPlan(
        status="not_required",
        reason="인사·서비스 설명 등은 추천 작업 계획을 만들지 않습니다.",
        request=None,
        steps=[],
        warnings=list(checked.warnings),
    )
    if checked.intent != "recommend":
        return plan
    if not checked.ready_for_planning:
        plan.status = "needs_user_input"
        plan.reason = "누락·오류·위치 모호성을 먼저 해결한 뒤 작업을 계획합니다."
        return plan
    values = checked.conditions.model_dump(mode="python", exclude_none=True)
    try:
        request = PlanningRequest.model_validate(
            {name: value for name, value in values.items() if name in PlanningRequest.model_fields}
        )
    except ValidationError as exc:
        raise ProbeFailure("INVALID_INPUT", "작업 계획의 주행 조건을 확인하세요.") from exc
    return request


def finalize_plan(analysis: RequestAnalysis, proposal: PlanProposal) -> WorkPlan:
    """모델의 작업 순서에서 누락·중복·선행조건 위반을 거절하고 고정 실행 계약을 붙인다.

    준비된 추천 입력에만 호출하며 analysis와 proposal은 변경하지 않는다. 아홉 작업이
    각각 한 번 있고 depends_on 작업이 모두 앞에 있어야 한다. 모델의 이유는 표시용이고
    입력·Tool 이름·정렬·호출 상한·실패 정책을 바꾸지 못한다. 코드에서 붙이는 request는
    기존 PlanningRequest의 검증된 단위·기본값이다. 반환 ready는 실행 순서 준비를 뜻하며
    경로/가격 확보·추천 성공은 아니다. 잘못된 순서는ProbeFailure(INVALID_RESPONSE),
    입력 미준비는INVALID_INPUT이다. 외부 호출·DB·계산·자동 복구·재시도는 없다.
    예: 상세를 후보 검색 전에 놓거나 안전거리 작업을 빼면 계획을 채택하지 않는다.
    """
    request = prepare_plan(analysis)
    if isinstance(request, WorkPlan):
        raise ProbeFailure("INVALID_INPUT", "작업 계획에 필요한 입력을 먼저 확인하세요.")
    plan = WorkPlan(
        status="ready",
        reason=proposal.reason,
        request=request,
        steps=[],
        warnings=list(analysis.warnings),
    )
    if analysis.conditions.departure_time or analysis.conditions.arrival_deadline:
        plan.warnings.append(
            "출발·도착 시각은 분석 결과에 보존했으며 시간 적합성은 아직 판정하지 않았습니다."
        )
    if analysis.conditions.trip_date is not None:
        plan.warnings.append(
            "계획 날짜의 미래 가격을 예측하지 않고 조회 시점의 가격·데이터를 사용합니다."
        )
    actions = [step.action for step in proposal.steps]
    if len(actions) != len(set(actions)) or set(actions) != set(STEP_SPECS):
        raise ProbeFailure("INVALID_RESPONSE", "작업 계획의 필수 단계·중복을 확인하세요.")
    seen = set()
    specs = plan_step_specs(request)
    for step in proposal.steps:
        spec = specs[step.action]
        if not set(spec["depends_on"]).issubset(seen):
            raise ProbeFailure("INVALID_RESPONSE", "작업 계획의 선행 단계 순서를 확인하세요.")
        plan.steps.append(PlanStep(action=step.action, reason=step.reason, **spec))
        seen.add(step.action)
    return plan
