"""F3005 요청의 원문 근거·단위·누락·범위를 검증한다. 공유 모델은 schemas/analysis.py에 둔다."""

import re
from datetime import date, time, timedelta
from decimal import Decimal

from pydantic import ValidationError

from fuelroute.schemas.analysis import (
    FIELD_LABELS,
    ExtractedRequest,
    Mention,
    RequestAnalysis,
    TripConditions,
)
from fuelroute.schemas.coordinates import Coordinate
from fuelroute.schemas.driving import RangeConditions

BASE_REQUIRED_FIELDS = ("origin", "destination", "fuel_type")


DISTANCE_FIELDS = ("remaining_range_km", "reserve_range_km")


FUEL_FIELDS = ("current_fuel_l", "reserve_fuel_l", "avg_efficiency_kmpl")


REQUIRED_FIELDS = BASE_REQUIRED_FIELDS + DISTANCE_FIELDS


NUMBER = re.compile(r"[-+]?\d+(?:,\d{3})*(?:\.\d+)?")


QUANTITY_UNITS = {
    "refuel_stop_min": r"(?:분|min(?:ute)?s?)",
    "remaining_range_km": r"(?:km(?![a-z/])|킬로미터)",
    "reserve_range_km": r"(?:km(?![a-z/])|킬로미터)",
    "current_fuel_l": r"(?:l(?![a-z])|리터)",
    "reserve_fuel_l": r"(?:l(?![a-z])|리터)",
    "avg_efficiency_kmpl": r"(?:km\s*/\s*l|kmpl|킬로미터\s*/\s*리터)",
    "max_detour_min": r"(?:분|min(?:ute)?s?)",
    "max_detour_km": r"(?:km(?![a-z])|킬로미터)",
}


def _number_present(value: float, quote: str) -> bool:
    """추출 숫자가 근거 구절에 실제 등장하는지 십진수로 비교한다. 단위 변환은 하지 않는다.

    15와 15.0, 천 단위 쉼표는 같은 값으로 본다. 숫자를 새로 계산하거나 외부를 조회하지
    않는다. 예: 12는 '연비 12km/L'에서 확인되지만 15는 확인되지 않는다.
    """
    return Decimal(str(value)) in {
        Decimal(token.replace(",", "")) for token in NUMBER.findall(quote)
    }


def _quantity_present(field: str, value: float, quote: str) -> bool:
    """연료·연비·우회 수치가 해당 단위와 함께 원문 근거에 있는지 검사한다.

    허용 단위는 L/리터, km/L, 분/min, km다. 갤런·mpg·시간·m를 자동 변환하거나
    단위 없는 숫자를 추정하지 않는다. 예: max_detour_min=1은 '1시간'에서 채택하지
    않는다. 단위가 빠졌으면 입력 확인 대상으로 남기며 HTTP·저장 부작용은 없다.
    """
    pattern = rf"({NUMBER.pattern})\s*{QUANTITY_UNITS[field]}"
    return Decimal(str(value)) in {
        Decimal(token.replace(",", "")) for token in re.findall(pattern, quote, re.I)
    }


def _date_present(value: date, quote: str, reference_date: date) -> bool:
    """명시 날짜 또는 오늘/내일/모레를 기준일과 대조한다. 누락 날짜의 기본값은 없다.

    ISO 날짜·연월일 표기와 상대 날짜만 확인한다. 연도가 빠진 날짜는 추정하지 않는다.
    예: 기준일이 2026-11-19일 때 '내일'의 값은 2026-11-20이어야 한다.
    """
    if value.isoformat() in quote:
        return True
    for year, month, day in re.findall(r"(\d{4})년\s*(\d{1,2})월\s*(\d{1,2})일", quote):
        if (value.year, value.month, value.day) == (int(year), int(month), int(day)):
            return True
    return any(
        token in quote and value == reference_date + timedelta(days=offset)
        for token, offset in [("오늘", 0), ("내일", 1), ("모레", 2)]
    )


def _time_present(value: str, quote: str) -> bool:
    """HH:MM 추출값을 원문 24시간 표기 또는 오전/오후 시·분과 대조한다.

    형식 오류는 False이며 외부 호출·날짜 보정은 없다. 예: '오후 3시'와 15:00은 같다.
    명시하지 않은 분은 0분이며 시각 자체를 언급하지 않은 요청에는 적용하지 않는다.
    """
    try:
        parsed = time.fromisoformat(value)
    except ValueError:
        return False
    if parsed.second or parsed.microsecond or parsed.tzinfo:
        return False
    if value in quote:
        return True
    for period, hour, minute in re.findall(r"(오전|오후)?\s*(\d{1,2})시(?:\s*(\d{1,2})분)?", quote):
        hours = int(hour)
        if period:
            if not 1 <= hours <= 12:
                continue
            hours = hours % 12 + (12 if period == "오후" else 0)
        if (parsed.hour, parsed.minute) == (hours, int(minute or 0)):
            return True
    return False


