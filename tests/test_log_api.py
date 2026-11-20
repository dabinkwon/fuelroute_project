"""F3014 로그 목록·상세·삭제 API와 오류 계약·계획 보존·수정 부재를 검증한다."""

import asyncio
import sqlite3
from contextlib import closing
from pathlib import Path

import httpx
from test_logging import greeting_run

from fuelroute.api.app import app
from fuelroute.api.logs import router as logs_router
from fuelroute.storage.logs import SQLiteExecutionLogStore


async def request(store: SQLiteExecutionLogStore, method: str, path: str) -> httpx.Response:
    """FastAPI 앱에 테스트 저장소를 주입해 한 ASGI 요청을 수행하고 상태를 원복한다.

    store는 임시 SQLite Repository, method/path는 HTTP 요청이다. 실제 서버·네트워크·LLM·
    외부 API는 사용하지 않는다. 기존 app.state.log_store가 있으면 요청 후 복원한다.
    반환은 httpx.Response이며 DB 변경은 해당 Endpoint 계약만 따른다.
    """
    marker = object()
    previous = getattr(app.state, "log_store", marker)
    app.state.log_store = store
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            return await client.request(method, path)
    finally:
        if previous is marker:
            delattr(app.state, "log_store")
        else:
            app.state.log_store = previous


def test_list_and_detail_return_typed_ordered_logs(tmp_path: Path) -> None:
    """목록은 최신 실행부터, 상세는 같은 실행의 이벤트를 sequence 오름차순으로 반환한다."""
    store = SQLiteExecutionLogStore(tmp_path / "logs.sqlite3")
    asyncio.run(greeting_run(store, "run-a"))
    asyncio.run(greeting_run(store, "run-b"))
    listed = asyncio.run(request(store, "GET", "/api/logs?limit=1"))
    assert listed.status_code == 200
    body = listed.json()
    assert body["count"] == 1 and body["items"][0]["execution_id"] == "run-b"
    detail = asyncio.run(request(store, "GET", "/api/logs/run-a"))
    assert detail.status_code == 200
    data = detail.json()
    assert data["session"]["status"] == "completed"
    assert data["session"]["event_count"] == len(data["events"])
    assert [event["sequence"] for event in data["events"]] == list(
        range(1, len(data["events"]) + 1)
    )


def test_delete_cascades_events_but_preserves_plan_row(tmp_path: Path) -> None:
    """DELETE204 뒤 로그는404이고 연결된 계획 행은 남으며 session_id만 NULL이 된다."""
    path = tmp_path / "logs.sqlite3"
    store = SQLiteExecutionLogStore(path)
    asyncio.run(greeting_run(store, "run-delete"))
    with closing(sqlite3.connect(path)) as connection, connection:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.executescript(Path("docs/storage.sql").read_text(encoding="utf-8"))
        connection.execute(
            "INSERT INTO fuel_plans(id, session_id, trip_date, origin_json, destination_json, "
            "input_snapshot_json, recommendation_json, planned_start_at, planned_end_at, "
            "created_at, updated_at) VALUES (?, ?, ?, '{}', '{}', '{}', '{}', NULL, NULL, ?, ?)",
            (
                "plan-1",
                "run-delete",
                "2026-11-20",
                "2026-11-20T00:00:00+09:00",
                "2026-11-20T00:00:00+09:00",
            ),
        )
    deleted = asyncio.run(request(store, "DELETE", "/api/logs/run-delete"))
    assert deleted.status_code == 204 and deleted.content == b""
    missing = asyncio.run(request(store, "GET", "/api/logs/run-delete"))
    assert missing.status_code == 404 and missing.json()["error"]["code"] == "LOG_NOT_FOUND"
    with closing(sqlite3.connect(path)) as connection:
        assert connection.execute(
            "SELECT session_id FROM fuel_plans WHERE id='plan-1'"
        ).fetchone() == (None,)
        assert connection.execute("SELECT COUNT(*) FROM execution_events").fetchone()[0] == 0


def test_missing_invalid_and_query_errors_use_common_shape(tmp_path: Path) -> None:
    """없는 로그404·잘못된 ID/limit422가 code/message/request_id 공통 구조를 사용한다."""
    store = SQLiteExecutionLogStore(tmp_path / "logs.sqlite3")
    cases = [
        ("/api/logs/missing", 404, "LOG_NOT_FOUND"),
        ("/api/logs/한글", 422, "INVALID_INPUT"),
        ("/api/logs?limit=0", 422, "INVALID_INPUT"),
    ]
    for path, status, code in cases:
        response = asyncio.run(request(store, "GET", path))
        body = response.json()
        assert response.status_code == status and body["error"]["code"] == code
        assert body["error"]["message"] and len(body["error"]["request_id"]) == 32


def test_store_failure_returns_sanitized_503(tmp_path: Path) -> None:
    """SQLite 파일 대신 디렉터리를 주입하면 경로를 노출하지 않는 LOG_STORE_ERROR503이다."""
    invalid = tmp_path / "directory"
    invalid.mkdir()
    response = asyncio.run(request(SQLiteExecutionLogStore(invalid), "GET", "/api/logs"))
    body = response.json()
    assert response.status_code == 503 and body["error"]["code"] == "LOG_STORE_ERROR"
    assert str(invalid) not in response.text and "sqlite" not in response.text.lower()


def test_log_routes_have_no_update_method(tmp_path: Path) -> None:
    """로그 상세 경로에 PUT/PATCH를 제공하지 않고 기존 GET/DELETE 메서드만 등록한다."""
    store = SQLiteExecutionLogStore(tmp_path / "logs.sqlite3")
    put = asyncio.run(request(store, "PUT", "/api/logs/run"))
    patch = asyncio.run(request(store, "PATCH", "/api/logs/run"))
    assert put.status_code == patch.status_code == 405
    methods = set().union(
        *[route.methods for route in logs_router.routes if "{execution_id}" in route.path]
    )
    assert methods == {"GET", "DELETE"}


def test_health_remains_independent_of_log_database(tmp_path: Path) -> None:
    """로그 저장소가 고장 나도 /health는 외부 API·DB 없이 기존200 정적 응답을 반환한다."""
    invalid = tmp_path / "directory"
    invalid.mkdir()
    response = asyncio.run(request(SQLiteExecutionLogStore(invalid), "GET", "/health"))
    assert response.status_code == 200 and response.json()["status"] == "ok"
