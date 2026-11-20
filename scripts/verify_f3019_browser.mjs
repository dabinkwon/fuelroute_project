/** F3019 실행 과정 모달을 실제 로컬 로그 API와 Chrome DevTools Protocol로 검증한다. */

import { writeFile } from "node:fs/promises";

const pages = await fetch("http://127.0.0.1:9223/json/list").then(parseJsonResponse);
const page = pages.find(isFuelRoutePage);
if (!page) throw new Error("FuelRoute 브라우저 탭을 찾을 수 없습니다.");

const socket = new WebSocket(page.webSocketDebuggerUrl);
const pending = new Map();
let nextId = 1;
socket.addEventListener("message", handleProtocolMessage);
await new Promise(resolveSocketOpen);
await command("Runtime.enable");
await command("Page.enable");

await evaluate(`(() => {
  document.querySelector('[data-dialog-target="executionDialog"]').click();
  return true;
})()`);
await waitFor(`document.querySelector('#executionDialog').open && state.selectedLog?.events?.length > 0`, 15000);

const result = await evaluate(`(() => {
  const detail = state.selectedLog;
  const types = detail.events.map((event) => event.event_type);
  const body = document.querySelector('#executionDialog').textContent;
  return {
    dialogOpen: document.querySelector('#executionDialog').open,
    listedExecutions: state.logs.length,
    executionId: detail.session.execution_id,
    status: detail.session.status,
    eventCount: detail.events.length,
    renderedEvents: document.querySelectorAll('#executionTimeline .timeline-event').length,
    ordered: detail.events.every((event, index) => event.sequence === index + 1),
    toolRequested: types.filter((type) => type === 'TOOL_REQUESTED').length,
    toolSucceeded: types.filter((type) => type === 'TOOL_SUCCEEDED').length,
    toolFailed: types.filter((type) => type === 'TOOL_FAILED').length,
    replanEvents: types.filter((type) => type.startsWith('REPLAN_')).length,
    hasSecretLabel: ['GMS_API_KEY', 'OPINET_API_KEY', 'KAKAO_REST_API_KEY', 'Authorization'].some((value) => body.includes(value)),
    deleteEnabled: !document.querySelector('#deleteLogButton').disabled,
  };
})()`);

if (!result.dialogOpen || !result.ordered || result.eventCount !== result.renderedEvents) {
  throw new Error(`실행 모달 렌더링 불일치: ${JSON.stringify(result)}`);
}
if (result.hasSecretLabel) throw new Error("실행 모달에 비밀 설정 이름이 노출됐습니다.");

const captured = await command("Page.captureScreenshot", { format: "png", captureBeyondViewport: false });
await writeFile("docs/verification/2026-11-20-f3019-execution-modal.png", captured.data, "base64");
process.stdout.write(`${JSON.stringify(result, null, 2)}\n`);
await command("Browser.close");
socket.close();

/**
 * Chrome 디버그 목록 HTTP 응답을 JSON으로 파싱하고 비정상 상태는 검증 실패로 처리한다.
 * 응답 읽기 외 브라우저·파일 변경 부작용은 없다.
 * @param {Response} response Chrome target 목록 응답.
 * @returns {Promise<any>} target 배열.
 */
async function parseJsonResponse(response) {
  if (!response.ok) throw new Error(`Chrome debug HTTP ${response.status}`);
  return response.json();
}

/**
 * target이 로컬 5173 FuelRoute page인지 type과 URL로만 판정한다.
 * 브라우저 상태를 바꾸지 않는다.
 * @param {object} target Chrome target 메타데이터.
 * @returns {boolean} 검증 대상 여부.
 */
function isFuelRoutePage(target) {
  return target.type === "page" && target.url.startsWith("http://127.0.0.1:5173/");
}

/**
 * WebSocket open 이벤트에 resolve를 한 번 등록한다.
 * 메시지를 보내거나 DOM을 변경하지 않는다.
 * @param {Function} resolve Promise resolve 함수.
 * @returns {void}
 */
function resolveSocketOpen(resolve) {
  socket.addEventListener("open", resolve, { once: true });
}

/**
 * CDP 응답을 ID별 대기 Promise에 전달하고 protocol 오류는 Error로 바꾼다.
 * pending map 정리 외 브라우저 부작용은 없다.
 * @param {MessageEvent} event WebSocket 메시지.
 * @returns {void}
 */
function handleProtocolMessage(event) {
  const message = JSON.parse(event.data);
  if (!message.id || !pending.has(message.id)) return;
  const waiter = pending.get(message.id);
  pending.delete(message.id);
  if (message.error) waiter.reject(new Error(message.error.message));
  else waiter.resolve(message.result);
}

/**
 * Chrome DevTools Protocol 명령과 직렬화 가능한 인자를 보내 같은 ID 응답을 기다린다.
 * 반환 부작용은 method에 따르며 timeout·자동 재시도는 없다.
 * @param {string} method CDP 메서드.
 * @param {object} [params={}] CDP 인자.
 * @returns {Promise<object>} CDP result.
 */
function command(method, params = {}) {
  const id = nextId++;
  socket.send(JSON.stringify({ id, method, params }));
  return new Promise((resolve, reject) => pending.set(id, { resolve, reject }));
}

/**
 * FuelRoute page 전역에서 이 파일의 고정 JavaScript 표현식을 평가하고 JSON 값을 반환한다.
 * 사용자 입력은 코드로 실행하지 않으며 예외면 검증을 중단한다.
 * @param {string} expression 고정 표현식.
 * @returns {Promise<any>} 평가 결과.
 */
async function evaluate(expression) {
  const response = await command("Runtime.evaluate", {
    expression,
    awaitPromise: true,
    returnByValue: true,
  });
  if (response.exceptionDetails) {
    throw new Error(response.exceptionDetails.exception?.description || "브라우저 평가 실패");
  }
  return response.result.value;
}

/**
 * 고정 DOM 조건식이 true가 될 때까지 250ms 간격으로 기다린다.
 * timeoutMs를 넘으면 실패하며 요청을 재실행하거나 상태를 보정하지 않는다.
 * @param {string} expression boolean 표현식.
 * @param {number} timeoutMs 최대 대기 시간.
 * @returns {Promise<void>}
 */
async function waitFor(expression, timeoutMs) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    if (await evaluate(expression)) return;
    await new Promise(delayQuarterSecond);
  }
  throw new Error(`브라우저 조건 대기 시간 초과: ${expression}`);
}

/**
 * polling 사이에 250ms를 기다린다. timer 외 부작용은 없다.
 * @param {Function} resolve Promise resolve 함수.
 * @returns {void}
 */
function delayQuarterSecond(resolve) {
  setTimeout(resolve, 250);
}
