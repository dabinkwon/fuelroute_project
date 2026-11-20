"use strict";

const LOCAL_API_BASE = "http://127.0.0.1:8000";
const state = {
  sessionId: window.localStorage.getItem("fuelroute.session_id"),
  latestChat: null,
  plans: [],
  editingPlan: null,
  logs: [],
  selectedLog: null,
  activeExecutionId: null,
};
const EVENT_LABELS = {
  STEP_STARTED: "단계 시작", STEP_COMPLETED: "단계 완료", TOOL_REQUESTED: "Tool 호출 요청",
  TOOL_SUCCEEDED: "Tool 호출 성공", TOOL_FAILED: "Tool 호출 실패",
  CONSTRAINT_EVALUATED: "제약 조건 판정", REPLAN_TRIGGERED: "재계획 실행",
  REPLAN_LIMIT_REACHED: "재계획 종료", LLM_DECISION: "Agent 판단",
  FINAL_RESPONSE_CREATED: "최종 응답 생성",
};
const STEP_LABELS = {
  execution: "전체 실행", analyze: "요청 분석", resolve_locations: "위치 확인",
  build_plan: "작업 계획", execute_plan: "Tool 실행", judge: "후보 판정",
  replan: "재계획", enrich: "보조정보 확인", respond: "최종 응답",
};
const TOOL_LABELS = {
  get_driving_route: "자동차 경로 조회", search_gas_stations: "주유소 후보 조회",
  get_station_details: "주유소 상세 조회", search_rest_area_info: "휴게소 정보 조회",
};
const REGION_HIERARCHY = {
  "서울특별시": ["강남구", "강동구", "강북구", "강서구", "관악구", "광진구", "구로구", "금천구", "노원구", "도봉구", "동대문구", "동작구", "마포구", "서대문구", "서초구", "성동구", "성북구", "송파구", "양천구", "영등포구", "용산구", "은평구", "종로구", "중구", "중랑구"],
  "부산광역시": ["강서구", "금정구", "기장군", "남구", "동구", "동래구", "부산진구", "북구", "사상구", "사하구", "서구", "수영구", "연제구", "영도구", "중구", "해운대구"],
  "대구광역시": ["군위군", "남구", "달서구", "달성군", "동구", "북구", "서구", "수성구", "중구"],
  "인천광역시": ["강화군", "계양구", "남동구", "동구", "미추홀구", "부평구", "서구", "연수구", "옹진군", "중구"],
  "광주광역시": ["광산구", "남구", "동구", "북구", "서구"],
  "대전광역시": ["대덕구", "동구", "서구", "유성구", "중구"],
  "울산광역시": ["남구", "동구", "북구", "울주군", "중구"],
  "세종특별자치시": ["세종특별자치시"],
  "경기도": ["가평군", "고양시", "과천시", "광명시", "광주시", "구리시", "군포시", "김포시", "남양주시", "동두천시", "부천시", "성남시", "수원시", "시흥시", "안산시", "안성시", "안양시", "양주시", "양평군", "여주시", "연천군", "오산시", "용인시", "의왕시", "의정부시", "이천시", "파주시", "평택시", "포천시", "하남시", "화성시"],
  "강원특별자치도": ["강릉시", "고성군", "동해시", "삼척시", "속초시", "양구군", "양양군", "영월군", "원주시", "인제군", "정선군", "철원군", "춘천시", "태백시", "평창군", "홍천군", "화천군", "횡성군"],
  "충청북도": ["괴산군", "단양군", "보은군", "영동군", "옥천군", "음성군", "제천시", "증평군", "진천군", "청주시", "충주시"],
  "충청남도": ["계룡시", "공주시", "금산군", "논산시", "당진시", "보령시", "부여군", "서산시", "서천군", "아산시", "예산군", "천안시", "청양군", "태안군", "홍성군"],
  "전북특별자치도": ["고창군", "군산시", "김제시", "남원시", "무주군", "부안군", "순창군", "완주군", "익산시", "임실군", "장수군", "전주시", "정읍시", "진안군"],
  "전라남도": ["강진군", "고흥군", "곡성군", "광양시", "구례군", "나주시", "담양군", "목포시", "무안군", "보성군", "순천시", "신안군", "여수시", "영광군", "영암군", "완도군", "장성군", "장흥군", "진도군", "함평군", "해남군", "화순군"],
  "경상북도": ["경산시", "경주시", "고령군", "구미시", "김천시", "문경시", "봉화군", "상주시", "성주군", "안동시", "영덕군", "영양군", "영주시", "영천시", "예천군", "울릉군", "울진군", "의성군", "청도군", "청송군", "칠곡군", "포항시"],
  "경상남도": ["거제시", "거창군", "고성군", "김해시", "남해군", "밀양시", "사천시", "산청군", "양산시", "의령군", "진주시", "창녕군", "창원시", "통영시", "하동군", "함안군", "함양군", "합천군"],
  "제주특별자치도": ["서귀포시", "제주시"],
};
const selectedLocations = { origin: null, destination: null };
const locationCandidateLists = { origin: [], destination: [] };
const elements = Object.fromEntries([
  "main", "chatForm", "messageInput", "submitButton", "newChatButton", "requestChip",
  "recommendation", "savePanel", "saveState", "planDate", "planStartTime", "planEndTime", "planMemo",
  "acceptPlanButton", "safeRangeValue", "reachValue", "detourValue", "remainingRangeValue",
  "usableRatio", "reserveRatio", "rangeBalanceBar", "detourDistanceValue", "recommendationScope",
  "evidenceCount", "evidenceLead", "evidenceList", "planCount", "plansList", "refreshPlansButton",
  "connectionDot", "connectionText", "toast", "editDialog", "editPlanForm", "editPlanId",
  "editRevision", "editPlanDate", "editStartTime", "editEndTime", "editMemo", "closeEditButton",
  "cancelEditButton", "plansDialog", "evidenceDialog", "closePlansDialog", "closeEvidenceDialog",
  "executionDialog", "closeExecutionDialog", "refreshLogsButton", "deleteLogButton",
  "executionTabDot", "executionTabLabel", "executionState", "executionCount", "executionList",
  "executionDetailTitle", "executionDetailMeta", "executionTimeline",
  "originProvince", "originDistrict", "originDetail", "originSearchButton", "originCandidate",
  "destinationProvince", "destinationDistrict", "destinationDetail", "destinationSearchButton",
  "destinationCandidate", "fuelType", "remainingRange", "reserveRange",
].map(mapElementById));

/**
 * DOM ID 문자열을 Object.fromEntries용 `[key, element]` 쌍으로 변환한다.
 * 모든 ID는 index.html에 존재해야 하며, 누락되면 null을 그대로 반환해 초기 개발 오류가
 * 숨겨지지 않게 한다. DOM 조회 외 저장·네트워크 부작용은 없다.
 * @param {string} id 조회할 element ID.
 * @returns {[string, Element|null]} key와 DOM 요소 쌍.
 */
function mapElementById(id) {
  return [id, document.getElementById(id)];
}

/**
 * 현재 페이지에서 사용할 Backend 기준 주소를 결정한다.
 * 로컬 정적 미리보기는 FastAPI 기본 포트 8000을 사용하고, 배포에서는
 * `window.FUELROUTE_API_BASE`가 있으면 우선한다. 끝 슬래시는 제거하며 부작용은 없다.
 * @returns {string} API origin 또는 같은 origin을 뜻하는 빈 문자열.
 */
function getApiBase() {
  const configured = typeof window.FUELROUTE_API_BASE === "string" ? window.FUELROUTE_API_BASE : "";
  if (configured.trim()) return configured.trim().replace(/\/$/, "");
  const local = ["127.0.0.1", "localhost"].includes(window.location.hostname);
  return local && window.location.port !== "8000" ? LOCAL_API_BASE : "";
}

