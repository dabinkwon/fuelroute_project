"""F3018 정적 대시보드의 필수 구조·API 연결·보안 경계를 검증한다."""

import json
import re
from pathlib import Path


def frontend_text(name: str) -> str:
    """frontend/dist의 UTF-8 파일 하나를 읽어 테스트 문자열로 반환한다.

    name은 고정 테스트 파일명이며 경로 구분자 입력을 받지 않는다. 파일 읽기 외 수정·서버·
    네트워크 부작용은 없다. 예: frontend_text('index.html').
    """
    assert name in {"index.html", "styles.css", "app.js", "config.js", "regions.js"}
    return (Path("frontend/dist") / name).read_text(encoding="utf-8")


def test_dashboard_has_reference_layout_and_accessible_controls() -> None:
    """사이드 레일·상단 탭·큰 추천 카드·지표/근거/플랜 영역과 label을 갖춘다."""
    html = frontend_text("index.html")
    required = [
        'class="side-rail"',
        'class="topbar"',
        'class="hero-card card"',
        'id="chatForm"',
        'id="recommendation"',
        'id="acceptPlanButton"',
        'id="saved-plans"',
        'id="evidence"',
        'id="planMemo"',
        'id="editMemo"',
        'id="executionDialog"',
        'id="executionTimeline"',
        'id="originProvince"',
        'id="originDistrict"',
        'id="originNeighborhood"',
        'id="originCandidate"',
        'id="destinationProvince"',
        'id="destinationDistrict"',
        'id="destinationNeighborhood"',
        'id="destinationCandidate"',
        'id="fuelType"',
        'id="sortBy"',
        'id="remainingRange"',
        'id="priceOnlyMode"',
        'id="reserveRange"',
        'for="messageInput"',
        'aria-live="polite"',
        '<script src="/app.js" defer></script>',
        '<script src="/config.js"></script>',
    ]
    assert all(item in html for item in required)
    assert "연료량" not in html and "평균 연비" not in html
    assert "주행 가능 거리" in html and "안전 여유" in html


def test_frontend_calls_chat_and_full_plan_crud() -> None:
    """브라우저 코드가 대화와 계획 POST/GET/PATCH/DELETE를 실제 API 경로로 연결한다."""
    html = frontend_text("index.html")
    script = frontend_text("app.js")
    assert 'apiFetch("/api/chat"' in script
    assert "apiFetch(`/api/locations/resolve?query=" in script
    assert 'apiFetch("/api/plans"' in script
    assert 'method: "POST"' in script
    assert 'method: "PATCH"' in script
    assert 'method: "DELETE"' in script
    assert "buildPlanPayload" in script and "rest_area_info" in script
    assert "fuelroute.session_id" in script
    assert "buildPlanUpdatePayload" in script
    assert "payload.memo" in script and "plan.memo" in script
    assert 'if (chat.status === "recommended") elements.messageInput.value = "";' in script
    assert "REGION_HIERARCHY" in script and "updateNeighborhoodOptions" in script
    assert "buildStructuredRequest" in script and "fields.neighborhood.value" in script
    assert 'elements.sortBy.value === "detour_time"' in script
    assert "추천 기준은 ${sortBy}" in script
    assert "candidate.longitude" in script and "candidate.latitude" in script
    assert "updateReserveMode" in script
    assert "elements.priceOnlyMode.checked" in script
    assert "안전 여유를 적용하지 않기로 선택" in script
    assert 'id="originDetails"' in html and 'id="destinationDetails"' in html
    assert 'class="advanced-request"' in html
    assert 'elements[`${side}Details`].open = false' not in script
    assert ".dashboard:not(.has-results)>.metric-card" in frontend_text("styles.css")


def test_region_data_contains_official_three_level_choices() -> None:
    """법정동 생성 자료가 대구 북구와 구미시의 대표 읍·면·동을 선택지로 제공한다."""
    regions = frontend_text("regions.js")
    prefix = "window.FUELROUTE_REGIONS="
    payload = regions[regions.index(prefix) + len(prefix) :].strip().removesuffix(";")
    hierarchy = json.loads(payload)
    assert {"동천동", "학정동"} <= set(hierarchy["대구광역시"]["북구"])
    assert "진평동" in hierarchy["경상북도"]["구미시"]
    assert sum(len(districts) for districts in hierarchy.values()) == 256


