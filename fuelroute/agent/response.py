"""F3011 실제 판정에서 한국어 근거 문장을 만들고 검증한 GMS 문단 구성으로 답한다."""

from collections import Counter
from collections.abc import Sequence
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from html import escape

from pydantic import ValidationError

from fuelroute.calculations.constraints import judge_execution
from fuelroute.core.errors import ProbeFailure
from fuelroute.schemas.response import (
    EvidenceSource,
    ExplanationOutline,
    FinalResponse,
    Narrator,
    ResponseContext,
    ResponseFact,
    ResponseFacts,
)

KST = timezone(timedelta(hours=9))
REASONS = {
    "UNREACHABLE": "안전 주행거리 초과",
    "DETOUR_TIME_EXCEEDED": "우회시간 상한 초과",
    "DETOUR_DISTANCE_EXCEEDED": "우회거리 상한 초과",
    "ARRIVAL_DEADLINE_EXCEEDED": "주행·정차시간의 도착 상한 초과",
    "MISSING_PRICE": "가격 미확인",
    "MISSING_DETAILS": "상세정보 미확인",
    "MISSING_ROUTE_COST": "실제 경로 비용 미확인",
    "FUEL_TYPE_MISMATCH": "입력 유종과 불일치",
    "STATION_ID_MISMATCH": "상세 ID 불일치",
    "TIMEOUT": "조회 응답시간 초과",
    "AUTH_FAILED": "조회 인증 실패",
    "RATE_LIMITED": "조회 한도 초과",
    "UPSTREAM_ERROR": "제공사 응답 오류",
    "INVALID_RESPONSE": "응답 형식 미확인",
    "NOT_FOUND": "상세 조회 결과 없음",
    "NO_ROUTE": "자동차 경로 없음",
    "EMPTY_RESULT": "조회 결과 없음",
    "CONFIG_ERROR": "API 설정 누락",
    "INVALID_INPUT": "요청 입력 오류",
    "UNVERIFIED_DETAILS": "상세정보 미확인",
    "UNVERIFIED_MISSING_PRICE": "가격 미확인",
    "UNVERIFIED_ROUTE_COST": "실제 경로 비용 미확인",
    "UNVERIFIED_UNREACHABLE": "도달 가능성 미확인",
    "UNVERIFIED_DETOUR_TIME_EXCEEDED": "우회시간 충족 여부 미확인",
    "UNVERIFIED_DETOUR_DISTANCE_EXCEEDED": "우회거리 충족 여부 미확인",
    "UNVERIFIED_ARRIVAL_DEADLINE_EXCEEDED": "도착 상한 충족 여부 미확인",
    "UNVERIFIED_LOOKUP_ERROR": "조회 오류로 미확인",
}
ORDER_LABELS = {
    "price_krw_per_l": "가격",
    "detour_duration_s": "추가 시간",
    "detour_distance_m": "추가 거리",
    "station_id": "주유소 ID",
}
STOP_LABELS = {
    "recommendation_found": "조건 충족 후보 발견",
    "evaluation_budget_exhausted": "전체 평가 예산 소진",
    "replan_limit_reached": "추가 탐색 횟수 상한",
    "search_space_exhausted": "설정한 검색 범위 소진",
}


def _number(value: float | int | Decimal) -> str:
    """검증한 유한 숫자를 단위 변환 없이 읽기 쉬운 십진수로 표시한다.

    실제 비용과 경계 비교를 반올림으로 숨기지 않도록 Decimal(str(value))를 사용한다.
    소수부의 불필요한0만 제거한다. 반환 문자열에는 천 단위 쉼표가 있고 단위는 호출자가
    붙인다. 외부 호출·입력 보정은 없다. 예:150.001은'150.001', 1800은'1,800'이다.
    """
    result = format(Decimal(str(value)), ",f")
    return result.rstrip("0").rstrip(".") if "." in result else result


