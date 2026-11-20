"""SQLite 저장 설계의 실제 제약을 검증한다. 운영 DB·Repository 구현은 실행하지 않는다."""

import sqlite3
from pathlib import Path

import pytest


def test_storage_design_log_immutability_and_plan_survival() -> None:
    """이벤트 순서 중복·수정은 거절하고, 로그 삭제 후 계획이 남는지 메모리 DB로 확인한다."""
    with sqlite3.connect(":memory:") as db:
        db.executescript(Path("docs/storage.sql").read_text(encoding="utf-8"))
        db.execute(
            "INSERT INTO execution_sessions VALUES (?,?,?,?)",
            ("session", "2026-11-18T15:00:00+09:00", None, "running"),
        )
        event = (
            "event",
            "session",
            1,
            "TOOL_SUCCEEDED",
            "candidate_search",
            "search_gas_stations",
            "request",
            100,
            '{"count":1}',
            "2026-11-18T15:00:01+09:00",
        )
        db.execute("INSERT INTO execution_events VALUES (?,?,?,?,?,?,?,?,?,?)", event)
        with pytest.raises(sqlite3.IntegrityError):
            db.execute("UPDATE execution_events SET payload_json='{}'")
        with pytest.raises(sqlite3.IntegrityError):
            db.execute(
                "INSERT INTO execution_events VALUES (?,?,?,?,?,?,?,?,?,?)", ("another", *event[1:])
            )
        plan = (
            "plan",
            "session",
            1,
            "valid",
            "2026-11-18",
            "{}",
            "{}",
            None,
            None,
            "2026-11-18T15:00:01+09:00",
            "2026-11-18T15:00:01+09:00",
        )
        db.execute("INSERT INTO fuel_plans VALUES (?,?,?,?,?,?,?,?,?,?,?)", plan)
        db.execute("DELETE FROM execution_sessions WHERE session_id='session'")
        assert db.execute("SELECT count(*) FROM execution_events").fetchone()[0] == 0
        assert db.execute("SELECT id,session_id FROM fuel_plans").fetchone() == ("plan", None)
