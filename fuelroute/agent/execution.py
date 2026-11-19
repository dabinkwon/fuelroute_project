"""F3007 작업 계획을 한 회차 실행하고 결과를 통합한다. 데이터 계약은 schemas/execution.py에 둔다."""

from collections import Counter

import httpx
from pydantic import ValidationError

from fuelroute.agent.planning import plan_step_specs
from fuelroute.agent.replanning import decide_replan, replan_search_points
from fuelroute.calculations.detour import assess_candidate_routes
from fuelroute.calculations.driving_range import calculate_safe_range
from fuelroute.calculations.ranking import rank_candidates
from fuelroute.calculations.route_points import RoutePath, generate_search_points
from fuelroute.core.config import Settings
from fuelroute.core.contracts import ToolError, ToolResult
from fuelroute.core.errors import FATAL_CODES, ProbeFailure
from fuelroute.schemas.coordinates import Coordinate
from fuelroute.schemas.driving import RangeConditions, SafeRangeOutput
from fuelroute.schemas.execution import (
    CandidateRoutes,
    PlanExecution,
    StepExecution,
    ToolCallRecord,
)
from fuelroute.schemas.judgement import TripJudgement
from fuelroute.schemas.planning import Action, WorkPlan
from fuelroute.schemas.recommendation import (
    CandidateEvaluation,
    RecommendationRequest,
)
from fuelroute.schemas.replanning import ReplanOutcome
from fuelroute.schemas.requests import PlanningRequest
from fuelroute.schemas.routes import DrivingRoute, DrivingRouteRequest, RoutePoint
from fuelroute.schemas.stations import RestAreasOutput, StationCandidate, StationDetails
from fuelroute.tools.registry import ToolRegistry


def _check_plan(plan: WorkPlan) -> None:
    """실행 직전에 계획의 상태·완전성·의존성·고정 메타데이터를 검사한다.

    plan은 F3006 결과이며 변형한 Tool 이름/입력 출처/예산 해석 문자열을 실행하지 않는다.
    보류/생략은 request=None·빈steps여야 하고 ready는 검증된 요청과 아홉 작업이 필요하다.
    모순·중복·누락·순서/정책 변경은 HTTP 전 ProbeFailure(INVALID_INPUT)다. 이유 문장은
    실행 지시로 사용하지 않는다. 외부 호출·변경·저장은 없다. 예: 임의 Tool 삽입은 거절한다.
    """
    if plan.status != "ready":
        if plan.request is not None or plan.steps:
            raise ProbeFailure("INVALID_INPUT", "미준비 계획에는 실행 요청·단계가 없어야 합니다.")
        return
    if plan.request is None:
        raise ProbeFailure("INVALID_INPUT", "실행할 주행 조건이 없습니다.")
    specs = plan_step_specs(plan.request)
    actions = [step.action for step in plan.steps]
    if len(actions) != len(specs) or set(actions) != set(specs):
        raise ProbeFailure("INVALID_INPUT", "실행 계획의 누락·중복을 확인하세요.")
    seen = set()
    for step in plan.steps:
        spec = specs[step.action]
        if step.model_dump(exclude={"action", "reason"}) != spec:
            raise ProbeFailure("INVALID_INPUT", "실행 계획의 Tool·입력·실패 정책을 확인하세요.")
        if not set(step.depends_on).issubset(seen):
            raise ProbeFailure("INVALID_INPUT", "실행 계획의 선행 순서를 확인하세요.")
        seen.add(step.action)


def _invalid_result(result: ToolResult[dict], message: str) -> ToolResult[dict]:
    """공통 메타를 보존해 조회 결과의 ID/유종 불일치를 INVALID_RESPONSE로 바꾼다.

    message는 내부 고정 안내이며 제공사 원문을 사용하지 않는다. 입력 결과를 수정하지 않고
    새 실패를 반환한다. HTTP·재시도·저장은 없다. 예: 요청 A의 응답 ID B는 채택하지 않는다.
    """
    return ToolResult(
        ok=False,
        tool_name=result.tool_name,
        data=None,
        error=ToolError(code="INVALID_RESPONSE", message=message),
        meta=result.meta,
    )


