"""명시한 주행 가능 거리·안전 여유(km) 또는 기존 L/연비로 안전거리를 계산한다."""

from math import isfinite

from fuelroute.schemas.driving import RangeConditions


def calculate_safe_range(
    current_fuel_l: float | None = None,
    reserve_fuel_l: float | None = None,
    avg_efficiency_kmpl: float | None = None,
    *,
    remaining_range_km: float | None = None,
    reserve_range_km: float | None = None,
) -> float:
    """사용자가 입력한 km 범위 또는 기존 L/연비로 안전 여유를 남기는 추정 거리(km)를 계산한다.

    기본 입력은 remaining_range_km>0와0<=reserve_range_km<remaining_range_km이며 둘 다
    명시해야 한다. 둘의 차이를 float(km)로 반환한다. 연비는 요구하지 않고 L를 역산하지
    않는다. 기존 위치 인자 current_fuel_l/reserve_fuel_l/avg_efficiency_kmpl도 지원하며
    이 경우 (현재L-안전L)*연비(km/L)를 쓴다. 두 방식의 연료량 혼용·누락·비유한 값은
    ValidationError, 계산 범위 초과는 ValueError다. km 입력에 선택 연비가 있어도 차감식은
    같다. 외부 호출·저장·재시도는 없다. 예:remaining_range_km=180,reserve_range_km=30은150,
    기존(15,3,12)는144다. 계기판 기반 입력도 추정치이며 실제 자동차 접근 거리와 비교한다.
    """
    conditions = RangeConditions(
        current_fuel_l=current_fuel_l,
        reserve_fuel_l=reserve_fuel_l,
        avg_efficiency_kmpl=avg_efficiency_kmpl,
        remaining_range_km=remaining_range_km,
        reserve_range_km=reserve_range_km,
    )
    if conditions.remaining_range_km is not None:
        distance = conditions.remaining_range_km - conditions.reserve_range_km
    else:
        distance = (
            conditions.current_fuel_l - conditions.reserve_fuel_l
        ) * conditions.avg_efficiency_kmpl
    if not isfinite(distance):
        raise ValueError("계산 가능한 숫자 범위를 초과했습니다.")
    return distance
