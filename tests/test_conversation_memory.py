"""F3010 세션별 조건 병합·SQLite 지속성·근거·실패·현재 데이터 재조회를 검증한다."""

import asyncio
import json
import sqlite3
from datetime import date
from pathlib import Path

import httpx
import pytest
from pydantic import ValidationError
from test_execution import TODAY, USER, Provider, extracted, proposal, settings

from fuelroute.agent.graph import analyze_request, execute_request, plan_request
from fuelroute.agent.llm import llm_analyze
from fuelroute.core.config import Settings
from fuelroute.core.contracts import ToolResult
from fuelroute.core.errors import ProbeFailure
from fuelroute.schemas.analysis import ExtractedRequest, RequestAnalysis
from fuelroute.schemas.locations import LocationCandidate, LocationMatchSet
from fuelroute.schemas.memory import ConversationMemory
from fuelroute.schemas.planning import PlanProposal
from fuelroute.storage.conversations import SQLiteConversationStore


def update(**mentions: object) -> ExtractedRequest:
    """현재 발언의 명시 값만 담은 분석 대역을 만든다. 과거 조건은 복사하지 않는다.

    mentions는Mention dict 또는clear/reset 제어필드이며 미언급은모델기본None이다.
    입력스키마검증만하고API·DB·값생성은없다. 예:max_detour_min={'value':10,'quote':'10분'}.
    """
    return ExtractedRequest.model_validate(
        {"intent": "recommend", "reason": "현재 조건 변경", **mentions}
    )


async def turn(
    store: SQLiteConversationStore,
    session_id: str,
    message: str,
    raw: ExtractedRequest,
    *,
    config: Settings | None = None,
) -> ToolResult[dict]:
    """모델만 주입하고 실제 분석·병합·SQLite 저장을 한 턴 수행한다.

    store는테스트임시파일이고ID는서로독립이다. 실제HTTP/LLM은호출하지않으며반환은
    analyze_request의공통결과다. 예:두번째turn에우회값만주어도기존조건을보존한다.
    """

    async def analyze(user: str) -> ExtractedRequest:
        """현재 원문이 그대로 전달되는지 확인하고 준비한 구조화 값을 반환한다."""
        assert user == message
        return raw

    return await analyze_request(
        message,
        config or Settings(_env_file=None),
        analyzer=analyze,
        reference_date=TODAY,
        session_id=session_id,
        memory_store=store,
    )


def test_same_session_merges_only_changed_fields_and_store_reopens(tmp_path: Path) -> None:
    """별도 저장소 객체·이벤트루프에서도 같은ID만이어받고현재발언/메모리출처를구분한다."""
    path = tmp_path / "memory.sqlite3"
    first = asyncio.run(turn(SQLiteConversationStore(path), "trip", USER, extracted()))
    second = asyncio.run(
        turn(
            SQLiteConversationStore(path),
            "trip",
            "우회10분으로 바꿔줘",
            update(max_detour_min={"value": 10, "quote": "우회10분"}),
        )
    )
    assert first.ok and second.ok
    assert second.data["analysis"]["ready_for_planning"]
    values = second.data["analysis"]["conditions"]
    assert values["max_detour_min"] == 10 and values["remaining_range_km"] == 180
    assert values["origin"] == first.data["analysis"]["conditions"]["origin"]
    assert second.data["analysis"]["field_sources"]["max_detour_min"] == "current"
    assert second.data["analysis"]["field_sources"]["origin"] == "memory"
    assert second.data["session"]["revision"] == 2 and second.data["session"]["memory_saved"]
    memory = asyncio.run(SQLiteConversationStore(path).load("trip"))
    assert memory.updated_at.tzinfo is not None
    assert "max_detour_min" in memory.last_request_summary
    assert "origin" in second.data["session"]["inherited_fields"]


