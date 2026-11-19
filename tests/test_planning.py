"""재계획 횟수·예산·캐시·치명적 오류 중단을 합성 제공사 결과로 검증한다."""

import asyncio
from collections import Counter

import httpx
import pytest

import fuelroute.agent.replanning as planning
from fuelroute.agent.replanning import recommend_route
from fuelroute.core.config import Settings
from fuelroute.core.contracts import ToolError, ToolMeta, ToolResult, utc_now
from fuelroute.schemas.requests import PlanningRequest


def outcome(
    name: str, provider: str, data: dict | None = None, error: str | None = None
) -> ToolResult:
    """테스트 전용 공통 응답을 만든다. 실제 인증·시간·HTTP 응답을 주장하지 않는다."""
    return ToolResult(
        ok=error is None,
        tool_name=name,
        data=data,
        error=ToolError(code=error, message="합성 실패") if error else None,
        meta=ToolMeta(provider=provider, request_id="fixture", elapsed_ms=0, fetched_at=utc_now()),
    )


def costs(distance: int, seconds: int, via: bool = False) -> dict:
    """기본 200km 경로와 모순되지 않는 합성 비용·검색용 기하를 반환한다."""
    lengths = [90000, distance - 90000] if via else [distance]
    sections = [
        {
            "distance": length,
            "duration": seconds // len(lengths),
            "roads": [{"distance": length, "vertexes": [127, 37, 129, 37]}],
        }
        for length in lengths
    ]
    return {
        "distance_m": distance,
        "duration_s": seconds,
        "response": {
            "routes": [
                {
                    "result_code": 0,
                    "summary": {"distance": distance, "duration": seconds},
                    "sections": sections,
                }
            ]
        },
    }


def request(**updates) -> PlanningRequest:
    """명시적인 144km/5분 제약과 조절 가능한 회차별 후보 상한의 합성 요청을 만든다."""
    return PlanningRequest(
        **{
            "origin": {"longitude": 127, "latitude": 37},
            "destination": {"longitude": 129, "latitude": 37},
            "current_fuel_l": 15,
            "reserve_fuel_l": 3,
            "avg_efficiency_kmpl": 12,
            "fuel_type": "gasoline",
            "max_detour_min": 5,
            "max_candidates": 1,
            **updates,
        }
    )