class _PlanRunner:
    """한 요청의 상세 경로·캐시·후보 작업 상태만 보유하는 실행기. 요청 사이 공유하지 않는다."""

    def __init__(
        self,
        plan: WorkPlan,
        settings: Settings,
        client: httpx.AsyncClient,
        *,
        defer_rest_info: bool = False,
    ) -> None:
        """검증된 ready 계획·설정·호출자 소유 클라이언트를 보관한다.

        생성 시 HTTP·LLM·DB 호출은 없다. defer_rest_info=True는 판정 뒤 보조 조회용이고
        False는 독립 실행의 기존 계약이다. 입력 검사는 Session.start가 먼저 수행한다.
        최초 round_index=0, 추가 회차는1~2이며 경로·검색 캐시는 이 요청에서만 유지한다.
        active_search_points=None은 최초 검색점 전체, 빈 목록은 검색 없는 후보 재사용이다.
        반환·실패·재시도는 없고 client 종료는 호출자 책임이다. 예:추가 회차에 기본 경로를
        다시 조회하지 않는다. 원본 기하는 공개 output에 넣지 않는다.
        """
        self.plan = plan
        self.request = plan.request
        self.registry = ToolRegistry(settings, client)
        self.output = PlanExecution(warnings=list(plan.warnings))
        self.path: RoutePath | None = None
        self.selected: list[StationCandidate] = []
        self.evaluations: dict[str, CandidateEvaluation] = {}
        self.route_cache: dict[str, ToolResult[dict]] = {}
        self.search_cache: dict[tuple, ToolResult[dict]] = {}
        self.active_search_points: list[RoutePoint] | None = None
        self.round_index = 0
        self.action: Action = "base_route"
        self.defer_rest_info = defer_rest_info

    def _record(
        self, result: ToolResult[dict], inputs: dict, station_id: str | None = None
    ) -> None:
        """인증 없는 명시 입력과 정규화된 조회 결과를 현재 단계 이력에 추가한다.

        결과는 ToolRegistry가 공통 계약/출력 모델로 검사한 값이다. 같은 캐시 결과는 이
        함수를 다시 호출하지 않는다. 추가 HTTP·LLM·DB·이벤트 발행은 없다. station_id는
        후보 상세/경로의 ID이며 호출 메타 request_id로 실제 조회를 연결한다.
        """
        self.output.calls.append(
            ToolCallRecord(
                action=self.action,
                station_id=station_id,
                inputs=inputs,
                result=result,
                round_index=self.round_index,
            )
        )

    def _require(self, result: ToolResult[dict], *, optional: bool = False) -> bool:
        """핵심 공통 실패를 중단 예외로 전달하고 개별 실패/보조정보 실패는 False로 구분한다.

        optional=True는 모든 보조 조회 실패를 허용한다. 그 외 인증/설정/한도/입력 오류는
        ProbeFailure로 중단하며 같은 실패를 재시도하지 않는다. 외부 호출·변경은 없다.
        예: 상세 NOT_FOUND는False, 상세 AUTH_FAILED는 중단, 휴게소 AUTH_FAILED는False다.
        """
        if result.error and not optional and result.error.code in FATAL_CODES:
            error = result.error
            raise ProbeFailure(
                error.code,
                error.message,
                retryable=error.retryable,
                replan_allowed=error.replan_allowed,
            )
        return result.ok

    async def _route(
        self,
        destination: Coordinate,
        via: Coordinate | None = None,
        station_id: str | None = None,
    ) -> ToolResult[dict]:
        """고정 출발/옵션의 접근·경유 경로를 요청 내 재사용하며 실제 조회만 기록한다.

        목적/선택 경유는 WGS84 도, station_id는 호출 추적용이다. 비용 m/초를 공통 결과로
        반환하고 HTTP 최대1회·재시도0회다. 캐시 성공/실패 모두 재사용하고 종료 시 폐기한다.
        예: 두 후보의 좌표가 같으면 같은 접근 경로를 다시 호출하지 않는다. DB는 없다.
        """
        query = DrivingRouteRequest(origin=self.request.origin, destination=destination, via=via)
        arguments = query.model_dump(mode="json")
        key = query.model_dump_json()
        if key in self.route_cache:
            self.output.cache_hits += 1
            return self.route_cache[key]
        result = await self.registry.invoke("get_driving_route", arguments)
        self._record(result, arguments, station_id)
        self.route_cache[key] = result
        return result

    async def base_route(self) -> bool:
        """기본 경로를 한 번 조회해 비용은 공개 결과, 상세 기하는 실행기 내부에 분리한다.

        plan.request의 WGS84 출발/목적을 사용한다. 실패는 전역 ProbeFailure로 중단하며
        비용/기하가 잘못되면 INVALID_RESPONSE다. 저장/재시도 없이 성공 시True를 반환한다.
        """
        query = DrivingRouteRequest(
            origin=self.request.origin, destination=self.request.destination
        )
        result, self.path = await self.registry.route_context(query, include_path=True)
        self._record(result, query.model_dump(mode="json"))
        if result.error:
            error = result.error
            raise ProbeFailure(
                error.code,
                error.message,
                retryable=error.retryable,
                replan_allowed=error.replan_allowed,
            )
        self.output.results.base_route = DrivingRoute.model_validate(result.data)
        self.route_cache[query.model_dump_json()] = result
        return True

    async def safe_range(self) -> bool:
        """km 주행 가능-안전 여유 또는 기존 L/연비를 일반 코드로 계산해 km 결과를 보관한다.

        검증된 요청만 사용하며 비유한 계산 범위는 INVALID_INPUT으로 중단한다. L 역산,
        HTTP·LLM·저장·재시도는 없다. 예:180km/30km는150km다. 성공은True다.
        """
        values = self.request.model_dump(include=set(RangeConditions.model_fields))
        try:
            distance = calculate_safe_range(**values)
        except ValueError as exc:
            raise ProbeFailure(
                "INVALID_INPUT", "주행 가능 거리·안전 여유 계산 범위를 확인하세요."
            ) from exc
        self.output.results.safe_range = SafeRangeOutput(safe_range_km=distance)
        return True

    async def search_points(self) -> bool:
        """기본 상세 경로·안전거리로 최초 검색점만 만들고 WGS84 좌표·경로 누적m를 보관한다.

        두 선행 단계 성공이 필요하다. 반경 최대5000m·최대3점은 request의 고정 정책이다.
        보간점은 실제 도달 판정이 아니며 HTTP·DB·재계획은 없다. 잘못된 내부 경로는
        ValueError이며 빈 성공으로 감추지 않는다. 정상 경로는 검색점이 있어True다.
        """
        self.output.results.search_points = generate_search_points(
            self.path,
            self.output.results.safe_range.safe_range_km,
            self.request.radius_m,
            self.request.max_search_points,
        )
        return True

    async def search_candidates(self) -> bool:
        """활성 검색점의 후보를 조회하고 기존 후보와 주유소 ID로 합친다.

        입력은 고정 유종·반경m와 WGS84 검색점이다. 최초 최대3점, 추가 회차 최대1점이며
        이미 호출한 동일 좌표/유종/반경은 성공·실패 모두 캐시에서 재사용한다. 같은 ID는
        최초 후보를 유지하고 실제 가격은 후속 상세에서 확인한다. 0건은 다음 점으로 진행,
        그 밖의 검색 실패는 ProbeFailure로 중단한다. 유종 불일치는 INVALID_RESPONSE다.
        공개 호출/결과만 기록하고 반환 True는 검색 처리 완료를 뜻한다. 후보0건은 실패를
        감춘 성공 추천이 아니며 후속 판정/재계획에서 처리한다. 자체 검색점 변경·재시도·
        LLM·DB는 없다. 예:캐시 후보 평가 회차는 이 함수를 호출하지 않는다.
        """
        candidates = {item.station_id: item for item in self.output.results.search_candidates}
        points = (
            self.output.results.search_points
            if self.active_search_points is None
            else self.active_search_points
        )
        for point in points:
            arguments = {
                "longitude": point.longitude,
                "latitude": point.latitude,
                "fuel_type": self.request.fuel_type,
                "radius_m": self.request.radius_m,
            }
            key = (point.longitude, point.latitude, self.request.fuel_type, self.request.radius_m)
            if key in self.search_cache:
                self.output.cache_hits += 1
                result = self.search_cache[key]
            else:
                result = await self.registry.invoke("search_gas_stations", arguments)
                if result.ok and any(
                    row["fuel_type"] != self.request.fuel_type for row in result.data["items"]
                ):
                    result = _invalid_result(result, "후보 유종이 요청과 일치하지 않습니다.")
                self._record(result, arguments)
                self.search_cache[key] = result
            if result.error:
                if result.error.code == "EMPTY_RESULT":
                    continue
                error = result.error
                raise ProbeFailure(
                    error.code,
                    error.message,
                    retryable=error.retryable,
                    replan_allowed=error.replan_allowed,
                )
            for row in result.data["items"]:
                candidate = StationCandidate.model_validate(row)
                candidates.setdefault(candidate.station_id, candidate)
        self.output.results.search_candidates = list(candidates.values())
        return True

    async def station_details(self) -> bool:
        """아직 평가하지 않은 후보 일부의 상세·ID·유종을 검사한다.

        후보는 조회 가격→ID 순서이며 미제공 가격은 뒤로 보낸다. 한 회차 선택 수는
        min(max_candidates, max_evaluations-누적평가수)다. 실패 후보도 예산을 사용하고
        이미 시도한 ID는 상세·실패를 다시 조회하지 않는다. NOT_FOUND/타임아웃/파싱 실패는
        해당 후보에 보존하며 인증·설정·한도·입력 오류는 중단한다. 가격 미제공이면
        MISSING_PRICE로 남기고 경로 조회를 생략한다. 후보당 HTTP1회·재시도0회이며
        DB·LLM·조건 완화는 없다. 반환은 선택 후보 존재 여부다. 예:최초3곳 실패 후
        전체상한4면 다음 회차는1곳만 선택한다.
        """
        ordered = sorted(
            (
                item
                for item in self.output.results.search_candidates
                if item.station_id not in self.evaluations
            ),
            key=lambda item: (
                item.price_krw_per_l is None,
                item.price_krw_per_l or 0,
                item.station_id,
            ),
        )
        remaining = max(0, self.request.max_evaluations - len(self.evaluations))
        self.selected = ordered[: min(self.request.max_candidates, remaining)]
        selected_ids = {item.station_id for item in self.selected}
        self.output.unevaluated_station_ids = [
            item.station_id for item in ordered if item.station_id not in selected_ids
        ]
        for candidate in self.selected:
            station_id = candidate.station_id
            arguments = {"station_id": station_id, "fuel_type": self.request.fuel_type}
            result = await self.registry.invoke("get_station_details", arguments)
            if result.ok and (
                result.data["station_id"] != station_id
                or result.data["fuel_type"] != self.request.fuel_type
            ):
                result = _invalid_result(result, "주유소 상세 ID·유종이 요청과 일치하지 않습니다.")
            self._record(result, arguments, station_id)
            item = CandidateEvaluation(station_id=station_id)
            self.evaluations[station_id] = item
            self.output.results.evaluate_constraints = list(self.evaluations.values())
            if not self._require(result):
                item.reason_codes = [result.error.code]
                continue
            item.station = StationDetails.model_validate(result.data)
            self.output.results.station_details.append(item.station)
            if item.station.price_krw_per_l is None:
                item.reason_codes = ["MISSING_PRICE"]
        return bool(self.selected)

    async def candidate_routes(self) -> bool:
        """이번 회차의 유효 상세·가격 후보만 실제 접근·단일 경유 비용을 조회한다.

        기본 경로와 같은 출발/옵션이며 WGS84 좌표를 사용한다. 거리m·시간초·request_id를
        후보별로 묶고 접근이 안전거리 초과면 경유는 생략해 UNREACHABLE로 남긴다.
        이전 회차 후보를 다시 순회하지 않으며 같은 인자의 성공·실패는 요청 캐시에서
        재사용한다. 개별 실패는 제외, 핵심 인증/설정/한도는 ProbeFailure다. 후보당
        HTTP최대2회·재시도0회이며 시도 후보가 있으면True다. HTTP외 DB·LLM·기하 저장은
        없다. 예:좌표가 같은 A/B는 회차가 달라도 같은 경로 비용을 재사용한다.
        """
        attempted = False
        for candidate in self.selected:
            item = self.evaluations[candidate.station_id]
            if item.reason_codes:
                continue
            attempted = True
            point = Coordinate(longitude=item.station.longitude, latitude=item.station.latitude)
            pair = CandidateRoutes()
            self.output.results.candidate_routes[item.station_id] = pair
            approach = await self._route(point, station_id=item.station_id)
            if not self._require(approach):
                item.reason_codes = [approach.error.code]
                continue
            pair.approach = DrivingRoute.model_validate(approach.data)
            pair.approach_request_id = approach.meta.request_id
            item.reach_distance_m = pair.approach.distance_m
            if pair.approach.distance_m > self.output.results.safe_range.safe_range_km * 1000:
                item.reason_codes = ["UNREACHABLE"]
                continue
            via = await self._route(self.request.destination, point, item.station_id)
            if not self._require(via):
                item.reason_codes = [via.error.code]
                continue
            pair.via = DrivingRoute.model_validate(via.data)
            pair.via_request_id = via.meta.request_id
        return attempted

    async def evaluate_constraints(self) -> bool:
        """기존 순수 함수로 기본/접근/경유 비용과 안전·우회 상한을 판정한다.

        완전한 경로가 있는 후보만 처리하고 비용 누락·개별 실패 사유는 유지한다. 거리m·
        시간초를 전달하며 잘못된 범위/구간은 후보의 INVALID_RESPONSE다. HTTP·LLM·재계획·
        DB는 없다. 도착시각 적합성은 아직 판정하지 않으며 평가 후보가 없으면False다.
        """
        for station_id, item in self.evaluations.items():
            pair = self.output.results.candidate_routes.get(station_id)
            if item.reason_codes or pair is None or pair.approach is None or pair.via is None:
                continue
            try:
                assessed = assess_candidate_routes(
                    self.output.results.base_route.model_dump(),
                    pair.approach.model_dump(),
                    pair.via.model_dump(),
                    self.output.results.safe_range.safe_range_km,
                    self.request.max_detour_min,
                    self.request.max_detour_km,
                )
            except ValueError:
                item.reason_codes = ["INVALID_RESPONSE"]
            else:
                self.evaluations[station_id] = CandidateEvaluation.model_validate(
                    {**item.model_dump(), **assessed}
                )
        self.output.results.evaluate_constraints = list(self.evaluations.values())
        return bool(self.evaluations)

    async def rank_candidates(self) -> bool:
        """기존 제약 우선 정렬로 평가한 후보만 비교하며 입력의 가격/시간 우선을 유지한다.

        조회 실패·미확인 가격은 제외하고 빈 후보도 recommended=None으로 반환한다.
        전체 경로 최저가·시간 제약 충족을 보장하지 않는다. HTTP·LLM·DB·가중치/연료비
        생성은 없다. 보조 조회와 후속 응답에 결과를 전달하며 입력 계약 오류는ValidationError다.
        """
        self.output.results.rank_candidates = rank_candidates(
            RecommendationRequest(
                candidates=self.output.results.evaluate_constraints,
                fuel_type=self.request.fuel_type,
                safe_range_km=self.output.results.safe_range.safe_range_km,
                max_detour_min=self.request.max_detour_min,
                max_detour_km=self.request.max_detour_km,
                sort_by=self.request.sort_by,
            )
        )
        return True

    async def rest_area_info(self) -> bool:
        """추천 후보 좌표 주변 휴게소만 조회하고 0건/실패는 보조정보 미확인으로 남긴다.

        유일한 추천 후보가 있을 때 한 번 호출하며 내부 HTTP는 최대10페이지다. 노선/방향을
        추측하지 않고 match_status=unverified·시설null·기준일을 유지한다. 실패해도 추천을
        제외하거나 재시도하지 않는다. 추천이 없거나 후속 판정으로 미루면False다.
        조회했다면True이며 DB는 없다.
        """
        if self.defer_rest_info:
            return False
        recommended = self.output.results.rank_candidates.recommended
        if recommended is None:
            return False
        self.output = await enrich_rest_area(
            self.output, self.request, self.registry.settings, self.registry.client
        )
        return True

    async def run(self, *, actions: set[Action] | None = None) -> PlanExecution:
        """고정 핸들러만 계획 순서대로 한 회차 실행하고 실제 단계·부분 결과를 반환한다.

        호출 전에 _check_plan을 통과해야 한다. 핵심 ProbeFailure는 stopped/error로 남기고
        이후 단계는 미실행 처리한다. 프로그래밍 오류는 숨기지 않는다. no_recommendation은
        정상 결과이며 replan_needed만 표시하고 자체 재계획/최종 문장/로그 저장은 하지 않는다.
        actions=None은 최초 전체 계획, 지정 집합은 추가 회차의 후보 관련 작업만 실행한다.
        기본 경로·안전거리·이전 비용은 객체에 유지하며 반복 횟수는 그래프가 관리한다.
        호출 집계는 논리 Tool 시도 수이며 인증 사전 실패·휴게소 페이지 수와 HTTP 수는 다르다.
        """
        handlers = {
            "base_route": self.base_route,
            "safe_range": self.safe_range,
            "search_points": self.search_points,
            "search_candidates": self.search_candidates,
            "station_details": self.station_details,
            "candidate_routes": self.candidate_routes,
            "evaluate_constraints": self.evaluate_constraints,
            "rank_candidates": self.rank_candidates,
            "rest_area_info": self.rest_area_info,
        }
        for step in self.plan.steps:
            if actions is not None and step.action not in actions:
                continue
            self.action = step.action
            if self.output.error:
                self.output.steps.append(
                    StepExecution(
                        action=step.action,
                        status="skipped",
                        message="앞선 핵심 오류로 실행하지 않았습니다.",
                        round_index=self.round_index,
                    )
                )
                continue
            try:
                executed = await handlers[step.action]()
            except ProbeFailure as exc:
                self.output.error = exc.error
                self.output.steps.append(
                    StepExecution(
                        action=step.action,
                        status="failed",
                        message=exc.error.message,
                        round_index=self.round_index,
                    )
                )
            else:
                self.output.steps.append(
                    StepExecution(
                        action=step.action,
                        status="completed" if executed else "skipped",
                        message="처리 완료" if executed else "실행할 후보·선행 결과가 없습니다.",
                        round_index=self.round_index,
                    )
                )
        if self.output.error:
            self.output.status = "stopped"
        else:
            recommended = self.output.results.rank_candidates.recommended
            self.output.status = "recommended" if recommended else "no_recommendation"
            self.output.replan_needed = recommended is None
        self.output.tool_call_counts = dict(
            Counter(call.result.meta.provider for call in self.output.calls)
        )
        return self.output


