"""F3009 실제 그래프·실행기·HTTP 어댑터의 재사용·상한·실패를 모의 제공사로 검증한다."""

import asyncio
from collections import Counter

import httpx
import pytest
from test_execution import TODAY, USER, Provider, extracted, proposal, settings

from fuelroute.agent.execution import PlanExecutionSession, enrich_rest_area
from fuelroute.agent.graph import Replanner, execute_request, make_graph
from fuelroute.agent.planning import finalize_plan
from fuelroute.calculations.constraints import judge_execution
from fuelroute.core.errors import ProbeFailure
from fuelroute.schemas.analysis import ExtractedRequest, RequestAnalysis
from fuelroute.schemas.execution import PlanExecution
from fuelroute.schemas.judgement import TripJudgement
from fuelroute.schemas.planning import PlanProposal, WorkPlan
from fuelroute.schemas.replanning import ReplanDecision, ReplanOutcome
from fuelroute.schemas.requests import PlanningRequest


class ReplanProvider(Provider):
    """첫 검색·추가 검색과 후보별 실제 도달 실패를 분리하는 합성 API 제공사."""

    def __init__(self, mode: str = "cached") -> None:
        """mode와 요청 종류 목록만 저장한다. 실제 키·위치·HTTP·DB는 사용하지 않는다.

        cached는 A제외/B성공, expand는 추가검색에서만B, twice는 추가B제외후C성공이다.
        failures는개별조회실패, limit/empty는상한종료, shared는다른ID동일경로재사용이다.
        """
        super().__init__("shared_coordinate" if mode.startswith("shared") else "normal")
        self.scenario = mode
        self.search_count = 0

    def __call__(self, request: httpx.Request) -> httpx.Response:
        """기존 합성 어댑터 응답을 바탕으로 검색점 확장·도달·인증/한도 상황을 만든다.

        입력 request는모의transport에서만받고공개요청종류만저장한다. 검색응답은KATEC,
        경로는m/초이며세션의실제변환·검증·판정이실행된다. 부작용은메모리호출기록뿐이다.
        """
        path = request.url.path
        if "around" in path.lower():
            self.search_count += 1
            self.calls.append("search")
            if self.scenario == "search_timeout":
                raise httpx.ReadTimeout("synthetic timeout", request=request)
            if self.scenario == "empty":
                ids = []
            elif self.scenario in {"expand", "expand_auth", "twice"}:
                ids = (
                    ["A"]
                    if self.search_count <= 3
                    else (["B", "C"] if self.scenario == "twice" else ["B"])
                )
                if self.scenario == "expand_auth" and self.search_count == 4:
                    return httpx.Response(403, json={})
            elif self.scenario == "limit":
                ids = list("ABCDEFGHIJ")
            elif self.scenario == "production":
                ids = list("ABCD")
            else:
                ids = ["A", "B"]
            return httpx.Response(200, json={"RESULT": {"OIL": [self.station(i) for i in ids]}})
        response = super().__call__(request)
        if "detail" in path.lower() and request.url.params["id"] == "A":
            if self.scenario == "detail_timeout":
                raise httpx.ReadTimeout("synthetic timeout", request=request)
            if self.scenario in {"detail_auth", "detail_rate"}:
                return httpx.Response(403 if self.scenario == "detail_auth" else 429, json={})
        if not path.endswith("directions"):
            return response
        via = request.url.params.get("waypoints")
        longitude = float((via or request.url.params["destination"]).split(",")[0])
        if not via and longitude >= 127.1:
            if self.scenario == "base_auth":
                return httpx.Response(403, json={})
            return response
        if self.scenario == "shared_failure":
            return httpx.Response(200, json={"routes": [{"result_code": 104}]})
        excluded = self.scenario == "limit" or (
            longitude
            < (
                127.035
                if self.scenario == "production"
                else (127.025 if self.scenario == "twice" else 127.015)
            )
            and self.scenario not in {"time", "shared_time", "detail_timeout"}
        )
        if not via and excluded:
            body = response.json()
            route = body["routes"][0]
            route["summary"]["distance"] = 151000
            route["sections"][0]["distance"] = 151000
            route["sections"][0]["roads"][0]["distance"] = 151000
            return httpx.Response(200, json=body)
        return response