def _grounded(field: str, mention: Mention, user: str, reference_date: date) -> bool:
    """원문에 존재하는 근거와 추출값을 대조한다. 확인하지 못한 값은 채택하지 않는다.

    숫자·좌표·날짜·시각·유종/정렬 별칭을 확인하며 지오코딩이나 의미의 완전한 검증은
    하지 않는다. 같은 숫자가 다른 맥락에 있는 경우의 의미 분류는 모델 책임으로 남는다.
    """
    value, quote = mention.value, mention.quote
    if quote not in user:
        return False
    if field in {"origin", "destination"}:
        return _number_present(value.longitude, quote) and _number_present(value.latitude, quote)
    if field in {"trip_date", "arrival_date"}:
        return _date_present(value, quote, reference_date)
    if field in {"departure_time", "arrival_deadline"}:
        return _time_present(value, quote)
    aliases = {
        "gasoline": ("휘발유", "가솔린", "gasoline"),
        "diesel": ("경유", "diesel"),
        "price": ("싼", "저렴", "최저가", "가격 우선", "가격순", "price"),
        "detour_time": ("시간 우선", "시간순", "가장 빨리", "detour_time"),
    }
    if field in {"fuel_type", "sort_by"}:
        return any(alias in quote.lower() for alias in aliases[value])
    if isinstance(value, (float, int)):
        return _quantity_present(field, value, quote)
    return bool(value.strip()) and value in quote


def condition_issues(conditions: TripConditions, mode: str) -> list[str]:
    """한 발언 또는 세션 통합 뒤의 조건에 같은 수치·입력 방식 검사를 적용한다.

    conditions는 유한값의 TripConditions, mode는 distance/fuel이다. 주행 가능거리·연료·
    연비는 양수, 여유/상한은 비음수이며 여유<현재량이어야 한다. km와L량 혼용은range_input.
    누락은 refresh_analysis가 별도로 처리한다. 반환은 무효 필드명 목록이고 보정·계산·
    HTTP·저장 부작용은 없다. 예:주행거리를20km로 바꾸고 여유30km를 이어받으면 여유가 무효다.
    """
    invalid = []
    for field in (
        "remaining_range_km",
        "reserve_range_km",
        "current_fuel_l",
        "avg_efficiency_kmpl",
        "reserve_fuel_l",
        "max_detour_min",
        "max_detour_km",
        "refuel_stop_min",
    ):
        value = getattr(conditions, field)
        if value is not None and (
            value < 0
            or field in {"remaining_range_km", "current_fuel_l", "avg_efficiency_kmpl"}
            and value == 0
        ):
            invalid.append(field)
    for current, reserve in [
        ("current_fuel_l", "reserve_fuel_l"),
        ("remaining_range_km", "reserve_range_km"),
    ]:
        if getattr(conditions, current) is not None and getattr(conditions, reserve) is not None:
            if getattr(conditions, reserve) >= getattr(conditions, current):
                invalid.append(reserve)
    distance = any(getattr(conditions, field) is not None for field in DISTANCE_FIELDS)
    fuel = any(getattr(conditions, field) is not None for field in FUEL_FIELDS[:2])
    if distance and fuel:
        invalid.append("range_input")
    fields = DISTANCE_FIELDS if mode == "distance" else FUEL_FIELDS
    if all(getattr(conditions, field) is not None for field in fields) and not invalid:
        RangeConditions.model_validate(
            conditions.model_dump(include=set(RangeConditions.model_fields))
        )
    return list(dict.fromkeys(invalid))


def analyze_conditions(
    user: str, extracted: ExtractedRequest, reference_date: date
) -> RequestAnalysis:
    """추출 근거·필수값·주행거리/연료/좌표 범위를 코드로 검사해 F3005 결과를 만든다.

    user는 이번 요청 원문이고 reference_date는 상대 날짜 해석 기준이다. 현재 발언만
    검증하며 과거 조건 병합은 memory.merge_conditions가 뒤에서 수행한다.
    기본 필수값은 출발/목적 좌표·유종·주행 가능 거리와
    안전 여유(km)다. L 연료량이 명시되고 km 입력이 없을 때만 기존 L/연비 방식을 선택한다.
    두 방식의 연료량을 혼용하면 입력 방식을 다시 묻는다. 연비만 있으면 km 방식을 유지한다.
    지명만 있으면 지명을 보존해 후속 위치 검색에 넘긴다. 불명확한 값은 제외하고
    unverified_fields로 알린다. 인사/설명은 주행 필수값을 요구하지 않는다.
    날짜/시각은 추출만 하며 도착 적합성·Tool 실행·계획·저장·재계획·최종 설명은 수행하지
    않는다. 예: '서울 가는 길에 주유'는 연료를 채우지 않고 누락 목록을 반환한다.
    """
    values, invalid, unverified = {}, [], []
    if extracted.intent == "recommend":
        for field in TripConditions.model_fields:
            mention = getattr(extracted, field)
            if mention is None:
                continue
            if not _grounded(field, mention, user, reference_date):
                unverified.append(field)
                continue
            value = mention.value
            if field in {"origin", "destination"}:
                try:
                    value = Coordinate.model_validate(value.model_dump())
                except ValidationError:
                    invalid.append(field)
                    continue
            values[field] = value
    conditions = TripConditions.model_validate(values)
    distance_mentioned = any(getattr(extracted, field) is not None for field in DISTANCE_FIELDS)
    fuel_mentioned = any(getattr(extracted, field) is not None for field in FUEL_FIELDS[:2])
    mode = "fuel" if fuel_mentioned and not distance_mentioned else "distance"
    if distance_mentioned and fuel_mentioned:
        invalid.append("range_input")
    invalid.extend(condition_issues(conditions, mode))
    result = RequestAnalysis(
        intent=extracted.intent,
        reason=extracted.reason,
        conditions=conditions,
        condition_groups={},
        missing_fields=[],
        invalid_fields=list(dict.fromkeys(invalid)),
        unverified_fields=unverified,
        needs_user_input=False,
        ready_for_planning=False,
        questions=[],
        range_input_mode=mode,
    )
    return refresh_analysis(result)