class PlanExecutionSession:
    """자연어 요청 하나의 최초 실행·추가 회차·비공개 경로·캐시를 소유한다."""

    def __init__(
        self, settings: Settings, client: httpx.AsyncClient, *, defer_rest_info: bool = True
    ) -> None:
        """열린 호출자 소유 client와 설정을 보관한다. 생성 시 API·LLM·DB 호출은 없다.

        기본 defer_rest_info=True는 그래프의 최종 판정 후 보조 조회용이다. False는 독립
        실행의 기존 계약이다. 객체는 요청당 하나 생성하고 다른 요청/스레드와 공유하지
        않는다. start는 한 번만 가능하고 추가 실행은 replan에 고정 계획을 전달한다.
        client 종료는 호출자 책임이다. 예:PlanExecutionSession(settings, client).
        """
        self.settings = settings
        self.client = client
        self.defer_rest_info = defer_rest_info
        self.runner: _PlanRunner | None = None
        self.started = False

    async def start(self, plan: WorkPlan) -> PlanExecution:
        """계획·핵심 설정을 HTTP 전에 검사하고 최초 한 회차만 실행한다.

        입력은 F3006 WorkPlan이며 검증 복사본만 보관한다. 비추천/입력 보완은 조회0회,
        핵심 키 누락은 stopped/CONFIG_ERROR다. 계획 변조·중복 시작은 INVALID_INPUT,
        프로그래밍 오류는 숨기지 않는다. ready만 내부 실행기를 만들고 기하/캐시를 후속
        회차까지 유지한다. 검색점·후보 상한은 고정 요청을 따르며 반복·재시도·LLM·DB는 없다.
        예:await session.start(plan)의 반환 PlanExecution을 그래프 판정에 전달한다.
        """
        if self.started:
            raise ProbeFailure("INVALID_INPUT", "실행 세션은 요청마다 새로 생성해야 합니다.")
        self.started = True
        try:
            checked = WorkPlan.model_validate(plan.model_dump())
        except ValidationError as exc:
            raise ProbeFailure("INVALID_INPUT", "실행 계획의 타입·주행 조건을 확인하세요.") from exc
        _check_plan(checked)
        if checked.status != "ready":
            return PlanExecution(status=checked.status, warnings=list(checked.warnings))
        missing = self.settings.missing("kakao") + self.settings.missing("opinet")
        if missing:
            return PlanExecution(
                status="stopped",
                error=ToolError(
                    code="CONFIG_ERROR", message="미설정 환경변수: " + ", ".join(missing)
                ),
                warnings=list(checked.warnings),
            )
        self.runner = _PlanRunner(
            checked, self.settings, self.client, defer_rest_info=self.defer_rest_info
        )
        return await self.runner.run()

    async def replan(
        self, plan: WorkPlan, execution: PlanExecution, judgement: TripJudgement, replan_count: int
    ) -> ReplanOutcome:
        """동일 요청의 미평가 후보 또는 새 검색점 한 곳으로 추가 회차를 최대 한 번 실행한다.

        start 뒤 판정한 누적 결과와 완료 추가 횟수0~2를 받는다. 고정 계획·회차·호출ID가
        내부 세션과 같아야 하며 불일치는 HTTP 전 INVALID_INPUT이다. 기존 실제 경로의
        안전 구간에서 미사용 점을 계산하고 decide_replan으로 후보 재사용을 우선한다.
        종료는execution=None, 추가 실행은 기본 경로/안전거리/기존 상세를 재조회하지 않은
        누적 결과다. 실패 후보도 전체 평가 예산을 사용하고 조건은 완화하지 않는다.
        호출/단계에 회차를 남기고 보조 조회는 최종 판정 뒤로 미룬다. 내부 반복·추가LLM·
        DB·자동 재시도는 없다. 예:캐시B평가는 후보검색0회, 새점은 검색최대1회다.
        """
        runner = self.runner
        if (
            runner is None
            or plan != runner.plan
            or replan_count != runner.round_index
            or [call.result.meta.request_id for call in execution.calls]
            != [call.result.meta.request_id for call in runner.output.calls]
        ):
            raise ProbeFailure("INVALID_INPUT", "재계획의 요청·회차·실행 세션이 일치하지 않습니다.")
        points = replan_search_points(
            runner.path,
            runner.output.results.safe_range.safe_range_km,
            runner.request.radius_m,
            runner.output.results.search_points,
        )
        points = [
            point
            for point in points
            if (point.longitude, point.latitude, runner.request.fuel_type, runner.request.radius_m)
            not in runner.search_cache
        ]
        decision = decide_replan(plan, execution, judgement, replan_count, points)
        if decision.action == "stop":
            return ReplanOutcome(decision=decision)
        runner.output = execution.model_copy(deep=True)
        runner.round_index += 1
        runner.defer_rest_info = True
        runner.active_search_points = [decision.search_point] if decision.search_point else []
        runner.output.results.search_points.extend(runner.active_search_points)
        actions: set[Action] = {
            "station_details",
            "candidate_routes",
            "evaluate_constraints",
            "rank_candidates",
            "rest_area_info",
        }
        if decision.search_point is not None:
            actions.add("search_candidates")
        return ReplanOutcome(decision=decision, execution=await runner.run(actions=actions))


