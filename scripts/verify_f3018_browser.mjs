/** F3018 로컬 브라우저의 실제 추천→계획 CRUD 흐름을 Chrome DevTools Protocol로 검증한다. */

const pages = await fetch("http://127.0.0.1:9222/json/list").then(parseJsonResponse);
const page = pages.find(isFuelRoutePage);
if (!page) throw new Error("FuelRoute 브라우저 탭을 찾을 수 없습니다.");

const socket = new WebSocket(page.webSocketDebuggerUrl);
const pending = new Map();
let nextId = 1;
socket.addEventListener("message", handleProtocolMessage);
await new Promise(resolveSocketOpen);
await command("Runtime.enable");

const result = {};
result.initial = await evaluate(`({
  title: document.title,
  online: document.querySelector('#connectionDot').classList.contains('online'),
  initialPlanCount: state.plans.length
})`);

await evaluate(`(() => {
  const input = document.querySelector('#messageInput');
  input.value = '2026년 11월 20일 대구에서 서울로 가는 길이야. 휘발유이고 주행 가능 거리는 180km, 안전 여유는 30km를 남길게. 우회 15분 이내에서 가격 우선으로 추천해줘.';
  input.dispatchEvent(new Event('input', { bubbles: true }));
  document.querySelector('#chatForm').requestSubmit();
  return true;
})()`);

await waitFor(`!document.querySelector('#main').getAttribute('aria-busy').includes('true')`, 120000);
result.chat = await evaluate(`({
  status: state.latestChat?.status || null,
  executionId: state.latestChat?.execution_id || null,
  station: state.latestChat?.judgement?.recommendation?.recommended?.station?.name || null,
  error: document.querySelector('#toast').classList.contains('error') ? document.querySelector('#toast').textContent : null
})`);
if (result.chat.status !== "recommended") {
  throw new Error(`추천 완료 상태가 아닙니다: ${JSON.stringify(result.chat)}`);
}

await evaluate(`(() => {
  document.querySelector('#planDate').value = '2026-11-20';
  document.querySelector('#planStartTime').value = '09:00';
  document.querySelector('#planEndTime').value = '12:00';
  document.querySelector('#acceptPlanButton').click();
  return true;
})()`);
await waitFor(`document.querySelector('#saveState').classList.contains('saved')`, 30000);
result.saved = await evaluate(`({
  state: document.querySelector('#saveState').textContent,
  planCount: state.plans.length,
  createdPlanId: state.plans[0]?.plan_id || null,
  validationStatus: state.plans[0]?.validation_status || null
})`);
if (!result.saved.createdPlanId) throw new Error("저장된 계획 ID를 확인할 수 없습니다.");

await evaluate(`(() => {
  const id = ${JSON.stringify(result.saved.createdPlanId)};
  document.querySelector('[data-action="edit"][data-plan-id="' + id + '"]').click();
  document.querySelector('#editStartTime').value = '10:00';
  document.querySelector('#editEndTime').value = '13:00';
  document.querySelector('#editPlanForm').requestSubmit();
  return true;
})()`);
await waitFor(`state.plans.find((plan) => plan.plan_id === ${JSON.stringify(result.saved.createdPlanId)})?.revision === 2`, 30000);
result.updated = await evaluate(`(() => {
  const plan = state.plans.find((item) => item.plan_id === ${JSON.stringify(result.saved.createdPlanId)});
  return { revision: plan.revision, validationStatus: plan.validation_status, start: plan.planned_start_at, end: plan.planned_end_at };
})()`);

await evaluate(`(() => {
  window.confirm = () => true;
  const id = ${JSON.stringify(result.saved.createdPlanId)};
  document.querySelector('[data-action="delete"][data-plan-id="' + id + '"]').click();
  return true;
})()`);
await waitFor(`!state.plans.some((plan) => plan.plan_id === ${JSON.stringify(result.saved.createdPlanId)})`, 30000);
result.deleted = await evaluate(`({ remainingPlanCount: state.plans.length })`);