@pytest.mark.parametrize(
    "mode", ["cached", "expand", "empty", "budget", "limit", "shared", "auth", "rate"]
)
def test_bounded_replanning_and_cache(monkeypatch, mode: str) -> None:
    """조건을 완화하지 않는 재계획·동일 요청 재사용·상한·전역 오류 중단을 검증한다."""
    calls = []

    async def fake_route(settings, client, *, origin, destination, via=None) -> ToolResult:
        """기본·접근·경유의 합성 비용을 제공하고 실제 실행 횟수를 기록한다."""
        calls.append(("route", destination.longitude, via.longitude if via else None))
        if via is not None:
            return outcome(
                "probe_route", "kakao", costs(205000, 2600 if mode == "shared" else 2100, True)
            )
        if destination.longitude == 129:
            return outcome("probe_route", "kakao", costs(200000, 2000))
        unreachable = mode in {"budget", "limit"} or (
            mode == "cached" and destination.longitude == 127.1
        )
        return outcome("probe_route", "kakao", costs(200000 if unreachable else 100000, 1000))

    class FakeTools:
        """클라이언트를 사용하지 않는 테스트용 Opinet 대체 구현."""

        def __init__(self, settings, client):
            """실제 설정·클라이언트를 보관하거나 호출하지 않는다."""

        async def search_gas_stations(self, longitude, latitude, fuel_type, radius_m):
            """초기 빈 검색과 새 검색점의 후보 발견 또는 반복 후보를 합성한다."""
            calls.append(("search", longitude, latitude))
            count = sum(call[0] == "search" for call in calls)
            if mode in {"auth", "rate"}:
                return outcome(
                    "search_gas_stations",
                    "opinet",
                    error="AUTH_FAILED" if mode == "auth" else "RATE_LIMITED",
                )
            if mode == "empty" or (mode == "expand" and count <= 3):
                return outcome("search_gas_stations", "opinet", error="EMPTY_RESULT")
            ids = ["A1", "A2", "A3"] if mode == "shared" else ["A1", "A2"]
            if mode == "expand":
                ids = ["A2"]
            return outcome(
                "search_gas_stations",
                "opinet",
                {
                    "items": [
                        {
                            "station_id": key,
                            "price_krw_per_l": 1700 + index,
                            "longitude": 127.1,
                            "latitude": 37,
                        }
                        for index, key in enumerate(ids)
                    ]
                },
            )

        async def get_station_details(self, station_id, fuel_type):
            """유종·가격·좌표를 실제 조회 없이 합성하고 상세 조회 중복을 감지한다."""
            calls.append(("detail", station_id))
            return outcome(
                "get_station_details",
                "opinet",
                {
                    "station_id": station_id,
                    "name": station_id,
                    "longitude": 127.1 if (station_id == "A1" or mode == "shared") else 127.2,
                    "latitude": 37,
                    "fuel_type": fuel_type,
                    "price_krw_per_l": 1800,
                    "price_observed_at": None,
                    "address": None,
                    "brand": None,
                    "facilities": {},
                },
            )

    monkeypatch.setattr(planning, "probe_route", fake_route)
    monkeypatch.setattr(planning, "FuelTools", FakeTools)

    async def scenario() -> None:
        """고정된 실행 범위에서만 캐시를 사용하며 외부 네트워크를 열지 않는다."""
        values = {}
        if mode == "shared":
            values["max_candidates"] = 3
        if mode == "budget":
            values["max_evaluations"] = 1
        if mode == "limit":
            values["max_replans"] = 0
        async with httpx.AsyncClient() as client:
            result = await recommend_route(
                request(**values),
                Settings(_env_file=None, kakao_rest_api_key="test", opinet_api_key="test"),
                client,
            )
        counts = Counter(call[0] for call in calls)
        if mode in {"auth", "rate"}:
            assert not result.ok and result.error.code == (
                "AUTH_FAILED" if mode == "auth" else "RATE_LIMITED"
            )
            assert counts == {"route": 1, "search": 1}
            return
        assert result.ok
        data = result.data
        assert data["replan_count"] <= 2
        assert counts["search"] <= 5 and counts["detail"] <= 9 and counts["route"] <= 19
        details = [call[1] for call in calls if call[0] == "detail"]
        assert len(details) == len(set(details))
        assert data["safe_range_km"] == 144
        if mode == "cached":
            assert data["recommendation"]["recommended"]["station_id"] == "A2"
            assert data["replan_count"] == 1 and counts["search"] == 3
            assert data["rounds"][1]["trigger"] == "cached_candidates"
        elif mode == "expand":
            assert data["recommendation"]["recommended"]["station_id"] == "A2"
            assert data["replan_count"] == 1 and counts["search"] == 4
            assert data["rounds"][1]["new_search_points"][0]["route_distance_m"] == 69000
        elif mode == "shared":
            assert counts["route"] == 3 and data["cache_hits"]["route"] == 4
            assert data["status"] == "no_recommendation"
            async with httpx.AsyncClient() as client:
                repeated = await recommend_route(
                    request(**values),
                    Settings(_env_file=None, kakao_rest_api_key="test", opinet_api_key="test"),
                    client,
                )
            assert repeated.ok
            assert Counter(call[0] for call in calls) == {
                key: count * 2 for key, count in counts.items()
            }
        elif mode == "budget":
            assert data["stop_reason"] == "evaluation_budget_exhausted"
        elif mode == "limit":
            assert data["replan_count"] == 0 and data["stop_reason"] == "replan_limit_reached"
        else:
            assert data["status"] == "no_recommendation" and data["replan_count"] == 2

    asyncio.run(scenario())