/**
 * FastAPI JSON Endpoint를 호출하고 공통 오류 본문을 안전한 Error로 바꾼다.
 * 204는 null, 그 밖의 성공은 JSON을 반환한다. 실패에서는 공개 message/code만 사용하며
 * 내부 본문·인증정보를 복사하지 않는다. 네트워크와 Endpoint별 서버 변경 부작용이 있다.
 * @param {string} path `/api/...` 상대 경로.
 * @param {RequestInit} [options={}] fetch 옵션.
 * @returns {Promise<any>} 성공 JSON 또는 null.
 * @throws {Error} 네트워크, JSON 또는 HTTP 실패.
 */
async function apiFetch(path, options = {}) {
  const response = await fetch(`${getApiBase()}${path}`, {
    ...options,
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
  });
  if (response.status === 204) return null;
  const body = await response.json().catch(parseFailureBody);
  if (!response.ok) {
    const error = new Error(body?.error?.message || "요청을 처리하지 못했습니다.");
    error.code = body?.error?.code || "HTTP_ERROR";
    throw error;
  }
  return body;
}

/**
 * JSON 파싱 실패 응답의 원문을 공개하지 않고 빈 객체로 대체한다.
 * apiFetch가 일반 오류 문구를 선택하도록 하며 인자·부작용은 없다.
 * @returns {object} 빈 공개 오류 객체.
 */
function parseFailureBody() { return {}; }

/**
 * 짧은 성공/오류 알림을 우측 하단에 표시하고 3.2초 뒤 숨긴다.
 * message는 정제된 문장이고 isError는 색상만 바꾼다. DOM/timer 외 부작용은 없다.
 * @param {string} message 표시 문장.
 * @param {boolean} [isError=false] 오류 스타일 여부.
 * @returns {void}
 */
function showToast(message, isError = false) {
  elements.toast.textContent = message;
  elements.toast.classList.toggle("error", isError);
  elements.toast.classList.add("show");
  window.clearTimeout(showToast.timerId);
  showToast.timerId = window.setTimeout(hideToast, 3200);
}

/**
 * 현재 toast의 표시 class를 제거한다.
 * setTimeout에서 호출되며 DOM 표시 외 부작용은 없다.
 * @returns {void}
 */
function hideToast() { elements.toast.classList.remove("show"); }

/**
 * Backend 접근 가능 여부를 사이드 레일 점과 접근성 문구에 반영한다.
 * 실제 재연결이나 재시도는 하지 않고 DOM만 바꾼다.
 * @param {boolean} online health 성공 여부.
 * @returns {void}
 */
function setConnectionState(online) {
  elements.connectionDot.classList.toggle("online", online);
  elements.connectionDot.classList.toggle("offline", !online);
  elements.connectionText.textContent = online ? "Backend 연결됨" : "Backend 연결 안 됨";
}

/**
 * `/health`를 한 번 호출해 Backend 접근 가능 여부를 표시한다.
 * 외부 API 인증 상태는 판정하지 않는다. 실패는 연결 점에만 반영하고 다시 던지지 않는다.
 * @returns {Promise<void>}
 */
async function checkHealth() {
  try { await apiFetch("/health"); setConnectionState(true); }
  catch (_error) { setConnectionState(false); }
}

/**
 * 현재 Agent 실행 상태를 상단 탭 점·라벨과 실행 모달 제목에 동기화한다.
 * status는 idle/running/completed/partial/failed 중 하나이며 label은 사용자에게 보여줄 짧은 한국어
 * 문장이다. 실제 실행을 시작·중단하거나 로그를 만들지 않고 DOM class/text만 변경한다.
 * @param {"idle"|"running"|"completed"|"partial"|"failed"} status 표시 상태.
 * @param {string} label 상태 설명.
 * @returns {void}
 */
function setExecutionState(status, label) {
  elements.executionTabDot.className = `tab-state-dot ${status === "idle" ? "" : status}`.trim();
  elements.executionTabLabel.textContent = status === "running" ? "실행 중" : "실행 과정";
  elements.executionState.textContent = label;
}

/**
 * `/api/logs`에서 최근 실행 목록을 읽어 시간 역순 선택 목록으로 표시한다.
 * 성공 시 state.logs와 건수를 교체하고, 현재 선택 ID가 목록에 없으면 선택을 유지하지 않는다.
 * 실패는 기존 목록을 보존하고 quiet=false일 때만 정제된 toast를 표시한다. 로그를 수정하거나
 * Agent/외부 API를 실행하지 않는다.
 * @param {boolean} [quiet=false] 실패 toast를 생략할지 여부.
 * @returns {Promise<void>}
 */
async function loadExecutionLogs(quiet = false) {
  try {
    const result = await apiFetch("/api/logs?limit=50");
    state.logs = result.items || [];
    renderExecutionList();
  } catch (error) {
    if (!quiet) showToast(error.message || "실행 기록을 불러오지 못했습니다.", true);
  }
}

/**
 * 최근 실행 세션을 상태·시각·이벤트 수가 있는 선택 버튼 목록으로 그린다.
 * 실행 ID와 API 문자열은 escape하고 현재 상세 ID에는 active class를 적용한다. 빈 목록도
 * 정상 안내로 표시한다. DOM 변경 외 네트워크·DB 부작용은 없다.
 * @returns {void}
 */
function renderExecutionList() {
  elements.executionCount.textContent = `${state.logs.length}개`;
  if (!state.logs.length) {
    elements.executionList.innerHTML = '<p class="execution-empty">아직 실행 기록이 없습니다.</p>';
    return;
  }
  elements.executionList.innerHTML = state.logs.map((session) => {
    const active = session.execution_id === state.selectedLog?.session?.execution_id;
    const status = session.status === "completed" ? "완료" : session.status === "failed" ? "실패" : "실행 중";
    return `<button type="button" class="${active ? "active" : ""}" data-execution-id="${escapeHtml(session.execution_id)}"><strong>${escapeHtml(status)} · 이벤트 ${formatNumber(session.event_count, 0)}개</strong><span>${escapeHtml(formatDateTime(session.started_at))}</span></button>`;
  }).join("");
}

/**
 * 실행 ID 한 건의 상세 로그를 읽어 sequence 순서 타임라인과 상태 요약을 갱신한다.
 * executionId는 서버가 발급한 안전한 ID다. 성공 시 선택 상태와 목록 강조를 바꾸고, 실패는
 * 기존 상세를 유지한다. quiet=true이면 Chat 성공 직후 로그 전달 지연/실패를 toast로 중복
 * 알리지 않는다. GET 외 로그 수정·Agent 실행 부작용은 없다.
 * @param {string} executionId 조회할 실행 ID.
 * @param {boolean} [quiet=false] 실패 toast 생략 여부.
 * @returns {Promise<void>}
 */
async function loadExecutionDetail(executionId, quiet = false) {
  if (!executionId) return;
  try {
    const detail = await apiFetch(`/api/logs/${encodeURIComponent(executionId)}`);
    state.selectedLog = detail;
    renderExecutionList();
    renderExecutionDetail();
  } catch (error) {
    if (!quiet) showToast(error.message || "실행 상세를 불러오지 못했습니다.", true);
  }
}

/**
 * 선택한 실행 세션의 상태·기간과 모든 이벤트를 순서대로 실행 모달에 표시한다.
 * selectedLog가 없으면 빈 안내와 비활성 삭제 버튼을 유지한다. 이벤트 데이터는 renderEvent가
 * 정제해 HTML로 변환한다. DOM 변경 외 네트워크·삭제 부작용은 없다.
 * @returns {void}
 */
function renderExecutionDetail() {
  const detail = state.selectedLog;
  if (!detail) {
    elements.executionDetailTitle.textContent = "실행을 선택해주세요.";
    elements.executionDetailMeta.textContent = "단계와 Tool 이력을 발생 순서대로 표시합니다.";
    elements.executionTimeline.innerHTML = '<li class="timeline-empty">추천 요청을 완료하면 실제 실행 과정이 이곳에 표시됩니다.</li>';
    elements.deleteLogButton.disabled = true;
    return;
  }
  const session = detail.session;
  const status = session.status === "completed" ? "완료" : session.status === "failed" ? "실패" : "실행 중";
  elements.executionState.textContent = `선택 실행 ${status}`;
  elements.executionDetailTitle.textContent = `${status} · 이벤트 ${session.event_count}개`;
  elements.executionDetailMeta.textContent = `${formatDateTime(session.started_at)} 시작 · ${session.finished_at ? `${formatDateTime(session.finished_at)} 종료` : "진행 중"}`;
  elements.executionTimeline.innerHTML = detail.events.map(renderExecutionEvent).join("");
  elements.deleteLogButton.disabled = false;
}

