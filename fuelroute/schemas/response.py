"""F3011 검증 사실·출처·문단 구성·최종 사용자 응답의 계약."""

from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from fuelroute.schemas.analysis import RequestAnalysis
from fuelroute.schemas.execution import PlanExecution
from fuelroute.schemas.judgement import TripJudgement
from fuelroute.schemas.memory import ConversationMemory
from fuelroute.schemas.planning import WorkPlan
from fuelroute.schemas.replanning import ReplanState


class ResponseContext(BaseModel):
    """최종 판정·보조 연결까지 마친 현재 요청. 모델에는 이 객체 전체를 전달하지 않는다."""

    analysis: RequestAnalysis
    plan: WorkPlan
    execution: PlanExecution
    judgement: TripJudgement
    replanning: ReplanState = Field(default_factory=ReplanState)
    memory: ConversationMemory | None = None


class EvidenceSource(BaseModel):
    """사용자 설명의 사실을 입력/계산/실제 Tool 호출로 추적하는 출처."""

    kind: Literal["input", "calculation", "tool_result", "normalized_result"]
    fields: list[str]
    provider: str | None = None
    tool_name: str | None = None
    request_id: str | None = None
    fetched_at: datetime | None = None


class ResponseFact(BaseModel):
    """일반 코드가 검증 결과로 생성한 완결 문장 하나. 모델이 숫자·본문을 바꾸지 못한다."""

    model_config = ConfigDict(extra="forbid")
    fact_id: str = Field(description="현재 응답 안의 유일한 사실 ID")
    category: Literal[
        "result",
        "input",
        "safety",
        "cost",
        "ranking",
        "exclusion",
        "rest_area",
        "scope",
        "warning",
        "source",
        "question",
        "conversation",
    ]
    text: str = Field(min_length=1, max_length=4000, description="확인한 데이터로 만든 한국어 문장")
    sources: list[EvidenceSource] = Field(default_factory=list)


class ResponseFacts(BaseModel):
    """LLM 문단 구성에 사용할 제한된 사실 집합. 출처 메타는 모델 프롬프트에서 제외한다."""

    model_config = ConfigDict(extra="forbid")
    status: Literal["recommended", "needs_user_input", "no_recommendation", "not_required", "error"]
    title: str
    facts: list[ResponseFact] = Field(min_length=1, max_length=64)
    questions: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    recommended_station_id: str | None = None
    scope: Literal["evaluated_candidates_only", "not_applicable"] = "not_applicable"


class ExplanationParagraph(BaseModel):
    """함께 읽을 사실 ID만 고르는 한 문단. 자유 텍스트·새 계산·Tool 실행은 허용하지 않는다."""

    model_config = ConfigDict(extra="forbid")
    fact_ids: list[str] = Field(
        min_length=1,
        max_length=8,
        description="입력 facts에 있는 ID만 사용. 모든 ID는 전체 문단에서 정확히 한 번 포함",
    )


class ExplanationOutline(BaseModel):
    """GMS가 구성하는 설명 순서. 본문은 ID에 연결된 코드 생성 문장으로만 렌더링한다."""

    model_config = ConfigDict(extra="forbid")
    paragraphs: list[ExplanationParagraph] = Field(
        min_length=1,
        max_length=64,
        description="추천/도달/비용/제외/보조/주의를 읽기 좋게 묶은 문단. 사실 누락·중복 금지",
    )


class FinalResponse(BaseModel):
    """사용자 답변과 근거. GMS 실패 시에도 확인된 사실의 응답을 유지한다."""

    model_config = ConfigDict(extra="forbid")
    status: Literal["recommended", "needs_user_input", "no_recommendation", "not_required", "error"]
    answer: str = Field(
        min_length=1, description="사용자용 한국어 답변. 숫자/시설은 검증 사실만 사용"
    )
    evidence: list[ResponseFact]
    questions: list[str]
    warnings: list[str]
    recommended_station_id: str | None
    scope: Literal["evaluated_candidates_only", "not_applicable"]
    source_request_ids: list[str]
    mode: Literal["model", "template", "fallback"]
    generation_error_code: str | None = None


Narrator = Callable[[ResponseFacts], Awaitable[ExplanationOutline]]
