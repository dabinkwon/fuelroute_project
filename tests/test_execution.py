"""F3007의 실제 HTTP 어댑터·작업 순서·부분 실패·그래프/CLI 연결을 모의 전송으로 검증한다."""

import asyncio
from datetime import date

import httpx
import pytest

from fuelroute.config import Settings
from fuelroute.coordinates import wgs84_to_katec
from fuelroute.execution import PlanExecution, execute_work_plan
from fuelroute.probes import ProbeFailure
from fuelroute.request_analysis import ExtractedRequest, analyze_conditions
from fuelroute.task_plan import PlanProposal, WorkPlan, finalize_plan

TODAY = date(2026, 11, 19)
USER = "출발(127,37)에서 목적(127.2,37)으로 휘발유 주유 추천해줘. 주행 가능180km, 안전 여유30km야."
ORDER = [
    "base_route",
    "safe_range",
    "search_points",
    "search_candidates",
    "station_details",
    "candidate_routes",
    "evaluate_constraints",
    "rank_candidates",
    "rest_area_info",
]


def settings(*, rest_key: bool = True) -> Settings:
    """.env 없는 합성 제공사 설정을 반환한다. 실제 인증키가 아니다."""
    return Settings(
        _env_file=None,
        kakao_rest_api_key="fake-kakao",
        opinet_api_key="fake-opinet",
        data_go_kr_service_key="fake-rest" if rest_key else None,
    )


def extracted(**changes: object) -> ExtractedRequest:
    """원문에 있는 WGS84 좌표·km 값만 합성한다. GMS/Local 조회는 하지 않는다."""
    return ExtractedRequest.model_validate(
        {
            "intent": "recommend",
            "reason": "주유 추천 요청",
            "origin": {"value": {"longitude": 127, "latitude": 37}, "quote": "출발(127,37)"},
            "destination": {
                "value": {"longitude": 127.2, "latitude": 37},
                "quote": "목적(127.2,37)",
            },
            "fuel_type": {"value": "gasoline", "quote": "휘발유"},
            "remaining_range_km": {"value": 180, "quote": "주행 가능180km"},
            "reserve_range_km": {"value": 30, "quote": "안전 여유30km"},
            **changes,
        }
    )


def proposal() -> PlanProposal:
    """독립적으로 명시한 아홉 작업을 계획 대역으로 반환한다. 실제 모델을 호출하지 않는다."""
    return PlanProposal(
        reason="경로/후보/상세/비용/보조정보 순서",
        steps=[{"action": name, "reason": "후속 작업에 필요한 결과"} for name in ORDER],
    )


def work_plan(**limits: int) -> WorkPlan:
    """실제 분석/계획 검증 함수로 ready 계획을 만든다. 테스트용 실행 상한만 명시적으로 바꾼다."""
    plan = finalize_plan(analyze_conditions(USER, extracted(), TODAY), proposal())
    plan.request = type(plan.request).model_validate({**plan.request.model_dump(), **limits})
    return plan


def route_payload(request: httpx.Request, *, mode: str = "normal") -> dict:
    """요청 목적/경유 좌표를 사용해 거리 합계와 기하가 맞는 합성 자동차 경로를 만든다.

    도로 보고 거리는 직선 길이와 의도적으로 다르다. 안전 판정이 검색점/직선거리 대신
    실제 경로 비용을 사용하도록 기본200km·접근90/100km·우회1/2km를 합성한다.
    """
    origin = tuple(map(float, request.url.params["origin"].split(",")))
    destination = tuple(map(float, request.url.params["destination"].split(",")))
    via_raw = request.url.params.get("waypoints")
    via = tuple(map(float, via_raw.split(","))) if via_raw else None
    is_a = (via or destination)[0] < 127.015
    if via:
        distance = 201000 if is_a else 202000
        duration = 12060 if is_a else 12120
        first = 90000 if is_a else 100000
        points = [origin, via, destination]
        lengths = [first, distance - first]
    else:
        distance = 200000 if destination[0] > 127.1 else (90000 if is_a else 100000)
        if (
            mode in {"unreachable", "all_excluded"}
            and (is_a or mode == "all_excluded")
            and destination[0] < 127.1
        ):
            distance = 151000
        duration = 12000 if destination[0] > 127.1 else 5000
        points = [origin, destination]
        lengths = [distance]
    sections = [
        {
            "distance": length,
            "duration": duration // len(lengths),
            "roads": [{"distance": length, "vertexes": [*start, *end]}],
        }
        for start, end, length in zip(points, points[1:], lengths)
    ]
    return {
        "routes": [
            {
                "result_code": 0,
                "summary": {"distance": distance, "duration": duration},
                "sections": sections,
            }
        ]
    }


