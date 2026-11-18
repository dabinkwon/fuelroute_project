"""LLM이나 외부 API 없이 검증할 수 있는 주행 조건과 계산."""

from math import isfinite

from pydantic import BaseModel, ConfigDict, Field, model_validator


class FuelConditions(BaseModel):
    """사용자가 입력한 연료(L)·연비(km/L)를 검증하는 안전거리 입력."""

    model_config = ConfigDict(allow_inf_nan=False, extra="forbid")
    current_fuel_l: float = Field(gt=0, description="현재 연료량(L), 0보다 커야 함")
    reserve_fuel_l: float = Field(ge=0, description="안전 잔량(L), 현재 연료량 미만")
    avg_efficiency_kmpl: float = Field(gt=0, description="평균 연비(km/L), 0보다 커야 함")

    @model_validator(mode="after")
    def validate_reserve(self) -> "FuelConditions":
        """현재 연료보다 안전 잔량이 적은지 확인하고 조건을 반환한다.

        같거나 크면 ValueError로 거절한다. 입력값 보정·외부 호출은 없다.
        예: 현재 3L/잔량 3L는 안전거리 0으로 보정하지 않고 거절한다.
        """
        if self.reserve_fuel_l >= self.current_fuel_l:
            raise ValueError("안전 잔량은 현재 연료량보다 적어야 합니다.")
        return self


def calculate_safe_range(
    current_fuel_l: float, reserve_fuel_l: float, avg_efficiency_kmpl: float
) -> float:
    """사용자 입력으로 안전 잔량을 남기는 최대 추정 주행거리(km)를 계산한다.

    현재 연료·안전 잔량은 L, 평균 연비는 km/L이며 모두 필수다. 현재 연료와
    연비는 양수, 잔량은 0 이상/현재 연료 미만이어야 하며 NaN·무한대는 거절한다.
    (현재 연료-안전 잔량)*평균 연비를 float(km)로 반환한다. 예: (15, 3, 12) → 144.
    잘못된 입력은 Pydantic ValidationError, 계산 범위 초과는 ValueError다.
    외부 호출·저장·재시도는 없다.
    이 수치는 입력 기반 추정치로 후보까지의 실제 자동차 경로 거리와 비교해야 한다.
    """
    conditions = FuelConditions(
        current_fuel_l=current_fuel_l,
        reserve_fuel_l=reserve_fuel_l,
        avg_efficiency_kmpl=avg_efficiency_kmpl,
    )
    distance = (
        conditions.current_fuel_l - conditions.reserve_fuel_l
    ) * conditions.avg_efficiency_kmpl
    if not isfinite(distance):
        raise ValueError("계산 가능한 숫자 범위를 초과했습니다.")
    return distance