def test_other_session_has_no_previous_trip(tmp_path: Path) -> None:
    """같은DB·같은발언이어도다른ID에는출발/목적/연료조건이전달되지않는다."""
    store = SQLiteConversationStore(tmp_path / "memory.db")
    asyncio.run(turn(store, "one", USER, extracted()))
    other = asyncio.run(
        turn(store, "two", "우회10분", update(max_detour_min={"value": 10, "quote": "10분"}))
    )
    assert other.ok and not other.data["analysis"]["ready_for_planning"]
    assert other.data["analysis"]["conditions"]["origin"] is None
    assert other.data["session"]["revision"] == 1


def test_missing_field_short_answer_completes_previous_request(tmp_path: Path) -> None:
    """안전여유만빠진첫요청을단위있는짧은답으로보완하고나머지조건은재요구하지않는다."""
    store = SQLiteConversationStore(tmp_path / "memory.db")
    first = asyncio.run(turn(store, "trip", USER, extracted(reserve_range_km=None)))
    assert first.data["analysis"]["missing_fields"] == ["reserve_range_km"]
    assert asyncio.run(store.load("trip")).pending_fields == ["reserve_range_km"]
    second = asyncio.run(
        turn(store, "trip", "30km", update(reserve_range_km={"value": 30, "quote": "30km"}))
    )
    assert second.ok and second.data["analysis"]["ready_for_planning"]
    assert second.data["analysis"]["missing_fields"] == []


@pytest.mark.parametrize("value,quote", [(30, "30"), (999, "30km")])
def test_unverified_new_value_does_not_fall_back_to_old_valid_value(
    tmp_path: Path, value: float, quote: str
) -> None:
    """잘못된단위/숫자의새입력을이전정상30km로가리지않고확인요청상태로유지한다."""
    store = SQLiteConversationStore(tmp_path / "memory.db")
    asyncio.run(turn(store, "trip", USER, extracted()))
    result = asyncio.run(
        turn(store, "trip", quote, update(reserve_range_km={"value": value, "quote": quote}))
    )
    assert result.ok and result.data["analysis"]["conditions"]["reserve_range_km"] is None
    assert "reserve_range_km" in result.data["analysis"]["unverified_fields"]
    assert not result.data["analysis"]["ready_for_planning"]
    assert asyncio.run(store.load("trip")).conditions.reserve_range_km is None


def test_merged_range_revalidates_and_relation_can_be_fixed(tmp_path: Path) -> None:
    """기존여유30km에주행가능20km는거절하고40km재입력으로다른값재입력없이해결한다."""
    store = SQLiteConversationStore(tmp_path / "memory.db")
    asyncio.run(turn(store, "trip", USER, extracted()))
    bad = asyncio.run(
        turn(
            store, "trip", "주행가능20km", update(remaining_range_km={"value": 20, "quote": "20km"})
        )
    )
    assert "reserve_range_km" in bad.data["analysis"]["invalid_fields"]
    fixed = asyncio.run(
        turn(
            store, "trip", "주행가능40km", update(remaining_range_km={"value": 40, "quote": "40km"})
        )
    )
    assert fixed.ok and fixed.data["analysis"]["ready_for_planning"]
    assert fixed.data["analysis"]["conditions"]["reserve_range_km"] == 30


@pytest.mark.parametrize("value", [-1, 0])
def test_invalid_new_range_remains_invalid_until_changed(tmp_path: Path, value: float) -> None:
    """새음수/0km를이전180km로보정하지않고후속인사뒤에도해당입력을보완하도록유지한다."""
    store = SQLiteConversationStore(tmp_path / "memory.db")
    asyncio.run(turn(store, "trip", USER, extracted()))
    result = asyncio.run(
        turn(
            store,
            "trip",
            f"주행가능{value}km",
            update(remaining_range_km={"value": value, "quote": f"{value}km"}),
        )
    )
    assert "remaining_range_km" in result.data["analysis"]["invalid_fields"]
    assert asyncio.run(store.load("trip")).conditions.remaining_range_km == value


