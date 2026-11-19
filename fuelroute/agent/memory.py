"""F3010 현재 발언 검증·조건 병합·요약 생성. 저장 I/O와 LLM 호출은 별도 계층이 맡는다."""

import json
import re

from fuelroute.agent.analysis import (
    DISTANCE_FIELDS,
    FUEL_FIELDS,
    condition_issues,
    refresh_analysis,
)
from fuelroute.core.contracts import utc_now
from fuelroute.schemas.analysis import (
    ConditionRemoval,
    ExtractedRequest,
    RequestAnalysis,
    TripConditions,
)
from fuelroute.schemas.execution import PlanExecution
from fuelroute.schemas.judgement import TripJudgement
from fuelroute.schemas.memory import ConversationMemory

REMOVAL_ALIASES = {
    "origin": ("출발",),
    "origin_label": ("출발",),
    "destination": ("목적", "도착 위치", "도착지"),
    "destination_label": ("목적", "도착 위치", "도착지"),
    "trip_date": ("날짜", "출발일"),
    "arrival_date": ("도착일", "도착 날짜"),
    "departure_time": ("출발 시간", "출발 시각", "출발시각"),
    "arrival_deadline": ("도착",),
    "fuel_type": ("유종", "휘발유", "경유"),
    "sort_by": ("정렬", "선호", "가격 우선", "시간 우선"),
    "remaining_range_km": ("주행 가능", "주행가능", "남은 거리"),
    "reserve_range_km": ("안전 여유", "안전여유", "안전 거리"),
    "current_fuel_l": ("연료",),
    "reserve_fuel_l": ("잔량", "안전 연료"),
    "avg_efficiency_kmpl": ("연비",),
    "max_detour_min": ("우회",),
    "max_detour_km": ("우회",),
    "refuel_stop_min": ("정차", "주유 시간", "주유시간"),
}
NUMERIC_FIELDS = set(DISTANCE_FIELDS + FUEL_FIELDS) | {
    "max_detour_min",
    "max_detour_km",
    "refuel_stop_min",
}


def _valid_removal(user: str, removal: ConditionRemoval) -> bool:
    """현재 원문의 제거/미정 요청과 허용된 조건 이름을 대조한다.

    removal.field는 스키마의 조건명, quote는 현재 원문에서 복사한 구절이다. 제거·해제·
    모름 표현과 해당 조건 별칭이 함께 있어야True다. 부정 명령은False이며 완전한 문맥
    분류는 모델 책임이다. API·DB·입력 변경·예외는 없다. 예:'우회 제한 없애줘'는 우회
    상한을 제거할 수 있지만 '우회 제한은 그대로 둬'는 삭제로 해석하지 않는다.
    """
    quote = removal.quote
    if quote not in user or re.search(r"하지\s*마|말고|금지", quote):
        return False
    return bool(
        re.search(r"없애|해제|없음|없어|제한\s*없|상관없|빼|지워|초기화|취소|모르|미정", quote)
    ) and any(alias in quote for alias in (*REMOVAL_ALIASES[removal.field], removal.field))


