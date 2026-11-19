"""계층 사이에 전달하는 명시적인 입력·출력 데이터 계약: judgement."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from fuelroute.schemas.recommendation import CandidateEvaluation, RecommendationResult


class ConstraintCheck(BaseModel):
    """판정 한 항목의 비교값·단위·출처. 문자열 참조는 설명용이며 코드로 실행하지 않는다."""

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    code: str = Field(description="판정 항목 또는 실패 사유의 고정 코드")
    status: Literal["passed", "failed", "unverified", "not_requested"]
    actual: float | str | None = Field(default=None, description="확인한 값; 미확인은 null")
    limit: float | str | None = Field(default=None, description="명시된 상한/유종; 미지정은 null")
    unit: str | None = Field(default=None, description="km, min, s, KRW/L 또는 단위 없음")
    message: str = Field(description="짧은 비교 결과와 미확인 이유")
    sources: list[str] = Field(default_factory=list, description="실제 결과/입력의 논리적 출처")


class TimeConstraint(BaseModel):
    """명시된 도착 상한을 비교할 시간 예산. 날짜·정차시간의 임의 기본값은 없다."""

    status: Literal["not_requested", "ready", "needs_user_input"] = "not_requested"
    basis: Literal["none", "same_day_clock", "explicit_dates"] = "none"
    available_s: float | None = None
    stop_s: float | None = None
    departure_at: datetime | None = None
    deadline_at: datetime | None = None
    departure_clock_s: float | None = None
    missing_fields: list[str] = Field(default_factory=list)


class CandidateJudgement(BaseModel):
    """후보의 검증된 비용·판정 상태와 근거. 제외와 데이터 미확인을 구분한다."""

    station_id: str
    status: Literal["eligible", "excluded", "unverified"]
    evaluation: CandidateEvaluation
    checks: list[ConstraintCheck]
    range_margin_km: float | None = None
    estimated_fuel_to_station_l: float | None = None
    estimated_arrival_at: datetime | None = None
    estimated_arrival_clock: str | None = None
    arrival_day_offset: int | None = None
    tool_request_ids: list[str] = Field(default_factory=list)


class TripJudgement(BaseModel):
    """선택한 제약으로 재판정한 최종 후보 집합. 실제 미래 도착이나 가격을 보장하지 않는다."""

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    status: Literal[
        "recommended", "no_recommendation", "needs_user_input", "unavailable", "not_required"
    ]
    safe_range_km: float | None = None
    candidates: list[CandidateJudgement] = Field(default_factory=list)
    recommendation: RecommendationResult | None = None
    time_constraint: TimeConstraint = Field(default_factory=TimeConstraint)
    missing_fields: list[str] = Field(default_factory=list)
    questions: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    replan_needed: bool = False
    scope: Literal["evaluated_candidates_only"] = "evaluated_candidates_only"
