"""GMS를 통한 텍스트·구조화 출력·로컬 Tool 왕복 검증."""

from typing import Literal

from langchain_core.exceptions import OutputParserException
from langchain_core.messages import HumanMessage, ToolMessage
from langchain_core.tools import StructuredTool
from langchain_openai import ChatOpenAI
from openai import (
    APIConnectionError,
    APIError,
    APIStatusError,
    APITimeoutError,
    DefaultAsyncHttpxClient,
    DefaultHttpxClient,
)
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from fuelroute.config import Settings
from fuelroute.contracts import ToolResult
from fuelroute.probes import ProbeFailure, run_probe
from fuelroute.services import FuelConditions, calculate_safe_range


class ExtractedConditions(BaseModel):
    """조건 추출 검증용 출력. 언급하지 않은 연료값은 None이어야 한다."""

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    current_fuel_l: float | None = Field(description="현재 연료(L), 언급이 없으면 null")
    reserve_fuel_l: float | None = Field(description="안전 잔량(L), 언급이 없으면 null")
    avg_efficiency_kmpl: float | None = Field(description="평균 연비(km/L), 언급이 없으면 null")


def safe_range_tool() -> StructuredTool:
    """안전거리 계산 함수를 설명·단위·범위가 포함된 LangChain Tool로 등록한다.

    인자·키 없이 등록할 수 있다. 반환 Tool은 FuelConditions 스키마와 함수 docstring을
    모델에 전달하고 호출 시 일반 Python 계산만 수행한다. 예: tool.invoke({...}) → km.
    외부 HTTP·DB·재계획 부작용이 없으며 유효하지 않은 입력은 ValidationError다.
    """
    return StructuredTool.from_function(
        func=calculate_safe_range,
        name="calculate_safe_range",
        args_schema=FuelConditions,
        description=calculate_safe_range.__doc__,
    )