def refresh_analysis(analysis: RequestAnalysis) -> RequestAnalysis:
    """추출·위치 검색 뒤의 조건으로 누락 목록·질문·좌표 출처를 다시 계산한다.

    RequestAnalysis를 복사해 원문 대조·범위 오류를 유지하고 조건 그룹을 갱신한다.
    위치가 모호하면 지역/주소를 보완하게 하고 검색 장애를 좌표 추측으로 대신하지 않는다.
    range_input_mode에 따라 km 두 값 또는 기존 연료 세 값만 필수로 요구한다. km 방식에서
    연비를 요구하지 않는다. 외부 호출·DB 변경은 없다. 예: 위치가 확정되면 origin은
    누락 질문에서 제거하고 주행 가능 거리만 있으면 안전 여유(km)를 묻는다.
    """
    result = analysis.model_copy(deep=True)
    conditions = result.conditions
    invalid, unverified = result.invalid_fields, result.unverified_fields
    missing = (
        [
            field
            for field in (
                BASE_REQUIRED_FIELDS
                + (DISTANCE_FIELDS if result.range_input_mode == "distance" else FUEL_FIELDS)
            )
            if getattr(conditions, field) is None and field not in invalid
        ]
        if result.intent == "recommend"
        else []
    )
    groups = {
        "basis": [
            "origin_label",
            "destination_label",
            "origin",
            "destination",
            "trip_date",
            "departure_time",
        ],
        "preferences": ["fuel_type", "sort_by"],
        "constraints": [
            "remaining_range_km",
            "reserve_range_km",
            "current_fuel_l",
            "reserve_fuel_l",
            "avg_efficiency_kmpl",
            "max_detour_min",
            "max_detour_km",
            "arrival_deadline",
            "arrival_date",
            "refuel_stop_min",
        ],
    }
    needs = bool(missing or invalid or unverified)
    result.condition_groups = {
        group: [field for field in fields if getattr(conditions, field) is not None]
        for group, fields in groups.items()
    }
    result.missing_fields = missing
    result.needs_user_input = needs
    result.ready_for_planning = result.intent == "recommend" and not needs
    result.questions = []
    for field in missing:
        label = FIELD_LABELS[field]
        location = result.location_results.get(field)
        if location is not None and location.error:
            if location.error.code == "EMPTY_RESULT":
                result.questions.append(
                    f"{label}을 찾지 못했습니다. 상세 주소나 정확한 장소명을 알려주세요."
                )
            elif location.error.code == "INVALID_INPUT":
                result.questions.append(
                    f"{label}이 너무 길거나 올바르지 않습니다. 지명·주소를 확인해주세요."
                )
            else:
                result.questions.append(
                    f"{label} 검색을 이용할 수 없습니다. 잠시 후 다시 시도해주세요."
                )
        elif location is not None and location.data.status == "ambiguous":
            choices = "; ".join(
                f"{index}. {candidate.name} "
                f"({candidate.road_address or candidate.address or '주소 미확인'})"
                for index, candidate in enumerate(location.data.candidates, start=1)
            )
            result.questions.append(
                f"{label}의 후보가 여러 곳입니다. {choices}. 지역·주소를 더 자세히 알려주세요."
            )
        else:
            result.questions.append(f"{label}을 알려주세요.")
    result.questions += [
        f"{FIELD_LABELS[field]}을 확인해 다시 입력해주세요."
        for field in dict.fromkeys(invalid + unverified)
        if field not in missing
    ]
    for field in ("origin", "destination"):
        if getattr(conditions, field) is not None:
            result.coordinate_sources.setdefault(field, "user")
    return result
