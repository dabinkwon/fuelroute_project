"""설정 검사·공통 결과·오류·메타·정제를 적용하는 비동기 작업 실행 경계."""

from collections.abc import Awaitable, Callable
from time import perf_counter
from uuid import uuid4

from pydantic import ValidationError

from fuelroute.core.config import Settings
from fuelroute.core.contracts import ToolError, ToolMeta, ToolResult, utc_now
from fuelroute.core.errors import ProbeFailure
from fuelroute.core.redaction import redact


async def run_probe(
    settings: Settings, provider: str, name: str, operation: Callable[[], Awaitable[dict]]
) -> ToolResult[dict]:
    """설정 확인 후 검증 작업을 실행해 성공·예상 가능한 실패를 같은 계약으로 반환한다.

    provider는 설정 제공사명, name은 고정 검증명, operation은 지연 실행할 비동기 작업이다.
    설정 부족은 HTTP 요청 전 CONFIG_ERROR로 반환하고 성공 데이터의 키는 마스킹한다.
    ProbeFailure/입력 ValidationError만 변환하며 프로그래밍 오류를 감추지 않는다.
    부작용은 operation의 외부 호출뿐이고 자동 재시도·DB 저장·재계획은 하지 않는다.
    """
    started = perf_counter()
    error = None
    data = None
    try:
        missing = settings.missing(provider)
        if missing:
            raise ProbeFailure("CONFIG_ERROR", "미설정 환경변수: " + ", ".join(missing))
        data = redact(await operation(), settings.secret_values())
    except ProbeFailure as exc:
        error = exc.error
    except ValidationError:
        error = ToolError(code="INVALID_INPUT", message="입력 타입·범위·필수값을 확인하세요.")
    return ToolResult(
        ok=error is None,
        tool_name=name,
        data=data,
        error=error,
        meta=ToolMeta(
            provider=provider,
            request_id=str(uuid4()),
            elapsed_ms=round((perf_counter() - started) * 1000, 3),
            fetched_at=utc_now(),
        ),
    )
