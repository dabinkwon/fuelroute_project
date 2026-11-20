"""F3014 실행 로그 목록·상세·삭제 FastAPI 라우터. 수정 Endpoint는 제공하지 않는다."""

from typing import Annotated

from fastapi import APIRouter, Query, Request, Response
from pydantic import ValidationError

from fuelroute.api.errors import ApiFailure
from fuelroute.core.config import Settings
from fuelroute.core.errors import ProbeFailure
from fuelroute.schemas.events import (
    EventExecutionKey,
    ExecutionLogDetail,
    ExecutionLogList,
)
from fuelroute.storage.logs import ExecutionLogStore, SQLiteExecutionLogStore

router = APIRouter(prefix="/api/logs", tags=["execution-logs"])


def log_store(request: Request) -> ExecutionLogStore:
    """앱에 주입한 로그 저장소 또는 설정 기본 SQLite 저장소를 반환한다.

    테스트/서버 조립에서 app.state.log_store를 지정하면 그대로 사용한다. 없으면 현재 Settings의
    database_path로 SQLiteExecutionLogStore를 만들되 이 함수에서 연결/파일은 열지 않는다.
    잘못된 설정은 CONFIG_ERROR ApiFailure다. HTTP·LLM·실제 DB I/O 부작용은 없다.
    """
    injected = getattr(request.app.state, "log_store", None)
    if injected is not None:
        return injected
    try:
        return SQLiteExecutionLogStore(Settings().database_path)
    except (ValidationError, ValueError) as exc:
        raise ApiFailure(500, "CONFIG_ERROR", "실행 로그 저장소 설정을 확인하세요.") from exc


def checked_execution_id(execution_id: str) -> str:
    """URL의 실행 ID를 영문·숫자·_·- 최대64자 계약으로 검증한다.

    유효하면 같은 문자열, 잘못되면422 INVALID_INPUT ApiFailure다. 경로/SQL 문자열을 조합하지
    않고 외부 호출·DB·LLM은 없다. 예: run-1은 허용하고 `../db`는 거절한다.
    """
    try:
        return EventExecutionKey(execution_id=execution_id).execution_id
    except ValidationError as exc:
        raise ApiFailure(422, "INVALID_INPUT", "실행 로그 ID 형식을 확인하세요.") from exc


def store_failure(failure: ProbeFailure) -> ApiFailure:
    """Repository 오류를 경로/원 예외 없는 HTTP 오류로 매핑한다.

    INVALID_INPUT은422, 그 밖의 LOG_STORE_ERROR는503이다. 입력 failure의 공개 code만 읽고
    원 message/cause는 응답에 복사하지 않는다. 반환은 ApiFailure이며 저장·LLM 부작용은 없다.
    """
    if failure.error.code == "INVALID_INPUT":
        return ApiFailure(422, "INVALID_INPUT", "로그 조회 조건을 확인하세요.")
    return ApiFailure(503, "LOG_STORE_ERROR", "실행 로그 저장소를 사용할 수 없습니다.")


@router.get("", response_model=ExecutionLogList)
async def list_logs(
    request: Request,
    limit: Annotated[int, Query(ge=1, le=100, description="최근 실행 최대 개수")] = 50,
) -> ExecutionLogList:
    """최근 실행 로그 세션을 시작시각 역순으로 최대 limit개 반환한다.

    limit는1~100이고 기본50이다. app.state 주입 또는 설정 SQLite 저장소를 사용한다. 반환에는
    실행 ID·상태·시작/종료시각·event_count만 있고 이벤트/원문은 없다. 빈 DB는 items=[]이다.
    Repository 실패는503이며 LLM·외부 API·로그 수정은 없다.
    """
    try:
        items = await log_store(request).list_sessions(limit)
    except ProbeFailure as exc:
        raise store_failure(exc) from exc
    return ExecutionLogList(items=items, count=len(items))


@router.get("/{execution_id}", response_model=ExecutionLogDetail)
async def get_log(execution_id: str, request: Request) -> ExecutionLogDetail:
    """한 실행 세션과 immutable 이벤트를 sequence 오름차순으로 반환한다.

    execution_id는 안전한 URL/SQLite 키여야 한다. 없는 ID는404 LOG_NOT_FOUND, 저장소 장애는
    503이다. 반환 이벤트는 정제된 payload와 Tool/request ID/소요시간을 포함한다. 이벤트를
    생성·수정하거나 LLM/API를 호출하지 않는다.
    """
    key = checked_execution_id(execution_id)
    store = log_store(request)
    try:
        detail = await store.load_detail(key)
        if detail is None:
            raise ApiFailure(404, "LOG_NOT_FOUND", "실행 로그를 찾을 수 없습니다.")
        return detail
    except ProbeFailure as exc:
        raise store_failure(exc) from exc


@router.delete("/{execution_id}", status_code=204)
async def delete_log(execution_id: str, request: Request) -> Response:
    """한 실행 세션과 소속 이벤트를 삭제하고 본문 없는204를 반환한다.

    execution_id는 검증 후 파라미터 바인딩된다. 없는 ID는404, 저장소 장애는503이다. SQLite
    foreign key로 이벤트는 CASCADE하지만 저장한 fuel_plans 행은 유지하고 session 참조만
    NULL로 바꾼다. 다른 실행·계획 내용은 삭제하지 않으며 수정 Endpoint/LLM/API 호출은 없다.
    """
    key = checked_execution_id(execution_id)
    try:
        deleted = await log_store(request).delete(key)
    except ProbeFailure as exc:
        raise store_failure(exc) from exc
    if not deleted:
        raise ApiFailure(404, "LOG_NOT_FOUND", "실행 로그를 찾을 수 없습니다.")
    return Response(status_code=204)