def drive(
    monkeypatch: pytest.MonkeyPatch,
    mode: str = "cached",
    *,
    limits: dict | None = None,
    raw: ExtractedRequest | None = None,
    user: str = USER,
    replanner_override: Replanner | None = None,
) -> tuple[dict, ReplanProvider, Counter]:
    """실제 make_graph·Session·judge·enrich를 모의 HTTP/모델과 연결해 최종 State를 얻는다.

    limits는검증된실행정책만변경하며사용자조건은바꾸지않는다. 모델호출은메모리카운터,
    제공사조회는MockTransport다. 실제네트워크·키·DB는없고동일계산/캐시/상한을통과한다.
    반환은State·제공사·모델회수다. 예:max_candidates=1이면A제외후B를다음회차에서평가한다.
    """

    def build(analysis: RequestAnalysis, proposed: PlanProposal) -> WorkPlan:
        """실제 계획 검증 뒤 테스트할 고정 예산만 바꾼다. 입력·Tool·계산은 대체하지 않는다."""
        plan = finalize_plan(analysis, proposed)
        if plan.request is not None and limits:
            plan.request = PlanningRequest.model_validate({**plan.request.model_dump(), **limits})
        return plan

    monkeypatch.setattr("fuelroute.agent.graph.finalize_plan", build)
    provider = ReplanProvider(mode)
    models = Counter()

    async def analyze(message: str) -> ExtractedRequest:
        """GMS 대신 명시한 원문 추출을 반환하고 분석 호출 수만 기록한다."""
        models["analyze"] += 1
        return raw or extracted()

    async def plan(analysis: RequestAnalysis) -> PlanProposal:
        """계획 제안 한 번만 기록한다. 반복 회차에서 호출되면 카운터로 검출한다."""
        models["plan"] += 1
        return proposal()

    async def scenario() -> dict:
        """한 요청의 세션·클라이언트 수명을 소유하고 실제 그래프를 끝까지 실행한다."""
        config = settings()
        async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as client:
            session = PlanExecutionSession(config, client)

            async def enrich(value: WorkPlan, result: PlanExecution) -> PlanExecution:
                """마지막 확정 후보만 실제 보조 어댑터에 전달한다. 전송은 모의다."""
                return await enrich_rest_area(result, value.request, config, client)

            graph = make_graph(
                analyze,
                TODAY,
                llm_plan_fn=plan,
                executor=session.start,
                judge=judge_execution,
                enricher=enrich,
                replanner=replanner_override or session.replan,
            )
            return await graph.ainvoke({"user": user})

    return asyncio.run(scenario()), provider, models


@pytest.mark.parametrize(
    "mode,expected_count,expected_search", [("cached", 1, 3), ("expand", 1, 4), ("twice", 2, 4)]
)
def test_replan_reuses_then_expands_and_judges_each_round(
    monkeypatch: pytest.MonkeyPatch, mode: str, expected_count: int, expected_search: int
) -> None:
    """후보재사용·검색점확장·확장뒤캐시평가가실제성공하고중복조회/추가모델이없는지검사한다."""
    state, provider, models = drive(monkeypatch, mode, limits={"max_candidates": 1})
    progress = state["replanning"]
    assert progress["replan_count"] == expected_count
    assert progress["stop_reason"] == "recommendation_found"
    assert provider.calls.count("search") == expected_search
    assert provider.calls.count("base") == 1
    assert provider.calls.count("detail:A") == 1
    assert provider.calls[-1] == "rest" and provider.calls.count("rest") == 1
    assert models == {"analyze": 1, "plan": 1}
    assert state["execution"]["status"] == "recommended"
    assert not state["execution"]["replan_needed"]
    assert len(progress["rounds"]) == expected_count + 1
    assert sum(row["tool_calls_added"] for row in progress["rounds"]) == len(
        state["execution"]["calls"]
    )
    assert state["results"]["rank_candidates"]["recommended"]["station_id"] == (
        "C" if mode == "twice" else "B"
    )
    if mode == "cached":
        assert progress["rounds"][1]["trigger"] == "evaluate_cached"
        assert progress["rounds"][1]["search_points"] == []
    assert state["execution"]["calls"][-1]["round_index"] == expected_count


