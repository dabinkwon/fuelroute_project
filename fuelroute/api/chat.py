"""F3017 전체 Agent 실행·대화 Memory·실행 로그를 연결하는 FastAPI 라우터."""

from collections.abc import Awaitable, Callable
from uuid import uuid4

from fastapi import APIRouter, Request
from pydantic import ValidationError

from fuelroute.agent.events import EventBus
from fuelroute.agent.graph import execute_request
from fuelroute.agent.logging import LoggingAgent
from fuelroute.api.errors import ApiFailure
from fuelroute.core.config import Settings
from fuelroute.core.contracts import ToolResult
from fuelroute.schemas.analysis import RequestAnalysis
from fuelroute.schemas.api import ChatRequest, ChatResponse
from fuelroute.schemas.judgement import TripJudgement
from fuelroute.schemas.memory import SessionKey
from fuelroute.schemas.replanning import ReplanState
from fuelroute.schemas.response import FinalResponse
from fuelroute.storage.conversations import ConversationStore, SQLiteConversationStore
from fuelroute.storage.logs import ExecutionLogStore, SQLiteExecutionLogStore

router = APIRouter(prefix="/api/chat", tags=["chat-agent"])

ChatExecutor = Callable[..., Awaitable[ToolResult[dict]]]


def api_settings(request: Request) -> Settings:
    """앱에 주입한 설정 또는 `.env`/환경변수 기반 Settings를 반환한다.

    테스트에서 `app.state.settings`가 있으면 재사용한다. 기본 생성은 설정을 검증하지만 DB·
    네트워크·LLM을 호출하지 않는다. 잘못된 설정은 비밀값 없는 CONFIG_ERROR 500이다.
    """
    injected = getattr(request.app.state, "settings", None)
    if injected is not None:
        return injected
    try:
        return Settings()
    except ValidationError as exc:
        raise ApiFailure(500, "CONFIG_ERROR", "서버 설정을 확인하세요.") from exc


def conversation_store(request: Request, settings: Settings) -> ConversationStore:
    """주입 저장소 또는 설정 DB의 대화 Memory 저장소 객체를 반환한다.

    이 함수는 연결/파일을 열지 않는다. 잘못된 경로는 CONFIG_ERROR 500이며 외부 API·LLM
    부작용은 없다. 대화 조건은 실행 로그/계획과 별도 테이블에 저장된다.
    """
    injected = getattr(request.app.state, "memory_store", None)
    if injected is not None:
        return injected
    try:
        return SQLiteConversationStore(settings.database_path)
    except ValueError as exc:
        raise ApiFailure(500, "CONFIG_ERROR", "대화 저장소 설정을 확인하세요.") from exc


def execution_log_store(request: Request, settings: Settings) -> ExecutionLogStore:
    """주입 저장소 또는 설정 DB의 실행 로그 저장소 객체를 반환한다.

    기존 `/api/logs`와 같은 `app.state.log_store`를 우선 사용한다. 생성 단계에는 DB I/O가
    없고 경로 오류는 CONFIG_ERROR 500이다. LLM·외부 API를 호출하지 않는다.
    """
    injected = getattr(request.app.state, "log_store", None)
    if injected is not None:
        return injected
    try:
        return SQLiteExecutionLogStore(settings.database_path)
    except ValueError as exc:
        raise ApiFailure(500, "CONFIG_ERROR", "실행 로그 저장소 설정을 확인하세요.") from exc


def agent_failure(result: ToolResult[dict]) -> ApiFailure:
    """Agent 공통 실패를 공개 HTTP 상태·메시지로 변환한다.

    INVALID_INPUT은422, Memory 동시 수정은409, 설정/인증/한도/통신/제공사 실패는503이다.
    원 오류 메시지·키·URL·내부 예외는 응답에 복사하지 않는다. 성공 결과에는 호출하지
    않으며 저장·LLM 부작용은 없다.
    """
    code = result.error.code if result.error is not None else "AGENT_ERROR"
    if code == "INVALID_INPUT":
        return ApiFailure(422, code, "대화 입력을 확인하세요.")
    if code == "MEMORY_CONFLICT":
        return ApiFailure(409, code, "대화 조건이 변경됐습니다. 다시 요청하세요.")
    return ApiFailure(503, code, "요청을 완료할 수 없습니다. 설정과 외부 서비스 상태를 확인하세요.")


