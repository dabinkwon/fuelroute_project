"""F3017 대화·계획 CRUD·로그의 FastAPI 연결과 OpenAPI/오류 계약을 검증한다."""

import asyncio
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
from test_plan_storage import create_command

from fuelroute.agent.graph import execute_request
from fuelroute.api.app import app
from fuelroute.core.config import Settings
from fuelroute.core.contracts import ToolError, ToolMeta, ToolResult, utc_now
from fuelroute.schemas.analysis import ExtractedRequest, RequestAnalysis
from fuelroute.schemas.execution import PlanExecution
from fuelroute.schemas.planning import PlanProposal, WorkPlan
from fuelroute.storage.conversations import SQLiteConversationStore
from fuelroute.storage.logs import SQLiteExecutionLogStore
from fuelroute.storage.plans import SQLitePlanStore

KST = timezone(timedelta(hours=9))
ChatExecutor = Callable[..., Awaitable[ToolResult[dict]]]


async def offline_greeting_executor(
    message: str, settings: Settings, **kwargs: object
) -> ToolResult[dict]:
    """실제 그래프의 인사 분기를 외부 네트워크/GMS 없이 실행한다.

    message/settings와 API가 전달한 session/memory/event 인자를 실제 `execute_request`에
    연결한다. 분석기는 고정 인사 의도이며 계획·데이터 Tool이 호출되면 실패한다. 반환은
    실제 typed ToolResult이고 SQLite Memory/로그 외 외부 부작용은 없다.
    """

    async def analyze(_message: str) -> ExtractedRequest:
        """현재 문장을 값 추출 없는 인사 의도로 반환하며 외부 호출은 없다."""
        return ExtractedRequest(intent="greeting", reason="오프라인 API 테스트")

    async def forbidden_plan(_analysis: RequestAnalysis) -> PlanProposal:
        """인사 요청에서 계획 모델이 호출되면 AssertionError로 잘못된 분기를 알린다."""
        raise AssertionError("인사에서 계획을 만들면 안 됩니다.")

    async def forbidden_execute(_plan: WorkPlan) -> PlanExecution:
        """인사 요청에서 외부 Tool 실행이 호출되면 AssertionError로 실패시킨다."""
        raise AssertionError("인사에서 Tool을 실행하면 안 됩니다.")

    return await execute_request(
        message,
        settings,
        analyzer=analyze,
        planner=forbidden_plan,
        executor=forbidden_execute,
        **kwargs,
    )


async def api_request(
    method: str,
    path: str,
    *,
    database_path: Path,
    json: dict | None = None,
    chat_executor: ChatExecutor = offline_greeting_executor,
) -> httpx.Response:
    """임시 SQLite 저장소와 선택 Chat executor를 주입해 ASGI 요청을 수행한다.

    method/path/json은 HTTP 입력이고 database_path는 테스트 전용 파일이다. app.state의 기존
    설정/저장소/runner를 모두 보존했다가 복원한다. 실제 소켓·GMS·외부 데이터 API를 사용하지
    않으며 반환은 httpx.Response, DB 변경은 호출 Endpoint 계약에 한정된다.
    """
    names = ("settings", "plan_store", "log_store", "memory_store", "chat_executor")
    marker = object()
    previous = {name: getattr(app.state, name, marker) for name in names}
    app.state.settings = Settings(_env_file=None, database_path=database_path)
    app.state.plan_store = SQLitePlanStore(database_path)
    app.state.log_store = SQLiteExecutionLogStore(database_path)
    app.state.memory_store = SQLiteConversationStore(database_path)
    app.state.chat_executor = chat_executor
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            return await client.request(method, path, json=json)
    finally:
        for name, value in previous.items():
            if value is marker:
                delattr(app.state, name)
            else:
                setattr(app.state, name, value)