async def execute_work_plan(
    plan: WorkPlan,
    settings: Settings,
    client: httpx.AsyncClient,
    *,
    defer_rest_info: bool = False,
) -> PlanExecution:
    """F3006 작업 계획을 검증하고 F3007 조회·전달·기존 계산 통합을 한 회차 수행한다.

    plan.request는 WGS84·km 주행 범위/기존 연료·유종·우회 상한이며 클라이언트는 호출자가
    소유한다. 보류/비추천은 조회0회다. ready는 최대검색3회·평가min(회차/전체상한)개이며
    후보당 상세1회·접근/경유2회, 추천 후보의 보조 조회1회(내부최대10페이지)를 수행한다.
    인증 없는 입력/공통 결과와 정규화 비용을 반환하고 원본 경로는 요청 내부에서 폐기한다.
    변형 계획은 HTTP 전ProbeFailure(INVALID_INPUT), 핵심 제공사 실패는 반환error/stopped,
    개별 후보/보조 실패는 부분 결과에 보존한다. 자동 재시도/재계획·추가LLM·DB·이벤트는 없다.
    예: plan.status=needs_user_input이면 같은 상태와 빈 결과를 반환한다. 기존 계산/정렬은
    재사용한다. defer_rest_info=True는 F3008 판정 뒤 보조 조회를 하기 위한 옵션이며
    기본False는 기존 직접 실행 계약을 유지한다. 최종 자연어 응답은 후속 단계다.
    """
    return await PlanExecutionSession(settings, client, defer_rest_info=defer_rest_info).start(plan)


