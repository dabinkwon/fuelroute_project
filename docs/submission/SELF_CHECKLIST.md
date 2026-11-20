# 셀프 체크리스트

결과 표기: `통과`는 코드·자동 테스트 또는 기존 실제 실행 증거로 확인한 항목, `부분 확인`은
자동/기존 증거는 있으나 최신 제출 화면 캡처 등이 남은 항목, `해당 없음`은 FuelRoute에
적용되지 않는 항목이다.

| 요구사항 | 결과 | 증빙(파일명 또는 캡처할 화면) | 비고(실패·미확인 사유) |
|---|---|---|---|
| F3001 Tool 명세·외부/로컬 구분·공통 실패 | 통과 | `docs/TOOL_CONTRACTS.md`, `docs/TOOL_CONTRACTS.json`, `tests/test_tool_registry.py`, `tests/test_tools.py`, `tests/test_probes.py` | `python -m fuelroute tool-schemas` 출력 화면을 추가 캡처한다. |
| F3002 기본 기준·조회 불가/미래 기준 안내 | 통과 | `docs/ARCHITECTURE.md`, `tests/test_final_response.py`, `docs/verification/2026-11-19-final-response.json` | 가격은 조회 기준시각의 값이며 미래 계획일 가격을 예측하지 않는다. 정상 추천 화면의 가격 기준시각을 캡처한다. |
| F3002·F3003 후보 ID→상세·누락값 비생성 | 통과 | `tests/test_execution.py`, `tests/test_tools.py`, `docs/verification/2026-11-19-tool-execution.json` | 가격 누락은 실패/미확인으로 남기며 임의 가격을 만들지 않는다. |
| F3004 보조정보 정상·0건 | 통과 | `tests/test_tools.py`, `tests/test_execution.py`, `docs/verification/2026-11-18.json` | 제공 필드인 이름·좌표·노선·방향·시설·기준일을 사용한다. API가 제공하지 않는 링크/주소는 생성하지 않으며 0건은 `EMPTY_RESULT`다. |
| F3005 조건 추출·미언급값 null | 통과 | `tests/test_request_analysis.py`, `tests/test_distance_input.py`, `docs/verification/2026-11-19-request-analysis.json` | 완전 입력과 누락 입력의 CLI JSON을 나란히 캡처한다. |
| F3006 계획·Tool 없는 요청·그래프 노드 | 통과 | `tests/test_task_plan.py`, `tests/test_planning.py`, `docs/verification/2026-11-19-task-plan.json`, `docs/ARCHITECTURE.md` | 인사는 `not_required`이며 데이터 Tool 0회다. `graph_nodes` 출력 또는 그래프 코드를 캡처한다. |
| F3007·NF3003 결과 전달·부분 실패 격리 | 통과 | `tests/test_execution.py`, `tests/test_final_response.py`, `docs/verification/2026-11-20-nf3003-partial-tool-failure.json` | 상세 TIMEOUT 후보를 격리하고 다음 후보를 계속 처리한다. 휴게소 실패는 추천을 막지 않는다. |
| F3008 계산·제약·추정 안내 | 통과 | `tests/test_judgement.py`, `tests/test_recommendation.py`, `docs/verification/2026-11-19-constraint-judgement.json` | 안전거리·도달·우회를 일반 코드로 계산한다. 미래 교통·대기·유가를 생성하지 않는다. |
| F3009 재계획·기존 결과 재사용 | 통과 | `tests/test_graph_replanning.py`, `docs/verification/2026-11-19-graph-replanning.json` | 실행 로그의 `REPLAN_TRIGGERED`와 회차별 호출 수를 캡처한다. |
| F3009·NF3004 재계획 상한 | 통과 | `tests/test_graph_replanning.py`, `docs/verification/2026-11-20-nf3004-replan-limits.json` | 추가 최대 2회·전체 평가 최대 9곳에서 종료 사유를 반환한다. |
| F3010 후속 요청 조건 병합 | 통과 | `tests/test_conversation_memory.py`, `docs/verification/2026-11-19-conversation-memory.json` | 같은 `session_id`의 최초/후속 `conditions`를 나란히 캡처한다. |
| F3011 추천·보조·근거·제외 사유 | 통과 | `tests/test_final_response.py`, `docs/verification/2026-11-19-final-response.json`, `frontend/dist/app.js` | 추천 근거 모달과 제외 사유가 포함된 응답 화면을 캡처한다. |
| F3011 건강·금융 위험 안내 | 해당 없음 | `docs/PRD.md` 서비스 범위 | FuelRoute는 건강·금융 판단 도메인이 아니다. 주행거리 추정·실시간 상태 미확인 한계는 별도 안내한다. |
| F3012·F3013 이벤트·세션 분리·LLM 판단 | 부분 확인 | `tests/test_events.py`, `tests/test_logging.py`, `docs/verification/2026-11-20-f3019-execution-modal.json` | 단계·Tool·소요시간·세션 분리는 통과했다. `LLM_DECISION`은 의도·상태를 저장하되 내부 추론은 저장하지 않는다. 서로 다른 두 세션 로그 화면을 추가 캡처한다. |
| F3012 Logging Agent 미연결 동작 | 통과 | `tests/test_events.py` | 구독자 없음·구독 해제·구독 실패에서 메인 응답이 유지된다. |
| F3013 Logging Agent LLM 미호출 | 통과 | `fuelroute/agent/logging.py`, `tests/test_logging.py` | Logging Agent는 EventBus와 SQLite만 사용한다. |
| F3013 원본 이벤트 보존·저장 실패 격리 | 통과 | `tests/test_logging.py`, `tests/test_events.py` | 이벤트를 LLM 요약으로 대체하지 않으며 DB 저장 실패가 대화 응답을 막지 않는다. |
| F3014 로그 목록·상세·삭제·수정 API 없음 | 통과 | `tests/test_log_api.py`, `tests/test_backend_api.py` | `/docs`에서 `/api/logs` GET/DELETE만 표시되는 화면을 캡처한다. |
| F3015 계획 저장·조회·시간 충돌 안내 | 통과 | `tests/test_plan_storage.py`, `tests/test_backend_api.py` | 겹치는 두 계획의 POST 결과 `conflicts`와 목록·상세를 캡처한다. |
| F3016 계획 수정·삭제·목록 반영 | 통과 | `tests/test_plan_storage.py`, `tests/test_frontend_ui.py`, `docs/verification/2026-11-20-f3018-browser.json` | 기존 실제 브라우저 검증이 있다. 최신 화면의 수정·삭제 전후 목록도 캡처한다. |
| F3017·F3018 API 분리·화면 연동 | 부분 확인 | `tests/test_backend_api.py`, `tests/test_frontend_ui.py`, `docs/verification/2026-11-20-f3018-dashboard.png` | ASGI/OpenAPI와 정적 API 호출은 통과했다. 최신 `/docs`와 Network의 `/api/chat`, `/api/plans`, `/api/logs`를 캡처한다. |
| F3019 단계·Tool·재계획·처리/실패 안내 | 부분 확인 | `tests/test_frontend_ui.py`, `docs/verification/2026-11-20-f3019-execution-modal.json`, `docs/verification/2026-11-20-f3019-execution-modal.png` | 실행 순서·상태·실패 코드는 구현됐다. 최신 화면에서 실행 중·재계획·Tool 실패·키 누락 안내를 각각 캡처한다. |
| 필수·NF3005 대표 요청 5종·응답·로그·차이·시간 | 통과 | `docs/verification/2026-11-19-final-response.json`, `docs/verification/2026-11-19-graph-replanning.json`, `docs/verification/2026-11-20-nf3003-partial-tool-failure.json`, `docs/verification/2026-11-20-nf3005-response-time.json` | 정상 실호출 약 15.1초, 상한 사례 약 24.5초다. PDF에 없는 합격 초 기준은 만들지 않았다. |
| 필수 전체 실행 로그·재계획 샘플 | 통과 | `docs/verification/2026-11-19-final-response.json`, `docs/verification/2026-11-19-graph-replanning.json`, `docs/verification/2026-11-20-f3019-execution-modal.json` | 제출 시 대표 execution 한 건과 재계획 회차를 발췌하거나 원본 파일 링크를 유지한다. |
| NF3001·NF3002 키·오류 상세 비노출 | 통과 | `docs/verification/2026-11-20-nf3001-secret-management.json`, `docs/verification/2026-11-20-nf3002-public-errors.json`, `tests/test_frontend_ui.py` | `.env`는 제출하지 않는다. 키 누락 화면에는 공개 code/message만 보이는지 캡처한다. |
| NF3006·NF3007 모듈 분리·API 출처/이용조건 | 통과 | `docs/verification/2026-11-20-nf3006-module-boundaries.json`, `docs/verification/2026-11-20-nf3007-api-sources.json`, `docs/API_USAGE.md`, `README.md` | Kakao Local 실제 앱 쿼터·무료 적용 여부는 사용자 콘솔 확인 대기다. Mobility 10,000회를 Local 한도로 사용하지 않는다. |
| 필수 전체·제출물·스크린샷·선택 심화 비교 | 부분 확인 | `fuelroute/`, `frontend/`, `tests/`, `docs/`, `README.md` | F3001~F3019·NF3001~NF3007 구현은 완료했고 F3020~F3023은 미선택이다. 최신 `/docs`·Network·실행 중·재계획·실패 화면 캡처와 Kakao Local 콘솔 확인이 남았다. |

## 제출용으로 추가 캡처할 화면

1. `python -m fuelroute tool-schemas`의 Tool 이름·description·입력 schema 출력.
2. 정상 Tool, 잘못된 입력, 키를 비운 별도 테스트 환경의 `ToolResult` 세 가지.
3. FastAPI `/docs`의 대화·위치·계획 CRUD·로그 GET/DELETE 경로.
4. 브라우저 Network의 `/api/chat`, `/api/plans`, `/api/logs` 요청과 상태.
5. 정상 추천과 추천 근거 모달(가격 기준시각·휴게소 출처/기준일/미확인 포함).
6. 계획 저장→수정→삭제 전후 목록과 겹침 경고.
7. 실행 중 상태, 재계획 표시, Tool 실패 안내, 키 누락 안내.
8. 후속 조건 변경 전·후와 서로 다른 두 세션의 로그 목록·상세.

캡처에는 `.env`, API 키, Authorization 헤더, 키가 포함된 URL, 로컬 사용자 경로와 원본 예외를
포함하지 않는다.
