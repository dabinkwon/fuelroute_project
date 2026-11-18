"""외부 호출의 성공과 실패를 같은 구조로 전달하는 공통 계약."""

from datetime import datetime, timezone
from typing import Generic, TypeVar

from pydantic import BaseModel, Field, model_validator

T = TypeVar("T")


class ToolError(BaseModel):
    """사용자에게 노출 가능한 오류 분류와 재시도·재계획 가능 여부."""

    code: str
    message: str
    retryable: bool = False
    replan_allowed: bool = False


class ToolMeta(BaseModel):
    """호출 식별·지연시간·완료 시각. 데이터 자체의 기준시각과는 별개다."""

    provider: str
    request_id: str
    elapsed_ms: float = Field(ge=0, allow_inf_nan=False)
    fetched_at: datetime


class ToolResult(BaseModel, Generic[T]):
    """성공이면 data, 실패이면 error를 갖는 상호 배타적 반환 계약."""

    ok: bool
    tool_name: str
    data: T | None
    error: ToolError | None
    meta: ToolMeta

    @model_validator(mode="after")
    def validate_outcome(self) -> "ToolResult[T]":
        """성공/실패 필드 조합과 시각의 시간대를 검증하고 자기 자신을 반환한다.

        성공은 data!=None/error=None, 실패는 반대다. 계약 위반은 ValueError이며
        네트워크·DB 부작용은 없다. 예: ok=False에 data를 넣는 것을 거절한다.
        """
        if self.ok != (self.error is None) or self.ok != (self.data is not None):
            raise ValueError("성공은 data만, 실패는 error만 포함해야 합니다.")
        if self.meta.fetched_at.utcoffset() is None:
            raise ValueError("fetched_at에는 시간대가 필요합니다.")
        return self


def utc_now() -> datetime:
    """호출 완료 기록용 현재 UTC 시각을 반환한다. 인자·저장 부작용은 없다.

    반환값은 시간대가 있는 datetime이며 가격 기준시각으로 사용하지 않는다.
    예: utc_now().isoformat()을 JSON 기록에 사용할 수 있다.
    """
    return datetime.now(timezone.utc)