def test_changed_place_clears_old_coordinate_and_other_role_is_preserved(tmp_path: Path) -> None:
    """새목적지명만주면이전GPS를지우고출발좌표는유지하며새좌표가없으면입력을요청한다."""
    store = SQLiteConversationStore(tmp_path / "memory.db")
    asyncio.run(turn(store, "trip", USER, extracted()))
    result = asyncio.run(
        turn(
            store,
            "trip",
            "목적지는부산",
            update(destination_label={"value": "부산", "quote": "부산"}),
        )
    )
    conditions = result.data["analysis"]["conditions"]
    assert conditions["destination"] is None and conditions["origin"]["longitude"] == 127
    assert result.data["analysis"]["missing_fields"] == ["destination"]
    assert "destination" not in result.data["analysis"]["coordinate_sources"]


def test_new_explicit_coordinate_drops_old_place_label(tmp_path: Path) -> None:
    """좌표를직접바꾸면이전지명을새GPS의이름처럼남기지않고출처는user로기록한다."""
    store = SQLiteConversationStore(tmp_path / "memory.db")
    asyncio.run(
        turn(
            store,
            "trip",
            USER + " 목적서울",
            extracted(destination_label={"value": "서울", "quote": "서울"}),
        )
    )
    result = asyncio.run(
        turn(
            store,
            "trip",
            "목적(129,35)",
            update(
                destination={"value": {"longitude": 129, "latitude": 35}, "quote": "목적(129,35)"}
            ),
        )
    )
    assert result.ok and result.data["analysis"]["conditions"]["destination_label"] is None
    assert result.data["analysis"]["coordinate_sources"]["destination"] == "user"


def test_known_locations_reuse_coordinates_but_changed_place_resolves_again(tmp_path: Path) -> None:
    """API확인좌표는후속제약변경에재사용하고새지명만다시해석하며지역수준안내를유지한다."""
    from fuelroute.core.contracts import ToolMeta, ToolResult, utc_now

    calls = []
    store = SQLiteConversationStore(tmp_path / "memory.db")

    async def resolve(label: str) -> ToolResult[dict]:
        """지명별유일한지역좌표를합성한다. API키/실제HTTP/지리추측은사용하지않는다."""
        calls.append(label)
        point = LocationCandidate(
            name=label,
            longitude=127 if label == "대구" else 128,
            latitude=37,
            source="kakao_address",
            match_level="area",
        )
        data = LocationMatchSet(
            query=label,
            status="resolved",
            search_type="address",
            total_count=1,
            candidates=[point],
            selected=point,
            api_call_count=1,
        )
        return ToolResult(
            ok=True,
            tool_name="resolve_location",
            data=data.model_dump(mode="json"),
            error=None,
            meta=ToolMeta(
                provider="kakao_local",
                request_id=str(len(calls)),
                fetched_at=utc_now(),
                elapsed_ms=0,
            ),
        )

    async def request(message: str, raw: ExtractedRequest) -> ToolResult[dict]:
        """실제분석/병합/위치노드에모델·위치응답만주입해같은ID로실행한다."""

        async def analyze(user: str) -> ExtractedRequest:
            """현재발언의추출만반환한다. 이전좌표는복사하지않는다."""
            return raw

        return await analyze_request(
            message,
            Settings(_env_file=None),
            analyzer=analyze,
            location_resolver=resolve,
            session_id="trip",
            memory_store=store,
            reference_date=TODAY,
        )

    asyncio.run(
        request(
            USER + " 대구서울",
            extracted(
                origin=None,
                destination=None,
                origin_label={"value": "대구", "quote": "대구"},
                destination_label={"value": "서울", "quote": "서울"},
            ),
        )
    )
    second = asyncio.run(request("우회10분", update(max_detour_min={"value": 10, "quote": "10분"})))
    assert calls == ["대구", "서울"]
    assert second.data["analysis"]["coordinate_sources"]["destination"] == "kakao_address"
    assert any("지역" in warning for warning in second.data["analysis"]["warnings"])
    third = asyncio.run(
        request("목적부산", update(destination_label={"value": "부산", "quote": "부산"}))
    )
    assert calls == ["대구", "서울", "부산"]
    assert not any("'서울'" in warning for warning in third.data["analysis"]["warnings"])