/**
 * 한 ExecutionEvent를 한국어 설명·메타 태그가 있는 안전한 타임라인 HTML로 변환한다.
 * event_type/step/tool/payload는 서버의 정제 계약만 읽고 모든 문자열을 escape한다. 원본 Tool
 * 응답·키·좌표는 입력에 없으며 추측해 표시하지 않는다. DOM·네트워크 부작용은 없다.
 * @param {object} event `/api/logs/{id}`의 ExecutionEvent.
 * @returns {string} 타임라인 `<li>` HTML.
 */
function renderExecutionEvent(event) {
  const label = EVENT_LABELS[event.event_type] || event.event_type;
  const step = STEP_LABELS[event.step] || event.step;
  const tool = event.tool_name ? (TOOL_LABELS[event.tool_name] || event.tool_name) : null;
  const classes = ["timeline-event"];
  if (event.event_type.startsWith("TOOL_")) classes.push("tool");
  if (["TOOL_SUCCEEDED", "STEP_COMPLETED", "FINAL_RESPONSE_CREATED"].includes(event.event_type)) classes.push("success");
  if (event.event_type === "TOOL_FAILED") classes.push("failure");
  if (event.event_type.startsWith("REPLAN_")) classes.push("replan");
  const tags = buildExecutionTags(event);
  const description = describeExecutionEvent(event, step, tool);
  return `<li class="${classes.join(" ")}"><div class="timeline-event-head"><strong>${escapeHtml(`${event.sequence}. ${label}`)}</strong><time>${escapeHtml(formatDateTime(event.created_at))}</time></div><p>${escapeHtml(description)}</p>${tags.length ? `<div class="timeline-tags">${tags.map((tag) => `<span>${escapeHtml(tag)}</span>`).join("")}</div>` : ""}</li>`;
}

/**
 * 이벤트 유형별로 사용자에게 필요한 실행 의미만 짧은 한국어 문장으로 만든다.
 * step/tool은 이미 표시명으로 변환된 값이며 payload의 상태·오류·재계획 사유만 사용한다.
 * 누락값은 생성하지 않고 일반 설명으로 대체한다. DOM·저장·외부 호출 부작용은 없다.
 * @param {object} event ExecutionEvent.
 * @param {string} step 단계 표시명.
 * @param {string|null} tool Tool 표시명.
 * @returns {string} 공개 실행 설명.
 */
function describeExecutionEvent(event, step, tool) {
  const payload = event.payload || {};
  if (event.event_type === "STEP_STARTED") return `${step} 단계를 시작했습니다.`;
  if (event.event_type === "STEP_COMPLETED") return payload.status === "failed" ? `${step} 단계가 실패했습니다.` : `${step} 단계를 완료했습니다.`;
  if (event.event_type === "TOOL_REQUESTED") return `${tool || "Tool"} 요청을 보냈습니다.`;
  if (event.event_type === "TOOL_SUCCEEDED") return `${tool || "Tool"} 결과를 정상적으로 받았습니다.`;
  if (event.event_type === "TOOL_FAILED") return `${tool || "Tool"} 호출에 실패했습니다. 오류 코드: ${payload.error_code || "미확인"}`;
  if (event.event_type === "CONSTRAINT_EVALUATED") return `후보 ${payload.candidate_count ?? 0}개의 도달 가능성과 사용자 제약을 판정했습니다.`;
  if (event.event_type === "REPLAN_TRIGGERED") return `조건을 유지한 채 추가 후보 탐색을 실행했습니다. ${payload.reason || ""}`.trim();
  if (event.event_type === "REPLAN_LIMIT_REACHED") return `재계획을 종료했습니다. 종료 사유: ${payload.stop_reason || "미확인"}`;
  if (event.event_type === "FINAL_RESPONSE_CREATED") return `검증한 근거 ${payload.evidence_count ?? 0}개로 최종 응답을 만들었습니다.`;
  if (event.event_type === "LLM_DECISION") return `${step} 단계의 구조화된 판단을 기록했습니다.`;
  return `${step} 실행 상태를 기록했습니다.`;
}

/**
 * 이벤트의 정제된 메타 중 화면 판단에 필요한 값만 태그 문자열로 선택한다.
 * provider·소요시간·결과 수·재계획 횟수·재시도/재계획 가능 여부를 표시하고 입력 필드나
 * request ID 전체는 노출하지 않는다. 빈 값은 제외하며 부작용은 없다.
 * @param {object} event ExecutionEvent.
 * @returns {string[]} 표시할 메타 태그.
 */
function buildExecutionTags(event) {
  const payload = event.payload || {};
  const tags = [];
  if (event.tool_name) tags.push(TOOL_LABELS[event.tool_name] || event.tool_name);
  if (payload.provider) tags.push(`제공사 ${payload.provider}`);
  if (event.elapsed_ms != null) tags.push(`${formatNumber(event.elapsed_ms, 0)}ms`);
  if (payload.result_size != null) tags.push(`결과 ${payload.result_size}건`);
  if (payload.round_index != null) tags.push(`${Number(payload.round_index) + 1}회차`);
  if (payload.replan_count != null) tags.push(`재계획 ${payload.replan_count}회`);
  if (payload.retryable === true) tags.push("동일 요청 재시도 가능");
  if (payload.replan_allowed === true) tags.push("조건 변경 재계획 가능");
  return tags;
}

/**
 * 최근 실행 목록에서 클릭한 실행 ID의 상세 로그를 조회한다.
 * 실행 버튼이 아닌 빈 영역 클릭은 무시한다. data 값은 서버 발급 ID이며 loadExecutionDetail이
 * URL encoding한다. GET 외 부작용은 없다.
 * @param {MouseEvent} event 실행 목록 click 이벤트.
 * @returns {Promise<void>}
 */
async function handleExecutionSelection(event) {
  const button = event.target.closest("button[data-execution-id]");
  if (!button) return;
  await loadExecutionDetail(button.dataset.executionId);
}

/**
 * 사용자가 확인한 현재 실행 로그 한 건을 DELETE하고 목록·상세 화면을 갱신한다.
 * 선택 로그가 없거나 confirm을 취소하면 아무 작업도 하지 않는다. 성공 시 계획은 서버 계약에
 * 따라 유지되며 실행 세션·이벤트만 복구 불가능하게 삭제된다. 네트워크/DB 삭제 부작용이 있다.
 * @returns {Promise<void>}
 */
async function deleteSelectedExecutionLog() {
  const executionId = state.selectedLog?.session?.execution_id;
  if (!executionId || !window.confirm("이 실행 기록을 삭제할까요? 저장한 플랜은 유지됩니다.")) return;
  try {
    await apiFetch(`/api/logs/${encodeURIComponent(executionId)}`, { method: "DELETE" });
    state.selectedLog = null;
    renderExecutionDetail();
    await loadExecutionLogs();
    showToast("실행 기록을 삭제했습니다.");
  } catch (error) { showToast(error.message || "실행 기록을 삭제하지 못했습니다.", true); }
}

/**
 * 숫자를 화면용 소수 자릿수로 제한하고 비유한 값은 대시로 표시한다.
 * 단위 변환이나 DOM 변경은 없다.
 * @param {number|null|undefined} value 표시 숫자.
 * @param {number} [digits=1] 최대 소수 자릿수.
 * @returns {string} 화면용 숫자 또는 대시.
 */
function formatNumber(value, digits = 1) {
  if (value == null || !Number.isFinite(Number(value))) return "—";
  return Number(value).toLocaleString("ko-KR", { maximumFractionDigits: digits });
}

