"""저장소 루트에서 python -m fuelroute로 실행하는 API 검증 CLI."""

import argparse
import asyncio
import json
from collections.abc import Sequence
from datetime import date

import httpx
from pydantic import TypeAdapter, ValidationError

from fuelroute.agent import analyze_request, plan_request
from fuelroute.config import Settings
from fuelroute.contracts import ToolError, ToolMeta, ToolResult, utc_now
from fuelroute.gms import probe_gms
from fuelroute.planning import PlanningRequest, recommend_route
from fuelroute.probes import Coordinate, OpinetSearch, probe_opinet, probe_rest_area, probe_route
from fuelroute.route_spike import RouteSpikeRequest, run_route_spike
from fuelroute.tool_registry import ToolRegistry
from fuelroute.tools import FuelTools


def build_parser() -> argparse.ArgumentParser:
    """키를 인자로 받지 않는 doctor/GMS/외부 API·경로 연결 검증 명령을 구성한다.

    반환 parser는 argparse 기본 도움말과 타입 검사를 제공한다. KATEC x/y와
    WGS84 경도/위도를 다른 옵션으로 받는다. 외부 호출·파일 생성은 없다.
    route-spike는 연결 실험, recommend는 유한한 재계획과 추천, tool-schemas는 정적 계약
    출력이다. 예: doctor 또는 --help. 명령을 정의하는 과정에서 HTTP를 실행하지 않는다.
    analyze는 F3005 분석·필요한 위치 검색, plan은 준비된 입력의 F3006 계획까지 수행한다.
    두 명령은 길찾기·주유소·휴게소 조회 실행·DB 저장을 하지 않는다.
    """
    parser = argparse.ArgumentParser(description="FuelRoute 환경·API 독립 검증")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("doctor", help="설정된 변수/누락 항목만 확인 (호출·키 출력 없음)")
    commands.add_parser("tool-schemas", help="일곱 Tool의 입력·출력 계약 출력 (호출 없음)")
    for name, help_text in (
        ("analyze", "F3005 분석·위치 해석·누락/오류 확인 (GMS 1회)"),
        ("plan", "F3006 작업 순서 수립 (GMS 최대 2회, 실행 Tool 호출 없음)"),
    ):
        command = commands.add_parser(name, help=help_text)
        command.add_argument("--message", required=True, help="주행 조건을 포함한 요청 원문")
        command.add_argument("--reference-date", help="상대 날짜 기준 YYYY-MM-DD, 기본 실행일 KST")
    gms = commands.add_parser("gms", help="GMS 텍스트·조건 추출·Tool 왕복 검증")
    gms.add_argument("--mode", choices=["text", "conditions", "tool"], default="text")
    nearby = commands.add_parser("opinet-nearby", help="KATEC 직접 입력으로 Opinet 인증 확인")
    nearby.add_argument("--x", type=float, required=True, help="KATEC X(m), 경도 아님")
    nearby.add_argument("--y", type=float, required=True, help="KATEC Y(m), 위도 아님")
    nearby.add_argument("--radius-m", type=int, default=3000)
    nearby.add_argument("--fuel-type", choices=["gasoline", "diesel"], default="gasoline")
    detail = commands.add_parser("opinet-detail", help="확보한 UNI_ID의 상세 응답 확인")
    detail.add_argument("--station-id", required=True)
    route = commands.add_parser("route", help="Kakao 기본·경유 상세 경로 확인")
    route.add_argument("--origin", nargs=2, type=float, required=True, metavar=("LON", "LAT"))
    route.add_argument("--destination", nargs=2, type=float, required=True, metavar=("LON", "LAT"))
    route.add_argument("--via", nargs=2, type=float, metavar=("LON", "LAT"))
    rest = commands.add_parser("rest-area", help="휴게소 API 한 페이지 인증·응답 확인")
    rest.add_argument("--page", type=int, default=1)
    rest.add_argument("--rows", type=int, default=10)
    stations = commands.add_parser("stations", help="WGS84 검색점의 정규화된 주유소 후보 조회")
    stations.add_argument("--point", nargs=2, type=float, required=True, metavar=("LON", "LAT"))
    stations.add_argument("--radius-m", type=int, default=5000)
    stations.add_argument("--fuel-type", choices=["gasoline", "diesel"], default="gasoline")
    station_detail = commands.add_parser("station-detail", help="후보 ID의 정규화된 상세·가격 조회")
    station_detail.add_argument("--station-id", required=True)
    station_detail.add_argument("--fuel-type", choices=["gasoline", "diesel"], default="gasoline")
    rest_search = commands.add_parser(
        "rest-search", help="전체 휴게소 페이지에서 반경 보조정보 검색"
    )
    rest_search.add_argument("--point", nargs=2, type=float, required=True, metavar=("LON", "LAT"))
    rest_search.add_argument("--radius-m", type=int, default=5000)
    rest_search.add_argument("--route-name")
    rest_search.add_argument("--direction")
    spike = commands.add_parser("route-spike", help="안전 구간 검색→후보→실제 도달·우회 검증")
    spike.add_argument("--origin", nargs=2, type=float, required=True, metavar=("LON", "LAT"))
    spike.add_argument("--destination", nargs=2, type=float, required=True, metavar=("LON", "LAT"))
    spike.add_argument("--current-fuel-l", type=float, required=True)
    spike.add_argument("--reserve-fuel-l", type=float, required=True)
    spike.add_argument("--efficiency-kmpl", type=float, required=True)
    spike.add_argument("--fuel-type", choices=["gasoline", "diesel"], default="gasoline")
    spike.add_argument("--radius-m", type=int, default=5000)
    spike.add_argument("--max-search-points", type=int, default=3)
    spike.add_argument("--max-candidates", type=int, default=3)
    spike.add_argument("--max-detour-min", type=float)
    spike.add_argument("--max-detour-km", type=float)
    recommend = commands.add_parser(
        "recommend",
        parents=[spike],
        add_help=False,
        help="캐시·최대 두 번의 재계획으로 평가 후보 중 추천",
    )
    recommend.add_argument("--max-replans", type=int, default=2)
    recommend.add_argument("--max-evaluations", type=int, default=9)
    recommend.add_argument("--sort-by", choices=["price", "detour_time"], default="price")
    return parser