def test_unit_mode_switch_clears_incompatible_quantities(tmp_path: Path) -> None:
    """km→L량→km전환시과거반대단위값을섞거나역산하지않고새방식의필수값을요청한다."""
    store = SQLiteConversationStore(tmp_path / "memory.db")
    asyncio.run(turn(store, "trip", USER, extracted()))
    fuel = asyncio.run(
        turn(
            store,
            "trip",
            "현재15L 연비12km/L",
            update(
                current_fuel_l={"value": 15, "quote": "15L"},
                avg_efficiency_kmpl={"value": 12, "quote": "12km/L"},
            ),
        )
    )
    assert fuel.data["analysis"]["range_input_mode"] == "fuel"
    assert fuel.data["analysis"]["conditions"]["remaining_range_km"] is None
    assert fuel.data["analysis"]["missing_fields"] == ["reserve_fuel_l"]
    km = asyncio.run(
        turn(
            store,
            "trip",
            "주행가능200km",
            update(remaining_range_km={"value": 200, "quote": "200km"}),
        )
    )
    assert km.data["analysis"]["range_input_mode"] == "distance"
    assert km.data["analysis"]["conditions"]["current_fuel_l"] is None
    assert km.data["analysis"]["missing_fields"] == ["reserve_range_km"]


def test_greeting_preserves_trip_and_followup_resumes_it(tmp_path: Path) -> None:
    """인사/설명으로과거조건을재추천하지않고다음조건변경에는원래여행을이어받는다."""
    store = SQLiteConversationStore(tmp_path / "memory.db")
    asyncio.run(turn(store, "trip", USER, extracted()))
    greeting = asyncio.run(
        turn(store, "trip", "안녕", ExtractedRequest(intent="greeting", reason="인사"))
    )
    assert greeting.data["analysis"]["intent"] == "greeting"
    assert not greeting.data["analysis"]["ready_for_planning"]
    assert asyncio.run(store.load("trip")).conditions.remaining_range_km == 180
    followup = asyncio.run(
        turn(store, "trip", "우회10분", update(max_detour_min={"value": 10, "quote": "10분"}))
    )
    assert followup.data["analysis"]["ready_for_planning"]


def test_explicit_reset_and_constraint_removal_require_current_quotes(tmp_path: Path) -> None:
    """명시상한삭제는그조건만지우고새여행초기화는모든과거주행조건을비운다."""
    store = SQLiteConversationStore(tmp_path / "memory.db")
    asyncio.run(
        turn(
            store,
            "trip",
            USER + " 우회10분",
            extracted(max_detour_min={"value": 10, "quote": "10분"}),
        )
    )
    cleared = asyncio.run(
        turn(
            store,
            "trip",
            "우회 제한 없애줘",
            update(clear_fields=[{"field": "max_detour_min", "quote": "우회 제한 없애줘"}]),
        )
    )
    assert cleared.data["analysis"]["conditions"]["max_detour_min"] is None
    assert cleared.data["analysis"]["ready_for_planning"]
    reset = asyncio.run(
        turn(
            store,
            "trip",
            "새 여행을 시작해",
            update(reset_context={"value": True, "quote": "새 여행"}),
        )
    )
    assert all(value is None for value in reset.data["analysis"]["conditions"].values())
    assert not reset.data["analysis"]["ready_for_planning"]


@pytest.mark.parametrize("control", ["reset", "clear"])
def test_hallucinated_control_action_does_not_delete_conditions(
    tmp_path: Path, control: str
) -> None:
    """모델이현재원문에없는초기화/삭제명령을만들면채택하지않고명시확인을요청한다."""
    store = SQLiteConversationStore(tmp_path / "memory.db")
    asyncio.run(turn(store, "trip", USER, extracted()))
    raw = (
        update(reset_context={"value": True, "quote": "초기화"})
        if control == "reset"
        else update(clear_fields=[{"field": "fuel_type", "quote": "유종 없애"}])
    )
    result = asyncio.run(turn(store, "trip", "다시 추천해", raw))
    assert not result.data["analysis"]["ready_for_planning"]
    assert result.data["analysis"]["conditions"]["fuel_type"] == "gasoline"