def test_plans_and_evidence_are_dialogs_outside_dashboard() -> None:
    """저장 플랜과 추천 근거는 본문 카드가 아니라 메뉴 버튼으로 여는 독립 모달이다."""
    html = frontend_text("index.html")
    main_end = html.index("</main>")
    assert main_end < html.index('id="plansDialog"') < html.index('id="saved-plans"')
    assert main_end < html.index('id="evidenceDialog"') < html.index('id="evidence"')
    assert html.count('data-dialog-target="plansDialog"') == 2
    assert html.count('data-dialog-target="evidenceDialog"') == 2
    script = frontend_text("app.js")
    assert "openDashboardDialog" in script and "closeDashboardDialog" in script


def test_execution_modal_connects_ordered_log_detail_and_delete() -> None:
    """F3019 모달이 로그 목록·상세·삭제와 상태/Tool/재계획 타임라인을 연결한다."""
    html = frontend_text("index.html")
    script = frontend_text("app.js")
    assert html.count('data-dialog-target="executionDialog"') == 2
    assert 'id="executionList"' in html and 'id="deleteLogButton"' in html
    assert 'apiFetch("/api/logs?limit=50")' in script
    assert "`/api/logs/${encodeURIComponent(executionId)}`" in script
    assert 'method: "DELETE"' in script
    assert "renderExecutionEvent" in script and "buildExecutionTags" in script
    assert "REPLAN_TRIGGERED" in script and "REPLAN_LIMIT_REACHED" in script
    assert 'setExecutionState("running", "Agent 실행 중")' in script
    assert "log_delivery_failures" in script
    assert "chat.elapsed_ms / 1000" in script
    assert "groupExecutionEvents" in script and "event.request_id" in script
    assert "ACTION_LABELS" in script and "후보별 실제 도달 거리" in script


def test_recommendation_evidence_compares_up_to_five_ranked_candidates() -> None:
    """추천 근거가 평가 후보 최대 5곳의 이름·가격·거리·안전·우회 비용을 구체화한다."""
    script = frontend_text("app.js")
    assert "buildRecommendationEvidence" in script
    assert "ranked.slice(0, 5)" in script
    for phrase in ["원/L", "실제 도달", "안전거리 내 여유", "우회시간 미확인"]:
        assert phrase in script
    assert "전체 경로의 모든 주유소나 전역 최저가를 뜻하지 않는다" in script


def test_frontend_does_not_embed_secrets_or_external_api_keys() -> None:
    """정적 배포 파일에 환경변수명·인증 헤더·제공사 키나 실제 비밀값 경로를 넣지 않는다."""
    combined = "\n".join(
        frontend_text(name)
        for name in ("index.html", "styles.css", "app.js", "config.js", "regions.js")
    )
    forbidden = [
        "GMS_API_KEY",
        "OPINET_API_KEY",
        "KAKAO_REST_API_KEY",
        "DATA_GO_KR_SERVICE_KEY",
        "Authorization",
        ".env",
        "/v1/chat/completions",
    ]
    assert all(value not in combined for value in forbidden)


def test_frontend_failure_path_uses_only_public_error_fields() -> None:
    """NF3002 화면 실패 경로가 응답 원문·stack 대신 공개 message/code와 textContent만 쓴다."""
    script = frontend_text("app.js")
    assert "body?.error?.message" in script and "body?.error?.code" in script
    assert "response.text()" not in script and "error.stack" not in script
    assert "elements.toast.textContent = message" in script
    assert "JSON.stringify(error)" not in script


def test_javascript_functions_have_jsdoc_and_no_inline_event_handlers() -> None:
    """이름 있는 브라우저 함수마다 JSDoc이 있으며 HTML에 inline JavaScript가 없다."""
    script = frontend_text("app.js")
    functions = re.findall(r"(?:async\s+)?function\s+([A-Za-z0-9_]+)\s*\(", script)
    assert functions
    for name in functions:
        pattern = rf"/\*\*[\s\S]*?\*/\s*(?:async\s+)?function\s+{re.escape(name)}\s*\("
        assert re.search(pattern, script), f"JSDoc missing: {name}"
    html = frontend_text("index.html")
    assert not re.search(r"\son(?:click|submit|change|input)=", html, re.IGNORECASE)


def test_css_uses_vivid_lime_and_responsive_breakpoints() -> None:
    """선명한 라임과 상단 플랜→3개 지표→조건/균형의 반응형 단일 흐름을 정의한다."""
    css = frontend_text("styles.css")
    assert "--lime:#dfff34" in css
    assert ".dashboard{grid-template-columns:repeat(6,minmax(0,1fr))" in css
    assert ".hero-card{grid-column:1/-1" in css
    assert ".metric-card{grid-column:span 2}" in css
    assert ".condition-card{grid-column:span 4}" in css
    assert ".detour-card{grid-column:span 2}" in css
    assert ".dashboard-dialog" in css
    assert "@media(max-width:880px)" in css
    assert "@media(max-width:580px)" in css
    assert "prefers-reduced-motion" in css