@pytest.mark.parametrize(
    "limits,count,evaluated,reason",
    [
        ({"max_replans": 0, "max_candidates": 1}, 0, 1, "replan_limit_reached"),
        ({"max_replans": 1, "max_candidates": 1}, 1, 2, "replan_limit_reached"),
        ({"max_candidates": 1}, 2, 3, "replan_limit_reached"),
        ({"max_candidates": 3, "max_evaluations": 4}, 1, 4, "evaluation_budget_exhausted"),
        ({"max_candidates": 3, "max_evaluations": 9}, 2, 9, "evaluation_budget_exhausted"),
        ({"max_candidates": 3, "max_evaluations": 12}, 2, 9, "replan_limit_reached"),
    ],
)
def test_round_and_evaluation_caps_are_global(
    monkeypatch: pytest.MonkeyPatch, limits: dict, count: int, evaluated: int, reason: str
) -> None:
    """최초/추가회차에같은전역상한을적용하고실패후보도예산을사용하며0/1/2회에서멈춘다."""
    state, provider, models = drive(monkeypatch, "limit", limits=limits)
    assert state["replanning"]["replan_count"] == count
    assert state["replanning"]["stop_reason"] == reason
    assert len(state["results"]["evaluate_constraints"]) == evaluated
    assert len([name for name in provider.calls if name.startswith("detail:")]) == evaluated
    assert provider.calls.count("base") == 1 and provider.calls.count("search") == 3
    assert "rest" not in provider.calls and state["replanning"]["next_input"]
    assert models == {"analyze": 1, "plan": 1}


def test_empty_search_changes_points_once_per_round(monkeypatch: pytest.MonkeyPatch) -> None:
    """0건은동일요청재시도없이서로다른앞쪽검색점두곳만추가하고상한에서종료한다."""
    state, provider, _ = drive(monkeypatch, "empty")
    assert provider.calls.count("search") == 5
    assert state["replanning"]["replan_count"] == 2
    assert state["replanning"]["stop_reason"] == "replan_limit_reached"
    points = state["results"]["search_points"]
    assert len({(p["longitude"], p["latitude"]) for p in points}) == 5
    assert all(p["route_distance_m"] <= 150000 for p in points)
    assert state["results"]["evaluate_constraints"] == []


@pytest.mark.parametrize(
    "mode,code",
    [
        ("base_auth", "AUTH_FAILED"),
        ("detail_auth", "AUTH_FAILED"),
        ("detail_rate", "RATE_LIMITED"),
        ("search_timeout", "TIMEOUT"),
        ("expand_auth", "AUTH_FAILED"),
    ],
)
def test_core_failure_stops_without_retry_or_enrichment(
    monkeypatch: pytest.MonkeyPatch, mode: str, code: str
) -> None:
    """인증·한도·검색통신실패는멈추고확장중핵심실패도누적이력과종료사유를보존한다."""
    state, provider, _ = drive(monkeypatch, mode, limits={"max_candidates": 1})
    assert state["execution"]["error"]["code"] == code
    assert state["replanning"]["stop_reason"] == "core_error"
    assert state["replanning"]["replan_count"] == (1 if mode == "expand_auth" else 0)
    assert "rest" not in provider.calls
    assert provider.calls.count("detail:A") <= 1


def test_individual_timeout_uses_other_candidate_without_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """상세타임아웃A를재시도하지않고다음회차B로성공하며이전실패를보존한다."""
    state, provider, _ = drive(monkeypatch, "detail_timeout", limits={"max_candidates": 1})
    assert state["replanning"]["replan_count"] == 1
    assert provider.calls.count("detail:A") == 1
    assert state["results"]["rank_candidates"]["recommended"]["station_id"] == "B"
    assert "TIMEOUT" in state["results"]["evaluate_constraints"][0]["reason_codes"]