class Provider:
    """실제 어댑터의 제공사 응답 대역. 요청 종류만 기록하며 인증 URL은 저장하지 않는다."""

    def __init__(self, mode: str = "normal") -> None:
        """검증 모드와 빈 공개 요청 목록을 보관한다. 파일·네트워크·DB는 사용하지 않는다."""
        self.mode = mode
        self.calls: list[str] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        """경로/Opinet/휴게소 HTTP를 합성하고 실패·누락·캐시 재사용을 확인한다."""
        path = request.url.path
        if path.endswith("directions"):
            via = request.url.params.get("waypoints")
            destination = request.url.params["destination"]
            stage = "via" if via else ("base" if destination.startswith("127.2,") else "approach")
            is_a = float((via or destination).split(",")[0]) < 127.015
            self.calls.append(stage)
            if self.mode == stage + "_auth":
                return httpx.Response(403, json={})
            if self.mode == "base_bad_geometry" and stage == "base":
                body = route_payload(request)
                body["routes"][0]["sections"][0]["roads"] = []
                return httpx.Response(200, json=body)
            if (
                self.mode == "base_no_route"
                and stage == "base"
                or self.mode == stage + "_no_route"
                and is_a
            ):
                return httpx.Response(200, json={"routes": [{"result_code": 104}]})
            body = route_payload(request, mode=self.mode)
            if self.mode == "bad_legs" and stage == "via" and is_a:
                body["routes"][0]["sections"] = body["routes"][0]["sections"][:1]
            return httpx.Response(200, json=body)
        if "around" in path.lower():
            self.calls.append("search")
            if self.mode == "search_auth":
                return httpx.Response(401, json={})
            if self.mode == "search_timeout":
                raise httpx.ReadTimeout("fake-opinet", request=request)
            if self.mode == "search_malformed":
                return httpx.Response(200, json={"RESULT": {"OIL": [{"PRICE": 1700}]}})
            ids = [] if self.mode == "empty_candidates" else ["A", "B"]
            if self.mode == "budget":
                ids += ["C", "D"]
            return httpx.Response(
                200, json={"RESULT": {"OIL": [self.station(name) for name in ids]}}
            )
        if "detail" in path.lower():
            station_id = request.url.params["id"]
            self.calls.append("detail:" + station_id)
            if station_id == "A":
                if self.mode == "detail_auth":
                    return httpx.Response(403, json={})
                if self.mode == "detail_rate":
                    return httpx.Response(429, json={})
                if self.mode == "detail_timeout":
                    raise httpx.ReadTimeout("fake-opinet", request=request)
                if self.mode == "detail_not_found":
                    return httpx.Response(200, json={"RESULT": {"OIL": []}})
                if self.mode == "detail_malformed":
                    return httpx.Response(200, json={"RESULT": {"OIL": [{}]}})
            row = self.station(station_id)
            row["OIL_PRICE"] = (
                []
                if self.mode == "missing_price" and station_id == "A"
                else [
                    {
                        "PRODCD": "B027",
                        "PRICE": row["PRICE"],
                        "TRADE_DT": "20261119",
                        "TRADE_TM": "120000",
                    }
                ]
            )
            row["NEW_ADR"] = "합성 주소"
            if self.mode == "id_mismatch" and station_id == "A":
                row["UNI_ID"] = "WRONG"
            return httpx.Response(200, json={"RESULT": {"OIL": [row]}})
        assert "rest_area" in path
        self.calls.append("rest")
        if self.mode == "rest_error":
            return httpx.Response(503, json={})
        item = {
            "entrpsNm": "합성 휴게소",
            "longitude": "127.01",
            "latitude": "37",
            "toiletYn": "Y",
            "oltYn": "",
            "referenceDate": "2026-11-01",
        }
        items = [] if self.mode == "rest_empty" else [item]
        return httpx.Response(
            200,
            json={
                "response": {
                    "header": {"resultCode": "00"},
                    "body": {"totalCount": len(items), "items": items},
                }
            },
        )

    def station(self, station_id: str) -> dict:
        """요청 ID에 맞는 KATEC 위치·가격을 합성한다. 실제 제공사 데이터가 아니다."""
        index = ord(station_id) - ord("A")
        longitude = 127.01 if self.mode == "shared_coordinate" else 127.01 + index * 0.01
        x, y = wgs84_to_katec(longitude, 37)
        return {
            "UNI_ID": station_id,
            "OS_NM": "합성" + station_id,
            "PRICE": 1700 + index * 10,
            "GIS_X_COOR": x,
            "GIS_Y_COOR": y,
            "DISTANCE": 100,
        }