def merge_conditions(
    user: str,
    extracted: ExtractedRequest,
    current: RequestAnalysis,
    previous: ConversationMemory | None,
) -> RequestAnalysis:
    """현재 원문 검증 결과를 같은 세션의 조건에 병합하고 수치·필수값을 재검사한다.

    current는 analyze_conditions가 검증한 이번 발언, previous는 같은 ID의 저장본 또는
    None이다. 미언급만 이전 값을 이어받는다. 명시했지만 무효/미확인인 값은 이전 정상값으로
    감추지 않는다. 새 지명은 이전 좌표·출처를 지우고, 직접 입력한 좌표는 이전 지명을 지운다.
    km/L량 방식 전환은 반대 방식의 연료량 필드를 지우되 단위를 변환하거나 값을 역산하지
    않는다. 초기화/조건 제거는 현재 원문을 추가 검사하며 도착 상한 제거 시 도착 날짜도
    지워 날짜만 남은 제약을 방지한다. 인사/설명은 과거 조건으로 추천하지 않는다.
    반환은 필수값·범위·field_sources를 갱신한 RequestAnalysis 복사본이다. HTTP·LLM·DB·
    과거 가격/경로 재사용은 없다. 예:기존 여유30km에 가능거리20km를 입력하면 보완 질문이다.
    """
    result = current.model_copy(deep=True)
    if result.intent != "recommend":
        return result
    control_errors = []
    reset = False
    if extracted.reset_context is not None:
        quote = extracted.reset_context.quote
        reset = (
            quote in user
            and any(
                token in quote
                for token in (
                    "초기화",
                    "새로 시작",
                    "새 여행",
                    "새로운 여행",
                    "새 계획",
                    "처음부터",
                )
            )
            and not re.search(r"하지\s*마|말고|금지", quote)
        )
        if not reset:
            control_errors.append("context_reset")
    old = previous if previous is not None and previous.has_trip and not reset else None
    values = old.conditions.model_dump() if old else TripConditions().model_dump()
    specified = {
        field for field in TripConditions.model_fields if getattr(extracted, field) is not None
    }
    touched = set(specified)
    removed = set()
    for removal in extracted.clear_fields:
        if _valid_removal(user, removal):
            removed.add(removal.field)
            if removal.field in specified:
                control_errors.append("condition_removal")
        else:
            control_errors.append("condition_removal")
    touched |= removed
    if "arrival_deadline" in removed:
        removed.add("arrival_date")
        touched.add("arrival_date")
    sources = dict(old.coordinate_sources) if old else {}
    notes = dict(old.location_notes) if old else {}
    for role in ("origin", "destination"):
        label = role + "_label"
        if role in removed or label in removed:
            removed |= {role, label}
            touched |= {role, label}
        elif label in specified and role not in specified:
            new_label = getattr(current.conditions, label)
            if new_label is None or new_label != values[label]:
                values[role] = None
                touched.add(role)
                sources.pop(role, None)
                notes.pop(role, None)
        elif role in specified and label not in specified:
            values[label] = None
            touched.add(label)
        if role in touched:
            sources.pop(role, None)
            notes.pop(role, None)
    distance = bool(specified & set(DISTANCE_FIELDS))
    fuel = bool(specified & set(FUEL_FIELDS[:2]))
    mode = old.range_input_mode if old else current.range_input_mode
    if distance != fuel:
        mode = "distance" if distance else "fuel"
        incompatible = FUEL_FIELDS[:2] if distance else DISTANCE_FIELDS
        for field in incompatible:
            values[field] = None
            touched.add(field)
    for field in specified:
        values[field] = getattr(current.conditions, field)
    for field in removed:
        values[field] = None
    result.conditions = TripConditions.model_validate(values)
    result.range_input_mode = mode
    result.invalid_fields = [
        field
        for field in (old.invalid_fields if old else [])
        if field not in touched
        and field not in NUMERIC_FIELDS | {"range_input"}
        and field in TripConditions.model_fields
    ]
    result.invalid_fields += [
        field
        for field in current.invalid_fields
        if field not in removed and field not in NUMERIC_FIELDS | {"range_input"}
    ]
    result.invalid_fields += condition_issues(result.conditions, mode)
    result.unverified_fields = (
        [
            field
            for field in (old.unverified_fields if old else [])
            if field not in touched and field in TripConditions.model_fields
        ]
        + [field for field in current.unverified_fields if field not in removed]
        + control_errors
    )
    result.invalid_fields = list(dict.fromkeys(result.invalid_fields))
    result.unverified_fields = list(dict.fromkeys(result.unverified_fields))
    result.coordinate_sources = sources
    result.warnings = list(notes.values())
    result.field_sources = {
        field: "current" if field in specified else "memory"
        for field in TripConditions.model_fields
        if getattr(result.conditions, field) is not None
    }
    return refresh_analysis(result)