def chat_response(result: ToolResult[dict], session_id: str, execution_id: str) -> ChatResponse:
    """Agent 성공 data를 UI용 typed HTTP 응답으로 축소·재검증한다.

    분석·판정·재계획·최종 응답과 이벤트 건수만 선택한다. 내부 계획, Tool 입력/응답 원문,
    캐시, 모델 추론은 노출하지 않는다. 이벤트 실행 ID가 요청 ID와 다르거나 필수 필드가
    손상되면 ValidationError/ValueError이며 저장·외부 호출은 없다.
    """
    if not result.ok or result.data is None:
        raise ValueError("성공한 Agent 결과가 필요합니다.")
    data = result.data
    final = FinalResponse.model_validate(data["final_response"])
    analysis = RequestAnalysis.model_validate(data["analysis"])
    judgement = TripJudgement.model_validate(data["judgement"])
    replanning = ReplanState.model_validate(data.get("replanning") or {})
    event_stream = data["event_stream"]
    if event_stream["execution_id"] != execution_id:
        raise ValueError("실행 로그 ID가 응답과 다릅니다.")
    return ChatResponse(
        session_id=session_id,
        execution_id=execution_id,
        status=final.status,
        answer=final.answer,
        needs_user_input=final.status == "needs_user_input",
        missing_fields=analysis.missing_fields,
        warnings=final.warnings,
        analysis=analysis,
        judgement=judgement,
        replanning=replanning,
        final_response=final,
        event_count=event_stream["event_count"],
        log_delivery_failures=event_stream["delivery_failures"],
    )


@router.post("", response_model=ChatResponse)
async def run_chat(command: ChatRequest, request: Request) -> ChatResponse:
    """한 대화 요청의 Agent·Memory·이벤트 로그를 연결해 검증된 응답을 반환한다.

    session_id를 생략하면 새 대화 ID, 매 요청마다 별도 execution_id를 UUID hex로 만든다.
    Logging Agent를 먼저 연결하고 전체 `execute_request`를 한 번 실행한다. 테스트에서는
    `app.state.chat_executor`만 대체할 수 있지만 동일 event_bus/execution_id 계약을 받는다.
    성공은 typed 응답, 입력/동시성/외부 실패는 공통4xx/503이다. 로그 저장 실패는 기존 이벤트
    버스 정책대로 메인 응답을 막지 않고 `log_delivery_failures`로 알린다.
    """
    settings = api_settings(request)
    generated_session = command.session_id or uuid4().hex
    try:
        session_id = SessionKey(session_id=generated_session).session_id
    except ValidationError as exc:
        raise ApiFailure(422, "INVALID_INPUT", "대화 세션 ID 형식을 확인하세요.") from exc
    execution_id = uuid4().hex
    bus = EventBus()
    LoggingAgent(execution_log_store(request, settings), settings.secret_values()).connect(bus)
    runner: ChatExecutor = getattr(request.app.state, "chat_executor", execute_request)
    result = await runner(
        command.message,
        settings,
        session_id=session_id,
        memory_store=conversation_store(request, settings),
        event_bus=bus,
        execution_id=execution_id,
    )
    if not result.ok:
        raise agent_failure(result)
    try:
        return chat_response(result, session_id, execution_id)
    except (KeyError, TypeError, ValueError, ValidationError) as exc:
        raise ApiFailure(
            503, "AGENT_RESPONSE_ERROR", "Agent 응답 형식을 확인할 수 없습니다."
        ) from exc