def run_plan(mode: str = "normal", **limits: int) -> tuple[PlanExecution, Provider]:
    """실제 실행기/HTTP 어댑터를 MockTransport로 호출한다. 모의 키만 쓰며 GMS는 호출하지 않는다."""
    provider = Provider(mode)

    async def scenario() -> PlanExecution:
        """클라이언트 수명을 테스트가 소유하고 한 회차 실행 결과를 반환한다."""
        async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as client:
            return await execute_work_plan(
                work_plan(**limits), settings(rest_key=mode != "rest_missing_key"), client
            )

    return asyncio.run(scenario()), provider


def test_plan_executes_real_adapters_and_preserves_contracts_without_raw_geometry() -> None:
    """경로→후보 ID→상세 좌표→실제 경로→보조 조회가 연결되고 메타/단위/미확인이 유지되는지 본다."""
    result, provider = run_plan()
    assert result.status == "recommended" and result.error is None
    assert result.results.safe_range.safe_range_km == 150
    assert [item.action for item in result.steps] == ORDER
    assert len(result.results.search_candidates) == 2
    assert provider.calls.count("search") == 3 and provider.calls.count("detail:A") == 1
    assert result.results.rank_candidates.recommended.station_id == "A"
    assert provider.calls[-1] == "rest"
    rest = result.results.rest_area_info.items[0]
    assert rest.match_status == "unverified" and rest.facilities["oltYn"] is None
    assert rest.reference_date == "2026-11-01"
    assert all(
        call.result.meta.request_id and call.result.meta.fetched_at.utcoffset() is not None
        for call in result.calls
    )
    assert result.results.candidate_routes["A"].approach_request_id
    serialized = result.model_dump_json()
    assert all(
        secret not in serialized
        for secret in [
            "fake-kakao",
            "fake-opinet",
            "fake-rest",
            "vertexes",
            "serviceKey",
            "Authorization",
        ]
    )


@pytest.mark.parametrize(
    ("mode", "reason"),
    [
        ("detail_not_found", "NOT_FOUND"),
        ("detail_timeout", "TIMEOUT"),
        ("detail_malformed", "INVALID_RESPONSE"),
        ("id_mismatch", "INVALID_RESPONSE"),
        ("missing_price", "MISSING_PRICE"),
        ("approach_no_route", "NO_ROUTE"),
        ("via_no_route", "NO_ROUTE"),
        ("bad_legs", "INVALID_RESPONSE"),
        ("unreachable", "UNREACHABLE"),
    ],
)
def test_individual_candidate_failures_continue_to_another_candidate(
    mode: str, reason: str
) -> None:
    """개별 상세/경로/가격 실패를 제외 근거로 보존하고 정상 후보 B 처리는 계속한다."""
    result, provider = run_plan(mode)
    assert (
        result.status == "recommended"
        and result.results.rank_candidates.recommended.station_id == "B"
    )
    a = next(
        item
        for item in result.results.rank_candidates.excluded_candidates
        if item.station_id == "A"
    )
    assert reason in a.reason_codes and "detail:B" in provider.calls


@pytest.mark.parametrize(
    ("mode", "code"),
    [
        ("base_auth", "AUTH_FAILED"),
        ("base_no_route", "NO_ROUTE"),
        ("base_bad_geometry", "INVALID_RESPONSE"),
        ("search_auth", "AUTH_FAILED"),
        ("search_timeout", "TIMEOUT"),
        ("search_malformed", "INVALID_RESPONSE"),
        ("detail_auth", "AUTH_FAILED"),
        ("detail_rate", "RATE_LIMITED"),
        ("approach_auth", "AUTH_FAILED"),
    ],
)
def test_core_failure_stops_with_common_error_and_no_following_calls(mode: str, code: str) -> None:
    """기본 경로/검색 실패와 핵심 인증·한도 오류는 이후 호출·보조 조회·재시도를 중단한다."""
    result, provider = run_plan(mode)
    assert result.status == "stopped" and result.error.code == code
    assert (
        "rest" not in provider.calls and sum(step.status == "failed" for step in result.steps) == 1
    )
    assert result.steps[-1].status == "skipped"
    if mode in {"detail_auth", "detail_rate"}:
        assert "detail:B" not in provider.calls


