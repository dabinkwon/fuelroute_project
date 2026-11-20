"""NF3007 외부 API 출처·Endpoint·계정 한도·요청 예산 문서의 일치성을 검사한다."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read_project_file(path: str) -> str:
    """저장소 상대경로의 UTF-8 텍스트를 NF3007 정적 대조용으로 반환한다.

    path는 테스트에 하드코딩한 소스·문서 파일이며 사용자 입력을 받지 않는다. 파일이 없거나
    UTF-8이 아니면 예외를 그대로 발생시켜 출처 검사를 실패시킨다. 네트워크·파일 쓰기·환경
    변경은 없고 예: `read_project_file('docs/API_USAGE.md')`는 현재 이용조건 문서를 반환한다.
    """
    return (ROOT / path).read_text(encoding="utf-8")


def test_provider_endpoints_match_documented_official_sources() -> None:
    """Opinet·Kakao Mobility·휴게소의 구현 Endpoint와 공식 출처 문서를 함께 고정한다.

    세 외부 데이터 API 클라이언트의 실제 URL과 `API_USAGE.md`의 공식 제공기관 링크를 읽는다.
    구현이 다른 호스트로 바뀌거나 출처 링크가 빠지면 assertion으로 실패한다. API 키·HTTP·DB는
    사용하지 않으며 URL 존재만 확인하고 약관의 법적 해석을 자동화하지 않는다.
    """
    usage = read_project_file("docs/API_USAGE.md")
    expected = {
        "fuelroute/clients/opinet.py": "https://www.opinet.co.kr/api/",
        "fuelroute/clients/kakao_routes.py": ("https://apis-navi.kakaomobility.com/v1/directions"),
        "fuelroute/clients/rest_area.py": (
            "https://api.data.go.kr/openapi/tn_pubr_public_rest_area_api"
        ),
    }
    for path, endpoint in expected.items():
        assert endpoint in read_project_file(path)
    official_sources = (
        "https://www.opinet.co.kr/user/custapi/custApiInfo.do",
        "https://developers.kakaomobility.com/guide/navi-api/directions.html",
        "https://developers.kakao.com/terms/ko/site-policies",
        "https://www.data.go.kr/data/15025446/standard.do",
        "https://www.data.go.kr/ugs/selectPortalPolicyView.do",
    )
    assert all(source in usage for source in official_sources)


def test_account_limits_are_distinguished_from_public_policy() -> None:
    """사용자 계정 확인값과 변경 가능한 공개 정책·미확인 Local 쿼터를 구분해 기록한다.

    API_USAGE와 TASKS에서 Opinet300회, Mobility10000회, 휴게소 개발계정10000회/활용기간을
    확인한다. Kakao Local 수치를 자동차 길찾기 한도에서 복사하지 않고 미확인으로 유지해야
    한다. 문자열 대조만 수행하며 콘솔을 대신 조회하거나 현재 잔여량을 보장하지 않는다.
    """
    usage = read_project_file("docs/API_USAGE.md")
    tasks = read_project_file("docs/TASKS.md")
    assert "Opinet 일반 API | 300회/일" in usage
    assert "Kakao Mobility 자동차 길찾기 | 10,000회/일" in usage
    assert "전국휴게소정보표준데이터 | 개발계정, 10,000회/일, 2028-11-18까지" in usage
    assert "Kakao Local 주소/장소 검색 | 실제 앱 쿼터 수치 미확인" in usage
    assert "사용자가 확인해 전달한 값" in tasks


def test_runtime_request_budgets_and_data_limits_are_documented() -> None:
    """현재 실행 상한·캐시·데이터 적용 한계가 코드 사용 범위와 함께 문서화됐는지 검사한다.

    기본 run의 GMS3·Local4·Opinet14·Mobility19·휴게소1논리조회/최대10HTTP, 재계획2회와
    평가9곳을 확인한다. 휴게소 매칭/실시간 운영 미확인과 전체 경로 최저가 비보장도 필수다.
    반환·외부 호출은 없으며 숫자 정책이 바뀌면 코드와 문서를 함께 갱신하도록 실패한다.
    """
    usage = read_project_file("docs/API_USAGE.md")
    required = (
        "| GMS | 3회 |",
        "| Kakao Local | 4회 |",
        "| Opinet | 14회 |",
        "| Kakao Mobility | 19회 |",
        "| 휴게소 | 1논리조회 / 최대10 HTTP |",
        "재계획 최대 2회",
        "전체 최대 9곳 평가",
        "전체 경로를 빠짐없이 검색하거나 모든 후보의 최저가를 보장",
        "같은 휴게소·주유소 또는 실시간 시설 상태라는 확인되지 않은 사실을 추가하지 않는다",
    )
    assert all(item in usage for item in required)
