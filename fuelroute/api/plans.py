"""F3017 주유 계획 저장·목록·상세·수정·삭제 FastAPI 라우터."""

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Query, Request, Response
from pydantic import ValidationError

from fuelroute.api.errors import ApiFailure
from fuelroute.core.config import Settings
from fuelroute.core.errors import ProbeFailure
from fuelroute.schemas.plans import (
    PlanCreate,
    PlanKey,
    PlanList,
    PlanUpdate,
    PlanWriteResult,
    StoredPlan,
)
from fuelroute.storage.plans import PlanStore, SQLitePlanStore

router = APIRouter(prefix="/api/plans", tags=["fuel-plans"])


def plan_store(request: Request) -> PlanStore:
    """앱에 주입한 계획 저장소 또는 설정 기본 SQLite 저장소를 반환한다.

    테스트/서버 조립에서 `app.state.plan_store`가 있으면 재사용한다. 없으면 Settings의
    database_path로 저장소 객체만 만들며 이 함수에서 파일/연결을 열지 않는다. 설정 오류는
    경로를 노출하지 않는 CONFIG_ERROR 500이다. LLM·외부 API 부작용은 없다.
    """
    injected = getattr(request.app.state, "plan_store", None)
    if injected is not None:
        return injected
    try:
        return SQLitePlanStore(Settings().database_path)
    except (ValidationError, ValueError) as exc:
        raise ApiFailure(500, "CONFIG_ERROR", "계획 저장소 설정을 확인하세요.") from exc


def checked_plan_id(plan_id: str) -> str:
    """URL 계획 ID를 영문·숫자·_·- 최대64자 계약으로 검증한다.

    유효하면 같은 문자열, 잘못되면 422 INVALID_INPUT이다. SQL/경로 문자열을 조합하거나
    저장·외부 호출·LLM을 수행하지 않는다.
    """
    try:
        return PlanKey(plan_id=plan_id).plan_id
    except ValidationError as exc:
        raise ApiFailure(422, "INVALID_INPUT", "계획 ID 형식을 확인하세요.") from exc


def plan_failure(failure: ProbeFailure) -> ApiFailure:
    """계획 Repository 공개 오류 코드를 정제된 HTTP 상태와 메시지로 매핑한다.

    없는 계획은404, 동시 수정 충돌은409, 입력 오류는422, 저장소 장애는503이다. 원 예외·
    DB 경로·입력 스냅샷은 응답에 복사하지 않는다. 반환만 하며 저장/LLM 부작용은 없다.
    """
    mapping = {
        "PLAN_NOT_FOUND": (404, "PLAN_NOT_FOUND", "계획을 찾을 수 없습니다."),
        "PLAN_CONFLICT": (409, "PLAN_CONFLICT", "계획이 변경됐습니다. 최신 값을 확인하세요."),
        "INVALID_INPUT": (422, "INVALID_INPUT", "계획 수정 값을 확인하세요."),
    }
    status, code, message = mapping.get(
        failure.error.code,
        (503, "PLAN_STORE_ERROR", "계획 저장소를 사용할 수 없습니다."),
    )
    return ApiFailure(status, code, message)


@router.post("", response_model=PlanWriteResult, status_code=201)
async def create_plan(command: PlanCreate, request: Request) -> PlanWriteResult:
    """검증된 추천 스냅샷을 저장하고 비차단 시간 충돌을 포함해 201로 반환한다.

    command는 F3015의 전체 조건·추천·근거 계약이다. 겹치는 계획도 저장한다. 시각이 없으면
    `not_comparable`이며 생성하지 않는다. 저장소 실패는503이다. Agent/외부 API를 다시
    실행하지 않고 전달된 검증 결과만 저장한다.
    """
    try:
        return await plan_store(request).create(command)
    except ProbeFailure as exc:
        raise plan_failure(exc) from exc


@router.get("", response_model=PlanList)
async def list_plans(
    request: Request,
    trip_date: Annotated[date | None, Query(description="선택 계획 날짜 YYYY-MM-DD")] = None,
) -> PlanList:
    """전체 또는 선택 날짜의 저장 계획을 정렬된 목록과 건수로 반환한다.

    날짜가 없으면 전체, 결과가 없으면 `items=[]`이다. 저장 내용을 변경하거나 최신 가격을
    조회하지 않는다. 잘못된 날짜는 공통422, 저장소 장애는503이다.
    """
    try:
        items = await plan_store(request).list(trip_date)
    except ProbeFailure as exc:
        raise plan_failure(exc) from exc
    return PlanList(items=items, count=len(items))


@router.get("/{plan_id}", response_model=StoredPlan)
async def get_plan(plan_id: str, request: Request) -> StoredPlan:
    """저장 계획 한 건의 입력·추천·근거·검증 상태를 반환한다.

    안전한 ID만 파라미터 바인딩한다. 없는 ID는404, 형식 오류는422, 저장소 장애는503이다.
    조회는 추천/가격을 갱신하지 않으며 외부 API·LLM을 호출하지 않는다.
    """
    key = checked_plan_id(plan_id)
    try:
        plan = await plan_store(request).get(key)
    except ProbeFailure as exc:
        raise plan_failure(exc) from exc
    if plan is None:
        raise ApiFailure(404, "PLAN_NOT_FOUND", "계획을 찾을 수 없습니다.")
    return plan


@router.patch("/{plan_id}", response_model=PlanWriteResult)
async def update_plan(plan_id: str, command: PlanUpdate, request: Request) -> PlanWriteResult:
    """예상 revision의 날짜·조건·계획 시각·메모를 수정하고 충돌/재검증 상태를 반환한다.

    날짜·추천 조건 변경 시 저장 추천은 `revalidation_required`가 되며 자동 재추천하지 않는다.
    일정 시각·메모만 바꾸면 기존 추천 상태를 유지한다. 오래된 revision은409, 없는 ID는404,
    무효/동일값은422, 저장소 장애는503이다.
    """
    key = checked_plan_id(plan_id)
    try:
        return await plan_store(request).update(key, command)
    except ProbeFailure as exc:
        raise plan_failure(exc) from exc


@router.delete("/{plan_id}", status_code=204)
async def delete_plan(plan_id: str, request: Request) -> Response:
    """선택 계획 한 건만 삭제하고 본문 없는 204를 반환한다.

    없는 ID는404, 잘못된 ID는422, 저장소 장애는503이다. 연결 로그·대화·다른 계획은
    삭제하지 않으며 복구/외부 API/LLM 호출은 없다.
    """
    key = checked_plan_id(plan_id)
    try:
        deleted = await plan_store(request).delete(key)
    except ProbeFailure as exc:
        raise plan_failure(exc) from exc
    if not deleted:
        raise ApiFailure(404, "PLAN_NOT_FOUND", "계획을 찾을 수 없습니다.")
    return Response(status_code=204)
