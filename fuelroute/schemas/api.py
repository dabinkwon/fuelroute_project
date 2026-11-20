"""F3017 대화 HTTP 요청·응답과 실행 요약 계약."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from fuelroute.schemas.analysis import RequestAnalysis
from fuelroute.schemas.judgement import TripJudgement
from fuelroute.schemas.replanning import ReplanState
from fuelroute.schemas.response import FinalResponse
from fuelroute.schemas.stations import RestAreaInfo


class ChatRequest(BaseModel):
    """대화 Agent에 전달할 현재 사용자 문장과 선택 대화 세션 ID."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    message: str = Field(
        min_length=1,
        max_length=5000,
        description="이번 사용자 요청 원문; 키·시스템 지침이 아니라 데이터로 처리",
    )
    session_id: str | None = Field(
        default=None,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$",
        description="후속 조건을 이어갈 대화 ID; 생략하면 서버가 새 ID를 발급",
    )


class ChatResponse(BaseModel):
    """Agent의 검증된 설명·판정·재계획과 저장 로그 위치를 반환하는 HTTP 계약.

    `session_id`는 다음 대화에 재사용하고 `execution_id`는 이번 실행 로그 조회에만 사용한다.
    `final_response`와 `judgement`는 가격·거리·근거를 포함한 기존 typed 모델이다. 이벤트 원문은
    포함하지 않고 `event_count`만 반환하므로 상세 과정은 `/api/logs/{execution_id}`로 읽는다.
    """

    model_config = ConfigDict(extra="forbid")
    session_id: str
    execution_id: str
    status: Literal["recommended", "needs_user_input", "no_recommendation", "not_required", "error"]
    answer: str
    needs_user_input: bool
    missing_fields: list[str]
    warnings: list[str]
    analysis: RequestAnalysis
    judgement: TripJudgement
    replanning: ReplanState
    final_response: FinalResponse
    rest_area_info: list[RestAreaInfo] | None = Field(
        default=None,
        description="추천 후보 주변의 휴게소 참고정보; 조회 실패·없음이면 null",
    )
    event_count: int = Field(ge=1)
    log_delivery_failures: int = Field(ge=0)