async def dispatch(args: argparse.Namespace, settings: Settings) -> ToolResult[dict]:
    """검증한 CLI 입력을 선택 제공사에 전달하고 공통 결과를 반환한다.

    doctor는 네트워크를 사용하지 않고, 나머지는 필요한 설정이 있을 때만 호출한다.
    HTTP 클라이언트는 명령 종료 시 닫는다. 외부 API 실패를 성공 종료로 감추지 않는다.
    예: opinet-detail은 검색 함수 없이 입력 ID로 상세 요청만 보낸다. route-spike는
    명시적 호출 상한 안에서 경로·후보를 연결한다. recommend는 캐시·재계획을 포함하며
    tool-schemas는 HTTP 없이 계약만 반환한다. 두 명령도 LLM이나 DB를 사용하지 않는다.
    analyze는 GMS1회·필요한 Local 검색, plan은 준비된 요청에 계획 GMS1회를 추가한다.
    계획의 누락/생략은 성공 데이터로 구분하며 실행 Tool 호출과 저장은 하지 않는다.
    """
    if args.command == "doctor":
        status = {
            provider: {
                "configured": not settings.missing(provider),
                "missing": settings.missing(provider),
            }
            for provider in ("gms", "opinet", "kakao", "rest_area")
        }
        return ToolResult(
            ok=True,
            tool_name="doctor",
            data={"providers": status},
            error=None,
            meta=ToolMeta(
                provider="local", request_id="doctor", elapsed_ms=0, fetched_at=utc_now()
            ),
        )
    if args.command == "gms":
        return await probe_gms(settings, args.mode)
    if args.command in {"analyze", "plan"}:
        request_fn = plan_request if args.command == "plan" else analyze_request
        return await request_fn(
            args.message,
            settings,
            reference_date=TypeAdapter(date).validate_python(args.reference_date)
            if args.reference_date
            else None,
        )
    async with httpx.AsyncClient() as client:
        if args.command == "tool-schemas":
            return ToolResult(
                ok=True,
                tool_name="tool_schemas",
                error=None,
                data={"contract_version": 1, "tools": ToolRegistry(settings, client).contracts()},
                meta=ToolMeta(
                    provider="local", request_id="tool-schemas", elapsed_ms=0, fetched_at=utc_now()
                ),
            )
        tools = FuelTools(settings, client)
        if args.command in {"route-spike", "recommend"}:
            values = dict(
                origin=Coordinate(longitude=args.origin[0], latitude=args.origin[1]),
                destination=Coordinate(longitude=args.destination[0], latitude=args.destination[1]),
                current_fuel_l=args.current_fuel_l,
                reserve_fuel_l=args.reserve_fuel_l,
                avg_efficiency_kmpl=args.efficiency_kmpl,
                fuel_type=args.fuel_type,
                radius_m=args.radius_m,
                max_search_points=args.max_search_points,
                max_candidates=args.max_candidates,
                max_detour_min=args.max_detour_min,
                max_detour_km=args.max_detour_km,
            )
            if args.command == "recommend":
                values.update(
                    max_replans=args.max_replans,
                    max_evaluations=args.max_evaluations,
                    sort_by=args.sort_by,
                )
                return await recommend_route(PlanningRequest(**values), settings, client)
            return await run_route_spike(RouteSpikeRequest(**values), settings, client)
        if args.command == "stations":
            return await tools.search_gas_stations(
                args.point[0], args.point[1], args.fuel_type, args.radius_m
            )
        if args.command == "station-detail":
            return await tools.get_station_details(args.station_id, args.fuel_type)
        if args.command == "rest-search":
            return await tools.search_rest_area_info(
                args.point[0], args.point[1], args.radius_m, args.route_name, args.direction
            )
        if args.command == "opinet-nearby":
            search = OpinetSearch(
                x=args.x, y=args.y, radius_m=args.radius_m, fuel_type=args.fuel_type
            )
            return await probe_opinet(settings, client, search=search)
        if args.command == "opinet-detail":
            return await probe_opinet(settings, client, station_id=args.station_id)
        if args.command == "route":
            return await probe_route(
                settings,
                client,
                origin=Coordinate(longitude=args.origin[0], latitude=args.origin[1]),
                destination=Coordinate(longitude=args.destination[0], latitude=args.destination[1]),
                via=Coordinate(longitude=args.via[0], latitude=args.via[1]) if args.via else None,
            )
        return await probe_rest_area(settings, client, page=args.page, rows=args.rows)


