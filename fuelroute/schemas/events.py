"""F3012 실행 이벤트의 세션·순서·종류·마스킹된 payload 계약."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

EventType = Literal[
    "STEP_STARTED",
    "STEP_COMPLETED",
    "TOOL_REQUESTED",
    "TOOL_SUCCEEDED",
    "TOOL_FAILED",
    "CONSTRAINT_EVALUATED",
    "REPLAN_TRIGGERED",
    "REPLAN_LIMIT_REACHED",
    "LLM_DECISION",
    "FINAL_RESPONSE_CREATED",
]


class ExecutionEvent(BaseModel):
    """한 Agent 실행에서 발행하는 순서가 있는 관찰 가능 이벤트.

    execution_id는 요청마다 새로 만드는 로그 세션 ID, sequence는 그 실행 안의 1부터
    증가하는 순서다. event_type/step은 고정 실행 단계, tool_name/request_id/elapsed_ms는
    Tool 이벤트에만 선택적으로 사용한다. payload에는 허용한 요약 필드만 넣고 키·URL·
    헤더·원문 응답·모델 내부 추론은 넣지 않는다. created_at은 시간대가 있는 발생 시각이다.
    이 모델은 검증만 하며 발행·저장·외부 호출 부작용은 없다.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    event_id: str = Field(min_length=1, max_length=64)
    execution_id: str = Field(min_length=1, max_length=64)
    sequence: int = Field(ge=1)
    event_type: EventType
    step: str = Field(min_length=1, max_length=80)
    tool_name: str | None = Field(default=None, max_length=120)
    request_id: str | None = Field(default=None, max_length=200)
    elapsed_ms: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    payload: dict[str, str | int | float | bool | None | list[str]] = Field(
        default_factory=dict,
        description="비밀값·원문 응답·경로 기하를 제외한 상태/개수/오류/판정 요약",
    )
    created_at: datetime

    @field_validator("created_at")
    @classmethod
    def aware_created_at(cls, value: datetime) -> datetime:
        """이벤트 시각에 UTC offset이 있는지 검사하고 그대로 반환한다.

        value는 발생 시각이며 naive datetime은 ValueError다. 시간대를 추측하거나 KST로
        바꾸지 않는다. 저장/화면 계층이 ISO 8601/KST 표시를 담당하며 부작용은 없다.
        """
        if value.utcoffset() is None:
            raise ValueError("created_at에는 시간대가 필요합니다.")
        return value


class EventExecutionKey(BaseModel):
    """사용자 입력과 분리한 실행 로그 ID의 URL/SQLite 키 계약.

    execution_id는 영문·숫자·밑줄·하이픈 1~64자다. 요청 원문이나 대화 session_id를
    자동 복사하지 않는다. 유효하지 않으면 ValidationError이며 저장·발행 부작용은 없다.
    예: `run_20261120`은 유효하고 공백·경로 구분자·한글은 거절한다.
    """

    execution_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,64}$")
