"use strict";

const LOCAL_API_BASE = "http://127.0.0.1:8000";
const state = {
  sessionId: window.localStorage.getItem("fuelroute.session_id"),
  latestChat: null,
  plans: [],
  editingPlan: null,
};
const elements = Object.fromEntries([
  "main", "chatForm", "messageInput", "submitButton", "newChatButton", "requestChip",
  "recommendation", "savePanel", "saveState", "planDate", "planStartTime", "planEndTime", "planMemo",
  "acceptPlanButton", "safeRangeValue", "reachValue", "detourValue", "remainingRangeValue",
  "usableRatio", "reserveRatio", "rangeBalanceBar", "detourDistanceValue", "recommendationScope",
  "evidenceCount", "evidenceLead", "evidenceList", "planCount", "plansList", "refreshPlansButton",
  "connectionDot", "connectionText", "toast", "editDialog", "editPlanForm", "editPlanId",
  "editRevision", "editPlanDate", "editStartTime", "editEndTime", "editMemo", "closeEditButton",
  "cancelEditButton", "plansDialog", "evidenceDialog", "closePlansDialog", "closeEvidenceDialog",
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
 * 성공 시 입력 DOM 초기화 부작용이 있다.
 * @param {SubmitEvent} event form 제출 이벤트.
 * @returns {Promise<void>}
 */
async function handleChatSubmit(event) {
  event.preventDefault();
  const message = elements.messageInput.value.trim();
  if (!message) return;
  setLoading(true);
  try {
    const chat = await apiFetch("/api/chat", { method: "POST", body: JSON.stringify({ message, session_id: state.sessionId || null }) });
    state.latestChat = chat; state.sessionId = chat.session_id;
    window.localStorage.setItem("fuelroute.session_id", chat.session_id);
    renderChat(chat); setConnectionState(true);
    if (chat.status === "recommended") elements.messageInput.value = "";
    showToast(chat.status === "recommended" ? "추천이 준비되었습니다." : "추가 입력 내용을 확인해주세요.");
  } catch (error) { setConnectionState(false); showToast(error.message || "추천 요청에 실패했습니다.", true); }
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
  elements.chatForm.reset(); elements.requestChip.textContent = "새 요청"; elements.savePanel.hidden = true;
  elements.planMemo.value = ""; elements.planStartTime.value = ""; elements.planEndTime.value = "";
  elements.recommendation.className = "recommendation empty";
  elements.recommendation.innerHTML = '<div class="route-visual" aria-hidden="true"><svg viewBox="0 0 460 190" preserveAspectRatio="none"><path class="route-base" d="M18 152 C98 152 95 92 172 92 S255 36 334 43 S390 112 442 77"/><path class="route-active" d="M18 152 C98 152 95 92 172 92 S255 36 334 43"/><circle class="route-start" cx="18" cy="152" r="7"/><circle class="route-stop" cx="334" cy="43" r="16"/></svg><span>요청을 입력하면 안전하게 도달 가능한 추천 지점을 표시합니다.</span></div>';
  renderEvidence([]); elements.messageInput.focus();
}

/** 새 요청 click을 resetChat에 전달한다. @returns {void} */
function handleNewChat() { resetChat(); }

/**
 * 상단 탭이나 사이드 메뉴가 지정한 저장 플랜·추천 근거 dialog를 모달로 연다.
 * `data-dialog-target`은 `plansDialog` 또는 `evidenceDialog`만 허용하며 다른 값은 무시한다.
 * 저장 플랜 모달은 열린 직후 최신 목록을 API에서 다시 읽고, 추천 근거는 현재 Chat 결과를
 * 그대로 보여준다. dialog DOM 변경과 선택적인 GET `/api/plans` 외 부작용은 없다.
 * @param {MouseEvent} event 모달 열기 버튼의 click 이벤트.
 * @returns {Promise<void>}
 */
async function openDashboardDialog(event) {
  const targetId = event.currentTarget.dataset.dialogTarget;
  if (!['plansDialog', 'evidenceDialog'].includes(targetId)) return;
  const dialog = document.getElementById(targetId);
  if (!dialog.open) dialog.showModal();
  if (targetId === 'plansDialog') await loadPlans();
}

/**
 * 저장 플랜 또는 추천 근거 모달의 닫기 버튼이 속한 가장 가까운 dialog를 닫는다.
 * 버튼이 dialog 밖에서 호출된 비정상 상황은 아무 작업 없이 끝낸다. 저장 데이터·추천 결과를
 * 지우지 않으며 DOM의 open 상태만 변경한다.
 * @param {MouseEvent} event 모달 닫기 버튼의 click 이벤트.
 * @returns {void}
 */
function closeDashboardDialog(event) {
  const dialog = event.currentTarget.closest('dialog');
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
  void Promise.allSettled([checkHealth(), loadPlans()]);
}

initialize();
