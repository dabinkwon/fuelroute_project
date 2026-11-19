"""GMS 구조화 SDK 호출·공통 오류 변환·HTTP 클라이언트 종료를 담당한다."""

from typing import TypeVar

from langchain_core.exceptions import OutputParserException
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from openai import (
    APIConnectionError,
    APIError,
    APIStatusError,
    APITimeoutError,
    DefaultAsyncHttpxClient,
    DefaultHttpxClient,
)
from pydantic import BaseModel, ValidationError

from fuelroute.core.config import Settings
from fuelroute.core.errors import ProbeFailure

Output = TypeVar("Output", bound=BaseModel)


async def _structured_response(
    schema: type[Output],
    messages: list[SystemMessage | HumanMessage],
    settings: Settings,
    stage: str,
) -> Output:
    """분석/계획에서 공통으로 쓰는 GMS 구조화 호출과 오류 정제·클라이언트 종료를 처리한다.

    schema는 반환할 Pydantic 모델, messages는 분리한 시스템/사용자 메시지, stage는 내부의
    고정 오류 표시명이다. 설정 누락을 먼저 검사하고 설정된 GMS로 1회·재시도0회 호출한다.
    잘못된 구조·인증·한도·타임아웃은 ProbeFailure이며 원본 예외는 결과에 싣지 않는다.
    생성 실패를 포함해 독립 동기/비동기 클라이언트를 닫는다. DB·Tool 실행·파일 쓰기는 없다.
    예: PlanProposal을 넘기면 작업 ID/이유 모델을 얻으며 계획의 의미 검증은 호출자 책임이다.
    """
    missing = settings.missing("gms")
    if missing:
        raise ProbeFailure("CONFIG_ERROR", "미설정 환경변수: " + ", ".join(missing))
    sync_http = DefaultHttpxClient(timeout=settings.http_timeout_seconds)
    async_http = DefaultAsyncHttpxClient(timeout=settings.http_timeout_seconds)
    model = None
    try:
        model = ChatOpenAI(
            api_key=settings.gms_api_key.get_secret_value(),
            base_url=settings.gms_base_url,
            model=settings.gms_model,
            timeout=settings.http_timeout_seconds,
            max_retries=0,
            use_responses_api=False,
            http_client=sync_http,
            http_async_client=async_http,
        )
        extractor = model.with_structured_output(schema, method="json_schema")
        return await extractor.ainvoke(messages)
    except APITimeoutError as exc:
        raise ProbeFailure(
            "TIMEOUT", f"GMS {stage} 시간이 초과되었습니다.", retryable=True
        ) from exc
    except APIStatusError as exc:
        if exc.status_code in (401, 403):
            raise ProbeFailure("AUTH_FAILED", "GMS 인증키·모델 권한을 확인하세요.") from exc
        if exc.status_code == 429:
            raise ProbeFailure(
                "RATE_LIMITED", "GMS 호출 한도를 확인하세요.", retryable=True
            ) from exc
        raise ProbeFailure(
            "UPSTREAM_ERROR", f"GMS {stage}에 실패했습니다.", retryable=exc.status_code >= 500
        ) from exc
    except APIConnectionError as exc:
        raise ProbeFailure("UPSTREAM_ERROR", "GMS에 연결할 수 없습니다.", retryable=True) from exc
    except (APIError, ValidationError, OutputParserException) as exc:
        raise ProbeFailure("INVALID_RESPONSE", f"GMS {stage}의 출력 형식을 확인하세요.") from exc
    finally:
        if model is not None:
            await model.root_async_client.close()
            model.root_client.close()
        await async_http.aclose()
        sync_http.close()