/**
 * ISO 시각을 월·일·시·분의 짧은 한국어 문자열로 바꾼다.
 * 없거나 잘못된 값은 `시간 미정`이며 서버 저장값은 변경하지 않는다.
 * @param {string|null|undefined} value ISO 8601 값.
 * @returns {string} 표시 시각.
 */
function formatDateTime(value) {
  if (!value) return "시간 미정";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "시간 미정";
  return new Intl.DateTimeFormat("ko-KR", { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" }).format(date);
}

/**
 * API 문자열의 HTML 특수문자를 entity로 바꿔 명령으로 실행되지 않게 한다.
 * 입력을 문자열로 변환하며 DOM·네트워크 부작용은 없다.
 * @param {unknown} value 표시 데이터.
 * @returns {string} HTML-safe 문자열.
 */
function escapeHtml(value) { return String(value ?? "").replace(/[&<>'"]/g, replaceHtmlCharacter); }

/**
 * HTML 특수문자 한 개를 대응 entity로 바꾼다.
 * 정규식 replace 콜백이며 부작용은 없다.
 * @param {string} character 변환 문자.
 * @returns {string} HTML entity.
 */
function replaceHtmlCharacter(character) {
  return ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;" })[character] || character;
}

/**
 * 현재 Chat 응답의 최종 추천 후보를 반환한다.
 * 추천 없음/입력 보완이면 null이고 후보를 임의 선택하지 않는다.
 * @returns {object|null} CandidateEvaluation 또는 null.
 */
function selectedCandidate() { return state.latestChat?.judgement?.recommendation?.recommended || null; }

/**
 * 추천 결과와 주행 조건을 모든 대시보드 카드에 반영한다.
 * m/s 비용은 화면에서만 km/min으로 바꾸며 API 미제공 값은 대시로 둔다. 추천 성공이면
 * 저장 패널, 입력 보완이면 질문을 표시한다. DOM 변경 외 부작용은 없다.
 * @param {object} chat F3017 ChatResponse.
 * @returns {void}
 */
function renderChat(chat) {
  const candidate = selectedCandidate();
  const conditions = chat.analysis?.conditions || {};
  const safeRange = chat.judgement?.safe_range_km;
  const remaining = conditions.remaining_range_km;
  const reserve = conditions.reserve_range_km;
  const reachKm = candidate?.reach_distance_m == null ? null : candidate.reach_distance_m / 1000;
  const detourKm = candidate?.detour_distance_m == null ? null : candidate.detour_distance_m / 1000;
  const detourMin = candidate?.detour_duration_s == null ? null : candidate.detour_duration_s / 60;
  elements.requestChip.textContent = chat.status === "recommended" ? "추천 완료" : "입력 확인";
  elements.safeRangeValue.innerHTML = `${formatNumber(safeRange)}<small> km</small>`;
  elements.reachValue.innerHTML = `${formatNumber(reachKm)}<small> km</small>`;
  elements.detourValue.innerHTML = `${formatNumber(detourMin)}<small> 분</small>`;
  elements.detourDistanceValue.innerHTML = `${formatNumber(detourKm)}<small> km</small>`;
  elements.remainingRangeValue.textContent = formatNumber(remaining, 0);
  const usable = remaining > 0 && safeRange != null ? Math.max(0, Math.min(100, safeRange / remaining * 100)) : 0;
  const reserved = remaining > 0 && reserve != null ? Math.max(0, Math.min(100, reserve / remaining * 100)) : 0;
  elements.usableRatio.textContent = usable ? `${Math.round(usable)}%` : "—%";
  elements.reserveRatio.textContent = reserved || reserve === 0 ? `${Math.round(reserved)}%` : "—%";
  elements.rangeBalanceBar.style.width = `${usable}%`;
  elements.recommendationScope.textContent = chat.final_response?.scope === "evaluated_candidates_only" ? "실제로 평가한 후보 안에서 가격과 우회 비용을 비교한 결과입니다." : "검증된 후보와 조건을 기준으로 결과를 표시합니다.";
  renderEvidence(chat.final_response?.evidence || []);
  if (chat.status === "recommended" && candidate?.station) {
    renderRecommendation(chat, candidate, reachKm, detourKm, detourMin);
    elements.savePanel.hidden = false;
    elements.planDate.value = conditions.trip_date || "";
    elements.planStartTime.value = conditions.departure_time?.slice(0, 5) || "";
    elements.planEndTime.value = conditions.arrival_deadline?.slice(0, 5) || "";
    elements.saveState.textContent = "저장 전";
    elements.saveState.classList.remove("saved");
    elements.acceptPlanButton.disabled = false;
  } else { renderQuestions(chat); elements.savePanel.hidden = true; }
}

/**
 * 추천 후보를 왼쪽 큰 카드에 채택 가능한 결과로 그린다.
 * 검증된 주유소·가격·비용만 사용하고 가격 누락은 미확인으로 표시한다. 문자열은 escape한 뒤
 * HTML에 넣으며 DOM 변경 외 부작용은 없다.
 * @param {object} chat 전체 ChatResponse.
 * @param {object} candidate 추천 CandidateEvaluation.
 * @param {number|null} reachKm 실제 도달거리(km).
 * @param {number|null} detourKm 추가거리(km).
 * @param {number|null} detourMin 추가시간(분).
 * @returns {void}
 */
function renderRecommendation(chat, candidate, reachKm, detourKm, detourMin) {
  const station = candidate.station;
  const observed = station.price_observed_at ? formatDateTime(station.price_observed_at) : "기준시각 미확인";
  const price = station.price_krw_per_l == null ? "가격 미확인" : `${formatNumber(station.price_krw_per_l, 0)}원`;
  elements.recommendation.className = "recommendation ready";
  elements.recommendation.innerHTML = `<div class="recommendation-content"><div class="recommendation-eyebrow"><span>RECOMMENDED STOP</span><span>${station.fuel_type === "diesel" ? "경유" : "휘발유"}</span></div><h2 class="station-title">${escapeHtml(station.name)}</h2><p class="station-address">${escapeHtml(station.address || "주소 미확인")} · ${escapeHtml(observed)}</p><div class="station-price"><strong>${escapeHtml(price)}</strong><span>${station.price_krw_per_l == null ? "" : "/ L"}</span></div><div class="recommendation-facts"><div><span>실제 도달거리</span><b>${formatNumber(reachKm)} km</b></div><div><span>추가 우회</span><b>${formatNumber(detourMin)}분</b></div><div><span>추가 거리</span><b>${formatNumber(detourKm)} km</b></div></div><p class="recommendation-answer">${escapeHtml(chat.answer)}</p></div>`;
}

/**
 * 추천을 확정할 수 없는 상태의 질문과 안내를 왼쪽 카드에 표시한다.
 * questions가 비면 final answer를 사용하고 미제공 값을 만들지 않는다.
 * @param {object} chat ChatResponse.
 * @returns {void}
 */
function renderQuestions(chat) {
  const questions = chat.final_response?.questions?.length ? chat.final_response.questions : [chat.answer];
  elements.recommendation.className = "recommendation question";
  elements.recommendation.innerHTML = `<h2>${chat.status === "needs_user_input" ? "조금 더 알려주세요" : "추천 결과를 확인해주세요"}</h2><ul>${questions.map(renderQuestionItem).join("")}</ul>`;
}

/** 질문 문자열 하나를 안전한 li로 바꾼다. 부작용은 없다. @param {unknown} question 질문. @returns {string} li HTML. */
function renderQuestionItem(question) { return `<li>${escapeHtml(question)}</li>`; }

/**
 * 검증 근거의 건수·대표 문장·최대 네 항목을 표시한다.
 * 빈 배열은 안내 placeholder를 복원하며 저장/네트워크 부작용은 없다.
 * @param {Array<object>} evidence ResponseFact 목록.
 * @returns {void}
 */
function renderEvidence(evidence) {
  elements.evidenceCount.innerHTML = `${evidence.length}<small>개</small>`;
  elements.evidenceLead.textContent = evidence[0]?.text || "추천을 요청하면 검증한 가격·거리·제약 근거를 보여드립니다.";
  elements.evidenceList.innerHTML = evidence.length ? evidence.slice(0, 4).map(renderEvidenceItem).join("") : '<li class="placeholder-line"></li><li class="placeholder-line short"></li>';
}

/** ResponseFact text를 안전한 li로 바꾼다. 내부 출처는 표시하지 않는다. @param {object} fact 근거. @returns {string} li HTML. */
function renderEvidenceItem(fact) { return `<li>${escapeHtml(fact.text)}</li>`; }

/**
 * 대화 form을 `/api/chat`에 보내고 session ID를 후속 요청용 localStorage에 보관한다.
 * 추천이 확정된 `recommended` 응답에서만 textarea를 비워 완료된 요청 문장을 제거한다.
 * 입력 보완·추천 없음·실패에서는 사용자가 내용을 참고하거나 수정할 수 있도록 유지한다.
 * 로딩 중 중복 제출을 막고 실패는 공개 toast로만 표시한다. 네트워크·Memory/로그 DB 쓰기와
 * 성공 시 입력 DOM 초기화 부작용이 있다. 완료 상태에는 서버가 실측한 전체 Agent 경과시간을
 * 초 단위로 표시하되 성능 보장이나 SLA로 해석하지 않는다.
 * @param {SubmitEvent} event form 제출 이벤트.
 * @returns {Promise<void>}
 */
/**
 * 출발지 또는 목적지에 대응하는 selector DOM 묶음을 반환한다.
 * `side`는 내부에서 정한 `origin` 또는 `destination`만 허용하며, initialize 이후
 * 존재하는 요소를 참조한다. 네트워크·저장 부작용은 없고 잘못된 값은 Error다.
 * @param {"origin"|"destination"} side 조회할 위치 구분.
 * @returns {{province: HTMLSelectElement, district: HTMLSelectElement, detail: HTMLInputElement, candidate: HTMLSelectElement, searchButton: HTMLButtonElement}} selector 요소 묶음.
 * @throws {Error} 지원하지 않는 위치 구분인 경우.
 */
function locationElements(side) {
  if (!Object.hasOwn(selectedLocations, side)) throw new Error("지원하지 않는 위치 구분입니다.");
  const prefix = side === "origin" ? "origin" : "destination";
  return {
    province: elements[`${prefix}Province`], district: elements[`${prefix}District`],
    detail: elements[`${prefix}Detail`], candidate: elements[`${prefix}Candidate`],
    searchButton: elements[`${prefix}SearchButton`],
  };
}

/**
 * 전국 시·도 목록을 출발지와 목적지 select에 동일하게 채운다.
 * 페이지 초기화와 전체 입력 초기화 직후 호출하며 REGION_HIERARCHY의 key 순서를
 * 그대로 사용한다. 기존 option은 제거되고 하위 지역·후보도 초기화된다. 반환값과
 * 외부 호출은 없으며 정적 목록에 없는 신규 행정구역은 코드 갱신 전 표시되지 않는다.
 * @returns {void}
 */
function populateProvinceSelectors() {
  for (const side of ["origin", "destination"]) {
    const fields = locationElements(side);
    fields.province.innerHTML = '<option value="">선택</option>';
    for (const province of Object.keys(REGION_HIERARCHY)) fields.province.add(new Option(province, province));
    updateDistrictOptions(side);
  }
}

/**
 * 선택한 시·도에 속한 시·군·구 option을 갱신하고 상세 장소 후보를 폐기한다.
 * 시·도 change와 form reset 뒤 호출한다. 정적 REGION_HIERARCHY만 읽으며 선택값이
 * 없으면 하위 select를 비활성화한다. DOM과 메모리 후보를 변경하지만 API는 호출하지 않는다.
 * @param {"origin"|"destination"} side 갱신할 위치 구분.
 * @returns {void}
 */
function updateDistrictOptions(side) {
  const fields = locationElements(side);
  const districts = REGION_HIERARCHY[fields.province.value] || [];
  fields.district.innerHTML = `<option value="">${districts.length ? "선택" : "먼저 시·도를 선택하세요"}</option>`;
  for (const district of districts) fields.district.add(new Option(district, district));
  fields.district.disabled = districts.length === 0;
  fields.detail.value = "";
  resetLocationCandidate(side);
}

/**
 * 한 위치의 API 후보와 확정 좌표를 비우고 후보 select를 비활성화한다.
 * 상위 지역·상세 검색어가 바뀌거나 전체 form을 초기화할 때 호출한다. DOM과 페이지
 * 메모리만 바꾸며 이미 서버로 보낸 요청이나 세션에는 영향을 주지 않는다.
 * @param {"origin"|"destination"} side 초기화할 위치 구분.
 * @returns {void}
 */
function resetLocationCandidate(side) {
  const fields = locationElements(side);
  locationCandidateLists[side] = [];
  selectedLocations[side] = null;
  fields.candidate.innerHTML = '<option value="">상세 장소를 검색하면 표시됩니다</option>';
  fields.candidate.disabled = true;
}

/**
 * 선택한 상위 지역 안에서 동·주소·장소명을 조회해 최대 5개 후보를 표시한다.
 * 시·도, 시·군·구, 상세 검색어가 모두 있을 때 후보 찾기 버튼으로 호출한다.
 * `/api/locations/resolve`를 한 번 호출하며 결과가 하나면 자동 확정하고, 여러 개면
 * 사용자가 직접 골라야 한다. 실패 시 기존 후보를 비우고 toast를 표시한다.
 * @param {"origin"|"destination"} side 검색할 위치 구분.
 * @returns {Promise<void>}
 */
async function searchLocationCandidates(side) {
  const fields = locationElements(side);
  const detail = fields.detail.value.trim();
  if (!fields.province.value || !fields.district.value || !detail) {
    showToast("시·도, 시·군·구와 상세 장소를 입력하세요.", true); return;
  }
  resetLocationCandidate(side);
  fields.searchButton.disabled = true;
  try {
    const query = [fields.province.value, fields.district.value, detail].filter((value, index, values) => values.indexOf(value) === index).join(" ");
    const result = await apiFetch(`/api/locations/resolve?query=${encodeURIComponent(query)}`);
    locationCandidateLists[side] = result.candidates;
    fields.candidate.innerHTML = '<option value="">후보를 선택하세요</option>';
    result.candidates.forEach((candidate, index) => {
      const address = candidate.road_address || candidate.address || candidate.name;
      fields.candidate.add(new Option(`${candidate.name} · ${address}`, String(index)));
    });
    fields.candidate.disabled = false;
    if (result.candidates.length === 1) {
      fields.candidate.value = "0"; selectLocationCandidate(side);
      showToast("위치 후보 1개를 확인했습니다.");
    } else showToast(`${result.candidates.length}개 후보 중 정확한 위치를 선택하세요.`);
  } catch (error) {
    resetLocationCandidate(side);
    showToast(error.message || "위치 후보를 찾지 못했습니다.", true);
  } finally { fields.searchButton.disabled = false; }
}

/**
 * 후보 select의 현재 index를 WGS84 확정 위치로 메모리에 저장한다.
 * 후보 조회 후 사용자가 option을 바꿀 때 호출하며 빈 option은 확정을 취소한다.
 * 잘못된 index는 null로 처리하고 네트워크·DB 부작용은 없다.
 * @param {"origin"|"destination"} side 확정할 위치 구분.
 * @returns {void}
 */
function selectLocationCandidate(side) {
  const value = locationElements(side).candidate.value;
  selectedLocations[side] = value === "" ? null : locationCandidateLists[side][Number(value)] || null;
}

/**
 * 선택한 행정구역과 선택적 상세 후보를 Agent가 해석할 한국어 위치 조건으로 만든다.
 * 시·도와 시·군·구가 필수이며 상세 후보가 있으면 제공사 WGS84 경도·위도를 함께
 * 넣어 동명 장소의 재질의를 줄인다. 메모리를 읽기만 하고 필수값 누락은 Error다.
 * @param {"origin"|"destination"} side 출발지 또는 목적지.
 * @returns {string} 요청 본문에 삽입할 위치 문장.
 * @throws {Error} 상위 지역이 선택되지 않은 경우.
 */
function buildLocationFragment(side) {
  const fields = locationElements(side);
  const role = side === "origin" ? "출발지" : "목적지";
  if (!fields.province.value || !fields.district.value) throw new Error(`${role}의 시·도와 시·군·구를 선택하세요.`);
  const candidate = selectedLocations[side];
  if (!candidate) return `${role}는 "${fields.province.value} ${fields.district.value}"`;
  const label = candidate.road_address || candidate.address || candidate.name;
  return `${role}는 "${label}"이고 좌표는 경도 ${candidate.longitude}, 위도 ${candidate.latitude}`;
}

/**
 * selector 값을 기존 `/api/chat`이 처리할 완전한 추천 요청 문장으로 조합한다.
 * 출발지·목적지, 유종, 계기판 주행 가능 거리(km), 안전 여유(km)가 필수이고 추가
 * 요청은 선택이다. 거리들은 유한수이며 `remaining > 0`, `0 <= reserve < remaining`
 * 규칙을 적용한다. DOM을 읽기만 하며 검증 실패는 Error, 성공은 문자열이다.
 * @returns {string} ChatRequest.message에 넣을 구조화된 한국어 문장.
 * @throws {Error} 필수 선택 또는 거리 범위가 잘못된 경우.
 */
function buildStructuredRequest() {
  const remaining = Number(elements.remainingRange.value), reserve = Number(elements.reserveRange.value);
  if (!Number.isFinite(remaining) || remaining <= 0) throw new Error("주행 가능 거리는 0보다 크게 입력하세요.");
  if (!Number.isFinite(reserve) || reserve < 0 || reserve >= remaining) throw new Error("안전 여유는 0 이상이며 주행 가능 거리보다 작아야 합니다.");
  const fuel = elements.fuelType.value === "diesel" ? "경유" : "휘발유";
  const extra = elements.messageInput.value.trim();
  return `${buildLocationFragment("origin")}. ${buildLocationFragment("destination")}. 유종은 ${fuel}, 계기판 주행 가능 거리는 ${remaining}km, 안전 여유는 ${reserve}km다.${extra ? ` 추가 요청: ${extra}` : ""}`;
}

/**
 * 선택형 주행 조건을 검증·조합해 `/api/chat` Agent 실행을 요청한다.
 * form submit 때 호출하며 중복 제출을 막고, 성공 시 세션·추천·실행 로그 UI를
 * 갱신한다. 입력 오류는 외부 호출 없이 toast로 안내하고 네트워크 실패는 연결 상태를
 * 실패로 표시한다. 서버 세션과 로그 생성, localStorage 갱신이 발생할 수 있다.
 * @param {SubmitEvent} event 추천 요청 form 제출 이벤트.
 * @returns {Promise<void>}
 */
async function handleChatSubmit(event) {
  event.preventDefault();
  let message;
  try { message = buildStructuredRequest(); }
  catch (error) { showToast(error.message, true); return; }
  setLoading(true);
  try {
    const chat = await apiFetch("/api/chat", { method: "POST", body: JSON.stringify({ message, session_id: state.sessionId || null }) });
    state.latestChat = chat; state.sessionId = chat.session_id;
    window.localStorage.setItem("fuelroute.session_id", chat.session_id);
    renderChat(chat); setConnectionState(true);
    if (chat.status === "recommended") elements.messageInput.value = "";
    state.activeExecutionId = chat.execution_id;
    setExecutionState(
      chat.log_delivery_failures ? "partial" : "completed",
      chat.log_delivery_failures ? `응답 완료 · 로그 ${chat.log_delivery_failures}건 누락 · ${(chat.elapsed_ms / 1000).toFixed(1)}초` : `응답 완료 · 이벤트 ${chat.event_count}개 · ${(chat.elapsed_ms / 1000).toFixed(1)}초`,
    );
    await loadExecutionLogs(true);
    await loadExecutionDetail(chat.execution_id, true);
    showToast(chat.status === "recommended" ? "추천이 준비되었습니다." : "추가 입력 내용을 확인해주세요.");
  } catch (error) {
    setConnectionState(false);
    setExecutionState("failed", "요청 실패 · 설정과 외부 서비스 상태를 확인하세요");
    showToast(error.message || "추천 요청에 실패했습니다.", true);
  }
  finally { setLoading(false); }
}

/**
 * 요청 진행 여부를 aria-busy와 제출 버튼에 반영한다.
 * DOM만 변경하고 요청을 시작·취소하지 않는다.
 * @param {boolean} loading 진행 중 여부.
 * @returns {void}
 */
function setLoading(loading) {
  elements.main.setAttribute("aria-busy", String(loading));
  elements.submitButton.disabled = loading;
  elements.submitButton.querySelector("span").textContent = loading ? "경로 분석 중" : "추천 받기";
  if (loading) setExecutionState("running", "Agent 실행 중");
}

/**
 * 날짜와 선택 시각을 KST ISO 문자열로 조합한다.
 * 시각이 비면 null로 두어 미입력 값을 생성하지 않는다.
 * @param {string} date YYYY-MM-DD.
 * @param {string} time HH:MM 또는 빈 값.
 * @returns {string|null} ISO 8601 또는 null.
 */
function combinePlanTime(date, time) { return date && time ? `${date}T${time}:00+09:00` : null; }

/**
 * 현재 추천과 일정 입력을 F3015 PlanCreate 본문으로 변환한다.
 * ChatResponse 검증값만 사용하고 m/s를 km/min으로 변환한다. 날짜·좌표·비용 누락이나 역전
 * 시간은 Error이며 API/DB 부작용은 없다.
 * @returns {object} POST /api/plans 본문.
 * @throws {Error} 필수 저장값 누락 또는 시간 역전.
 */
function buildPlanPayload() {
  const chat = state.latestChat, candidate = selectedCandidate(), station = candidate?.station;
  const conditions = chat?.analysis?.conditions, date = elements.planDate.value;
  if (!chat || !candidate || !station || !conditions) throw new Error("저장할 추천이 없습니다.");
  if (!date) throw new Error("계획 날짜를 선택해주세요.");
  if (!conditions.origin || !conditions.destination) throw new Error("확인된 출발지와 목적지가 필요합니다.");
  if ([candidate.reach_distance_m, candidate.detour_distance_m, candidate.detour_duration_s, chat.judgement.safe_range_km].some(isMissingValue)) throw new Error("추천 비용이 모두 확인된 경우에만 저장할 수 있습니다.");
  const start = combinePlanTime(date, elements.planStartTime.value), end = combinePlanTime(date, elements.planEndTime.value);
  if (start && end && new Date(end) <= new Date(start)) throw new Error("종료 시각은 시작 시각보다 늦어야 합니다.");
  return {
    execution_id: chat.log_delivery_failures ? null : chat.execution_id, trip_date: date,
    input_snapshot: {
      origin: { label: conditions.origin_label || "출발지", coordinate: conditions.origin },
      destination: { label: conditions.destination_label || "목적지", coordinate: conditions.destination },
      conditions: { ...conditions, trip_date: date },
    },
    recommendation: {
      station, safe_range_km: chat.judgement.safe_range_km,
      reach_distance_km: candidate.reach_distance_m / 1000,
      detour_distance_km: candidate.detour_distance_m / 1000,
      detour_duration_min: candidate.detour_duration_s / 60,
      evidence: chat.final_response.evidence, rest_area_info: chat.rest_area_info || null,
    },
    planned_start_at: start, planned_end_at: end, memo: elements.planMemo.value.trim(),
  };
}

/** null 또는 undefined만 누락으로 판정해 0을 보존한다. @param {unknown} value 값. @returns {boolean} 누락 여부. */
function isMissingValue(value) { return value == null; }

/**
 * 현재 추천을 `/api/plans`에 저장하고 충돌 안내 후 목록을 갱신한다.
 * 성공 후 중복 저장 버튼을 막는다. 네트워크·SQLite 계획 쓰기 부작용이 있다.
 * @returns {Promise<void>}
 */
async function acceptRecommendation() {
  elements.acceptPlanButton.disabled = true;
  try {
    const result = await apiFetch("/api/plans", { method: "POST", body: JSON.stringify(buildPlanPayload()) });
    elements.saveState.textContent = result.conflicts.length ? `저장됨 · 일정 겹침 ${result.conflicts.length}건` : "플랜 저장됨";
    elements.saveState.classList.add("saved");
    showToast(result.conflicts.length ? "저장했습니다. 겹치는 일정을 확인해주세요." : "추천을 내 플랜에 저장했습니다.");
    await loadPlans();
  } catch (error) { elements.acceptPlanButton.disabled = false; showToast(error.message || "플랜 저장에 실패했습니다.", true); }
}

/**
 * 저장 계획 전체를 읽어 state와 목록을 갱신한다.
 * 실패는 기존 목록을 유지하고 공개 toast만 표시한다. 서버 상태 변경은 없다.
 * @returns {Promise<void>}
 */
async function loadPlans() {
  try { const result = await apiFetch("/api/plans"); state.plans = result.items || []; renderPlans(); setConnectionState(true); }
  catch (error) { showToast(error.message || "저장한 플랜을 불러오지 못했습니다.", true); }
}

/**
 * state.plans를 카드 목록으로 그린다.
 * 빈 목록도 정상 안내로 표시하며 DOM 변경 외 부작용은 없다.
 * @returns {void}
 */
function renderPlans() {
  elements.planCount.textContent = `${state.plans.length}개`;
  elements.plansList.innerHTML = state.plans.length ? state.plans.map(renderPlanItem).join("") : '<div class="empty-plans"><strong>아직 저장한 플랜이 없습니다.</strong><span>추천을 채택하면 이곳에 일정이 쌓입니다.</span></div>';
}

/**
 * StoredPlan 한 건을 수정/삭제 버튼이 있는 안전한 카드 HTML로 변환한다.
 * 검증 상태·가격·시간을 미확인과 구분하며 DOM/네트워크 부작용은 없다.
 * @param {object} plan StoredPlan JSON.
 * @returns {string} 카드 HTML.
 */
function renderPlanItem(plan) {
  const station = plan.recommendation?.station || {}, review = plan.validation_status === "revalidation_required";
  const price = station.price_krw_per_l == null ? "가격 미확인" : `${formatNumber(station.price_krw_per_l, 0)}원/L`;
  const memo = plan.memo ? `<p class="plan-memo">${escapeHtml(plan.memo)}</p>` : "";
  return `<article class="plan-item"><div><div class="plan-title"><strong>${escapeHtml(station.name || "주유 계획")}</strong><span class="plan-status ${review ? "warning" : ""}">${review ? "재검증 필요" : "검증됨"}</span></div><div class="plan-meta"><span>${escapeHtml(plan.trip_date)}</span><span>${escapeHtml(formatDateTime(plan.planned_start_at))}</span><span>${escapeHtml(price)}</span><span>우회 ${formatNumber(plan.recommendation?.detour_duration_min)}분</span></div>${memo}</div><div class="plan-actions"><button type="button" data-action="edit" data-plan-id="${escapeHtml(plan.plan_id)}">수정</button><button class="delete" type="button" data-action="delete" data-plan-id="${escapeHtml(plan.plan_id)}">삭제</button></div></article>`;
}

/**
 * 목록의 수정/삭제 버튼을 data-action으로 분기한다.
 * 알 수 없는 ID/action은 무시한다. 해당 분기에만 dialog/네트워크 부작용이 있다.
 * @param {MouseEvent} event 목록 click 이벤트.
 * @returns {Promise<void>}
 */
async function handlePlanAction(event) {
  const button = event.target.closest("button[data-action]");
  if (!button) return;
  const plan = state.plans.find((item) => item.plan_id === button.dataset.planId);
  if (!plan) return;
  if (button.dataset.action === "edit") openEditDialog(plan);
  if (button.dataset.action === "delete") await deletePlan(plan);
}

/**
 * 선택 계획 날짜·시간·revision을 수정 dialog에 채워 연다.
 * 없는 시각은 빈 값으로 두며 서버 데이터는 변경하지 않는다.
 * @param {object} plan StoredPlan.
 * @returns {void}
 */
function openEditDialog(plan) {
  state.editingPlan = plan; elements.editPlanId.value = plan.plan_id;
  elements.editRevision.value = String(plan.revision); elements.editPlanDate.value = plan.trip_date;
  elements.editStartTime.value = extractTime(plan.planned_start_at); elements.editEndTime.value = extractTime(plan.planned_end_at);
  elements.editMemo.value = plan.memo || "";
  if (typeof elements.editDialog.showModal === "function") elements.editDialog.showModal();
  else elements.editDialog.setAttribute("open", "");
}

/**
 * ISO 문자열에서 원래 HH:MM 부분만 추출하고 없으면 빈 문자열을 반환한다.
 * 시간대를 추측/변환하지 않으며 부작용은 없다.
 * @param {string|null|undefined} value ISO 시각.
 * @returns {string} HH:MM 또는 빈 값.
 */
function extractTime(value) { const match = typeof value === "string" ? value.match(/T(\d{2}:\d{2})/) : null; return match ? match[1] : ""; }

/**
 * 기존 ISO 계획 시각과 수정 입력으로 만든 시각이 같은 순간인지 비교한다.
 * 둘 다 null이면 같고 하나만 null이면 다르다. 문자열 표기나 UTC offset이 달라도 유효한
 * Date의 epoch가 같으면 같은 값으로 본다. DOM·네트워크·시간 추측 부작용은 없다.
 * @param {string|null|undefined} current 서버에 저장된 ISO 8601 시각.
 * @param {string|null} candidate 수정 폼에서 만든 KST ISO 8601 시각.
 * @returns {boolean} 저장 의미가 같은 시각인지 여부.
 */
function isSamePlanTime(current, candidate) {
  if (!current && !candidate) return true;
  if (!current || !candidate) return false;
  return new Date(current).getTime() === new Date(candidate).getTime();
}

/**
 * 수정 폼과 현재 StoredPlan을 비교해 실제로 달라진 필드만 PlanUpdate 본문에 넣는다.
 * expected_revision은 항상 포함하고 날짜·시각·메모는 변경된 경우에만 포함한다. 메모는
 * 앞뒤 공백을 제거하며 빈 문자열은 기존 메모 삭제다. 변경이 없으면 null을 반환해 불필요한
 * PATCH와 백엔드 INVALID_INPUT 422를 예방한다. DOM 읽기 외 DB·네트워크 부작용은 없다.
 * @param {object} plan 현재 수정 중인 StoredPlan.
 * @returns {object|null} PATCH 본문 또는 변경 없음 null.
 */
function buildPlanUpdatePayload(plan) {
  const date = elements.editPlanDate.value;
  const start = combinePlanTime(date, elements.editStartTime.value);
  const end = combinePlanTime(date, elements.editEndTime.value);
  const memo = elements.editMemo.value.trim();
  const payload = { expected_revision: Number(elements.editRevision.value) };
  if (date !== plan.trip_date) payload.trip_date = date;
  if (!isSamePlanTime(plan.planned_start_at, start)) payload.planned_start_at = start;
  if (!isSamePlanTime(plan.planned_end_at, end)) payload.planned_end_at = end;
  if (memo !== (plan.memo || "")) payload.memo = memo;
  return Object.keys(payload).length === 1 ? null : payload;
}

/**
 * 수정 dialog 값을 revision 기반 PATCH로 저장한다.
 * 빈 시각은 null로 삭제하고 역전은 요청 전에 거절한다. 성공하면 목록을 다시 읽는다.
 * @param {SubmitEvent} event form 제출 이벤트.
 * @returns {Promise<void>}
 */
async function submitPlanEdit(event) {
  event.preventDefault();
  const date = elements.editPlanDate.value, start = combinePlanTime(date, elements.editStartTime.value), end = combinePlanTime(date, elements.editEndTime.value);
  if (start && end && new Date(end) <= new Date(start)) { showToast("종료 시각은 시작 시각보다 늦어야 합니다.", true); return; }
  const payload = buildPlanUpdatePayload(state.editingPlan);
  if (!payload) { showToast("변경된 일정이나 메모가 없습니다.", true); return; }
  try {
    const result = await apiFetch(`/api/plans/${encodeURIComponent(elements.editPlanId.value)}`, { method: "PATCH", body: JSON.stringify(payload) });
    elements.editDialog.close();
    showToast(result.plan.validation_status === "revalidation_required" ? "플랜을 수정했습니다. 추천을 다시 확인해주세요." : "플랜 일정과 메모를 수정했습니다.");
    await loadPlans();
  } catch (error) { showToast(error.message || "플랜 수정에 실패했습니다.", true); }
}

/**
 * 사용자 확인 후 계획 하나를 DELETE하고 목록을 갱신한다.
 * 취소하면 아무 작업도 하지 않으며 삭제는 복구되지 않는다.
 * @param {object} plan 삭제할 StoredPlan.
 * @returns {Promise<void>}
 */
async function deletePlan(plan) {
  const name = plan.recommendation?.station?.name || "이 플랜";
  if (!window.confirm(`${name} 계획을 삭제할까요?`)) return;
  try { await apiFetch(`/api/plans/${encodeURIComponent(plan.plan_id)}`, { method: "DELETE" }); showToast("플랜을 삭제했습니다."); await loadPlans(); }
  catch (error) { showToast(error.message || "플랜 삭제에 실패했습니다.", true); }
}

/**
 * 대화 세션과 추천 UI를 초기화하되 저장한 계획은 유지한다.
 * localStorage/DOM만 변경하고 서버 데이터를 삭제하지 않는다.
 * @returns {void}
 */
function resetChat() {
  state.sessionId = null; state.latestChat = null; window.localStorage.removeItem("fuelroute.session_id");
  state.activeExecutionId = null; setExecutionState("idle", "대기 중");
  elements.chatForm.reset(); populateProvinceSelectors(); elements.requestChip.textContent = "새 요청"; elements.savePanel.hidden = true;
  elements.planMemo.value = ""; elements.planStartTime.value = ""; elements.planEndTime.value = "";
  elements.recommendation.className = "recommendation empty";
  elements.recommendation.innerHTML = '<div class="route-visual" aria-hidden="true"><svg viewBox="0 0 460 190" preserveAspectRatio="none"><path class="route-base" d="M18 152 C98 152 95 92 172 92 S255 36 334 43 S390 112 442 77"/><path class="route-active" d="M18 152 C98 152 95 92 172 92 S255 36 334 43"/><circle class="route-start" cx="18" cy="152" r="7"/><circle class="route-stop" cx="334" cy="43" r="16"/></svg><span>요청을 입력하면 안전하게 도달 가능한 추천 지점을 표시합니다.</span></div>';
  renderEvidence([]); elements.originProvince.focus();
}

/** 새 요청 click을 resetChat에 전달한다. @returns {void} */
function handleNewChat() { resetChat(); }

/**
 * 상단 탭이나 사이드 메뉴가 지정한 실행 과정·저장 플랜·추천 근거 dialog를 모달로 연다.
 * `data-dialog-target`은 세 허용 ID만 받는다. 저장 플랜은 최신 계획 목록, 실행 과정은 최신
 * 로그 목록과 현재/최근 상세를 읽고, 추천 근거는 현재 Chat 결과를 그대로 보여준다. dialog
 * DOM 변경과 선택적인 GET `/api/plans`·`/api/logs` 외 부작용은 없다.
 * @param {MouseEvent} event 모달 열기 버튼의 click 이벤트.
 * @returns {Promise<void>}
 */
async function openDashboardDialog(event) {
  const targetId = event.currentTarget.dataset.dialogTarget;
  if (!["executionDialog", "plansDialog", "evidenceDialog"].includes(targetId)) return;
  const dialog = document.getElementById(targetId);
  if (!dialog.open) dialog.showModal();
  if (targetId === "plansDialog") await loadPlans();
  if (targetId === "executionDialog") {
    await loadExecutionLogs();
    const available = new Set(state.logs.map((item) => item.execution_id));
    const selected = state.selectedLog?.session?.execution_id;
    const preferred = available.has(state.activeExecutionId) ? state.activeExecutionId : available.has(selected) ? selected : state.logs[0]?.execution_id;
    if (preferred) await loadExecutionDetail(preferred);
  }
}

/**
 * 실행 과정 모달의 새로고침 버튼에서 목록을 다시 읽고 현재 선택 상세도 갱신한다.
 * 선택이 없으면 가장 최근 실행을 선택한다. 로그를 생성·수정하지 않고 GET 요청만 수행하며
 * 조회 실패는 각 로더의 정제 toast로 표시한다.
 * @returns {Promise<void>}
 */
async function refreshExecutionLogs() {
  const selected = state.selectedLog?.session?.execution_id;
  await loadExecutionLogs();
  const available = new Set(state.logs.map((item) => item.execution_id));
  const target = available.has(selected) ? selected : state.logs[0]?.execution_id;
  if (target) await loadExecutionDetail(target);
  else { state.selectedLog = null; renderExecutionDetail(); }
}

/**
 * 저장 플랜 또는 추천 근거 모달의 닫기 버튼이 속한 가장 가까운 dialog를 닫는다.
 * 버튼이 dialog 밖에서 호출된 비정상 상황은 아무 작업 없이 끝낸다. 저장 데이터·추천 결과를
 * 지우지 않으며 DOM의 open 상태만 변경한다.
 * @param {MouseEvent} event 모달 닫기 버튼의 click 이벤트.
 * @returns {void}
 */
function closeDashboardDialog(event) {
  const dialog = event.currentTarget.closest("dialog");
  if (dialog?.open) dialog.close();
}

/**
 * 수정 dialog의 닫기/취소 버튼에서 변경 요청 없이 dialog를 닫는다.
 * 입력값은 다음 openEditDialog에서 다시 채우므로 별도 초기화하지 않는다. DOM 외 부작용은 없다.
 * @returns {void}
 */
function closeEditDialog() { elements.editDialog.close(); }

/**
 * 이벤트를 등록하고 health와 저장 계획을 병렬로 불러온다.
 * 페이지당 한 번 호출되며 listener 등록과 네트워크 읽기 부작용이 있다.
 * @returns {void}
 */
function initialize() {
  populateProvinceSelectors();
  for (const side of ["origin", "destination"]) {
    const fields = locationElements(side);
    fields.province.addEventListener("change", () => updateDistrictOptions(side));
    fields.district.addEventListener("change", () => resetLocationCandidate(side));
    fields.detail.addEventListener("input", () => resetLocationCandidate(side));
    fields.searchButton.addEventListener("click", () => searchLocationCandidates(side));
    fields.candidate.addEventListener("change", () => selectLocationCandidate(side));
  }
  elements.chatForm.addEventListener("submit", handleChatSubmit);
  elements.acceptPlanButton.addEventListener("click", acceptRecommendation);
  elements.newChatButton.addEventListener("click", handleNewChat);
  elements.refreshPlansButton.addEventListener("click", loadPlans);
  elements.plansList.addEventListener("click", handlePlanAction);
  elements.editPlanForm.addEventListener("submit", submitPlanEdit);
  elements.closeEditButton.addEventListener("click", closeEditDialog);
  elements.cancelEditButton.addEventListener("click", closeEditDialog);
  document.querySelectorAll("[data-dialog-target]").forEach((button) => {
    button.addEventListener("click", openDashboardDialog);
  });
  elements.closePlansDialog.addEventListener("click", closeDashboardDialog);
  elements.closeEvidenceDialog.addEventListener("click", closeDashboardDialog);
  elements.closeExecutionDialog.addEventListener("click", closeDashboardDialog);
  elements.refreshLogsButton.addEventListener("click", refreshExecutionLogs);
  elements.executionList.addEventListener("click", handleExecutionSelection);
  elements.deleteLogButton.addEventListener("click", deleteSelectedExecutionLog);
  void Promise.allSettled([checkHealth(), loadPlans()]);
}

initialize();
