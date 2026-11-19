"""F3007 작업 계획을 한 회차 실행하고 결과를 통합한다. 데이터 계약은 schemas/execution.py에 둔다."""

from collections import Counter

import httpx
from pydantic import ValidationError

from fuelroute.agent.planning import plan_step_specs
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
from fuelroute.schemas.planning import Action, WorkPlan
from fuelroute.schemas.recommendation import (
    CandidateEvaluation,
    RecommendationRequest,
)
from fuelroute.schemas.requests import PlanningRequest
from fuelroute.schemas.routes import DrivingRoute, DrivingRouteRequest
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
        """검증된 ready 계획과 호출자 소유 클라이언트를 보관한다. 생성 시 HTTP·DB는 없다.

        defer_rest_info=True이면 보조 조회를 후속 판정 뒤로 미룬다. 기본False는 기존 직접
        실행 계약이다. 상세 경로와 캐시는 이 객체에서만 사용한다. 공개 output에는 기하를
        넣지 않는다. 입력 검사는 execute_work_plan이 먼저 수행하며 종료는 호출자 책임이다.
        """
        self.plan = plan
        self.request = plan.request
        self.registry = ToolRegistry(settings, client)
        self.output = PlanExecution(warnings=list(plan.warnings))
        self.path: RoutePath | None = None
        self.selected: list[StationCandidate] = []
        self.evaluations: dict[str, CandidateEvaluation] = {}
        self.route_cache: dict[str, ToolResult[dict]] = {}
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
        """각 검색점에서 후보를 조회하고 주유소 ID로 병합한다. 0건 외 검색 실패는 중단한다.

        WGS84 점·고정 유종·반경m를 전달하며 최대3회 조회한다. 여러 검색점의 같은 ID는
        최초 후보만 유지하고 실제 가격은 후속 상세에서 확인한다. 0건이면 다음 점으로
        진행하고 전체0건은 재계획 필요 상태로 남긴다. 유종 불일치는 INVALID_RESPONSE다.
        호출/공통 실패는 기록하고 재시도·검색점 확대·DB 쓰기는 하지 않는다.
        """
        candidates = {}
        for point in self.output.results.search_points:
            arguments = {
                "longitude": point.longitude,
                "latitude": point.latitude,
                "fuel_type": self.request.fuel_type,
                "radius_m": self.request.radius_m,
            }
            result = await self.registry.invoke("search_gas_stations", arguments)
            if result.ok and any(
                row["fuel_type"] != self.request.fuel_type for row in result.data["items"]
            ):
                result = _invalid_result(result, "후보 유종이 요청과 일치하지 않습니다.")
            self._record(result, arguments)
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
        """가격/ID 순 후보 일부의 상세를 조회해 ID·유종을 검사하고 개별 실패를 남긴다.

        후보는 미제공 가격을 뒤에 두고 ID로 동률을 정한다. 한 회차 평가 수는
        min(max_candidates,max_evaluations) 이하이며 실패 후보도 예산을 사용한다.
        상세 NOT_FOUND/타임아웃/파싱 실패는 해당 후보만 제외하고 다른 후보를 계속한다.
        인증/설정/한도/입력은 중단한다. 가격 미제공은 MISSING_PRICE로 남기고 경로 조회를
        생략한다. HTTP는 후보당1회·재시도0회, DB는 없다. 후보0건이면False다.
        """
        ordered = sorted(
            self.output.results.search_candidates,
            key=lambda item: (
                item.price_krw_per_l is None,
                item.price_krw_per_l or 0,
                item.station_id,
            ),
        )
        self.selected = ordered[: min(self.request.max_candidates, self.request.max_evaluations)]
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
            if not self._require(result):
                item.reason_codes = [result.error.code]
                continue
            item.station = StationDetails.model_validate(result.data)
            self.output.results.station_details.append(item.station)
            if item.station.price_krw_per_l is None:
                item.reason_codes = ["MISSING_PRICE"]
        return bool(self.selected)

    async def candidate_routes(self) -> bool:
        """유효 상세/가격이 있는 후보만 실제 접근·단일 경유 비용을 조회해 후보별로 묶는다.

        기본 경로와 같은 출발/옵션이며 거리m·시간초·호출 request_id를 보존한다. 접근이
        안전거리 초과면 경유 조회를 생략하고 UNREACHABLE로 남긴다. 개별 경로 실패는
        후보 제외, 인증/설정/한도는 중단이다. 후보당 최대2회·재시도0회, 캐시는 요청 내
        동일 인자만 재사용한다. 유효 가격 후보가 없으면False다. 기하/DB 저장은 없다.
        """
        attempted = False
        for item in self.evaluations.values():
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

    async def run(self) -> PlanExecution:
        """고정 핸들러만 계획 순서대로 한 회차 실행하고 실제 단계·부분 결과를 반환한다.

        호출 전에 _check_plan을 통과해야 한다. 핵심 ProbeFailure는 stopped/error로 남기고
        이후 단계는 미실행 처리한다. 프로그래밍 오류는 숨기지 않는다. no_recommendation은
        정상 결과이며 replan_needed만 표시하고 자동 재계획/최종 문장/로그 저장은 하지 않는다.
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
            self.action = step.action
            if self.output.error:
                self.output.steps.append(
                    StepExecution(
                        action=step.action,
                        status="skipped",
                        message="앞선 핵심 오류로 실행하지 않았습니다.",
                    )
                )
                continue
            try:
                executed = await handlers[step.action]()
            except ProbeFailure as exc:
                self.output.error = exc.error
                self.output.steps.append(
                    StepExecution(action=step.action, status="failed", message=exc.error.message)
                )
            else:
                self.output.steps.append(
                    StepExecution(
                        action=step.action,
                        status="completed" if executed else "skipped",
                        message="처리 완료" if executed else "실행할 후보·선행 결과가 없습니다.",
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
    try:
        plan = WorkPlan.model_validate(plan.model_dump())
    except ValidationError as exc:
        raise ProbeFailure("INVALID_INPUT", "실행 계획의 타입·주행 조건을 확인하세요.") from exc
    _check_plan(plan)
    if plan.status != "ready":
        return PlanExecution(status=plan.status, warnings=list(plan.warnings))
    missing = settings.missing("kakao") + settings.missing("opinet")
    if missing:
        return PlanExecution(
            status="stopped",
            error=ToolError(
                code="CONFIG_ERROR",
                message="미설정 환경변수: " + ", ".join(missing),
            ),
            warnings=list(plan.warnings),
        )
    return await _PlanRunner(plan, settings, client, defer_rest_info=defer_rest_info).run()


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
    step = next((item for item in output.steps if item.action == "rest_area_info"), None)
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
