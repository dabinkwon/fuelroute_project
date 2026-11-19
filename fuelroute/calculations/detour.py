"""기본·접근·단일 경유의 실제 경로 비용으로 안전거리와 우회 상한을 평가한다."""

from pydantic import BaseModel, Field

from fuelroute.schemas.routes import DrivingRoute


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
    반환 비용과 구간 목록은 안전거리·우회 상한 계산에만 사용한다. Tool 레지스트리의
    정규화된 DrivingRoute dict도 같은 검증을 거쳐 사용할 수 있다.
    """
    try:
        normalized = (
            DrivingRoute.from_probe_data(data)
            if "response" in data
            else DrivingRoute.model_validate(data)
        )
        cost = _Cost(distance_m=normalized.distance_m, duration_s=normalized.duration_s)
        sections = [_Cost(**part.model_dump()) for part in normalized.legs]
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
