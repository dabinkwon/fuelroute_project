"""FastAPI 서버 진입점. F3017 대화·계획 CRUD와 실행 로그 API를 제공한다."""

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError

from fuelroute.api.chat import router as chat_router
from fuelroute.api.errors import ApiFailure, api_failure_handler, request_validation_handler
from fuelroute.api.logs import router as logs_router
from fuelroute.api.plans import router as plans_router

app = FastAPI(title="FuelRoute Agent", version="0.1.0")
app.add_exception_handler(ApiFailure, api_failure_handler)
app.add_exception_handler(RequestValidationError, request_validation_handler)
app.include_router(chat_router)
app.include_router(plans_router)
app.include_router(logs_router)


@app.get("/health")
def health() -> dict[str, str]:
    """프로세스 실행 여부를 확인하는 정적인 상태 응답을 반환한다.

    인자·API 키·외부 호출·DB 의존성이 없다. 예: GET /health → status=ok.
    외부 API 인증이나 추천 기능 준비 여부를 보장하는 응답은 아니다.
    """
    return {"status": "ok", "stage": "api-verification"}
