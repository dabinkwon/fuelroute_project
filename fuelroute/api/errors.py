"""FuelRoute HTTP API의 정제된 오류 본문과 예외 처리기."""

from uuid import uuid4

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse


class ApiFailure(Exception):
    """라우터가 상태 코드와 공개 코드/메시지만 전달하는 HTTP 오류."""

    def __init__(self, status_code: int, code: str, message: str) -> None:
        """공개 HTTP 상태·오류 코드·한국어 메시지를 보관한다.

        status_code는 라우터가 선택한4xx/5xx, code/message는 비밀값·경로·원 예외가 없는
        고정값이어야 한다. 생성 시 응답·로그·DB·LLM 부작용은 없다.
        """
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message


async def api_failure_handler(_request: Request, failure: ApiFailure) -> JSONResponse:
    """ApiFailure를 request ID가 있는 일관된 JSON 오류 응답으로 변환한다.

    요청 본문·헤더·원 예외는 응답에 넣지 않는다. request_id는 오류 응답마다 새 UUID hex다.
    반환 status는 failure.status_code이고 본문은 error.code/message/request_id다. 파일·DB·
    외부 호출·LLM은 없으며 성공 응답에는 사용하지 않는다.
    """
    return JSONResponse(
        status_code=failure.status_code,
        content={
            "error": {
                "code": failure.code,
                "message": failure.message,
                "request_id": uuid4().hex,
            }
        },
    )


async def request_validation_handler(
    _request: Request, _failure: RequestValidationError
) -> JSONResponse:
    """FastAPI 경로·쿼리·본문 검증 오류를 원 입력값 없는 공통422 응답으로 변환한다.

    ValidationError의 상세 입력/위치는 외부 응답에 복사하지 않는다. 새 request_id와 고정
    INVALID_INPUT/message만 반환한다. 파일·DB·로그·LLM·외부 호출 부작용은 없다.
    """
    return JSONResponse(
        status_code=422,
        content={
            "error": {
                "code": "INVALID_INPUT",
                "message": "요청 입력 형식을 확인하세요.",
                "request_id": uuid4().hex,
            }
        },
    )
