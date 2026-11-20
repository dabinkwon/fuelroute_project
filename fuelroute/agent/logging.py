"""F3013 이벤트를 정제해 SQLite Repository에 전달하는 LLM 없는 Logging Agent."""

from collections.abc import Callable

from fuelroute.agent.events import EventBus
from fuelroute.core.redaction import redact
from fuelroute.schemas.events import ExecutionEvent
from fuelroute.storage.logs import ExecutionLogStore


class LoggingAgent:
    """EventBus 구독자로서 이벤트를 다시 정제하고 순서대로 Repository에 저장한다."""

    def __init__(self, store: ExecutionLogStore, secrets: list[str] | None = None) -> None:
        """저장소와 설정에서 얻은 비밀값 복사본만 보관한다.

        store는 비동기 record 계약, secrets는 원문/URL 인코딩까지 제거할 실제 키 목록이다.
        None은 빈 목록이다. 생성 시 구독·DB 연결·파일·LLM·HTTP 부작용은 없다.
        """
        self.store = store
        self.secrets = list(secrets or [])

    async def __call__(self, event: ExecutionEvent) -> None:
        """한 이벤트를 재귀 정제·재검증해 Repository에 저장한다.

        event의 ID/sequence/종류/시각과 제한 payload를 유지하고 설정된 비밀 문자열과 인증
        필드를 `[REDACTED]`로 바꾼다. 반환은 없으며 LLM·Tool·API를 호출하거나 이벤트를
        요약/재작성하지 않는다. 저장 실패는 그대로 발생시키고 EventBus가 메인 실행과 격리한다.
        """
        cleaned = ExecutionEvent.model_validate(
            redact(event.model_dump(mode="python"), self.secrets)
        )
        await self.store.record(cleaned)

    def connect(self, bus: EventBus) -> Callable[[], None]:
        """현재 Logging Agent를 EventBus에 등록하고 멱등 해제 함수를 반환한다.

        같은 Agent를 여러 번 등록하면 각각 별도 전달이므로 일반적으로 실행 버스당 한 번
        호출한다. 등록 자체는 DB/LLM/HTTP를 사용하지 않고 실제 저장은 이후 이벤트부터다.
        """
        return bus.subscribe(self)