def test_merged_ready_trip_plans_and_requeries_current_prices_each_turn(tmp_path: Path) -> None:
    """조건맥락만저장하며경로·가격·F3009캐시를다음요청에재사용하지않는지실제어댑터로검사한다."""
    provider = Provider()
    store = SQLiteConversationStore(tmp_path / "memory.db")

    async def scenario() -> None:
        """두턴모두실제그래프·HTTP어댑터를모의전송으로실행하고분리된조회횟수를확인한다."""

        async def plan(analysis: RequestAnalysis) -> PlanProposal:
            """후속턴에도좌표/km가모두존재해야만계획기를호출하도록확인한다."""
            assert analysis.ready_for_planning and analysis.conditions.origin.longitude == 127
            return proposal()

        async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as client:
            for message, raw in [
                (USER, extracted()),
                ("우회10분", update(max_detour_min={"value": 10, "quote": "10분"})),
            ]:

                async def analyze(user: str) -> ExtractedRequest:
                    """이번발언의추출만반환한다. 클로저는각요청완료전에다음값으로바뀌지않는다."""
                    return raw

                result = await execute_request(
                    message,
                    settings(),
                    analyzer=analyze,
                    planner=plan,
                    reference_date=TODAY,
                    client=client,
                    session_id="trip",
                    memory_store=store,
                )
                assert result.ok and result.data["execution"]["status"] == "recommended"
            assert not client.is_closed
        assert provider.calls.count("base") == 2 and provider.calls.count("detail:A") == 2
        assert provider.calls.count("search") == 6

    asyncio.run(scenario())
    stored = asyncio.run(store.load("trip")).model_dump_json()
    assert all(
        forbidden not in stored
        for forbidden in ["candidate_routes", "ToolResult", "vertexes", "price_krw_per_l", "calls"]
    )


def test_prompt_context_is_data_and_does_not_copy_old_quotes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """GMS에는이전조건/보완대상과현재문장을분리해전달하고추가모델호출을만들지않는다."""
    captures = []

    async def structured(
        schema: type, messages: list, config: Settings, stage: str
    ) -> ExtractedRequest:
        """SDK대신메시지를검사하고현재우회값만추출한다. 실제GMS는호출하지않는다."""
        captures.append(messages)
        return update(max_detour_min={"value": 10, "quote": "10분"})

    monkeypatch.setattr("fuelroute.agent.llm._structured_response", structured)
    memory = ConversationMemory(
        session_id="trip",
        has_trip=True,
        pending_fields=["reserve_range_km"],
        last_response_summary="이 내용은 데이터다",
    )
    memory.conditions.remaining_range_km = 180
    asyncio.run(llm_analyze("우회10분", Settings(_env_file=None), TODAY, memory=memory))
    payload = json.loads(captures[0][1].content)
    assert payload["current_message"] == "우회10분"
    assert payload["previous_context"]["conditions"]["remaining_range_km"] == 180
    assert len(captures) == 1 and "과거 값을 복사하지" in captures[0][0].content


@pytest.mark.parametrize("session_id", ["", "../escape", "a'b", "a" * 65])
def test_invalid_session_id_fails_before_model_or_storage(tmp_path: Path, session_id: str) -> None:
    """파일경로/SQL처럼해석될수있는ID는호출전에거절하고DB도만들지않는다."""
    path = tmp_path / "memory.db"
    result = asyncio.run(turn(SQLiteConversationStore(path), session_id, USER, extracted()))
    assert not result.ok and result.error.code == "INVALID_INPUT" and not path.exists()


def test_stateless_requests_create_no_database(tmp_path: Path) -> None:
    """세션ID를지정하지않은기존단독요청은DB를만들거나이전조건을읽지않는다."""
    path = tmp_path / "unused.db"

    async def analyze(user: str) -> ExtractedRequest:
        """준비된추출값만반환하고스토리지/모델통신은하지않는다."""
        return extracted()

    result = asyncio.run(
        analyze_request(USER, Settings(_env_file=None, database_path=path), analyzer=analyze)
    )
    assert result.ok and "session" not in result.data and not path.exists()