async def enrich_rest_area(
    execution: PlanExecution,
    request: PlanningRequest | None,
    settings: Settings,
    client: httpx.AsyncClient,
) -> PlanExecution:
    """확정 추천 후보의 보조정보만 조회해 실행 결과 복사본에 연결한다.

    선행 제약 판정이 끝난 execution과 검증 요청을 받는다. 추천/요청/정상 실행이 없으면
    HTTP0회다. 같은 ID의 기존 보조 호출은 성공·실패 모두 재사용해 재시도하지 않는다.
    후보가 바뀌면 이전 후보의 보조정보는 지우고 새 좌표로 최대1논리조회(내부10페이지)를
    수행한다. 실패는 추천을 유지하고 경고·공통 호출 결과에 남긴다. 입력/client를 닫거나
    수정하지 않고 단계 처리·논리 호출 집계를 갱신한 복사본을 반환한다. DB·LLM·이벤트는 없다.
    예:시간 제약 판정으로A가 제외되고B가 선택되면A의 휴게소를B에 붙이지 않는다.
    """
    output = execution.model_copy(deep=True)
    recommendation = output.results.rank_candidates
    selected = recommendation.recommended if recommendation else None
    step = next((item for item in reversed(output.steps) if item.action == "rest_area_info"), None)
    output.results.rest_area_info = None
    if selected is None or request is None or output.error:
        if step:
            step.status = "skipped"
            step.message = "확정 추천 후보가 없어 보조 조회를 생략했습니다."
        return output
    previous = next(
        (
            call
            for call in reversed(output.calls)
            if call.action == "rest_area_info" and call.station_id == selected.station_id
        ),
        None,
    )
    if previous is not None:
        result = previous.result
    else:
        station = selected.station
        arguments = {
            "longitude": station.longitude,
            "latitude": station.latitude,
            "radius_m": request.radius_m,
        }
        result = await ToolRegistry(settings, client).invoke("search_rest_area_info", arguments)
        output.calls.append(
            ToolCallRecord(
                action="rest_area_info",
                station_id=selected.station_id,
                inputs=arguments,
                result=result,
                round_index=step.round_index if step else 0,
            )
        )
    if result.ok:
        output.results.rest_area_info = RestAreasOutput.model_validate(result.data)
    else:
        warning = (
            f"휴게소 보조정보 미확인({result.error.code}). "
            "주유소와 동일 시설·방향임을 확인하지 않았습니다."
        )
        if warning not in output.warnings:
            output.warnings.append(warning)
    if step:
        step.status = "completed"
        step.message = "확정 후보의 보조 조회를 처리했습니다."
    output.tool_call_counts = dict(Counter(call.result.meta.provider for call in output.calls))
    return output