def main(argv: Sequence[str] | None = None) -> int:
    """CLI 인자를 해석하고 키가 제거된 JSON 결과를 출력한 뒤 종료 코드를 반환한다.

    argv=None은 터미널 인자를, 명시적 목록은 테스트 입력을 사용한다. 성공은 0,
    API/설정/입력 실패는 1이다. 원본 값이 담긴 Pydantic 예외는 출력하지 않고
    설정 오류는 CONFIG_ERROR로 알린다. 파일 저장은 없다. 예: gms --mode tool.
    """
    args = build_parser().parse_args(argv)
    try:
        settings = Settings()
    except ValidationError:
        result = ToolResult(
            ok=False,
            tool_name=args.command,
            data=None,
            error=ToolError(code="CONFIG_ERROR", message=".env의 설정 타입·GMS URL을 확인하세요."),
            meta=ToolMeta(
                provider="local", request_id="config", elapsed_ms=0, fetched_at=utc_now()
            ),
        )
    else:
        try:
            result = asyncio.run(dispatch(args, settings))
        except ValidationError:
            result = ToolResult(
                ok=False,
                tool_name=args.command,
                data=None,
                error=ToolError(
                    code="INVALID_INPUT", message="입력 타입·범위·좌표계를 확인하세요."
                ),
                meta=ToolMeta(
                    provider="local", request_id="input", elapsed_ms=0, fetched_at=utc_now()
                ),
            )
    print(json.dumps(result.model_dump(mode="json"), ensure_ascii=False, indent=2))
    return 0 if result.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