@pytest.mark.parametrize("mode", ["rest_error", "rest_empty", "rest_missing_key"])
def test_optional_rest_information_failure_keeps_recommendation(mode: str) -> None:
    """휴게소 0건/서버/키 누락은 공통 호출 실패로 남기며 추천을 없애지 않는다."""
    result, _ = run_plan(mode)
    assert result.status == "recommended" and result.results.rest_area_info is None
    assert result.results.rank_candidates.recommended.station_id == "A" and result.warnings
    assert (
        result.calls[-1].result.tool_name == "search_rest_area_info"
        and not result.calls[-1].result.ok
    )


@pytest.mark.parametrize("mode", ["empty_candidates", "all_excluded"])
def test_no_recommendation_is_not_retried_and_rest_is_skipped(mode: str) -> None:
    """0건/전체 도달 불가면 재계획 필요만 표시하고 같은 호출 반복·보조 조회를 하지 않는다."""
    result, provider = run_plan(mode)
    assert result.status == "no_recommendation" and result.replan_needed and result.error is None
    assert provider.calls.count("search") == 3 and "rest" not in provider.calls
    assert result.steps[-1].status == "skipped"


def test_search_deduplication_limits_and_route_cache_are_request_local() -> None:
    """후보 ID 중복 제거·평가 상한·미평가 ID와 같은 좌표 경로 캐시의 요청 단위 수명을 확인한다."""
    limited, provider = run_plan("budget", max_candidates=1, max_evaluations=1)
    assert len(limited.results.search_candidates) == 4 and len(limited.results.station_details) == 1
    assert len(limited.unevaluated_station_ids) == 3 and provider.calls.count("detail:A") == 1
    first, first_provider = run_plan("shared_coordinate")
    second, second_provider = run_plan("shared_coordinate")
    assert first.cache_hits == second.cache_hits == 2
    assert first_provider.calls.count("approach") == second_provider.calls.count("approach") == 1
    assert first_provider.calls.count("via") == 1


@pytest.mark.parametrize(
    "mode", ["metadata", "duplicate", "order", "range", "budget", "missing_request"]
)
def test_tampered_work_plan_is_rejected_before_http(mode: str) -> None:
    """실행 단계에서 변형한 Tool/입력·중복·역순·조건/예산·요청 누락을 사전 거절한다."""
    plan = work_plan()
    if mode == "metadata":
        plan.steps[0].tool_name = "delete_logs"
    if mode == "duplicate":
        plan.steps[-1] = plan.steps[0]
    if mode == "order":
        plan.steps.reverse()
    if mode == "range":
        plan.request.remaining_range_km = -1
    if mode == "budget":
        plan.request.max_candidates = 100
    if mode == "missing_request":
        plan.request = None

    def forbidden(request: httpx.Request) -> httpx.Response:
        """변형 계획에서 HTTP가 발생하면 테스트를 실패시킨다."""
        raise AssertionError("HTTP forbidden")

    async def scenario() -> None:
        """모의 클라이언트로 사전 검증 예외만 확인한다."""
        async with httpx.AsyncClient(transport=httpx.MockTransport(forbidden)) as client:
            with pytest.raises(ProbeFailure) as failure:
                await execute_work_plan(plan, settings(), client)
        assert failure.value.error.code == "INVALID_INPUT"

    asyncio.run(scenario())


def test_missing_core_configuration_is_checked_before_any_provider_request() -> None:
    """필수 Kakao/Opinet 설정 누락은 첫 경로 호출도 실행하지 않는다."""

    def forbidden(request: httpx.Request) -> httpx.Response:
        """설정 누락에서 어떤 제공사로도 요청을 보내면 테스트를 실패시킨다."""
        raise AssertionError("HTTP forbidden")

    async def scenario() -> None:
        """환경 파일 없이 stopped/CONFIG_ERROR와 빈 호출 이력을 확인한다."""
        async with httpx.AsyncClient(transport=httpx.MockTransport(forbidden)) as client:
            result = await execute_work_plan(work_plan(), Settings(_env_file=None), client)
        assert result.error.code == "CONFIG_ERROR" and result.calls == []

    asyncio.run(scenario())