async def probe_gms(
    settings: Settings, mode: Literal["text", "conditions", "tool"] = "text"
) -> ToolResult[dict]:
    """GMS 계정의 선택 모델을 고정 예시로 검증하고 정제된 결과를 반환한다.

    text는 텍스트 응답 1회, conditions는 완전/누락 조건의 구조화 추출 각 1회,
    tool은 단일 Tool 선택→로컬 안전거리 계산→최종 응답의 2회 LLM 호출이다.
    GMS_API_KEY/BASE_URL/MODEL이 필요하며 timeout 설정을 사용하고 자동 재시도는 0회다.
    인증·한도·통신 오류와 예상 밖 출력은 공통 실패다. 모델을 자동 변경하지 않는다.
    API 비용이 발생할 수 있으며 DB 저장은 없다. 예: mode='tool'의 계산 결과는 144km다.
    """

    async def operation() -> dict:
        """설정된 GMS 클라이언트로 고정 smoke 시나리오를 실행한다.

        성공 시 모델 ID·응답·확인된 구조/계산을 반환한다. 임의의 원격 Tool을 실행하지
        않고 calculate_safe_range만 허용한다. 클라이언트는 완료·실패 시 모두 닫는다.
        """
        # LangChain 기본 클라이언트 캐시를 공유하면 앞 검증의 close()가 다음 호출을 막는다.
        sync_http = DefaultHttpxClient(timeout=settings.http_timeout_seconds)
        async_http = DefaultAsyncHttpxClient(timeout=settings.http_timeout_seconds)
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
        try:
            if mode == "text":
                response = await model.ainvoke(
                    "연결 확인용 요청입니다. 'FuelRoute 연결 확인'이라고 답하세요."
                )
                if not response.content or response.tool_calls:
                    raise ProbeFailure(
                        "INVALID_RESPONSE", "텍스트 응답이 비었거나 예상 밖 Tool 호출입니다."
                    )
                return {
                    "model": settings.gms_model,
                    "mode": mode,
                    "content": response.content,
                    "usage": response.usage_metadata,
                }
            if mode == "conditions":
                extractor = model.with_structured_output(ExtractedConditions, method="json_schema")
                complete = await extractor.ainvoke(
                    "명시된 값만 추출하세요. 현재 연료 15L, 안전 잔량 3L, 평균 연비 12km/L입니다."
                )
                missing = await extractor.ainvoke(
                    "명시된 값만 추출하고 없는 값은 null로 두세요. 서울 가는 길에 주유하고 싶어."
                )
                expected = {"current_fuel_l": 15, "reserve_fuel_l": 3, "avg_efficiency_kmpl": 12}
                if complete.model_dump() != expected or any(
                    value is not None for value in missing.model_dump().values()
                ):
                    raise ProbeFailure(
                        "INVALID_RESPONSE", "조건 추출값 또는 누락값 처리가 다릅니다."
                    )
                return {
                    "model": settings.gms_model,
                    "mode": mode,
                    "complete": complete.model_dump(),
                    "missing": missing.model_dump(),
                }
            if mode != "tool":
                raise ProbeFailure("INVALID_INPUT", "text/conditions/tool 모드를 사용하세요.")
            tool = safe_range_tool()
            messages = [
                HumanMessage(
                    content=(
                        "현재 연료 15L, 안전 잔량 3L, 연비 12km/L입니다. "
                        "calculate_safe_range로 계산하고 결과의 단위를 설명하세요."
                    )
                )
            ]
            choice = await model.bind_tools([tool], tool_choice=tool.name).ainvoke(messages)
            if len(choice.tool_calls) != 1 or choice.tool_calls[0]["name"] != tool.name:
                raise ProbeFailure("INVALID_RESPONSE", "단일 안전거리 Tool 선택에 실패했습니다.")
            call = choice.tool_calls[0]
            args = FuelConditions.model_validate(call["args"])
            if args.model_dump() != {
                "current_fuel_l": 15,
                "reserve_fuel_l": 3,
                "avg_efficiency_kmpl": 12,
            }:
                raise ProbeFailure("INVALID_RESPONSE", "Tool 인자가 고정 예시 입력과 다릅니다.")
            distance = tool.invoke(args.model_dump())
            final = await model.ainvoke(
                [*messages, choice, ToolMessage(content=f"{distance} km", tool_call_id=call["id"])]
            )
            if not final.content or final.tool_calls:
                raise ProbeFailure(
                    "INVALID_RESPONSE", "Tool 결과 이후 최종 텍스트 응답이 없습니다."
                )
            return {
                "model": settings.gms_model,
                "mode": mode,
                "tool_name": tool.name,
                "arguments": args.model_dump(),
                "safe_range_km": distance,
                "content": final.content,
                "usage": final.usage_metadata,
            }
        except APITimeoutError as exc:
            raise ProbeFailure(
                "TIMEOUT", "GMS 응답 시간이 초과되었습니다.", retryable=True
            ) from exc
        except APIStatusError as exc:
            if exc.status_code in (401, 403):
                raise ProbeFailure("AUTH_FAILED", "GMS 인증키·모델 권한을 확인하세요.") from exc
            if exc.status_code == 429:
                raise ProbeFailure(
                    "RATE_LIMITED", "GMS 호출 한도를 확인하세요.", retryable=True
                ) from exc
            raise ProbeFailure(
                "UPSTREAM_ERROR",
                f"GMS HTTP 오류: {exc.status_code}",
                retryable=exc.status_code >= 500,
            ) from exc
        except APIConnectionError as exc:
            raise ProbeFailure(
                "UPSTREAM_ERROR", "GMS에 연결할 수 없습니다.", retryable=True
            ) from exc
        except (APIError, ValidationError, OutputParserException) as exc:
            raise ProbeFailure(
                "INVALID_RESPONSE", "GMS 응답 스키마·지원 기능을 확인하세요."
            ) from exc
        finally:
            await model.root_async_client.close()
            model.root_client.close()
            await async_http.aclose()
            sync_http.close()

    return await run_probe(settings, "gms", f"probe_gms_{mode}", operation)
