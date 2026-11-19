"""F3009 재계획의 결정·회차·종료 근거. 캐시·상세 경로 기하는 공개하지 않는다."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from fuelroute.schemas.execution import PlanExecution
from fuelroute.schemas.routes import RoutePoint


class ReplanDecision(BaseModel):
    """다음 한 회차의 작업 범위 또는 종료 안내. 기존 주행 조건은 변경하지 않는다."""

    model_config = ConfigDict(extra="forbid")
    action: Literal["stop", "evaluate_cached", "expand_search"]
    reason: str = Field(description="추천 발견·기존 후보 재사용·검색점 변경·상한 등 고정 사유")
    search_point: RoutePoint | None = Field(default=None, description="새 검색점 WGS84·누적 m")
    next_input: list[str] = Field(default_factory=list, description="종료 시 입력 확인 안내")

    @model_validator(mode="after")
    def validate_search_point(self) -> "ReplanDecision":
        """검색점 확장에만 정확히 한 점을 허용한다. 다른 분기에 점이 있거나 확장에 없으면

        ValueError다. 좌표 범위·누적m는 RoutePoint가 검증하며 안전 구간·기존 점 중복은
        실행기가 재확인한다. HTTP·LLM·변경·저장은 없다. 예:stop은search_point=None이다.
        """
        if (self.action == "expand_search") != (self.search_point is not None):
            raise ValueError("검색점 확장에만 새 검색점 하나가 필요합니다.")
        return self


class ReplanOutcome(BaseModel):
    """추가 회차 한 번의 실행 결과. 종료 결정에는 새 실행 결과가 없다."""

    decision: ReplanDecision
    execution: PlanExecution | None = None

    @model_validator(mode="after")
    def validate_execution(self) -> "ReplanOutcome":
        """종료에는execution=None, 추가 회차에는실행 결과가 있는지 검사한다.

        전자는HTTP0회, 후자는이미 실행된 한 회차다. 모순은ValueError이며 자동 실행·
        복구·외부 호출·저장은 없다. 예:evaluate_cached는PlanExecution이 필수다.
        """
        if (self.decision.action == "stop") != (self.execution is None):
            raise ValueError("추가 회차에만 실행 결과가 필요합니다.")
        return self


class ReplanRound(BaseModel):
    """한 회차의 실제 검색점·평가 ID·판정 결과와 호출 증가량. 최종 응답 근거용이다."""

    round_index: int = Field(ge=0, le=2)
    trigger: Literal["initial", "evaluate_cached", "expand_search"]
    search_points: list[RoutePoint]
    evaluated_station_ids: list[str]
    eligible_count: int = Field(ge=0)
    tool_calls_added: int = Field(ge=0, description="논리 조회 시도 증가량, 휴게소 페이지 수 아님")


class ReplanState(BaseModel):
    """요청별 재계획 횟수·회차·종료 사유. 최초 회차는 count에 포함하지 않는다."""

    model_config = ConfigDict(extra="forbid")
    replan_count: int = Field(default=0, ge=0, le=2)
    rounds: list[ReplanRound] = Field(default_factory=list, max_length=3)
    decision: ReplanDecision | None = None
    stop_reason: str | None = None
    next_input: list[str] = Field(default_factory=list)