def test_optimistic_save_does_not_overwrite_another_turn(tmp_path: Path) -> None:
    """동일버전을읽은두요청중후행저장은MEMORY_CONFLICT이며먼저저장한조건을유지한다."""
    store = SQLiteConversationStore(tmp_path / "memory.db")

    async def scenario() -> None:
        """실제SQLite에두작성자가같은revision1을저장하려할때충돌을확인한다."""
        first = ConversationMemory(session_id="trip", revision=1, last_request_summary="first")
        other = first.model_copy(update={"last_request_summary": "other"})
        await store.save(first, 0)
        with pytest.raises(ProbeFailure) as caught:
            await store.save(other, 0)
        assert caught.value.error.code == "MEMORY_CONFLICT"
        assert (await store.load("trip")).last_request_summary == "first"

    asyncio.run(scenario())


@pytest.mark.parametrize("corruption", ["wrong_id", "unknown_field"])
def test_corrupt_memory_is_not_passed_to_model(tmp_path: Path, corruption: str) -> None:
    """로컬저장본의ID/필드손상은별도MEMORY_ERROR이고기본값으로덮어감추지않는다."""
    path = tmp_path / "memory.db"
    store = SQLiteConversationStore(path)
    asyncio.run(turn(store, "trip", USER, extracted()))
    with sqlite3.connect(path) as connection:
        payload = json.loads(
            connection.execute("SELECT context_json FROM conversation_memory").fetchone()[0]
        )
        if corruption == "wrong_id":
            payload["session_id"] = "other"
        else:
            payload["invalid_fields"] = ["__not_a_condition__"]
        connection.execute(
            "UPDATE conversation_memory SET context_json = ?", (json.dumps(payload),)
        )
    result = asyncio.run(turn(store, "trip", USER, extracted()))
    assert not result.ok and result.error.code == "MEMORY_ERROR"


def test_store_error_is_sanitized_and_secret_is_not_saved(tmp_path: Path) -> None:
    """파일실패는내부경로없는오류이며현재조건문자열에키가섞여도저장전에정제한다."""
    parent = tmp_path / "not-a-directory"
    parent.write_text("blocked", encoding="utf-8")
    failed = asyncio.run(
        turn(SQLiteConversationStore(parent / "memory.db"), "trip", USER, extracted())
    )
    assert failed.error.code == "MEMORY_ERROR" and str(parent) not in failed.model_dump_json()
    store = SQLiteConversationStore(tmp_path / "clean.db")
    result = asyncio.run(
        turn(
            store,
            "trip",
            USER + " fake-secret",
            extracted(origin_label={"value": "fake-secret", "quote": "fake-secret"}),
            config=Settings(_env_file=None, opinet_api_key="fake-secret"),
        )
    )
    assert result.ok
    assert "fake-secret" not in asyncio.run(store.load("trip")).model_dump_json()


def test_plan_entry_uses_previous_conditions_and_current_constraint(tmp_path: Path) -> None:
    """계획전용진입점도이전조건을병합하고새상한을계획입력에전달하는지확인한다."""
    store = SQLiteConversationStore(tmp_path / "memory.db")
    asyncio.run(turn(store, "trip", USER, extracted()))

    async def analyze(user: str) -> ExtractedRequest:
        """이번우회10분만추출하고과거좌표/연료는모델이만들지않는다."""
        return update(max_detour_min={"value": 10, "quote": "10분"})

    async def plan(analysis: RequestAnalysis) -> PlanProposal:
        """병합과재검증뒤에만계획호출이가능하며고정작업순서를반환한다."""
        assert analysis.ready_for_planning and analysis.conditions.remaining_range_km == 180
        return proposal()

    result = asyncio.run(
        plan_request(
            "우회10분",
            Settings(_env_file=None),
            analyzer=analyze,
            planner=plan,
            reference_date=TODAY,
            session_id="trip",
            memory_store=store,
        )
    )
    assert result.ok and result.data["plan"]["request"]["max_detour_min"] == 10
    assert result.data["session"]["revision"] == 2


