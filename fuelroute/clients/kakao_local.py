"""Kakao Local 주소·장소 검색으로 입력 지명을 WGS84 좌표로 해석한다."""

import httpx
from pydantic import BaseModel, Field

from fuelroute.clients.http import get_json
from fuelroute.core.config import Settings
from fuelroute.core.contracts import ToolResult
from fuelroute.core.errors import ProbeFailure
from fuelroute.core.results import run_probe
from fuelroute.schemas.locations import LocationCandidate, LocationMatchSet, LocationQuery

LOCAL_BASE_URL = "https://dapi.kakao.com/v2/local/search"


class _LocalMeta(BaseModel):
    """검색 응답의 전체 건수와 마지막 페이지 여부를 엄격히 검사한다."""

    total_count: int = Field(ge=0, strict=True)
    is_end: bool = Field(strict=True)


def _parse_matches(
    body: dict, query: str, search_type: str, call_count: int
) -> LocationMatchSet | None:
    """카카오 검색 응답을 좌표·이름·주소와 모호성 상태로 정규화한다.

    0건만 None이며 누락·잘못된 좌표·개수 불일치는 ValueError/ValidationError다.
    전체 결과 1건이고 마지막 페이지일 때만 selected를 채운다. 기하 평균·거리순 임의
    선택·주소 추측은 없다. REGION/ROAD는 area로 표시해 상세 주소와 구분한다.
    """
    meta = _LocalMeta.model_validate(body.get("meta"))
    documents = body.get("documents")
    if not isinstance(documents, list) or len(documents) > 5:
        raise ValueError("검색 문서 배열을 확인하세요.")
    if meta.total_count == 0:
        if documents or not meta.is_end:
            raise ValueError("빈 결과의 건수·페이지가 맞지 않습니다.")
        return None
    if not documents:
        raise ValueError("검색 결과가 누락되었습니다.")
    candidates = []
    for document in documents:
        if not isinstance(document, dict):
            raise ValueError("검색 문서 객체가 필요합니다.")
        if search_type == "address":
            kind = document.get("address_type")
            if kind not in {"REGION", "ROAD", "REGION_ADDR", "ROAD_ADDR"}:
                raise ValueError("주소 결과 유형을 확인하세요.")
            road = document.get("road_address")
            candidates.append(
                LocationCandidate(
                    name=document.get("address_name"),
                    address=document.get("address_name"),
                    road_address=road.get("address_name") if isinstance(road, dict) else None,
                    longitude=document.get("x"),
                    latitude=document.get("y"),
                    source="kakao_address",
                    match_level="area" if kind in {"REGION", "ROAD"} else "address",
                )
            )
        else:
            candidates.append(
                LocationCandidate(
                    name=document.get("place_name"),
                    address=document.get("address_name") or None,
                    road_address=document.get("road_address_name") or None,
                    place_id=document.get("id"),
                    place_url=document.get("place_url") or None,
                    longitude=document.get("x"),
                    latitude=document.get("y"),
                    source="kakao_keyword",
                    match_level="place",
                )
            )
    unique = meta.total_count == 1 and meta.is_end and len(candidates) == 1
    return LocationMatchSet(
        query=query,
        status="resolved" if unique else "ambiguous",
        search_type=search_type,
        total_count=meta.total_count,
        candidates=candidates,
        selected=candidates[0] if unique else None,
        api_call_count=call_count,
    )


async def resolve_location(
    query: str, settings: Settings, client: httpx.AsyncClient
) -> ToolResult[dict]:
    """지명·주소를 Kakao Local로 검색해 유일한 위치만 자동 확정한다.

    REST 키와 카카오맵 활성화가 필요하다. 주소 검색을 먼저 1회 수행하고 0건일 때만
    키워드 검색을 1회 추가한다. 각 첫 페이지 5건만 요청하며 전체 건수가 여러 개면
    후보를 반환해 사용자가 상세 위치를 보완하게 한다. '대구'처럼 지역 결과가 유일하면
    좌표를 반환하되 match_level=area다. 사용자 GPS나 도로 진입점으로 단정하지 않는다.
    빈 결과 EMPTY_RESULT, 설정/권한/한도/통신/형식 오류는 공통 실패다. 예: '서울'은
    API가 반환한 좌표만 사용한다. 재시도·다른 제공사 대체·DB·키 출력은 없고 client
    종료는 호출자 책임이다. 이 함수 자체는 GMS·LLM Tool 레지스트리를 사용하지 않는다.
    """

    async def operation() -> dict:
        """검증한 문자열을 순차 검색하고 원본 응답을 제거한 공통 위치 데이터를 반환한다."""
        request = LocationQuery(query=query)
        for count, search_type in enumerate(("address", "keyword"), start=1):
            params = {"query": request.query, "size": 5, "page": 1}
            if search_type == "address":
                params["analyze_type"] = "exact"
            body = await get_json(
                client,
                f"{LOCAL_BASE_URL}/{search_type}.json",
                params=params,
                headers={
                    "Authorization": "KakaoAK " + settings.kakao_rest_api_key.get_secret_value()
                },
                timeout=settings.http_timeout_seconds,
                rate_limit_codes=(-10,),
            )
            try:
                matches = _parse_matches(body, request.query, search_type, count)
            except (ValueError, TypeError) as exc:
                raise ProbeFailure("INVALID_RESPONSE", "위치 검색 응답을 확인하세요.") from exc
            if matches is not None:
                return matches.model_dump(mode="json")
        raise ProbeFailure(
            "EMPTY_RESULT", "일치하는 위치가 없습니다. 상세 주소나 장소명을 알려주세요."
        )

    return await run_probe(settings, "kakao_local", "resolve_location", operation)