process.stdout.write(`${JSON.stringify(result, null, 2)}\n`);
socket.close();

/**
 * fetch 응답을 JSON으로 파싱하며 비정상 상태는 검증을 중단한다.
 * response는 Chrome 로컬 디버그 목록 응답이고 성공 시 배열/객체를 반환한다. 네트워크 읽기
 * 외 부작용은 없으며 본문에 키나 사용자 데이터를 포함하지 않는다.
 * @param {Response} response fetch 응답.
 * @returns {Promise<any>} 파싱한 JSON.
 */
async function parseJsonResponse(response) {
  if (!response.ok) throw new Error(`Chrome debug HTTP ${response.status}`);
  return response.json();
}

/**
 * Chrome target 한 건이 로컬 FuelRoute 정적 화면인지 판정한다.
 * target의 type/url만 읽으며 브라우저·DOM을 변경하지 않는다.
 * @param {object} target Chrome target 메타데이터.
 * @returns {boolean} page이면서 5173 FuelRoute URL이면 true.
 */
function isFuelRoutePage(target) {
  return target.type === "page" && target.url.startsWith("http://127.0.0.1:5173/");
}

/**
 * WebSocket open 이벤트에서 Promise를 완료한다.
 * 브라우저 메시지 전송이나 상태 변경은 없으며 resolve를 한 번 호출한다.
 * @param {Function} resolve Promise resolve 함수.
 * @returns {void}
 */
function resolveSocketOpen(resolve) {
  socket.addEventListener("open", resolve, { once: true });
}

/**
 * CDP 응답을 ID별 대기 Promise에 전달한다.
 * protocol 오류는 Error로 reject하고 정상 결과는 resolve한다. event 메시지는 무시하며
 * pending map 제거 외 브라우저 상태를 바꾸지 않는다.
 * @param {MessageEvent} event WebSocket 메시지 이벤트.
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
 * Chrome DevTools Protocol 명령 하나를 보내고 같은 ID 응답을 기다린다.
 * method는 Runtime/Page 등 CDP 메서드, params는 직렬화 가능한 인자다. 반환은 result 객체이며
 * 브라우저 상태 변경 범위는 해당 CDP 명령에 따른다. timeout 재시도는 하지 않는다.
 * @param {string} method CDP 메서드 이름.
 * @param {object} [params={}] CDP 인자.
 * @returns {Promise<object>} CDP result.
 */
function command(method, params = {}) {
  const id = nextId++;
  socket.send(JSON.stringify({ id, method, params }));
  return new Promise((resolve, reject) => pending.set(id, { resolve, reject }));
}

/**
 * 현재 FuelRoute page의 전역 컨텍스트에서 JavaScript 표현식을 평가한다.
 * expression은 이 검증 파일의 고정 문자열만 사용하며 사용자 입력을 코드로 실행하지 않는다.
 * Promise 결과를 기다리고 객체를 JSON 값으로 반환한다. 예외이면 검증을 즉시 실패시킨다.
 * @param {string} expression 고정 JavaScript 표현식.
 * @returns {Promise<any>} 평가 결과 값.
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
 * 고정 브라우저 조건식이 true가 될 때까지 250ms 간격으로 기다린다.
 * expression은 이 파일이 만든 상태/DOM 검사만 허용하고 timeoutMs를 넘으면 Error다. 요청을
 * 재실행하거나 값을 변경하지 않으며 반복적인 Runtime 읽기와 timer만 사용한다.
 * @param {string} expression boolean JavaScript 표현식.
 * @param {number} timeoutMs 최대 대기 밀리초.
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
 * waitFor polling 사이에 250ms를 기다린다.
 * resolve를 timer에 등록하는 것 외 DOM·네트워크·저장 부작용은 없다.
 * @param {Function} resolve Promise resolve 함수.
 * @returns {void}
 */
function delayQuarterSecond(resolve) {
  setTimeout(resolve, 250);
}
