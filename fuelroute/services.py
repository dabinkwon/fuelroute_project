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


class RangeConditions(BaseModel):
    """km 기반 도달 범위 입력과 기존 L/연비 입력을 검증한다. 두 방식의 연료량 혼용은 거절한다."""

    model_config = ConfigDict(
        allow_inf_nan=False,
        extra="forbid",
        json_schema_extra={
            "anyOf": [
                {"required": ["remaining_range_km", "reserve_range_km"]},
                {"required": ["current_fuel_l", "reserve_fuel_l", "avg_efficiency_kmpl"]},
            ]
        },
    )
    remaining_range_km: float | None = Field(
        default=None, gt=0, description="계기판에 표시된 현재 주행 가능 거리(km), 목적지 거리 아님"
    )
    reserve_range_km: float | None = Field(
        default=None,
        ge=0,
        description="남겨둘 안전 여유 거리(km), 주행 가능 거리 미만; 임의 기본값 없음",
    )
    current_fuel_l: float | None = Field(
        default=None, gt=0, description="기존 L 입력의 현재 연료량"
    )
    reserve_fuel_l: float | None = Field(default=None, ge=0, description="기존 L 입력의 안전 잔량")
    avg_efficiency_kmpl: float | None = Field(
        default=None,
        gt=0,
        description="평균 연비(km/L); km 입력에서는 선택이며 도달거리 계산에 쓰지 않음",
    )

    @model_validator(mode="after")
    def validate_input_mode(self) -> "RangeConditions":
        """한 가지 방식의 입력이 완전하며 안전 여유가 전체 범위보다 작은지 검사한다.

        km 필드가 하나라도 있으면 두 km 값을 요구하고 L 연료량 혼용을 거절한다. 연비만
        선택 입력하는 것은 허용한다. km 필드가 전혀 없으면 기존 세 연료 값을 요구한다.
        잘못된 값·누락·같거나 큰 안전 여유는 ValueError/ValidationError이며 자동 변환,
        보정·외부 호출·계산·저장은 없다. 예:180km/30km는 유효,180km/180km는 거절한다.
        """
        if self.remaining_range_km is not None or self.reserve_range_km is not None:
            if self.remaining_range_km is None or self.reserve_range_km is None:
                raise ValueError("주행 가능 거리와 안전 여유 거리를 km로 모두 입력하세요.")
            if self.current_fuel_l is not None or self.reserve_fuel_l is not None:
                raise ValueError("주행 가능 거리(km)와 연료량(L) 중 한 방식만 입력하세요.")
            if self.reserve_range_km >= self.remaining_range_km:
                raise ValueError("안전 여유 거리는 주행 가능 거리보다 작아야 합니다.")
        else:
            FuelConditions(
                current_fuel_l=self.current_fuel_l,
                reserve_fuel_l=self.reserve_fuel_l,
                avg_efficiency_kmpl=self.avg_efficiency_kmpl,
            )
        return self


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
