"""응답·공개 요약의 인증 필드와 설정된 비밀값을 재귀 정제한다."""

from typing import Any
from urllib.parse import quote, quote_plus


def redact(value: Any, secrets: list[str]) -> Any:
    """JSON 값의 인증 필드와 설정된 키(원문·URL 인코딩)를 재귀적으로 마스킹한다.

    입력은 응답 JSON 또는 공개할 요약이다. dict/list/str를 복사해 정제하며 다른
    스칼라는 그대로 반환한다. 네트워크·저장은 없다. 예: {'certkey': 'x'} → [REDACTED].
    키 자체가 포함된 필드명도 정제하며, 외부 예외 문자열은 애초에 이 함수에 넘기지 않는다.
    """
    sensitive = {"certkey", "servicekey", "api_key", "authorization", "access_token"}
    if isinstance(value, dict):
        return {
            redact(str(key), secrets): (
                "[REDACTED]" if str(key).lower() in sensitive else redact(item, secrets)
            )
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact(item, secrets) for item in value]
    if isinstance(value, str):
        for secret in secrets:
            for token in {secret, quote(secret, safe=""), quote_plus(secret)}:
                if token:
                    value = value.replace(token, "[REDACTED]")
        return value
    return value