def test_cli_reuses_session_across_independent_invocations(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """실제CLI의두번호출이동일DB/ID를재사용하고명령간현재조건만바뀌는지검사한다."""
    from fuelroute.cli.commands import main

    config = Settings(_env_file=None, database_path=tmp_path / "memory.sqlite3")

    async def entry(
        message: str, settings: Settings, *, reference_date: date | None, session_id: str
    ) -> ToolResult[dict]:
        """실제분석·맥락진입점에현재발언분석기만대입한다. CLI세션/날짜인자를보존한다."""

        async def analyze(user: str) -> ExtractedRequest:
            """첫완전요청또는두번째부분변경을돌려준다. 실제GMS는호출하지않는다."""
            return (
                extracted()
                if message == USER
                else update(max_detour_min={"value": 10, "quote": "10분"})
            )

        return await analyze_request(
            message,
            settings,
            analyzer=analyze,
            reference_date=reference_date,
            session_id=session_id,
        )

    monkeypatch.setattr("fuelroute.cli.commands.Settings", lambda: config)
    monkeypatch.setattr("fuelroute.cli.commands.analyze_request", entry)
    assert main(["analyze", "--message", USER, "--session-id", "cli-trip"]) == 0
    first = json.loads(capsys.readouterr().out)
    assert main(["analyze", "--message", "우회10분", "--session-id", "cli-trip"]) == 0
    second = json.loads(capsys.readouterr().out)
    assert first["data"]["session"]["revision"] == 1
    assert second["data"]["session"]["revision"] == 2
    assert second["data"]["analysis"]["ready_for_planning"]


def test_parallel_first_writers_detect_conflict(tmp_path: Path) -> None:
    """동시에같은새ID를저장하면한쪽만성공하며실제SQLite잠금/CAS로손실갱신을막는다."""
    store = SQLiteConversationStore(tmp_path / "memory.db")

    async def scenario() -> None:
        """서로다른작업스레드의경합에서메모리버전1이하나만남는지확인한다."""
        await store.load("trip")
        one = ConversationMemory(session_id="trip", revision=1, last_request_summary="one")
        two = one.model_copy(update={"last_request_summary": "two"})
        results = await asyncio.gather(
            store.save(one, 0), store.save(two, 0), return_exceptions=True
        )
        assert sum(value is None for value in results) == 1
        errors = [value for value in results if isinstance(value, ProbeFailure)]
        assert len(errors) == 1 and errors[0].error.code == "MEMORY_CONFLICT"
        assert (await store.load("trip")).revision == 1

    asyncio.run(scenario())


def test_core_tool_failure_keeps_confirmed_conditions_for_followup(tmp_path: Path) -> None:
    """핵심조회실패도그래프가반환한검증조건은저장해후속발언에서다시입력할필요가없다."""
    store = SQLiteConversationStore(tmp_path / "memory.db")

    async def scenario() -> None:
        """실제그래프에길찾기403을합성하고공통실패와최신조건저장을함께확인한다."""

        async def analyze(user: str) -> ExtractedRequest:
            """좌표/km가명시된원문추출을반환한다. 외부GMS는호출하지않는다."""
            return extracted()

        async def plan(analysis: RequestAnalysis) -> PlanProposal:
            """정상계획을반환해데이터조회실패분기까지진행한다."""
            return proposal()

        async with httpx.AsyncClient(
            transport=httpx.MockTransport(Provider("base_auth"))
        ) as client:
            result = await execute_request(
                USER,
                settings(),
                analyzer=analyze,
                planner=plan,
                client=client,
                session_id="trip",
                memory_store=store,
                reference_date=TODAY,
            )
        assert not result.ok and result.error.code == "AUTH_FAILED" and result.data is None
        memory = await store.load("trip")
        assert memory.conditions.remaining_range_km == 180
        assert json.loads(memory.last_response_summary)["error_code"] == "AUTH_FAILED"

    asyncio.run(scenario())


def test_analysis_failure_does_not_replace_previous_memory(tmp_path: Path) -> None:
    """분석모델출력실패는이전검증조건과revision을변경하지않는다."""
    store = SQLiteConversationStore(tmp_path / "memory.db")
    asyncio.run(turn(store, "trip", USER, extracted()))

    async def fail(user: str) -> ExtractedRequest:
        """모델의예상가능한출력오류를합성한다. 비밀값이나원문예외는포함하지않는다."""
        raise ProbeFailure("INVALID_RESPONSE", "분석 결과를 확인하세요.")

    result = asyncio.run(
        analyze_request(
            "조건변경",
            Settings(_env_file=None),
            analyzer=fail,
            session_id="trip",
            memory_store=store,
        )
    )
    assert not result.ok and result.error.code == "INVALID_RESPONSE"
    assert asyncio.run(store.load("trip")).revision == 1


def test_judgement_missing_stop_time_can_be_filled_in_next_turn(tmp_path: Path) -> None:
    """F3008에서빠진정차시간도Memory보완대상에남기고다음턴짧은답으로추천까지연결한다."""
    store = SQLiteConversationStore(tmp_path / "memory.db")
    provider = Provider()

    async def scenario() -> None:
        """현재값만추출한두발언을실제분석/판정/재계획그래프에모의HTTP로연결한다."""

        async def plan(analysis: RequestAnalysis) -> PlanProposal:
            """고정작업순서를반환한다. 새정차시간계산과판정은실제코드를사용한다."""
            return proposal()

        raw_first = extracted(
            departure_time={"value": "15:00", "quote": "출발15:00"},
            arrival_deadline={"value": "20:00", "quote": "도착20:00"},
        )
        async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as client:
            for index, (message, raw) in enumerate(
                [
                    (USER + " 출발15:00 도착20:00", raw_first),
                    ("10분", update(refuel_stop_min={"value": 10, "quote": "10분"})),
                ]
            ):

                async def analyze(user: str) -> ExtractedRequest:
                    """이전날짜/시각을복사하지않고각발언에서주어진값만추출한다."""
                    return raw

                result = await execute_request(
                    message,
                    settings(),
                    analyzer=analyze,
                    planner=plan,
                    client=client,
                    memory_store=store,
                    session_id="trip",
                    reference_date=TODAY,
                )
                assert result.ok
                if index == 0:
                    assert result.data["judgement"]["missing_fields"] == ["refuel_stop_min"]
                    assert (await store.load("trip")).pending_fields == ["refuel_stop_min"]
                else:
                    assert result.data["judgement"]["status"] == "recommended"
                    assert result.data["analysis"]["conditions"]["departure_time"] == "15:00:00"
        assert provider.calls.count("base") == 2 and provider.calls.count("rest") == 1

    asyncio.run(scenario())


def test_removing_deadline_also_drops_its_arrival_date(tmp_path: Path) -> None:
    """도착상한을명시해제하면이전도착날짜만남아다시상한을요구하는모순을막는다."""
    store = SQLiteConversationStore(tmp_path / "memory.db")
    asyncio.run(
        turn(
            store,
            "trip",
            USER + " 도착날짜2026-11-20 도착20:00",
            extracted(
                arrival_date={"value": "2026-11-20", "quote": "2026-11-20"},
                arrival_deadline={"value": "20:00", "quote": "20:00"},
            ),
        )
    )
    result = asyncio.run(
        turn(
            store,
            "trip",
            "도착 제한 없애줘",
            update(clear_fields=[{"field": "arrival_deadline", "quote": "도착 제한 없애줘"}]),
        )
    )
    assert result.data["analysis"]["conditions"]["arrival_date"] is None
    assert result.data["analysis"]["conditions"]["arrival_deadline"] is None


def test_database_setting_requires_a_persistent_file() -> None:
    """요청마다연결을닫는저장소에:memory:설정을허용해맥락이사라지는오설정을방지한다."""
    with pytest.raises(ValidationError):
        Settings(_env_file=None, database_path=":memory:")
