"""안전 우선·누락 데이터·동가 정렬을 검증한다. 외부 API는 호출하지 않는다."""

from copy import deepcopy

import pytest

from fuelroute.calculations.ranking import rank_candidates
from fuelroute.schemas.recommendation import RecommendationRequest


def candidate(station_id: str, price: float = 1800, seconds: float = 60) -> dict:
    """실제 값으로 오해하지 않을 합성 주유소 평가를 만든다. 좌표·가격은 테스트 전용이다."""
    return {
        "station_id": station_id,
        "station": {
            "longitude": 127,
            "latitude": 37,
            "station_id": station_id,
            "name": station_id,
            "fuel_type": "gasoline",
            "price_krw_per_l": price,
            "price_observed_at": None,
            "address": None,
            "brand": None,
            "facilities": {},
        },
        "eligible": True,
        "reason_codes": [],
        "reach_distance_m": 100000,
        "detour_distance_m": 1000,
        "detour_duration_s": seconds,
    }


@pytest.mark.parametrize(
    ("kind", "reason"),
    [
        ("unsafe", "UNREACHABLE"),
        ("time", "DETOUR_TIME_EXCEEDED"),
        ("fuel", "FUEL_TYPE_MISMATCH"),
        ("price", "MISSING_PRICE"),
        ("cost", "MISSING_ROUTE_COST"),
        ("id", "STATION_ID_MISMATCH"),
    ],
)
def test_cheap_candidate_cannot_bypass_hard_constraints(kind: str, reason: str) -> None:
    """최저가와 eligible=True가 있어도 안전·유종·필수 비용/가격 검증을 우회하지 못한다."""
    cheap = candidate("cheap", 1000)
    if kind == "unsafe":
        cheap["reach_distance_m"] = 144001
    elif kind == "time":
        cheap["detour_duration_s"] = 301
    elif kind == "fuel":
        cheap["station"]["fuel_type"] = "diesel"
    elif kind == "price":
        cheap["station"]["price_krw_per_l"] = None
    elif kind == "cost":
        cheap["reach_distance_m"] = None
    else:
        cheap["station"]["station_id"] = "other"
    request = RecommendationRequest(
        candidates=[cheap, candidate("valid")],
        fuel_type="gasoline",
        safe_range_km=144,
        max_detour_min=5,
    )
    assert rank_candidates(request).recommended.station_id == "valid"
    assert reason in rank_candidates(request).excluded_candidates[0].reason_codes


def test_ranking_is_deterministic_and_has_no_weights() -> None:
    """가격/시간 우선과 동가 시간·거리·ID 정렬을 확인하고 원본 평가를 수정하지 않는다."""
    values = [candidate("B", 1800, 60), candidate("A", 1800, 60), candidate("C", 1810, 30)]
    before = deepcopy(values)
    request = RecommendationRequest(candidates=values, fuel_type="gasoline", safe_range_km=144)
    assert [item.station_id for item in rank_candidates(request).ranked_candidates] == [
        "A",
        "B",
        "C",
    ]
    assert (
        rank_candidates(
            request.model_copy(update={"sort_by": "detour_time"})
        ).recommended.station_id
        == "C"
    )
    assert values == before


def test_empty_failures_boundaries_and_duplicate_ids() -> None:
    """0건·제공사 실패를 명시하고 상한 경계는 허용하며 중복 ID는 거절한다."""
    request = RecommendationRequest(candidates=[], fuel_type="gasoline", safe_range_km=100)
    assert rank_candidates(request).recommended is None
    item = candidate("A", seconds=300)
    request = RecommendationRequest(
        candidates=[item], fuel_type="gasoline", safe_range_km=100, max_detour_min=5
    )
    assert rank_candidates(request).recommended.station_id == "A"
    item["reason_codes"] = ["NO_ROUTE"]
    assert (
        rank_candidates(
            RecommendationRequest(**{**request.model_dump(), "candidates": [item]})
        ).recommended
        is None
    )
    with pytest.raises(ValueError):
        RecommendationRequest(candidates=[item, item], fuel_type="gasoline", safe_range_km=100)
