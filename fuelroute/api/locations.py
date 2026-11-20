"""Frontend 위치 선택기를 위한 Kakao Local 후보 조회 API."""

from fastapi import APIRouter, Query, Request

from fuelroute.agent.location_search import search_location_candidates
from fuelroute.api.chat import api_settings
from fuelroute.api.errors import ApiFailure
from fuelroute.schemas.locations import LocationMatchSet

router = APIRouter(prefix="/api/locations", tags=["location-selector"])


@router.get("/resolve", response_model=LocationMatchSet)
async def resolve_location_candidates(
    request: Request,
    query: str = Query(
        min_length=1, max_length=200, description="상위 행정구역을 포함한 위치 검색어"
    ),
) -> LocationMatchSet:
    """선택기의 상세 검색어를 Kakao Local 후보 목록으로 반환한다.

    Frontend가 시·도, 시·군·구, 읍·면·동을 먼저 선택하고 건물·학교·사업장명을 입력한 뒤
    호출한다. ``query``는 네 행정/장소 값을 공백으로 합친 필수 1~200자 문자열이며,
    서버 설정에는 Kakao REST 키가 필요하다. 주소 검색 후 필요한 경우 장소 검색을
    수행하고 최대 5개 WGS84 후보를 반환한다. 단일 결과도 사용자가 확인할 수 있게
    목록으로 반환한다. 빈 결과는 404, 입력 오류는 422, 인증·통신·제공사 실패는
    비밀값이나 원문 응답을 노출하지 않는 503 공통 오류로 변환한다. 요청은 DB,
    LLM, 세션 캐시를 변경하지 않으며 테스트에서는 ``app.state.location_resolver``로
    외부 호출을 대체할 수 있다.

    예: ``GET /api/locations/resolve?query=경상북도%20구미시%20진평동%20삼성전자``는
    ``LocationMatchSet``을 반환한다. 행정구역 전체 목록이나 장소 영업 상태는 이
    endpoint의 책임이 아니다.
    """
    settings = api_settings(request)
    resolver = getattr(request.app.state, "location_resolver", search_location_candidates)
    result = await resolver(query, settings)
    if not result.ok or result.data is None:
        code = result.error.code if result.error is not None else "LOCATION_ERROR"
        if code == "INVALID_INPUT":
            raise ApiFailure(422, code, "위치 검색어를 확인하세요.")
        if code == "EMPTY_RESULT":
            raise ApiFailure(
                404, code, "일치하는 위치가 없습니다. 상위 지역과 상세 이름을 확인하세요."
            )
        raise ApiFailure(503, code, "위치 검색 서비스를 사용할 수 없습니다.")
    return LocationMatchSet.model_validate(result.data)