@pytest.mark.parametrize("mode", ["shared_failure", "shared_time"])
def test_route_success_and_failure_cache_survives_rounds(
    monkeypatch: pytest.MonkeyPatch, mode: str
) -> None:
    """다른ID가동일좌표면회차가달라도접근/경유성공또는실패를재사용한다."""
    raw = extracted()
    user = USER
    if mode == "shared_time":
        user += " 출발15:00 도착15:01 주유0분"
        raw = extracted(
            departure_time={"value": "15:00", "quote": "출발15:00"},
            arrival_deadline={"value": "15:01", "quote": "도착15:01"},
            refuel_stop_min={"value": 0, "quote": "주유0분"},
        )
    state, provider, _ = drive(monkeypatch, mode, limits={"max_candidates": 1}, raw=raw, user=user)
    assert provider.calls.count("approach") == 1
    assert provider.calls.count("via") == (1 if mode == "shared_time" else 0)
    assert state["execution"]["cache_hits"] >= 1
    assert state["execution"]["status"] == "no_recommendation"
    assert "rest" not in provider.calls


def test_missing_time_input_stops_before_any_additional_batch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """정차시간누락은후보가남아도검색·평가추가회차없이필요한값만질문한다."""
    user = USER + " 출발15:00 도착20:00"
    raw = extracted(
        departure_time={"value": "15:00", "quote": "출발15:00"},
        arrival_deadline={"value": "20:00", "quote": "도착20:00"},
    )
    state, provider, _ = drive(
        monkeypatch, "time", limits={"max_candidates": 1}, raw=raw, user=user
    )
    assert state["replanning"]["replan_count"] == 0
    assert state["replanning"]["stop_reason"] == "needs_user_input"
    assert state["judgement"]["missing_fields"] == ["refuel_stop_min"]
    assert state["replanning"]["next_input"] == state["judgement"]["questions"]
    assert provider.calls.count("detail:B") == 0 and "rest" not in provider.calls


def test_production_entry_activates_the_request_scoped_replanner() -> None:
    """생산execute_request가세션을연결하고분석/계획만대역일때재계획을생략하지않는지검사한다."""
    provider = ReplanProvider("production")

    async def analyze(user: str) -> ExtractedRequest:
        """준비된명시좌표요청을반환한다. 실제GMS나Local은호출하지않는다."""
        return extracted()

    async def plan(analysis: RequestAnalysis) -> PlanProposal:
        """고정순서만제안한다. 실제계획검증과기본예산은유지한다."""
        return proposal()

    async def scenario() -> None:
        """생산 진입점에서 최초 세 후보가 제외된 뒤 추가 회차 D를 추천하는지 확인한다."""
        async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as client:
            result = await execute_request(
                USER,
                settings(),
                analyzer=analyze,
                planner=plan,
                client=client,
                reference_date=TODAY,
            )
            assert not client.is_closed
        assert result.ok and result.data["replanner_mode"] == "bounded_tools"
        assert result.data["replanning"]["stop_reason"] == "recommendation_found"
        assert result.data["replanning"]["replan_count"] == 1
        assert result.data["results"]["rank_candidates"]["recommended"]["station_id"] == "D"
        assert "replan" in result.data["graph_nodes"]

    asyncio.run(scenario())


def test_short_safe_segment_exhausts_search_space(monkeypatch: pytest.MonkeyPatch) -> None:
    """안전구간1km·반경5km의검색점이출발점하나이면같은점을재호출하지않고종료한다."""
    user = USER.replace("180km", "6km").replace("30km", "5km")
    raw = extracted(
        remaining_range_km={"value": 6, "quote": "6km"},
        reserve_range_km={"value": 5, "quote": "5km"},
    )
    state, provider, _ = drive(monkeypatch, "empty", user=user, raw=raw)
    assert state["replanning"]["replan_count"] == 0
    assert state["replanning"]["stop_reason"] == "search_space_exhausted"
    assert provider.calls == ["base", "search"]


@pytest.mark.parametrize("missing", [False, True])
def test_unready_input_has_no_execution_or_replan_calls(
    monkeypatch: pytest.MonkeyPatch, missing: bool
) -> None:
    """인사/필수값누락은계획·데이터API·재계획실행없이0회와입력/생략사유를반환한다."""
    raw = (
        extracted(reserve_range_km=None)
        if missing
        else ExtractedRequest(intent="greeting", reason="인사")
    )
    state, provider, models = drive(monkeypatch, raw=raw, user=USER if missing else "안녕")
    assert provider.calls == [] and models == {"analyze": 1}
    assert state["replanning"]["stop_reason"] == ("needs_user_input" if missing else "not_required")
    assert state["replanning"]["replan_count"] == 0 and state["replanning"]["rounds"] == []


