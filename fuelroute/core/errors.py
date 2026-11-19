"""예상 가능한 외부 조회 오류와 핵심 중단 코드. 상위 실행 정책·통신은 포함하지 않는다."""

from fuelroute.core.contracts import ToolError

FATAL_CODES = {"AUTH_FAILED", "CONFIG_ERROR", "RATE_LIMITED", "INVALID_INPUT"}


class ProbeFailure(Exception):
    """예상 가능한 외부 호출 오류만 공통 실패 응답으로 변환하기 위한 예외."""

    def __init__(
        self, code: str, message: str, *, retryable: bool = False, replan_allowed: bool = False
    ) -> None:
        """비밀값 없는 오류 코드·안내와 재시도/재계획 플래그를 보관한다.

        호출자는 제공사 원문 대신 내부 메시지를 전달한다. ToolError를 생성하며
        외부 호출·저장 부작용은 없다. 예: ProbeFailure('AUTH_FAILED', '인증 확인 필요').
        """
        super().__init__(message)
        self.error = ToolError(
            code=code, message=message, retryable=retryable, replan_allowed=replan_allowed
        )