def _duration(seconds: float | int) -> str:
    """비음수의 검증된 경로/정차 초를 분·초로 나눠 정확히 표시한다.

    반올림·시간 보정·외부 호출은 없다. 0초도 값으로 표시하며 소수초를 보존한다.
    예:352초→5분52초, 0초→0초. 계산 판단은 이미 끝났고 이 함수는 표현만 담당한다.
    """
    total = Decimal(str(seconds))
    minutes = int(total // 60)
    rest = total - minutes * 60
    return f"{minutes}분 {_number(rest)}초" if minutes else f"{_number(rest)}초"


def _safe_text(value: str, limit: int = 4000) -> str:
    """응답에 표시할 입력/제공사 문자열을 한 줄의 일반 텍스트로 정제한다.

    value는 사용자/제공사의 표시용 문자열이며 limit는 최대 문자 수(기본4000)다.
    줄바꿈·제어 공백을 공백으로 합치고 HTML/Markdown 제어문자를 이스케이프한다.
    사실 여부를 추론하거나 키를 정제하지 않으며 비밀값 정제는 상위 경계 책임이다.
    부작용·외부 호출은 없다. 예: '[name](url)'은 링크가 아닌 문자열로 표시된다.
    """
    text = escape(" ".join(value.split())[:limit], quote=False)
    for token in ("\\", "`", "*", "_", "[", "]", "!", "#", "|"):
        text = text.replace(token, "\\" + token)
    return text[:limit]


def _timestamp(value: datetime | None) -> str:
    """시간대가 확인된 기준/조회 시각만 KST로 표시한다. 누락/naive 시각은 미확인이다.

    가격 기준시각과 Tool 처리 완료시각은 호출자가 구분해 전달한다. 시간대를 추측하지
    않고 부작용은 없다. 예: UTC 01:00은 KST 10:00, None은 '미확인'이다.
    """
    if value is None or value.tzinfo is None or value.utcoffset() is None:
        return "미확인"
    return value.astimezone(KST).strftime("%Y-%m-%d %H:%M:%S KST")


def _sources(
    context: ResponseContext,
    fields: list[str],
    *,
    actions: Sequence[str] = (),
    station_id: str | None = None,
    request_ids: Sequence[str] = (),
) -> list[EvidenceSource]:
    """정규화 결과의 출처 경로를 실제 호출 ID에 연결하고 없으면 정규화 입력임을 표시한다.

    actions/후보ID 또는 명시 request_ids로 현재 실행 호출만 고른다. 캐시 경로의 실제 호출이
    다른 후보에 기록됐어도 request_ids로 추적할 수 있다. 키·URL·원본 응답은 포함하지 않는다.
    호출 메타가 없는 주입 비용을 실호출로 꾸미지 않는다. HTTP·DB·변경은 없고 새 목록이다.
    """
    calls = [
        call
        for call in context.execution.calls
        if (
            call.result.meta.request_id in request_ids
            if request_ids
            else (
                (not actions or call.action in actions)
                and (station_id is None or call.station_id == station_id)
            )
        )
    ]
    return [
        EvidenceSource(
            kind="tool_result",
            fields=fields,
            provider=call.result.meta.provider,
            tool_name=call.result.tool_name,
            request_id=call.result.meta.request_id,
            fetched_at=call.result.meta.fetched_at,
        )
        for call in calls
    ] or [EvidenceSource(kind="normalized_result", fields=fields)]


def _reason_text(codes: Sequence[str]) -> str:
    """제외·미확인 코드를 고정 한국어로 바꾼다. 알 수 없는 코드는 사유 미확인으로 표시한다.

    제공사 원문/예외를 출력하거나 사유를 생성하지 않는다. 중복 문구는 제거하고 빈 목록도
    미확인으로 표시한다. 외부 호출·변경은 없다. 예: UNREACHABLE은 안전 주행거리 초과다.
    """
    return (
        ", ".join(dict.fromkeys(REASONS.get(code, "검증 사유 미확인") for code in codes))
        or "검증 사유 미확인"
    )


class _FactBuilder:
    """현재 응답의 사실·질문·경고를 순서대로 모은다. 실행 상태는 수정하지 않는다."""

    def __init__(self) -> None:
        """빈 목록만 생성한다. 입력·외부 호출·파일·LLM 부작용은 없다."""
        self.facts: list[ResponseFact] = []
        self.questions: list[str] = []
        self.warnings: list[str] = []

    def add(
        self, fact_id: str, category: str, text: str, sources: list[EvidenceSource] | None = None
    ) -> None:
        """코드로 만든 완결 문장과 출처를 추가하고 질문/경고는 별도로 분류한다.

        fact_id는 응답 안의 유일한 ID, category는 ResponseFact의 고정 분류, text는 검증한
        한국어 문장, sources는 출처 목록이며 생략하면 빈 목록이다. 본문은 일반 텍스트로
        이스케이프한다. 모델 출력은 받지 않는다. 스키마 불일치는 ValidationError이며
        사실을 보정하지 않는다. 반환은 없고 부작용은 이 객체의 목록 추가뿐이다.
        """
        safe = _safe_text(text)
        self.facts.append(
            ResponseFact(fact_id=fact_id, category=category, text=safe, sources=sources or [])
        )
        if category == "question":
            self.questions.append(safe)
        elif category == "warning":
            self.warnings.append(safe)

    def bundle(self, status: str, title: str, recommended_id: str | None = None) -> ResponseFacts:
        """수집한 사실을 복사한 typed 묶음으로 반환한다. 계산·모델·HTTP·저장은 없다.

        status는 최종 상태, title은 표시 제목, recommended_id는 Opinet 추천 ID 또는 None이다.
        추천/미발견은 평가 집합 범위, 나머지는 미평가 범위다. 잘못된 상태/빈 사실은
        ValidationError다. 예: needs_user_input에는 확인 질문이 있고 추천 ID는 없다.
        """
        return ResponseFacts(
            status=status,
            title=title,
            facts=self.facts,
            questions=self.questions,
            warnings=self.warnings,
            recommended_station_id=recommended_id,
            scope="evaluated_candidates_only"
            if status in {"recommended", "no_recommendation"}
            else "not_applicable",
        )


def _recommendation_facts(context: ResponseContext, builder: _FactBuilder) -> str:
    """현재 확정 추천의 가격·도달·우회·선택 시간·정렬을 출처와 함께 문장으로 만든다.

    context의 선행 판정과 정규화 실행에서 추천 결과가 같아야 한다. 현재 가격(원/L), 실제
    경로 거리(m), 시간(초)만 사용하고 화면 문장은 km·분/초로 변환한다. 가격 기준/조회
    완료시각은 구분하며 시간대 미확인은 추정하지 않는다. 모순/비용 누락은
    ProbeFailure(INVALID_RESPONSE)다. 반환은 Opinet 추천 ID이며 부작용은 builder의
    사실 목록 추가뿐이다. HTTP·DB·LLM은 없다. 예: 149999m는 149.999km로 표시한다.
    """
    chosen = context.judgement.recommendation.recommended
    selected = context.execution.results.rank_candidates.recommended
    if chosen is None or selected is None or chosen != selected or chosen.station is None:
        raise ProbeFailure("INVALID_RESPONSE", "최종 추천과 판정 근거가 일치하지 않습니다.")
    station = chosen.station
    if (
        station.price_krw_per_l is None
        or chosen.reach_distance_m is None
        or chosen.detour_distance_m is None
        or chosen.detour_duration_s is None
    ):
        raise ProbeFailure("INVALID_RESPONSE", "추천의 가격·도달·우회 근거가 없습니다.")
    details = _sources(
        context,
        ["results.station_details"],
        actions=["station_details"],
        station_id=station.station_id,
    )
    pair = context.execution.results.candidate_routes.get(station.station_id)
    ids = (
        [value for value in [pair.approach_request_id, pair.via_request_id] if value]
        if pair
        else []
    )
    route_sources = _sources(
        context,
        ["results.candidate_routes"],
        actions=["candidate_routes"],
        station_id=station.station_id,
        request_ids=ids,
    )
    route_sources += _sources(context, ["results.base_route"], actions=["base_route"])
    fuel = "휘발유" if station.fuel_type == "gasoline" else "경유"
    builder.add(
        "recommended",
        "result",
        f"{station.name[:120]}({fuel})를 추천합니다. "
        f"조회 가격은 {_number(station.price_krw_per_l)}원/L입니다.",
        details,
    )
    if station.address:
        builder.add(
            "station_address", "result", f"주유소 주소는 {station.address[:240]}입니다.", details
        )
    price_time = _timestamp(station.price_observed_at)
    fetched = next((item.fetched_at for item in details if item.fetched_at is not None), None)
    builder.add(
        "price_time",
        "source",
        f"가격 기준시각: {price_time}. 조회 처리 완료시각: {_timestamp(fetched)}. "
        "두 시각은 구분합니다.",
        details,
    )
    if price_time == "미확인":
        builder.add(
            "price_time_unknown",
            "warning",
            "가격 기준시각 또는 그 시간대가 확인되지 않았습니다.",
            details,
        )
    request = context.plan.request
    input_source = [
        EvidenceSource(kind="input", fields=["analysis.conditions", "judgement.safe_range_km"])
    ]
    if request.remaining_range_km is not None:
        text = (
            f"입력한 주행 가능거리 {_number(request.remaining_range_km)}km에서 "
            f"안전 여유 {_number(request.reserve_range_km)}km를 남겨 "
            f"안전 주행거리를 {_number(context.judgement.safe_range_km)}km로 계산했습니다."
        )
    else:
        text = (
            f"입력한 현재 연료 {_number(request.current_fuel_l)}L·"
            f"안전 잔량 {_number(request.reserve_fuel_l)}L·"
            f"연비 {_number(request.avg_efficiency_kmpl)}km/L 기준 "
            f"안전 주행거리는 {_number(context.judgement.safe_range_km)}km입니다."
        )
    builder.add("safe_range", "safety", text, input_source)
    builder.add(
        "reach",
        "safety",
        "도달 판정에 사용한 실제 자동차 경로 거리는 "
        f"{_number(Decimal(str(chosen.reach_distance_m)) / 1000)}km로 안전 주행거리 이내입니다.",
        route_sources + input_source,
    )
    builder.add(
        "detour",
        "cost",
        "주유소 경유 경로는 기본 경로보다 "
        f"{_number(Decimal(str(chosen.detour_distance_m)) / 1000)}km, "
        f"{_duration(chosen.detour_duration_s)} 추가되는 것으로 조회됐습니다.",
        route_sources,
    )
    limits = []
    if request.max_detour_min is not None:
        limits.append(f"시간 {_number(request.max_detour_min)}분")
    if request.max_detour_km is not None:
        limits.append(f"거리 {_number(request.max_detour_km)}km")
    if limits:
        builder.add(
            "detour_limits",
            "cost",
            "입력한 우회 상한(" + ", ".join(limits) + ")을 충족합니다.",
            route_sources + input_source,
        )
    if chosen.negative_delta_observed:
        builder.add(
            "negative_delta",
            "warning",
            "조회된 경유-기본 비용 차이가 음수여서 비교에는0을 사용했습니다. "
            "실제 추가 비용이0이라고 보장하지 않습니다.",
            route_sources,
        )
    candidate = next(
        item for item in context.judgement.candidates if item.station_id == station.station_id
    )
    if context.judgement.time_constraint.status == "ready":
        arrival = (
            _timestamp(candidate.estimated_arrival_at)
            if candidate.estimated_arrival_at is not None
            else (
                f"{candidate.estimated_arrival_clock} "
                f"(출발일 기준 +{candidate.arrival_day_offset}일, 날짜 미지정)"
            )
        )
        builder.add(
            "arrival",
            "cost",
            f"입력한 주유·정차시간 {_duration(context.judgement.time_constraint.stop_s)}을 "
            f"포함한 예상 도착은 {arrival}이며 입력 도착 상한 이내입니다.",
            route_sources + input_source,
        )
    order = " → ".join(
        ORDER_LABELS[name] for name in context.judgement.recommendation.ranking_fields
    )
    builder.add(
        "ranking",
        "ranking",
        f"평가한 후보 중 조건을 통과한 후보를 {order} 순으로 비교했습니다.",
        [EvidenceSource(kind="calculation", fields=["judgement.recommendation.ranking_fields"])],
    )
    return station.station_id


def _rest_facts(context: ResponseContext, builder: _FactBuilder) -> None:
    """최종 추천 주변의 보조정보를 데이터 기준일·시설 미확인·매칭 한계와 함께 표시한다.

    context의 휴게소 보조 조회가 끝난 뒤 호출한다. 성공해도 같은 주유소/방향으로 단정하지
    않고 최대3곳의 이름/자료상 시설을 표시한다. 실패·빈 결과는 미확인으로 남기며 추천을
    지우지 않는다. 직선거리(m)는 실제 도로 도달거리로 쓰지 않는다. 반환은 없고 builder에
    사실을 추가한다. API·저장은 없다. 예: 시설 None은 '미확인', False는 '없음'이다.
    """
    rest = context.execution.results.rest_area_info
    references = _sources(context, ["results.rest_area_info"], actions=["rest_area_info"])
    if rest is None or not rest.items:
        builder.add(
            "rest_unavailable",
            "warning",
            "휴게소 보조정보는 미확인입니다. "
            "주유 후보 추천은 확인된 가격·경로·제약 근거로 유지합니다.",
            references,
        )
        return
    names = {"toiletYn": "화장실", "rstrtYn": "음식점", "shopYn": "매점", "nrsgYn": "수유실"}
    facility_values = {True: "있음", False: "없음", None: "미확인"}
    for index, item in enumerate(rest.items[:3]):
        facilities = [
            f"{label} {facility_values[item.facilities[key]]}"
            for key, label in names.items()
            if key in item.facilities
        ]
        builder.add(
            f"rest_{index}",
            "rest_area",
            f"참고 휴게소 {item.name[:120]}: 검색 좌표 기준 직선거리 "
            f"약 {_number(round(item.distance_m))}m, "
            f"자료 기준일 {item.reference_date or '미확인'}, "
            f"자료 노선 {item.route_name or '미확인'}·표기 방향 {item.direction or '미확인'}. "
            f"자료상 시설은 {', '.join(facilities) or '미확인'}입니다.",
            references,
        )
    builder.add(
        "rest_matching",
        "warning",
        "휴게소와 추천 주유소가 같은 시설·진행 방향인지는 미확인(unverified)입니다. "
        "시설의 실시간 운영 여부도 확인하지 않았습니다.",
        references,
    )


def build_response_facts(context: ResponseContext) -> ResponseFacts:
    """최종 상태에 맞는 한국어 사실·질문·주의·출처를 코드로 만들고 환각 여지를 제한한다.

    context는 현재 요청의 마지막 판정/보조 연결 뒤 ResponseContext다. 추천·미발견은 원본
    비용으로 다시 판정해 상태 모순을 거절하고 모델이 만든 계산/시설/가격은 받지 않는다.
    주요 제외와 휴게소는 각각 최대3곳만 표시하며 평가 범위·실패·미확인을 구분한다.
    인사/설명/입력 보완/핵심 오류는 조회 없는 문장으로 처리한다. 반환 ResponseFacts에는
    단위가 포함된 문장·출처·질문·주의가 있다. HTTP·LLM·DB·입력 변경은 없고 모순은
    ProbeFailure(INVALID_RESPONSE)다. 예: 시설 None은 '없음'이 아닌 '미확인'이다.
    """
    builder = _FactBuilder()
    analysis = context.analysis
    if analysis.intent != "recommend":
        if analysis.intent == "greeting":
            text = (
                "안녕하세요. 변경할 주행 조건이나 주유 추천 요청을 알려주세요."
                if context.memory and context.memory.has_trip
                else "안녕하세요. 출발지·목적지·유종·계기판 주행 가능거리와 "
                "남길 안전 여유(km)를 알려주시면 주유 후보를 비교하겠습니다."
            )
        elif analysis.intent == "explain":
            text = (
                "FuelRoute는 안전하게 도달 가능한 주유소를 먼저 확인하고, "
                "사용자 제약을 통과한 후보의 조회 가격과 우회 비용을 비교합니다. "
                "미래 유가나 실시간 시설 운영을 예측하지 않습니다."
            )
        else:
            text = "주유 경로 추천을 도와드릴 수 있습니다. 출발지·목적지와 주행 조건을 알려주세요."
        builder.add("conversation", "conversation", text)
        if context.memory and context.memory.has_trip:
            builder.add(
                "memory_retained",
                "conversation",
                "같은 대화의 기존 주행 조건은 유지했습니다. "
                "현재 가격·경로를 확인하려면 다시 추천을 요청해주세요.",
            )
        return builder.bundle("not_required", "FuelRoute 안내")
    if context.execution.error:
        code = context.execution.error.code
        builder.add(
            "core_error",
            "result",
            f"추천을 완료하지 못했습니다: {_reason_text([code])}. "
            "현재 결과로 주유소를 확정하지 않습니다.",
            _sources(context, ["execution.error"]),
        )
        builder.add(
            "error_guidance",
            "question",
            "API 설정·권한·호출 한도와 오류 코드를 확인한 뒤 다시 요청해주세요.",
        )
        return builder.bundle("error", "조회 결과 확인이 필요합니다")
    if (
        context.plan.status == "needs_user_input"
        or analysis.needs_user_input
        or context.judgement.status == "needs_user_input"
    ):
        builder.add(
            "input_needed",
            "result",
            "추천에 필요한 입력을 먼저 확인해야 합니다. "
            "확인되지 않은 값으로 주유소를 확정하지 않습니다.",
        )
        for index, question in enumerate(
            dict.fromkeys([*analysis.questions, *context.judgement.questions])
        ):
            builder.add(
                f"question_{index}",
                "question",
                question,
                [
                    EvidenceSource(
                        kind="input", fields=["analysis.questions", "judgement.questions"]
                    )
                ],
            )
        return builder.bundle("needs_user_input", "추가 정보를 알려주세요")
    fresh = judge_execution(analysis, context.plan, context.execution)
    if fresh.status != context.judgement.status:
        raise ProbeFailure("INVALID_RESPONSE", "최종 응답과 실제 제약 판정이 일치하지 않습니다.")
    context = context.model_copy(update={"judgement": fresh})
    recommended_id = None
    if fresh.status == "recommended":
        recommended_id = _recommendation_facts(context, builder)
        _rest_facts(context, builder)
        status, title = "recommended", "추천 결과"
    else:
        builder.add(
            "no_recommendation",
            "result",
            "현재 확인한 후보 중 확정 추천할 주유소를 찾지 못했습니다. "
            "전체 경로에 주유소가 없다는 뜻은 아닙니다.",
        )
        for index, guidance in enumerate(context.replanning.next_input):
            builder.add(f"next_input_{index}", "question", guidance)
        status, title = "no_recommendation", "조건에 맞는 추천을 찾지 못했습니다"
    for index, candidate in enumerate(
        [item for item in fresh.candidates if item.status != "eligible"][:3]
    ):
        label = (
            candidate.evaluation.station.name[:120]
            if candidate.evaluation.station
            else candidate.station_id
        )
        action = (
            "조회·검증 미확인으로 보류"
            if candidate.status == "unverified"
            else "제약 미충족으로 제외"
        )
        builder.add(
            f"excluded_{index}",
            "exclusion",
            f"{label}: {_reason_text(candidate.evaluation.reason_codes)} 때문에 {action}했습니다.",
            _sources(context, ["judgement.candidates"], station_id=candidate.station_id),
        )
    evaluated = len(context.execution.results.evaluate_constraints)
    total = len(
        {item.station_id for item in context.execution.results.search_candidates}
        | {item.station_id for item in context.execution.results.evaluate_constraints}
    )
    builder.add(
        "scope",
        "scope",
        f"조회한 고유 후보 {total}곳 중 {evaluated}곳을 평가했습니다. "
        "평가한 집합 안의 결과이며 전체 경로 최저가나 모든 후보의 비교를 보장하지 않습니다.",
        [
            EvidenceSource(
                kind="calculation",
                fields=["results.search_candidates", "results.evaluate_constraints"],
            )
        ],
    )
    if context.replanning.stop_reason:
        builder.add(
            "replan",
            "scope",
            f"추가 탐색은 {context.replanning.replan_count}회 수행했습니다. "
            f"종료 사유: {STOP_LABELS.get(context.replanning.stop_reason, '현재 단계 종료')}.",
            [EvidenceSource(kind="calculation", fields=["replanning"])],
        )
    builder.add(
        "estimates",
        "warning",
        "주행 가능거리·소요시간·예상 도착은 입력과 조회 응답에 따른 추정치입니다. "
        "미래 교통·대기시간·계획일의 유가를 보장하지 않습니다.",
    )
    if any(source == "memory" for source in analysis.field_sources.values()):
        builder.add(
            "inherited",
            "warning",
            "이번에 바꾸지 않은 조건은 같은 대화의 이전 입력값을 이어받았습니다.",
            [EvidenceSource(kind="input", fields=["analysis.field_sources"])],
        )
    for index, warning in enumerate(
        [text for text in analysis.warnings if text.startswith(("출발 위치", "도착 위치"))][:2]
    ):
        builder.add(
            f"area_{index}",
            "warning",
            warning,
            [EvidenceSource(kind="input", fields=["analysis.coordinate_sources"])],
        )
    builder.add(
        "providers",
        "source",
        "가격은 오피넷, 자동차 경로는 카카오모빌리티의 정규화 응답을 사용합니다. "
        "휴게소 보조정보는 전국휴게소정보표준데이터 기준이며 미확인 여부를 따로 표시합니다.",
        _sources(context, ["execution.calls"]),
    )
    return builder.bundle(status, title, recommended_id)


def render_response(
    facts: ResponseFacts, outline: ExplanationOutline, *, mode: str, error_code: str | None = None
) -> FinalResponse:
    """모델 문단의 사실 ID를 검사하고 원문 사실 문장만으로 최종 답변을 렌더링한다.

    facts는 코드 생성 사실, outline은 모델/기본 문단, mode는 model/template/fallback,
    error_code는 fallback 원인 또는 None이다. 모든 사실 ID가 정확히 한 번 있어야 하고
    첫 사실은 결과여야 한다. 누락/중복/새 ID/순서 위반은 ProbeFailure(INVALID_RESPONSE)다.
    본문/숫자/추천 ID는 모델이 바꿀 수 없다. FinalResponse는 답변·근거·출처 ID·질문·경고를
    반환한다. HTTP·LLM·DB·입력 변경은 없다. 예: 임의 가격 문장을 ID로 보내면 거절한다.
    """
    by_id = {item.fact_id: item for item in facts.facts}
    ordered = [fact_id for paragraph in outline.paragraphs for fact_id in paragraph.fact_ids]
    if (
        len(by_id) != len(facts.facts)
        or Counter(ordered) != Counter(by_id.keys())
        or ordered[0] != facts.facts[0].fact_id
    ):
        raise ProbeFailure("INVALID_RESPONSE", "최종 설명의 근거 누락·중복·새 내용을 확인하세요.")
    answer = (
        facts.title
        + "\n\n"
        + "\n\n".join(
            " ".join(by_id[fact_id].text for fact_id in paragraph.fact_ids)
            for paragraph in outline.paragraphs
        )
    )
    evidence = [by_id[fact_id] for fact_id in ordered]
    return FinalResponse(
        status=facts.status,
        answer=answer,
        evidence=evidence,
        questions=facts.questions,
        warnings=facts.warnings,
        recommended_station_id=facts.recommended_station_id,
        scope=facts.scope,
        source_request_ids=list(
            dict.fromkeys(
                source.request_id
                for fact in evidence
                for source in fact.sources
                if source.request_id
            )
        ),
        mode=mode,
        generation_error_code=error_code,
    )


async def finalize_response(
    context: ResponseContext, narrator: Narrator | None = None
) -> FinalResponse:
    """검증 사실을 만들고 필요한 경우에만 GMS 설명 구성1회를 사용해 사용자 답변을 만든다.

    context는 현재 요청의 최종 상태다. narrator는 ResponseFacts를 받아 ExplanationOutline을
    반환하는 비동기 함수 또는 None(기본)이다. 추천/미발견일 때만 한 번 호출하며 입력은
    깊은 복사다. 인사/설명/입력 보완/핵심 오류는 템플릿이다. 예상 모델 오류·스키마/근거
    위반은 같은 사실의 fallback으로 추천을 유지한다. 반환은 FinalResponse이고 원인 코드는
    generation_error_code에 남긴다. 재시도·추가 데이터 조회·DB·재계산 LLM은 없다.
    프로그래밍 오류는 숨기지 않는다. 예: 최종 모델 TIMEOUT도 이미 확인한 추천을 지우지 않는다.
    """
    facts = build_response_facts(context)
    fallback = ExplanationOutline(paragraphs=[{"fact_ids": [fact.fact_id]} for fact in facts.facts])
    if narrator is None or facts.status not in {"recommended", "no_recommendation"}:
        return render_response(facts, fallback, mode="template")
    try:
        outline = ExplanationOutline.model_validate(await narrator(facts.model_copy(deep=True)))
        return render_response(facts, outline, mode="model")
    except (ProbeFailure, ValidationError) as exc:
        code = exc.error.code if isinstance(exc, ProbeFailure) else "INVALID_RESPONSE"
        return render_response(facts, fallback, mode="fallback", error_code=code)