def test_graph_rejects_replanner_with_no_new_work(monkeypatch: pytest.MonkeyPatch) -> None:
    """주입재계획기가기존결과를새회차처럼돌려주면추가반복대신출력계약오류로중단한다."""

    async def unchanged(
        plan: WorkPlan, execution: PlanExecution, judgement: TripJudgement, count: int
    ) -> ReplanOutcome:
        """고의로진행없는결과를반환한다. API/LLM/변경은없고그래프방어검사용이다."""
        return ReplanOutcome(
            decision=ReplanDecision(action="evaluate_cached", reason="synthetic"),
            execution=execution,
        )

    with pytest.raises(ProbeFailure) as caught:
        drive(monkeypatch, limits={"max_candidates": 1}, replanner_override=unchanged)
    assert caught.value.error.code == "INVALID_RESPONSE"


def test_graph_checks_zero_cap_before_injected_replanner(monkeypatch: pytest.MonkeyPatch) -> None:
    """추가횟수0의상한은주입함수의정책과무관하게그래프에서호출전에검사한다."""

    async def forbidden(
        plan: WorkPlan, execution: PlanExecution, judgement: TripJudgement, count: int
    ) -> ReplanOutcome:
        """호출되면실패한다. 그래프의상한방어가실제재계획호출을막아야한다."""
        raise AssertionError("replanning forbidden")

    state, _, _ = drive(
        monkeypatch, limits={"max_candidates": 1, "max_replans": 0}, replanner_override=forbidden
    )
    assert state["replanning"]["stop_reason"] == "replan_limit_reached"


def test_sessions_are_isolated_and_reject_replay_or_changed_request() -> None:
    """클라이언트를공유해도요청별캐시는독립이며중복시작/다른계획/잘못된회차는거절한다."""
    from test_execution import work_plan

    async def scenario() -> None:
        """실제세션두개를같은모의클라이언트에서실행하고불일치시추가HTTP가없는지확인한다."""
        provider = ReplanProvider()
        async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as client:
            first = PlanExecutionSession(settings(), client)
            second = PlanExecutionSession(settings(), client)
            plan = work_plan(max_candidates=1)
            execution = await first.start(plan)
            await second.start(plan)
            before = list(provider.calls)
            with pytest.raises(ProbeFailure):
                await first.start(plan)
            changed = plan.model_copy(deep=True)
            changed.request.max_replans = 0
            from fuelroute.agent.analysis import analyze_conditions

            analysis = analyze_conditions(USER, extracted(), TODAY)
            judgement = judge_execution(analysis, plan, execution)
            for request, count in [(changed, 0), (plan, 1)]:
                with pytest.raises(ProbeFailure):
                    await first.replan(request, execution, judgement, count)
            assert provider.calls == before and provider.calls.count("base") == 2
            assert provider.calls.count("detail:A") == 2

    asyncio.run(scenario())


def test_repeated_time_judgement_does_not_turn_old_reasons_into_provider_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """회차마다시간판정해도이전판정코드를조회오류로누적하지않고동일근거를반환한다."""

    user = USER + " 출발15:00 도착15:01 주유0분"
    raw = extracted(
        departure_time={"value": "15:00", "quote": "출발15:00"},
        arrival_deadline={"value": "15:01", "quote": "도착15:01"},
        refuel_stop_min={"value": 0, "quote": "주유0분"},
    )
    state, _, _ = drive(
        monkeypatch, "shared_time", limits={"max_candidates": 1}, raw=raw, user=user
    )
    repeated = judge_execution(
        RequestAnalysis.model_validate(state["analysis"]),
        WorkPlan.model_validate(state["plan"]),
        PlanExecution.model_validate(state["execution"]),
    )
    assert repeated == TripJudgement.model_validate(state["judgement"])
    assert all(
        check.code != "LOOKUP_ERROR"
        for candidate in repeated.candidates
        for check in candidate.checks
    )