def test_chat_runs_graph_and_persists_queryable_execution_log(tmp_path: Path) -> None:
    """POST /api/chat은 typed 응답과 새 IDs를 주고 같은 DB의 로그 API로 실행을 조회한다."""
    path = tmp_path / "backend.sqlite3"
    response = asyncio.run(
        api_request("POST", "/api/chat", database_path=path, json={"message": "안녕"})
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "not_required" and body["answer"]
    assert len(body["session_id"]) == len(body["execution_id"]) == 32
    assert body["event_count"] > 0 and body["log_delivery_failures"] == 0
    detail = asyncio.run(
        api_request("GET", f"/api/logs/{body['execution_id']}", database_path=path)
    )
    assert detail.status_code == 200
    assert detail.json()["session"]["event_count"] == body["event_count"]


def test_plan_http_crud_persists_and_marks_update_for_revalidation(tmp_path: Path) -> None:
    """계획 CRUD와 일정·메모 수정이 한 파일에 반영되고 추천 검증 상태를 유지한다."""
    path = tmp_path / "backend.sqlite3"
    command = create_command()
    created_response = asyncio.run(
        api_request(
            "POST",
            "/api/plans",
            database_path=path,
            json=command.model_dump(mode="json"),
        )
    )
    assert created_response.status_code == 201
    created = created_response.json()["plan"]
    plan_id = created["plan_id"]
    listed = asyncio.run(api_request("GET", "/api/plans?trip_date=2026-11-20", database_path=path))
    assert listed.status_code == 200 and listed.json()["count"] == 1
    detail = asyncio.run(api_request("GET", f"/api/plans/{plan_id}", database_path=path))
    assert detail.status_code == 200 and detail.json() == created
    updated = asyncio.run(
        api_request(
            "PATCH",
            f"/api/plans/{plan_id}",
            database_path=path,
            json={
                "expected_revision": 1,
                "planned_start_at": "2026-11-20T09:00:00+09:00",
                "planned_end_at": "2026-11-20T12:00:00+09:00",
            },
        )
    )
    assert updated.status_code == 200
    assert updated.json()["plan"]["revision"] == 2
    assert updated.json()["plan"]["validation_status"] == "valid"
    memo_updated = asyncio.run(
        api_request(
            "PATCH",
            f"/api/plans/{plan_id}",
            database_path=path,
            json={"expected_revision": 2, "memo": "도착 전에 연락"},
        )
    )
    assert memo_updated.status_code == 200
    assert memo_updated.json()["plan"]["memo"] == "도착 전에 연락"
    assert memo_updated.json()["plan"]["validation_status"] == "valid"
    deleted = asyncio.run(api_request("DELETE", f"/api/plans/{plan_id}", database_path=path))
    assert deleted.status_code == 204 and deleted.content == b""
    missing = asyncio.run(api_request("GET", f"/api/plans/{plan_id}", database_path=path))
    assert missing.status_code == 404 and missing.json()["error"]["code"] == "PLAN_NOT_FOUND"


def test_plan_overlap_is_success_and_stale_revision_is_conflict(tmp_path: Path) -> None:
    """겹친 계획 POST는201과 conflicts를 주고 오래된 PATCH는409 공통 오류를 준다."""
    path = tmp_path / "backend.sqlite3"
    start = datetime(2026, 11, 20, 9, 0, tzinfo=KST)
    first_command = create_command(start=start, end=start + timedelta(hours=3))
    first = asyncio.run(
        api_request(
            "POST",
            "/api/plans",
            database_path=path,
            json=first_command.model_dump(mode="json"),
        )
    ).json()["plan"]
    second_command = create_command(
        start=start + timedelta(hours=1), end=start + timedelta(hours=2)
    )
    second = asyncio.run(
        api_request(
            "POST",
            "/api/plans",
            database_path=path,
            json=second_command.model_dump(mode="json"),
        )
    )
    assert second.status_code == 201
    assert [item["plan_id"] for item in second.json()["conflicts"]] == [first["plan_id"]]
    stale = asyncio.run(
        api_request(
            "PATCH",
            f"/api/plans/{first['plan_id']}",
            database_path=path,
            json={"expected_revision": 2, "planned_end_at": None},
        )
    )
    assert stale.status_code == 409 and stale.json()["error"]["code"] == "PLAN_CONFLICT"


def test_invalid_requests_and_agent_failure_use_common_error_shape(tmp_path: Path) -> None:
    """Pydantic 오류·없는 계획·Agent 설정 실패가 code/message/request_id 공통 형식이다."""
    path = tmp_path / "backend.sqlite3"

    async def failed_agent(
        _message: str, _settings: Settings, **_kwargs: object
    ) -> ToolResult[dict]:
        """외부 설정 오류의 공통 실패를 반환하며 이벤트/DB/네트워크 부작용은 없다."""
        return ToolResult(
            ok=False,
            tool_name="execute_request",
            data=None,
            error=ToolError(code="CONFIG_ERROR", message="비공개 상세"),
            meta=ToolMeta(
                provider="local",
                request_id="failed",
                elapsed_ms=0,
                fetched_at=utc_now(),
            ),
        )

    cases = [
        asyncio.run(api_request("POST", "/api/chat", database_path=path, json={"message": ""})),
        asyncio.run(api_request("GET", "/api/plans/한글", database_path=path)),
        asyncio.run(api_request("GET", "/api/plans/missing", database_path=path)),
        asyncio.run(
            api_request(
                "POST",
                "/api/chat",
                database_path=path,
                json={"message": "추천해줘"},
                chat_executor=failed_agent,
            )
        ),
    ]
    assert [(item.status_code, item.json()["error"]["code"]) for item in cases] == [
        (422, "INVALID_INPUT"),
        (422, "INVALID_INPUT"),
        (404, "PLAN_NOT_FOUND"),
        (503, "CONFIG_ERROR"),
    ]
    for response in cases:
        error = response.json()["error"]
        assert error["message"] and len(error["request_id"]) == 32
        assert "비공개 상세" not in response.text


def test_openapi_contains_chat_plan_crud_and_read_delete_logs(tmp_path: Path) -> None:
    """OpenAPI와 /docs가 F3017 경로를 제공하고 로그 수정 메서드는 노출하지 않는다."""
    paths = app.openapi()["paths"]
    assert set(paths["/api/chat"]) == {"post"}
    assert set(paths["/api/plans"]) == {"get", "post"}
    assert set(paths["/api/plans/{plan_id}"]) == {"get", "patch", "delete"}
    assert set(paths["/api/logs"]) == {"get"}
    assert set(paths["/api/logs/{execution_id}"]) == {"get", "delete"}
    docs = asyncio.run(api_request("GET", "/docs", database_path=tmp_path / "docs.sqlite3"))
    assert docs.status_code == 200 and "Swagger UI" in docs.text


def test_local_and_netlify_frontends_are_allowed_by_cors() -> None:
    """정적 UI의 로컬 미리보기와 운영 Netlify origin만 필요한 API 메서드로 허용한다."""

    async def preflight(origin: str) -> httpx.Response:
        """지정 origin의 계획 POST preflight를 ASGI에서 수행하며 서버/DB를 변경하지 않는다."""
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            return await client.options(
                "/api/plans",
                headers={
                    "Origin": origin,
                    "Access-Control-Request-Method": "POST",
                    "Access-Control-Request-Headers": "content-type",
                },
            )

    for origin in ("http://127.0.0.1:5173", "https://fuelroute-348.netlify.app"):
        response = asyncio.run(preflight(origin))
        assert response.status_code == 200
        assert response.headers["access-control-allow-origin"] == origin
