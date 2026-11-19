"""도로 누적거리·안전 구간과 실제 접근·경유 비용을 합성 HTTP로 검증한다."""

import asyncio
from copy import deepcopy

import httpx
import pytest

from fuelroute.calculations.coordinates import wgs84_to_katec
from fuelroute.calculations.detour import assess_candidate_routes
from fuelroute.calculations.route_points import build_route_path, generate_search_points
from fuelroute.cli.route_spike import run_route_spike
from fuelroute.core.config import Settings
from fuelroute.schemas.requests import RouteSpikeRequest


def route_payload(
    distance: int,
    duration: int,
    origin: tuple = (127.0, 37.0),
    destination: tuple = (129.0, 37.0),
    via: tuple | None = None,
    first_distance: int = 90000,
) -> dict:
    """명시적 도로 거리·구간 합계가 일치하는 합성 응답을 만든다. 실제 도로 사실이 아니다."""
    stops = [origin, destination] if via is None else [origin, via, destination]
    distances = [distance] if via is None else [first_distance, distance - first_distance]
    durations = [duration] if via is None else [duration // 2, duration - duration // 2]
    sections = [
        {
            "distance": length,
            "duration": seconds,
            "roads": [{"distance": length, "vertexes": [*start, *end]}],
        }
        for start, end, length, seconds in zip(stops, stops[1:], distances, durations)
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


def probe_data(payload: dict) -> dict:
    """합성 제공사 응답을 probe_route의 성공 data 형태로 감싼다. 외부 호출은 없다."""
    summary = payload["routes"][0]["summary"]
    return {
        "distance_m": summary["distance"],
        "duration_s": summary["duration"],
        "response": payload,
    }


def test_reported_road_distance_and_boundaries() -> None:
    """직선 총길이 비율 대신 도로별 보고 거리를 쓰고 시작·도로 경계·끝을 보존하는지 확인한다."""
    payload = route_payload(10000, 600, destination=(127.02, 37))
    payload["routes"][0]["sections"][0]["roads"] = [
        {"distance": 1000, "vertexes": [127, 37, 127, 37, 127.01, 37]},
        {"distance": 9000, "vertexes": [127.01, 37, 127.02, 37]},
    ]
    path = build_route_path(probe_data(payload))
    assert path.point_at_distance(0).longitude == pytest.approx(127)
    assert path.point_at_distance(1000).longitude == pytest.approx(127.01)
    assert path.point_at_distance(5500).longitude == pytest.approx(127.015, abs=1e-6)
    assert path.point_at_distance(10000).longitude == pytest.approx(127.02)
    points = generate_search_points(path, 6)
    assert [point.route_distance_m for point in points] == [1000, 0]
    assert all(point.route_distance_m <= 6000 for point in points)


@pytest.mark.parametrize("distance", [-1, 10001, float("nan"), float("inf")])
def test_out_of_route_distance_is_rejected(distance: float) -> None:
    """범위 밖·비유한 누적거리를 임의의 끝점으로 보정하지 않는지 검증한다."""
    path = build_route_path(probe_data(route_payload(10000, 600)))
    with pytest.raises(ValueError):
        path.point_at_distance(distance)


@pytest.mark.parametrize("kind", ["odd", "empty", "coordinate", "sum", "gap", "zero_geometry"])
def test_malformed_geometry_does_not_produce_search_points(kind: str) -> None:
    """누락·홀수 좌표·잘못된 축·불일치 거리·단절·빈 기하를 정상 검색점으로 위장하지 않는다."""
    payload = route_payload(10000, 600)
    section = payload["routes"][0]["sections"][0]
    road = section["roads"][0]
    if kind == "odd":
        road["vertexes"].append(127)
    elif kind == "empty":
        road["vertexes"] = []
    elif kind == "coordinate":
        road["vertexes"][0] = 181
    elif kind == "sum":
        road["distance"] = 9000
    elif kind == "gap":
        road["distance"] = 5000
        section["roads"].append({"distance": 5000, "vertexes": [130, 37, 131, 37]})
    else:
        road["vertexes"] = [127, 37, 127, 37]
    with pytest.raises(ValueError):
        build_route_path(probe_data(payload))


def test_detour_safety_first_leg_boundaries_and_negative_deltas() -> None:
    """독립 접근보다 긴 경유 첫 구간, 상한 경계값, 음수 차이의 보존을 검증한다."""
    base = probe_data(route_payload(200000, 2000))
    approach = probe_data(route_payload(100000, 1000, destination=(128, 37)))
    via = probe_data(route_payload(205000, 2060, via=(128, 37), first_distance=145000))
    result = assess_candidate_routes(base, approach, via, 144, 1, 5)
    assert result["reason_codes"] == ["UNREACHABLE"]
    assert result["reach_distance_m"] == 145000
    assert assess_candidate_routes(base, approach, via, 145, 1, 5)["eligible"]
    result = assess_candidate_routes(base, approach, via, 145, 0.5, 4)
    assert result["reason_codes"] == ["DETOUR_TIME_EXCEEDED", "DETOUR_DISTANCE_EXCEEDED"]
    faster = probe_data(route_payload(190000, 1900, via=(128, 37)))
    result = assess_candidate_routes(base, approach, faster, 144, 0, 0)
    assert result["eligible"] and result["negative_delta_observed"]
    assert result["raw_detour_duration_s"] == -100
    assert result["detour_duration_s"] == 0
    with pytest.raises(ValueError):
        assess_candidate_routes(base, approach, base, 144)


def spike_request() -> RouteSpikeRequest:
    """144km 안전거리와 5분 우회 상한의 고정 합성 요청을 만든다. 실제 계정은 사용하지 않는다."""
    return RouteSpikeRequest(
        origin={"longitude": 127, "latitude": 37},
        destination={"longitude": 129, "latitude": 37},
        current_fuel_l=15,
        reserve_fuel_l=3,
        avg_efficiency_kmpl=12,
        fuel_type="gasoline",
        max_detour_min=5,
    )


@pytest.mark.parametrize("mode", ["normal", "detail_missing", "auth", "empty"])
def test_spike_call_bounds_dedup_and_individual_failures(mode: str) -> None:
    """실제 경로로 최저가 도달 불가·우회 초과를 제외하고 중복·개별 실패·인증 중단을 검증한다."""
    requests = []
    coordinates = {"A1": (127.1, 37), "A2": (127.2, 37), "A3": (127.3, 37)}

    def handler(request: httpx.Request) -> httpx.Response:
        """Opinet 후보/상세와 Kakao 세 경로 종류에 서로 다른 합성 응답을 반환한다."""
        requests.append(request)
        if request.url.host == "www.opinet.co.kr":
            if mode == "auth":
                return httpx.Response(401)
            station_ids = list(coordinates)
            detail = request.url.path.endswith("detailById.do")
            if detail:
                station_ids = [request.url.params["id"]]
            if mode == "empty" or (detail and mode == "detail_missing" and station_ids == ["A2"]):
                station_ids = []
            rows = []
            for station_id in station_ids:
                x, y = wgs84_to_katec(*coordinates[station_id])
                price = 1500 + int(station_id[-1]) * 100
                rows.append(
                    {
                        "UNI_ID": station_id,
                        "OS_NM": station_id,
                        "GIS_X_COOR": x,
                        "GIS_Y_COOR": y,
                        "PRICE": price,
                        "DISTANCE": 100,
                        "OIL_PRICE": [{"PRODCD": "B027", "PRICE": price}],
                    }
                )
            return httpx.Response(200, json={"RESULT": {"OIL": rows}})
        via = request.url.params.get("waypoints")
        destination = tuple(map(float, request.url.params["destination"].split(",")))
        if via:
            point = tuple(map(float, via.split(",")))
            station = min(coordinates, key=lambda name: abs(coordinates[name][0] - point[0]))
            distance, duration = (220000, 2400) if station == "A2" else (205000, 2100)
            payload = route_payload(distance, duration, via=point)
        elif destination[0] == 129:
            payload = route_payload(200000, 2000)
        else:
            station = min(coordinates, key=lambda name: abs(coordinates[name][0] - destination[0]))
            distance = 200000 if station == "A1" else 100000
            payload = route_payload(distance, 1000, destination=destination)
        return httpx.Response(200, json=payload)

    async def scenario() -> None:
        """소유권이 명확한 모의 HTTP 클라이언트로 실험 한 번을 수행한다. 실제 API 호출은 없다."""
        settings = Settings(_env_file=None, opinet_api_key="test-opinet", kakao_rest_api_key="test")
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await run_route_spike(spike_request(), settings, client)
        if mode == "auth":
            assert not result.ok and result.error.code == "AUTH_FAILED"
            assert len(requests) == 2
            return
        assert result.ok
        data = result.data
        assert all(point["route_distance_m"] <= 144000 for point in data["search_points"])
        if mode == "empty":
            assert data["status"] == "no_eligible_candidates"
            assert data["api_call_counts"] == {"kakao": 1, "opinet": 3}
            return
        assert data["duplicate_hits_removed"] == 6
        assert data["eligible_station_ids"] == ["A3"]
        reasons = {
            item["station_id"]: item["reason_codes"] for item in data["evaluated_candidates"]
        }
        assert reasons["A1"] == ["UNREACHABLE"]
        assert reasons["A2"] == (
            ["NOT_FOUND"] if mode == "detail_missing" else ["DETOUR_TIME_EXCEEDED"]
        )
        assert data["api_call_counts"]["opinet"] == 6
        assert data["api_call_counts"]["kakao"] <= 7
        if mode == "normal":
            assert data["api_call_counts"]["kakao"] == 6
        assert data["coverage"] == "sampled_points_and_limited_candidates"

    asyncio.run(scenario())


def test_mismatched_summary_and_zero_distance_jump_are_rejected() -> None:
    """도로 합계가 같아도 외부 요약이나 0m 도로의 긴 좌표 이동이 모순이면 거절한다."""
    data = probe_data(route_payload(10000, 600))
    bad = deepcopy(data)
    bad["distance_m"] = 9000
    with pytest.raises(ValueError):
        build_route_path(bad)
    section = data["response"]["routes"][0]["sections"][0]
    section["roads"].append({"distance": 0, "vertexes": [129, 37, 130, 37]})
    with pytest.raises(ValueError):
        build_route_path(data)


def test_zero_distance_approach_is_reachable() -> None:
    """현재 위치의 주유소를 검색 기하의 0m 제한 때문에 제외하지 않고 비용으로 평가한다."""
    base = probe_data(route_payload(10000, 600))
    approach = probe_data(route_payload(0, 0, destination=(127, 37)))
    via = probe_data(route_payload(10000, 600, via=(127, 37), first_distance=0))
    assert assess_candidate_routes(base, approach, via, 1, 0, 0)["eligible"]
    bad = deepcopy(approach)
    bad["distance_m"] = 1
    with pytest.raises(ValueError):
        assess_candidate_routes(base, bad, via, 1)


@pytest.mark.parametrize("missing_key", [True, False])
def test_spike_preflight_stops_missing_key_and_overflow(missing_key: bool) -> None:
    """인증 설정 누락과 연료 계산 오버플로가 HTTP 요청 전에 공통 실패로 끝나는지 확인한다."""

    def handler(request: httpx.Request) -> httpx.Response:
        """사전 검증 실패에서 HTTP가 실행되면 즉시 테스트를 실패시킨다. 외부 접속은 없다."""
        raise AssertionError("예상하지 않은 HTTP 호출")

    async def scenario() -> None:
        """모의 클라이언트로 두 실패 조건을 검증하며 실제 설정·계정은 사용하지 않는다."""
        values = spike_request().model_dump()
        if not missing_key:
            values.update(current_fuel_l=1e308, reserve_fuel_l=0, avg_efficiency_kmpl=1e308)
        settings = Settings(
            _env_file=None,
            kakao_rest_api_key="test",
            opinet_api_key=None if missing_key else "test",
        )
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await run_route_spike(RouteSpikeRequest(**values), settings, client)
        assert not result.ok
        assert result.error.code == ("CONFIG_ERROR" if missing_key else "INVALID_INPUT")

    asyncio.run(scenario())
