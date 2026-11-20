"""위치 선택 UI가 사용하는 Kakao Local 후보 조회 서비스."""

import httpx

from fuelroute.clients.kakao_local import resolve_location
from fuelroute.core.config import Settings
from fuelroute.core.contracts import ToolResult


async def search_location_candidates(query: str, settings: Settings) -> ToolResult[dict]:
    """상위 행정구역이 포함된 검색어를 Kakao Local 위치 후보로 변환한다.

    API 라우터가 위치 선택기의 상세 장소를 조회할 때 호출하며, ``settings``에
    ``KAKAO_REST_API_KEY``가 준비되어 있어야 한다. ``query``는 시·도, 시·군·구,
    읍·면·동과 사용자가 입력한 건물·학교·사업장명을 합친 1~200자 문자열이다.
    주소 검색을 먼저 하고 필요할 때 키워드 검색으로 보완하는 규칙, 최대 5개
    후보와 WGS84 경도·위도 정규화, 오류 분류는 ``resolve_location``이 담당한다.
    성공과 실패를 모두 ``ToolResult[dict]``로 반환하며 빈 결과·설정 누락·인증
    실패·타임아웃도 예외 대신 해당 오류 계약에 담긴다. 매 호출마다 새 비동기
    HTTP 클라이언트를 열고 닫으며 DB·LLM·세션 캐시는 사용하지 않는다.

    예: ``await search_location_candidates("경상북도 구미시 진평동 삼성전자", settings)``는
    선택 UI에 표시할 좌표 후보를 반환한다. 이 함수는 행정구역 목록을 제공하지
    않으며 Kakao 응답의 최신성이나 장소의 실제 영업 여부를 보장하지 않는다.
    """
    async with httpx.AsyncClient() as client:
        return await resolve_location(query, settings, client)