def prompt_memory(previous: ConversationMemory) -> dict:
    """분석 모델에 전달할 검증 조건·보완 대상·직전 요약만 담은 작은 사전을 반환한다.

    previous는 한 ID에서 읽은 typed Memory다. 무효/미확인 값은 조건 사전에서 제외하고
    근거 인용·전체 대화·API 결과·인증은 넣지 않는다. HTTP·저장·보정 없이 새 사전만 반환한다.
    예:pending_fields=['reserve_range_km']는 '30km' 답변의 대상을 파악하는 데 쓰지만,
    값은 이번 원문에서 추출해야 한다. 과거 조건을 이번 Mention으로 복사하면 안 된다.
    """
    excluded = set(previous.invalid_fields + previous.unverified_fields)
    return {
        "has_trip": previous.has_trip,
        "conditions": previous.conditions.model_dump(
            mode="json", exclude_none=True, exclude=excluded
        ),
        "pending_fields": previous.pending_fields,
        "last_request_summary": previous.last_request_summary,
        "last_response_summary": previous.last_response_summary,
    }


def remember_turn(
    previous: ConversationMemory,
    analysis: RequestAnalysis,
    *,
    execution: PlanExecution | None = None,
    judgement: TripJudgement | None = None,
) -> ConversationMemory:
    """완료한 그래프의 검증 조건·짧은 요청/응답 요약으로 다음 버전의 맥락을 만든다.

    previous는 현재 ID의 저장본 또는 revision0, analysis는 위치 해석까지 마친 결과다.
    execution/judgement는 실행 단계가 있을 때만 전달한다. 추천 의도에서만 조건·오류·
    보완 대상을 갱신하며 인사/설명은 기존 여행을 유지한다. 가격·경로·원본 Tool 응답·전체
    발언은 넣지 않고 변경 필드명·상태·추천ID·제외 코드·보완 필드를1000/2000자 이내로
    요약한다. revision은1 증가하고 updated_at은 시간대 있는UTC다. 반환은 복사본이며
    실제 저장/비밀값 정제는 호출자 책임이다. HTTP·LLM·재시도는 없다. 예:정차시간이 빠진
    판정은 pending_fields에 남아 다음 '10분' 답변의 대상을 식별할 수 있다.
    """
    result = previous.model_copy(deep=True)
    result.revision += 1
    result.updated_at = utc_now()
    if analysis.intent == "recommend":
        result.has_trip = True
        result.conditions = analysis.conditions.model_copy(deep=True)
        result.range_input_mode = analysis.range_input_mode
        result.invalid_fields = list(analysis.invalid_fields)
        result.unverified_fields = list(analysis.unverified_fields)
        result.coordinate_sources = dict(analysis.coordinate_sources)
        result.location_notes = {
            role: note
            for role, prefix in (("origin", "출발 위치"), ("destination", "도착 위치"))
            for note in analysis.warnings
            if note.startswith(prefix)
        }
        pending = analysis.missing_fields + analysis.invalid_fields + analysis.unverified_fields
        if judgement is not None:
            pending += judgement.missing_fields
        result.pending_fields = list(dict.fromkeys(pending))
    changed = [field for field, source in analysis.field_sources.items() if source == "current"]
    result.last_request_summary = (
        f"의도={analysis.intent}; 이번에 확인한 조건={','.join(changed) or '없음'}"
    )
    selected = (
        execution.results.rank_candidates.recommended
        if execution and execution.results.rank_candidates
        else None
    )
    response = {
        "status": execution.status
        if execution
        else (
            "ready"
            if analysis.ready_for_planning
            else "needs_user_input"
            if analysis.needs_user_input
            else "not_required"
        ),
        "recommended_station_id": selected.station_id if selected else None,
        "missing_fields": result.pending_fields,
        "reason_codes": sorted(
            {
                code
                for candidate in execution.results.evaluate_constraints
                for code in candidate.reason_codes
            }
        )[:20]
        if execution
        else [],
        "error_code": execution.error.code if execution and execution.error else None,
    }
    result.last_response_summary = json.dumps(response, ensure_ascii=False)[:2000]
    return ConversationMemory.model_validate(result.model_dump())
