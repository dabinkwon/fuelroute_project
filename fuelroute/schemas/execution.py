"""계층 사이에 전달하는 명시적인 입력·출력 데이터 계약: execution."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from fuelroute.core.contracts import ToolError, ToolResult
from fuelroute.schemas.driving import SafeRangeOutput
from fuelroute.schemas.planning import Action
from fuelroute.schemas.recommendation import CandidateEvaluation, RecommendationResult
from fuelroute.schemas.routes import DrivingRoute, RoutePoint
from fuelroute.schemas.stations import RestAreasOutput, StationCandidate, StationDetails


class ToolCallRecord(BaseModel):
    """실제 조회 시도 하나의 공개 입력·공통 응답. 캐시 재사용은 새 호출로 기록하지 않는다."""

    action: Action = Field(description="계획의 작업 ID")
    station_id: str | None = Field(description="후보별 호출의 Opinet ID, 기본/검색점 호출은 null")
    inputs: dict = Field(description="좌표·유종·ID 등 공개 입력; 인증/원본 경로/URL 제외")
    result: ToolResult[dict] = Field(description="검증·정규화한 ok/data/error/meta 공통 결과")


class StepExecution(BaseModel):
    """계획 단계의 실제 실행 여부. 이벤트 발행/저장과 분리된 요청 내 결과다."""

    action: Action
    status: Literal["completed", "skipped", "failed"]
    message: str


class CandidateRoutes(BaseModel):
    """한 후보의 실제 접근/경유 비용과 해당 조회의 식별자. 원본 기하는 포함하지 않는다."""

    approach: DrivingRoute | None = None
    via: DrivingRoute | None = None
    approach_request_id: str | None = None
    via_request_id: str | None = None


class WorkResults(BaseModel):
    """후속 판단·응답 노드가 사용할 타입 있는 작업별 출력. 필드명은 계획의 작업 ID다."""

    model_config = ConfigDict(extra="forbid")
    base_route: DrivingRoute | None = None
    safe_range: SafeRangeOutput | None = None
    search_points: list[RoutePoint] = Field(default_factory=list)
    search_candidates: list[StationCandidate] = Field(default_factory=list)
    station_details: list[StationDetails] = Field(default_factory=list)
    candidate_routes: dict[str, CandidateRoutes] = Field(default_factory=dict)
    evaluate_constraints: list[CandidateEvaluation] = Field(default_factory=list)
    rank_candidates: RecommendationResult | None = None
    rest_area_info: RestAreasOutput | None = None


class PlanExecution(BaseModel):
    """한 회차의 작업 출력·실패·호출 이력. ready 계획 자체나 최종 자연어 응답과 구분한다."""

    model_config = ConfigDict(extra="forbid")
    status: Literal[
        "recommended", "no_recommendation", "stopped", "needs_user_input", "not_required"
    ] = "no_recommendation"
    results: WorkResults = Field(default_factory=WorkResults)
    calls: list[ToolCallRecord] = Field(default_factory=list)
    steps: list[StepExecution] = Field(default_factory=list)
    error: ToolError | None = None
    warnings: list[str] = Field(default_factory=list)
    tool_call_counts: dict[str, int] = Field(default_factory=dict)
    cache_hits: int = 0
    unevaluated_station_ids: list[str] = Field(default_factory=list)
    replan_needed: bool = False
