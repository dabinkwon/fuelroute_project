"""GMS 검증 로직의 Tool 왕복·조건 추출 검증. 합성 응답이며 실제 모델 검증은 아니다."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import httpx
import pytest
from langchain_core.messages import AIMessage, ToolMessage
from langchain_openai import ChatOpenAI
from openai import AuthenticationError

from fuelroute.cli.gms_probe import ExtractedConditions, probe_gms
from fuelroute.core.config import Settings


@pytest.mark.parametrize("mode", ["text", "conditions", "tool"])
def test_gms_modes_pass_gms_configuration_and_close_clients(monkeypatch, mode: str) -> None:
    """모드별 고정 입력·로컬 계산·GMS 전용 설정·클라이언트 정리를 모의 검증한다."""
    selected = []
    seen_messages = []

    async def invoke(messages: object) -> AIMessage:
        """최종 응답을 반환하고 Tool 모드에서는 실제 Tool 결과 메시지를 확인한다."""
        seen_messages.append(messages)
        if isinstance(messages, list):
            assert isinstance(messages[-1], ToolMessage)
            assert messages[-1].content == "144.0 km"
        return AIMessage(content="연결 확인. 추정 안전거리는 144km입니다.")

    async def choose(messages: list) -> AIMessage:
        """허용한 단일 Tool의 인자를 고정 응답으로 제공한다. 계산은 Python이 수행한다."""
        return AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "calculate_safe_range",
                    "args": {"current_fuel_l": 15, "reserve_fuel_l": 3, "avg_efficiency_kmpl": 12},
                    "id": "call-test",
                }
            ],
        )

    async def extract(message: str) -> ExtractedConditions:
        """완전 조건과 누락 조건을 구분한 합성 구조화 응답을 반환한다."""
        complete = "15L" in message
        return ExtractedConditions(
            current_fuel_l=15 if complete else None,
            reserve_fuel_l=3 if complete else None,
            avg_efficiency_kmpl=12 if complete else None,
        )

    def bind(tools: list, tool_choice: str) -> SimpleNamespace:
        """허용 Tool 이름과 필드 설명이 실제 바인딩 인자로 전달되는지 검사한다."""
        assert len(tools) == 1 and tools[0].name == tool_choice == "calculate_safe_range"
        assert "km/L" in tools[0].description
        return SimpleNamespace(ainvoke=choose)

    fake = SimpleNamespace(
        ainvoke=invoke,
        bind_tools=bind,
        with_structured_output=lambda schema, method: SimpleNamespace(ainvoke=extract),
        root_async_client=SimpleNamespace(close=AsyncMock()),
        root_client=SimpleNamespace(close=Mock()),
    )

    def factory(**kwargs) -> SimpleNamespace:
        """GMS 설정으로만 클라이언트를 구성하는지 검사하고 모의 클라이언트를 반환한다."""
        selected.append(kwargs)
        return fake

    monkeypatch.setattr("fuelroute.cli.gms_probe.ChatOpenAI", factory)
    settings = Settings(
        _env_file=None,
        gms_api_key="test-gms-key",
        gms_base_url="https://gms.ssafy.io/console-provided-path/",
        gms_model="account-test-model",
    )
    result = asyncio.run(probe_gms(settings, mode))
    assert result.ok
    assert selected[0]["base_url"] == settings.gms_base_url
    assert selected[0]["max_retries"] == 0
    assert selected[0]["use_responses_api"] is False
    assert "test-gms-key" not in result.model_dump_json()
    fake.root_async_client.close.assert_awaited_once()
    fake.root_client.close.assert_called_once()
    if mode == "tool":
        assert result.data["safe_range_km"] == 144


def test_gms_auth_failure_is_sanitized(monkeypatch) -> None:
    """SDK 인증 예외의 원문 URL/키를 출력하지 않고 클라이언트를 닫는지 검증한다."""

    async def invoke(message: str) -> AIMessage:
        """키가 포함된 합성 SDK 인증 예외를 발생시킨다. 실제 통신은 없다."""
        raise AuthenticationError(
            "sensitive-key",
            response=httpx.Response(
                401, request=httpx.Request("POST", "https://gms.ssafy.io/test")
            ),
            body={"key": "sensitive-key"},
        )

    fake = SimpleNamespace(
        ainvoke=invoke,
        root_async_client=SimpleNamespace(close=AsyncMock()),
        root_client=SimpleNamespace(close=Mock()),
    )
    monkeypatch.setattr("fuelroute.cli.gms_probe.ChatOpenAI", lambda **kwargs: fake)
    settings = Settings(
        _env_file=None,
        gms_api_key="sensitive-key",
        gms_base_url="https://gms.ssafy.io/test/",
        gms_model="account-model",
    )
    result = asyncio.run(probe_gms(settings))
    assert not result.ok and result.error.code == "AUTH_FAILED"
    assert "sensitive-key" not in result.model_dump_json()
    fake.root_async_client.close.assert_awaited_once()


def test_installed_chatopenai_constructor_without_network() -> None:
    """설치 SDK가 선택한 생성자 옵션을 지원하는지 확인한다. 모델 호출·과금은 없다."""
    model = ChatOpenAI(
        api_key="not-a-real-key",
        base_url="https://gms.ssafy.io/test/",
        model="not-a-real-model",
        use_responses_api=False,
        max_retries=0,
    )
    asyncio.run(model.root_async_client.close())
    model.root_client.close()


def test_consecutive_gms_probes_use_separate_http_clients(monkeypatch) -> None:
    """연속 검증이 앞 호출에서 닫은 기본 HTTP 풀을 재사용하지 않는지 회귀 검증한다."""
    clients = []

    def factory(**kwargs) -> SimpleNamespace:
        """호출별 HTTP 소유권을 기록하고 모의 SDK를 반환한다. 외부 통신은 없다."""
        clients.append((kwargs["http_client"], kwargs["http_async_client"]))
        return SimpleNamespace(
            ainvoke=AsyncMock(return_value=AIMessage(content="ok")),
            root_async_client=SimpleNamespace(close=AsyncMock()),
            root_client=SimpleNamespace(close=Mock()),
        )

    async def scenario() -> None:
        """동일 설정으로 두 번 실행하고 매번 독립 클라이언트가 생성·종료되는지 확인한다."""
        settings = Settings(
            _env_file=None,
            gms_api_key="fake-key",
            gms_model="fake-model",
            gms_base_url="https://gms.ssafy.io/test/",
        )
        assert (await probe_gms(settings)).ok
        assert (await probe_gms(settings)).ok

    monkeypatch.setattr("fuelroute.cli.gms_probe.ChatOpenAI", factory)
    asyncio.run(scenario())
    assert clients[0][0] is not clients[1][0]
    assert clients[0][1] is not clients[1][1]
    assert all(sync.is_closed and asynchronous.is_closed for sync, asynchronous in clients)
