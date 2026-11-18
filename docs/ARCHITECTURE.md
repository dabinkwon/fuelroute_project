# ARCHITECTURE — FuelRoute Agent

> **상태:** API·독립 Tool·저장/API 설계와 안전 구간 후보·실제 도달·우회비용 연결 검증 완료 / 전체 Agent 연결은 후속  
> 실제 API 호출과 기술 타당성 검증 결과에 따라 수정한다.  
> 요구사항 기준은 [`PRD.md`](PRD.md)를 따른다.

---

# 1. 시스템 개요

FuelRoute는 다음 책임을 분리한다.

1. **Frontend** — 사용자 입력, 추천 결과, 실행상태, 계획 관리
2. **FastAPI Backend** — HTTP API, 입력 검증, Agent 호출, CRUD
3. **Main Agent** — 요청 분석, 작업계획, Tool 선택, 재계획, 최종 응답
4. **Deterministic Services** — 연료/거리/비용 계산, 후보 판정
5. **External API Tools** — 경로·주유소·휴게소 보조정보 조회
6. **Event / Logging Agent** — 실행 이벤트 구독 및 저장
7. **SQLite** — 계획과 실행 로그 저장

---

# 2. 기술 스택

| 영역 | 기술 | 상태 | 이유 |
|---|---|---|---|
| Language | Python 3.11+ | 3.11.9 검증 | pyproj 3.7.2 요구 버전 반영, 원본 3.10+ 허용 범위 |
| Backend | FastAPI | 확정 | 원본 프로젝트 요구사항 |
| Agent Orchestration | LangGraph | 확정 | 원본 프로젝트 요구사항 및 상태 기반 재계획에 적합 |
| LLM | GMS / OpenAI 호환 Chat Completions | 현재 계정의 텍스트·구조화 출력·Tool 왕복 검증 완료 | 사용자 지정 LLM 제공 경로 |
| LLM Client | LangChain `ChatOpenAI` (`langchain-openai`) | 우선 선택 | GMS 주소·키 설정과 Tool 연결 검증 |
| Database | SQLite | 확정 | 원본 프로젝트 요구사항 |
| Validation | Pydantic | 확정 | FastAPI 스키마 정의 |
| Frontend | HTML/CSS 정적 소개 화면 / Netlify | 운영 배포 완료, 기능 화면은 후속 | 카카오 앱 대표 도메인 확보 |
| HTTP Client | httpx | 구현·실호출 검증 | 비동기 외부 API 호출 및 타임아웃 처리 |
| Coordinate Conversion | pyproj 3.7.2 / KATEC TM128 | 독립 기준점·실제 조회 검증 | Korean 1985(EPSG:4162) datum의 사용자 정의 TM128 |

## 2.1 GMS LLM 호출

**LLM은 GMS를 통해 호출한다.** OpenAI 호환 Chat Completions 방식을 우선 선택하고 `ChatOpenAI`의 `base_url`, `api_key`, `model`에 GMS 설정을 전달한다. 정확한 주소와 인증 방식은 GMS 콘솔 안내로 확인한다. GMS를 거치지 않는 제공사 직접 호출로 자동 전환하지 않는다.

- **현재 모델:** `gpt-5.4-mini`. 2026-11-18 현재 계정에서 텍스트·구조화 출력·Tool 왕복 검증을 통과했다.
- **선정 근거:** 원 제공사 문서상 Chat Completions, function calling, structured outputs를 지원하므로 조건 추출·Tool 선택·결과 설명 역할에 적합하다고 판단했다. 계산과 제약 판정은 일반 코드가 맡는다.
- **검증 범위:** 해당 계정의 설정된 주소·모델·인증으로 검증했다. 전체 모델 목록과 계정별 사용량 한도는 별도 콘솔 확인 대상이다.
- **모델 변경:** 다른 계정에서 제공되지 않으면 GMS 제공 모델 중 같은 검증을 통과한 모델로 `GMS_MODEL`을 변경한다.

검증 순서:

1. GMS 콘솔에서 제공 모델 ID·인증·호출 주소·한도를 확인한다.
2. 텍스트 요청 1건으로 인증과 응답을 확인한다.
3. 자연어에서 주행 조건을 구조화해 추출하고 필수값 누락을 검출한다.
4. 상세 설명이 등록된 Tool 하나를 선택·호출하고, 결과를 전달한 뒤 최종 응답까지 확인한다.
5. 인증 실패, 타임아웃, 스키마 불일치를 처리한다. 키를 제외한 모델 ID·호출 경로·응답 시간·사용량·검증 결과를 기록한다.

근거: [OpenAI 모델 명세](https://developers.openai.com/api/docs/models/gpt-5.4-mini), [LangChain ChatOpenAI 문서](https://docs.langchain.com/oss/python/integrations/chat/openai). GMS 콘솔 설정을 사용한 실제 결과는 [검증 기록](verification/2026-11-18.json)에 남겼다. 전체 `/chat/completions` 주소 입력은 SDK base로 정규화하며 검증별 HTTP 클라이언트를 분리해 이전 호출의 종료가 다음 호출을 막지 않게 했다.

## 2.2 개발 환경과 키 분리

`.env.example`·로컬 `.env`·가상환경 구성을 완료했고 사용자가 입력한 키로 실제 호출을 검증했다. 아래는 비밀값이 없는 템플릿이며 실제 키는 문서에 기록하지 않는다.

```dotenv
GMS_API_KEY=
GMS_BASE_URL=
GMS_MODEL=gpt-5.4-mini
OPINET_API_KEY=
KAKAO_REST_API_KEY=
DATA_GO_KR_SERVICE_KEY=
HTTP_TIMEOUT_SECONDS=20
```

- `.venv`를 만들고 프로젝트의 `requirements.txt`로 의존성을 설치한다. 초기 후보는 `fastapi`, `uvicorn`, `langgraph`, `langchain-openai`, `httpx`, `pydantic-settings`이며 실제 호환성을 확인한 버전을 기록한다.
- 좌표 변환 패키지는 정확한 CRS와 변환 결과를 검증한 뒤 추가한다.
- 실제 값은 `.env`에만 넣고 `.gitignore`에 `.env`, `.venv/`, 로컬 DB·민감 응답 파일을 등록한다. 공유하는 `.env.example`에는 키를 넣지 않는다.
- 환경변수 누락은 외부 호출 전에 검출한다. 키가 포함된 URL·헤더를 로그나 응답 샘플에 저장하지 않는다.
- GMS 인증 확인과 외부 데이터 API 3종 인증 확인은 별도 작업이다.

### 구현한 기반과 남은 검증

- `frontend/dist/`: 카카오 등록용 서비스 소개 프론트를 Sites에 선배포한 뒤 사용자 요청으로 Netlify에 배포했다. `netlify.toml`에서 정적 폴더만 지정한다. Backend 미연결이며 API 키를 사용하지 않는다. 배포 주소와 외부 공개 상태는 README에서 관리한다.
- `fuelroute/config.py`: `.env`보다 OS 환경변수를 우선하고 빈 값을 미설정으로 처리한다. 키는 `SecretStr`로 보관하며 `doctor`는 누락 변수명만 출력한다.
- `fuelroute/main.py`: 실제 구현된 Endpoint는 `GET /health`다. Chat·계획·로그 API는 아래 설계안대로 후속 구현한다.
- `fuelroute/contracts.py`: `ToolResult[T]`의 성공/실패 필드 불변조건과 완료 시각의 시간대를 검증한다.
- `fuelroute/services.py`: 연료 입력의 단위·범위·유한성을 검증하고 안전거리를 계산한다. LLM용 Tool에도 설명과 입력 스키마를 등록했다.
- `fuelroute/gms.py`: GMS ChatOpenAI 텍스트·구조화 조건 추출·단일 로컬 Tool 왕복을 실제 계정에서 검증했다.
- `fuelroute/probes.py`: Opinet KATEC 반경/ID 상세, Kakao 기본/경유 상세경로, 휴게소 한 페이지를 독립 조회한다. 원본 응답과 공통 메타를 반환하고 키는 마스킹한다. 요청별 타임아웃은 기본 20초, 자동 재시도는 0회다.
- `fuelroute/tools.py`: `search_gas_stations`, `get_station_details`, `search_rest_area_info`를 공통 응답·입력 설명과 함께 등록했다. WGS84↔KATEC 변환, 가격·시각 정규화, 휴게소 전체 페이지·반경/방향 필터를 실제 응답으로 확인했다. 휴게소와 동일 주유소임을 추측하지 않는다.
- 설치 버전은 `requirements.txt`, 개발 도구는 `requirements-dev.txt`, 전체 Windows/Python 3.11.9 환경은 `requirements.lock.txt`에 기록했다.

실제 요청 근거: [Opinet 반경 검색](https://www.opinet.co.kr/user/custapi/openApiInfoDtl.do?apiId=3), [Opinet ID 상세](https://www.opinet.co.kr/user/custapi/openApiInfoDtl.do?apiId=1), [Kakao 길찾기](https://developers.kakaomobility.com/en/guide/navi-api/directions), [휴게소 표준데이터](https://www.data.go.kr/data/15025446/standard.do). 2026-11-18 사용자 확인값인 계정별 호출 한도·휴게소 API 활용기간은 [개인 실행 기록](daily/2026-11-18.md)과 [검증 기록](verification/2026-11-18.json)에 남겼다. 공개 이용조건·현재 적용 범위는 [API_USAGE](API_USAGE.md)에 기록했다. 개별 계약과 상용 재배포 권한 확보를 뜻하지 않는다.

---

# 3. 시스템 아키텍처

```text
[Frontend]
    │
    │ HTTP/JSON
    ▼
[FastAPI]
    │
    ├──────────────► [Plan Repository] ─► [SQLite]
    │
    ├──────────────► [Log Repository]  ─► [SQLite]
    │
    ▼
[Main Agent / LangGraph]
    │
    ├─ request analysis
    ├─ planning
    ├─ tool execution
    ├─ constraint evaluation
    ├─ replanning
    └─ final response
    │
    ├────────► [Deterministic Services]
    │             ├─ safe range
    │             ├─ route sampling
    │             ├─ candidate filtering
    │             └─ detour/cost calculation
    │
    ├────────► [Kakao Mobility Tool]
    ├────────► [Opinet Tool]
    └────────► [Rest Area Information Tool]

[Main Agent]
    │ emits events
    ▼
[Event Bus]
    ▼
[Logging Agent]
    ▼
[Execution Log Repository]
```

### 설계 원칙

- LLM은 계산기가 아니다.
- 연료량·거리·비용과 같은 계산은 deterministic service가 수행한다.
- Tool이 제공하지 않는 값을 LLM이 채우지 않는다.
- 로깅 Agent는 메인 Agent의 실행을 막지 않는다.
- 재계획에서 이미 확보한 Tool 결과를 최대한 재사용한다.

---

# 4. 데이터 흐름

## 4.1 정상 흐름

```text
사용자 요청
→ 조건 추출
→ 필수값 확인
→ 기본 자동차 경로 조회
→ 안전 주행 가능거리 계산
→ 검색 대상 경로 구간 결정
→ 경로상 검색점 추출
→ 주유소 후보 검색
→ 후보 상세정보 조회
→ 휴게소 보조정보 검색 및 매칭 상태 확인
→ 후보 경유 경로 조회
→ 제약 판정
→ 후보 정렬
→ 최종 추천
→ 선택 시 계획 저장
```

## 4.2 재계획 흐름

```text
제약 충족 후보 없음
→ 실패 원인 확인
→ 재계획 가능한 실패인가?
   ├─ 아니오: 사용자 안내 후 종료
   └─ 예
       → 기존 Tool 결과 재사용
       → 검색 지점/후보 범위 수정
       → 추가 조회
       → 다시 제약 판정
       → 상한 확인
```

---

# 5. 데이터 설계

## 5.1 주요 엔티티

### TripContext

세션에서 현재 주행 조건을 표현한다.

```text
session_id
origin
destination
fuel_type
current_fuel_l
avg_efficiency_kmpl
reserve_fuel_l
max_detour_min?
max_detour_km?
budget_krw?
preferred_refuel_after_km?
```

전체 원본 Tool 응답을 장기 Memory로 저장하지 않는다.

### FuelPlan

저장 가능한 최종 계획.

```text
id
session_id
trip_date
origin
destination
fuel_type
recommended_station_id
recommended_station_name
station_price
price_observed_at?
rest_area_info?       # 보조정보·출처·데이터 기준일·매칭 상태
distance_from_origin_km
detour_distance_km
detour_duration_min
created_at
updated_at
```

원본 응답 전체 대신 추천 근거의 스냅샷을 저장한다. 보조정보가 없으면 `null`을 허용한다. 정확한 컬럼과 JSON 저장 범위는 실제 응답으로 검증한다.

### ExecutionSession

```text
session_id
started_at
finished_at
status
```

### ExecutionEvent

```text
id
session_id
sequence
event_type
step
payload
created_at
```

실행 로그는 수정하지 않는다.

### SQLite 저장 초안

| 테이블 | 키·관계 | 저장 내용 |
|---|---|---|
| `execution_sessions` | `session_id` PK | 실행 시작·종료·상태 |
| `execution_events` | `id` PK, `session_id` FK, `(session_id, sequence)` UNIQUE | 이벤트 종류·단계·마스킹된 payload·시각 |
| `fuel_plans` | `id` PK, `session_id` nullable FK | 입력 조건·추천 근거·가격 시각·보조정보 스냅샷·계획 날짜 |

계획은 로그 삭제와 독립적으로 남아야 한다. 세션 로그 삭제 시 이벤트는 함께 삭제하고 계획의 세션 참조는 `NULL`로 바꾸는 정책을 설계한다. 시각은 시간대가 있는 ISO 8601 기준으로 처리하고 화면에서 KST로 표시한다. 오늘은 스키마·관계·샘플 저장 형식을 검증하며 전체 CRUD 구현은 후속 단계다.

실행 가능한 설계는 [`storage.sql`](storage.sql)에 기록했다. `tests/test_storage_design.py`와 실제 조회 스냅샷을 넣은 메모리 SQLite 실험에서 이벤트 순서 중복·수정 금지, 로그 삭제 시 이벤트 제거·계획 보존을 확인했다. 실제 서비스 DB나 Repository는 아직 생성하지 않는다. `fuel_plans`는 입력·추천 근거를 JSON 스냅샷으로 보관하고 `planned_start_at/end_at`으로 시간 충돌 안내를 설계한다. 실제 샘플은 최종 추천이 아닌 `draft-validation-sample`로 구분했다.

---

# 6. API 설계안

`GET /health`를 제외한 아래 Endpoint는 **설계안**이다.

## POST `/api/chat`

- **기능:** 사용자 요청을 Main Agent에 전달
- **관련 요구사항:** FR-001 ~ FR-009, FR-011, FR-012
- **Request 예시:**

```json
{
  "session_id": null,
  "message": "대구에서 서울 가는데 휘발유 15L 남았고 연비는 12km/L야. 최소 3L는 남기고 싶어."
}
```

- **Response 개념:**

```json
{
  "session_id": "string",
  "answer": "string",
  "recommendation": null,
  "needs_user_input": false,
  "execution_summary": []
}
```

실제 schema는 Agent State 확정 후 결정한다.

## Plans

| Method | Endpoint | 기능 | 관련 요구사항 |
|---|---|---|---|
| POST | `/api/plans` | 계획 저장 | FR-010 |
| GET | `/api/plans` | 계획 목록 | FR-010 |
| GET | `/api/plans/{plan_id}` | 계획 상세 | FR-010 |
| PATCH | `/api/plans/{plan_id}` | 계획 수정 | FR-010 |
| DELETE | `/api/plans/{plan_id}` | 계획 삭제 | FR-010 |

계획 시간 충돌은 저장을 막지 않고 응답에 충돌 정보를 포함해야 한다.

## Logs

| Method | Endpoint | 기능 | 관련 요구사항 |
|---|---|---|---|
| GET | `/api/logs` | 세션 로그 목록 | FR-011 |
| GET | `/api/logs/{session_id}` | 세션 로그 상세 | FR-011 |
| DELETE | `/api/logs/{session_id}` | 로그 삭제 | FR-011 |

**로그 수정 Endpoint는 만들지 않는다.**

## 화면 구성과 연결

| 화면/영역 | 주요 내용 | 연결 API |
|---|---|---|
| 주행 조건·대화 | 출발·목적지, 연료 조건, 추가 입력 요청 | `/api/chat` |
| 추천 결과 | 가격·안전거리·우회시간·제외 사유·휴게소 보조정보 | `/api/chat` 응답 |
| 실행상태·이력 | 단계·Tool 성공/실패·재계획·로그 조회/삭제 | `/api/logs` 계열 |
| 계획 관리 | 저장·목록·상세·수정·삭제, 시간 충돌 안내 | `/api/plans` 계열 |

오늘은 입력·결과 필드와 화면의 연결을 확정한다. 세 필수 조회 TODO의 개별 검증은 Python 함수 또는 CLI로 수행하며, 검증을 위해 공개 디버그 Endpoint를 추가할 필요는 없다.

## 오늘 확정한 HTTP 입출력 계약 — 구현은 후속

실제 조회 Tool의 데이터를 바탕으로 아래 계약을 설계한다. `GET /health` 외 HTTP Endpoint가 이미 동작한다고 해석하지 않는다.

| Endpoint | 입력 | 성공 응답·상태 |
|---|---|---|
| `POST /api/chat` | `message: string`, `session_id: string 또는 null`; UI 입력 시 `trip: TripContext 또는 null` | 200, `session_id`, `answer`, `recommendation: FuelPlan 또는 null`, `needs_user_input: bool`, `missing_fields: string[]`, `execution_summary: EventSummary[]`, `warnings: string[]` |
| `POST /api/plans` | 주행 입력 스냅샷·검증된 추천 스냅샷·`trip_date`·선택 계획 시작/종료시각 | 201, `plan: FuelPlan`, `conflicts: [{plan_id, reason}]`; 충돌을 이유로 저장을 거절하지 않음 |
| `GET /api/plans` | 선택 `trip_date: YYYY-MM-DD` | 200, `{items: FuelPlan[]}` |
| `GET /api/plans/{plan_id}` | 저장한 계획 ID | 200, `FuelPlan` |
| `PATCH /api/plans/{plan_id}` | 변경할 필드만; 조건이 바뀌면 기존 추천은 재검증 필요 상태로 전환 | 200, `{plan: FuelPlan, conflicts: []}` |
| `DELETE /api/plans/{plan_id}` | 저장한 계획 ID | 204, 본문 없음 |
| `GET /api/logs` | 선택 세션 상태 | 200, `{items: ExecutionSession[]}` |
| `GET /api/logs/{session_id}` | 세션 ID | 200, `{session: ExecutionSession, events: ExecutionEvent[]}`; sequence 오름차순 |
| `DELETE /api/logs/{session_id}` | 세션 ID | 204, 본문 없음; 저장한 계획은 유지 |

- `TripContext`의 좌표는 `{longitude: number, latitude: number, label?: string}`인 WGS84, 연료는 L, 연비는 km/L다. 필수값 누락은 임의 보충하지 않고 Chat의 `needs_user_input=true`/`missing_fields`로 안내한다.
- `FuelPlan`은 5장의 저장 구조를 따르며 추천 주유소는 `StationDetails`, 실제 도달·우회 거리(km)/시간(min), 추천 근거, 보조정보 `RestAreaInfo[]|null`을 담는다. HTTP 직전 도메인 계산에서 m/s→km/min 변환을 명시한다.
- `ExecutionEvent`는 11장의 세션/순서/Tool/요청ID/시각과 마스킹된 payload를 사용한다. 원본 인증 URL·헤더·내부 예외 trace는 응답에 포함하지 않는다.
- HTTP 오류는 `{error: {code, message, request_id}}`로 통일한다. 잘못된 입력은 422, 없는 계획/로그는 404, 일시 외부 장애로 핵심 판단 불가 시 503이다. 후보 없음·추가 입력·보조정보 실패는 대화 상태로 표현하고 UI가 `warnings`와 근거를 표시한다.
- 화면 상태: 요청 중은 처리 중 표시, 필수값 누락은 입력 보완, 추천 없음은 근거 안내, 휴게소만 실패하면 보조정보 미확인 표시다. 로그 화면은 수정 버튼을 제공하지 않는다.
- 계획 시간 충돌은 겹치는 시작/종료 구간을 비교하고, 구체 시각이 없으면 같은 날짜 계획을 참고 경고한다. 날짜 충돌만으로 저장 실패를 반환하지 않는다.

오늘은 위 필드·단위·누락 정책을 실응답에 맞춰 정리했고, Backend/Frontend 전체 구현·연결은 진행하지 않았다.

---

# 7. 핵심 비즈니스 로직

## 7.1 안전 주행 가능 거리 — 확정

```text
usable_fuel_l = current_fuel_l - reserve_fuel_l
safe_range_km = usable_fuel_l × avg_efficiency_kmpl
```

검증:

```text
current_fuel_l > 0
avg_efficiency_kmpl > 0
reserve_fuel_l >= 0
reserve_fuel_l < current_fuel_l
```

별도 안전계수 적용은 아직 확정하지 않는다.

---

## 7.2 접근 가능한 주유소 판별 — 설계안

1. 기본 경로 총거리를 조회한다.
2. `safe_range_km` 범위 안의 경로 구간만 대상으로 한다.
3. 검색 지점을 몇 개 선택한다.
4. 각 검색 지점 주변 주유소를 조회한다.
5. 후보별 출발지→주유소 실제 경로 거리가 안전 한계 안인지 최종 확인한다.

단순히 검색 기준점과 주유소의 직선거리만으로 도달 가능 여부를 최종 판정하지 않는다.

---

## 7.3 경로상 검색 지점 생성 — 구현·연결 실험 검증 완료

Opinet은 한 번의 요청에서 기준 KATEC 좌표 주변 최대 5km까지 검색한다.

따라서 전체 경로를 한 번에 조회할 수 없다.

현재 구현은 `routing.py`의 `build_route_path`와 `generate_search_points`다.

- 도로 거리 합=구간 거리, 구간 거리 합=요약 거리를 검증한다. 도로별 거리 축을 유지하며 도로 내부의 보고 거리를 인접 vertex의 지오데식 길이에 비례해 배분한다.
- 누락·홀수 좌표·5m 초과 도로 단절·양수 거리의 빈 기하는 거절한다. 도로 내부 보간은 근사이며 자동차 도달 판정을 대신하지 않는다.
- 안전거리와 경로 끝 중 가까운 곳에서 반경의 1/5/10배 앞을 최대 세 점으로 선택하고 0m로 제한·중복 제거한다. 기본 반경 5km, 안전거리 144km이면 139/119/94km다.
- 전체 경로를 고정 5km 간격으로 검색하지 않는다. 샘플 검색점 바깥의 후보 누락 가능성이 있으므로 완전 탐색으로 해석하지 않는다.

예:

```text
safe_range = 144km
반경 = 5km
검색점 = 139km → 119km → 94km
후보별 실제 자동차 접근·경유 첫 구간으로 도달 가능성 확인
```

저장된 실제 장거리 응답에서 80/100/130km 좌표를 추출했다. 새 실호출에서는 검색점 세 곳에서 후보 16곳을 확보했다. 추가 검색·최종 검색점 전략은 후속 정책 작업이다. [실험 기록](verification/2026-11-18-route-spike.json)을 따른다.

---

## 7.4 우회 거리 및 시간 — 개별 비용 평가 구현·실호출 완료

```text
base_distance = origin → destination 거리
base_duration = origin → destination 시간

candidate_distance = origin → station → destination 거리
candidate_duration = origin → station → destination 시간

detour_distance = candidate_distance - base_distance
detour_duration = candidate_duration - base_duration
```

`route_spike.py`는 동일한 옵션의 기본·독립 접근·단일 경유 경로를 조회한다. 도달거리는 독립 접근 거리와 경유 첫 구간 거리 중 큰 값이다. 단일 경유 응답은 구간이 두 개여야 하며 안전거리를 넘으면 제외한다.

초 단위 시간·m 단위 거리를 분/km 상한과 명시적으로 환산해 비교한다. 경유-기본 차이가 음수면 원래 차이를 raw 필드에 보존하고 추가 비용 상한에는 0을 적용한다. 교통·제공사 좌표 보정 때문에 요청 간 값은 달라질 수 있다. 실제 후보 세 곳의 추가 시간은 537/582/459초였으며, 같은 실응답 비용을 5분 상한으로 로컬 재판정하면 모두 제외됐다.

---

## 7.5 추가 이동 연료비 — 제안

필요하면 다음 근사식을 사용할 수 있다.

```text
extra_fuel_l = detour_distance_km / avg_efficiency_kmpl
extra_fuel_cost = extra_fuel_l × reference_fuel_price
```

하지만 `reference_fuel_price`를 무엇으로 둘지 아직 확정하지 않았다.

따라서 MVP 추천 로직의 필수 계산으로 확정하지 않는다.

---

## 7.6 추천 방식 — 제안

가중치 기반 AI 점수보다 먼저 하드 제약 필터링을 사용한다.

### 1단계 안전 필터

- 유종 일치
- 안전거리 이내 도달 가능

### 2단계 사용자 제약

- 최대 우회시간
- 최대 우회거리
- 예산
- 원하는 주유 시점

### 3단계 정렬

현재 제안:

1. 조건 충족
2. 우회 비용
3. 가격

정확한 정렬 기준은 실제 후보 데이터로 테스트 후 확정한다.

---

## 7.7 연결 실험 CLI와 적용 범위

`python -m fuelroute route-spike`는 연료·좌표·유종과 검색/평가 상한을 명시적으로 받는다. LLM이나 전체 Agent는 실행하지 않는다. 기본값에서 Opinet 최대 6회·Kakao 최대 7회이며 실제 호출 수·각 호출 메타·검색점·평가 결과·미평가 건수를 반환한다.

후보는 ID로 중복 제거하고 가격순 일부만 평가한다. 상세 누락·개별 후보 경로 실패는 제외 사유로 남기고 다음 후보를 평가하며, 인증·설정·한도 오류는 중단한다. 실행이 끝났지만 후보가 없거나 모두 제외되면 `status=no_eligible_candidates`를 반환한다. 이는 단일 조회 Tool의 EMPTY_RESULT 오류와 구분되는 실험 실행 결과다.

현재 실험은 실제 도달·우회비용의 기술 검증이다. 전역 최저가 추천·전체 경로 커버리지·자동 재계획·공유 일일 쿼터 집계·Agent 등록 전체 완료를 의미하지 않는다. 운영 조건과 출처는 [API_USAGE](API_USAGE.md)를 따른다.

# 8. 외부 API 및 데이터 의존성

## 8.1 Opinet

### 공식 문서상 확인된 기능

- 특정 위치 중심 반경 내 주유소 조회
- 기준 좌표: KATEC X/Y
- 반경 최대 5,000m
- 유종 지정
- 가격순/거리순 정렬
- 주유소 ID, 이름, 가격, 기준점 거리, KATEC 좌표 반환

### 현재 상태

**휘발유/경유 반경 검색·후보 ID 상세 조회·0건 실제 검증 완료**

공식 문서는 인증 파라미터를 `certkey`로 안내하지만, 동일 키·조건 비교에서 실제 서버는 `code`에 정상 데이터를 반환했다. 구현은 실제 검증한 `code`를 사용한다. 잘못된 키도 HTTP 200/빈 배열을 반환했으므로 0건만으로 인증 실패와 후보 없음을 구분할 수 없다. 인증 성공은 정상 데이터 응답으로 확인한다. 후보 가격에는 기준시각이 없고 상세 `OIL_PRICE.TRADE_DT/TRADE_TM`을 KST 시각으로 정규화한다.

### 검증 과제

- 실제 API 키 발급
- 정상 호출
- 결과 0건
- 인증 실패
- 호출 한도
- 상세정보 API 연결
- 데이터 갱신 시각

---

## 8.2 Kakao Mobility 길찾기

### 공식 문서상 확인된 기능

- 출발지/목적지 자동차 경로
- 경유지
- 거리·시간
- 상세 도로 정보
- `vertexes`: 경도/위도 좌표 배열

### 현재 상태

**기본·경유·대구→서울 상세경로 실호출 검증 완료**

대구→서울 샘플은 총 292,235m / 14,525초였으며 `sections[].roads[].vertexes`를 확인했다. 교통과 요청 시점에 따라 결과는 달라진다. 거리(m)·시간(s)을 보존하며 경유 요청은 기본 요청과 같은 옵션으로 보낸다. N km 검색점 추출·실제 후보 우회 판정은 후속 작업이다.

### 검증 과제

- 실제 키 발급
- `summary=false` 상세경로 호출
- 대구→서울 같은 장거리 요청 응답 크기/시간
- 경로의 N km 지점 좌표 추출
- 주유소 경유 경로 계산

---

## 8.3 제3 외부 API

### 선정: 공공데이터포털 전국휴게소정보표준데이터

- **제공기관:** 한국도로공사. [공식 데이터·API 안내](https://www.data.go.kr/data/15025446/standard.do).
- **요청 주소:** `https://api.data.go.kr/openapi/tn_pubr_public_rest_area_api`.
- **용도:** 휴게소 위치·도로 방향·주유소 유무·화장실·음식점 등 보조정보를 조회해 추천 결과에 함께 표시한다.
- **선정 이유:** 위치·노선·방향 정보로 경로 및 주유 후보와의 연관성을 검증할 수 있고, 가격·우회시간과 다른 사용자 정보를 제공한다.
- **상태:** 실호출·215건 페이지 수집·좌표/방향/시설/기준일·반경 필터 검증 완료. 주유소와 동일 시설임을 입증하는 매칭은 미확인.

실제 응답은 최상위 `header/body`, `body.items.item` 배열이고 숫자 좌표는 문자열이다. 기존 문서형 `response.header/body`도 파싱하되 성공 코드 `00`과 전체 건수를 확인한다. `search_rest_area_info`는 페이지당 1000건, 최대 10페이지로 전체를 읽고 애플리케이션에서 반경·노선·방향을 적용한다. 시설 Y/N은 true/false, 누락은 JSON null(미확인)로 표현한다. 데이터 기준일과 호출 완료시각을 구분한다.

Tool은 페이지 단위 응답을 정규화하고 애플리케이션에서 거리·노선·방향 조건을 적용한다. API 자체가 반경 검색을 제공한다고 가정하지 않는다. API 인증 파라미터, 페이지 처리, 응답 형식은 실제 명세와 응답으로 확정한다.

공통 주유소 ID가 있다고 가정하지 않는다. 위치·노선·방향 등을 함께 검증하고, 매칭을 입증할 수 없으면 `match_status=unverified`인 주변 참고 정보로 표시한다. 시설 누락은 `unknown`으로 처리한다. 데이터 기준일과 조회 시각을 구분하고 실시간 영업 여부를 보장하지 않는다. API 실패 또는 미매칭이 주유 후보 자체를 제외하는 근거가 되지는 않는다.

### 대안 비교

| 후보 | 판단 |
|---|---|
| 전국휴게소정보표준데이터 | 위치·방향·시설을 함께 검증할 수 있어 기본 보조조회용으로 선정 |
| [한국도로공사 휴게소 편의시설 현황](https://www.data.go.kr/data/15076644/openapi.do) | 더 세부적인 시설 정보가 필요할 때 검토 |
| 별도 실시간 교통 API | 현재 경로·우회 판단과 역할이 겹칠 수 있어 후순위 |
| 국제유가 API | 현재 주유 후보의 안전·비용 판정과 직접 연결되지 않아 제외 |

**외부 데이터 3종은 Opinet, Kakao Mobility, 휴게소 API이며 GMS LLM 호출을 이 개수에 포함하지 않는다.**

---

# 9. 좌표 변환

## 문제

- Kakao Mobility: 경도/위도
- Opinet 반경 검색: KATEC X/Y

두 API를 연결하려면 좌표 변환이 필요하다.

## 상태

**pyproj 3.7.2 구현 및 독립 기준점·실제 Opinet 검색 연결 검증 완료**

## 검증 목표

```text
Kakao 경로 좌표
→ 좌표 변환
→ KATEC X/Y
→ Opinet 반경 검색
→ 예상 지역의 주유소 반환
```

KATEC는 Korean 1985(EPSG:4162)의 Bessel datum에 TM128 투영(원점 위도38/경도128, 축척0.9999, X오프셋400000m/Y오프셋600000m)을 적용한다. EPSG:5178/5179로 대체하지 않는다. `always_xy=True`, `allow_ballpark=False`를 사용한다.

[Naver 공식 좌표계 정의](https://api-fin.ncloud-docs.com/docs/ai-naver-mapsreversegeocoding-gc)와 [TMAP의 WGS84↔KATECH 기준점](https://tmap-skopenapi.readme.io/reference/자동차-경로안내)을 대조했다. 서울·부산·대전 세 기준점의 최대 차이는 2.9405m로 검증 허용치 5m 이내다. 실제 Kakao 경로 좌표→변환→Opinet 후보/상세 주소로 지역 연결도 확인한다. Kakao Local 변환 API는 현재 앱에서 비활성(403)이지만 서비스가 그 API에 의존하지 않으므로 추가 활성화는 필요 없다.

---

# 10. Agent 구조 설계안

```text
START
 ↓
analyze_request
 ↓
validate_required_conditions
 ├─ missing → ask_user
 ↓
build_plan
 ↓
get_base_route
 ↓
calculate_safe_range
 ↓
select_search_points
 ↓
search_gas_stations
 ↓
get_station_details
 ↓
search_rest_area_info   # 실패하면 보조정보 미확인으로 기록하고 계속
 ↓
calculate_candidate_routes
 ↓
evaluate_constraints
 ├─ candidates → rank_candidates
 └─ none → replan
              ├─ limit reached → failure response
              └─ retry with revised plan
 ↓
generate_final_response
 ↓
END
```

## 재계획하는 경우

- 검색 결과 0건
- 모든 후보가 안전거리 초과
- 모든 후보가 사용자 우회 제한 초과
- 검색 지점을 바꾸면 결과가 달라질 가능성이 있음

## 재계획하지 않는 경우

- API 키 누락
- 인증 실패
- 필수 사용자 입력 누락
- 현재 연료가 안전 잔량 이하
- 동일 호출을 반복해도 결과가 같을 치명적 오류

재계획 상한은 필요하다. 현재 `3회`는 원본 명세의 예시를 참고한 초안이며 실제 값은 검증 후 확정한다.

---

# 11. 이벤트 및 로깅

오늘 정의하고 실제 Tool 샘플에 대입할 이벤트:

```text
STEP_STARTED
STEP_COMPLETED
TOOL_REQUESTED
TOOL_SUCCEEDED
TOOL_FAILED
CONSTRAINT_EVALUATED
REPLAN_TRIGGERED
REPLAN_LIMIT_REACHED
LLM_DECISION
FINAL_RESPONSE_CREATED
```

Logging Agent는 이벤트를 세션 단위로 그대로 저장한다.

**수집·저장에는 LLM을 사용하지 않는다.**

이벤트에는 `session_id`, `sequence`, `event_type`, `step`, `tool_name?`, `request_id?`, `elapsed_ms?`, `payload`, `created_at`을 포함한다. `payload`에는 입력 요약·결과 개수·오류 코드·판정 근거를 남긴다. LLM 내부 추론을 수집하지 않고 선택한 Tool과 결과 등 확인 가능한 실행 정보를 기록한다. 오늘의 독립 호출도 같은 형식으로 `TOOL_REQUESTED` → `TOOL_SUCCEEDED` 또는 `TOOL_FAILED`를 남길 수 있도록 계약을 정한다.

---

# 12. 오류 처리 및 보안

## 외부 API 실패

- Tool 내부 예외를 공통 실패 형태로 변환
- 다른 후보가 있으면 계속 진행
- 판단에 필수인 데이터가 없으면 판단 불가로 종료

## API 키

- 환경변수
- Git 저장 금지
- Frontend 노출 금지

## 위치 및 주행정보

현재 MVP는 사용자가 직접 입력한 출발지·목적지와 차량 조건을 처리한다.

정확한 개인정보 보존기간과 저장정책은 아직 미정이며, 저장 필요성이 없는 원시 위치/경로 데이터는 최소화하는 방향을 권장한다.

---

# 13. 기술적 위험과 검증 계획

| Risk | 영향 | 상태 | 먼저 할 검증 |
|---|---|---|---|
| GMS 모델·호환 기능 | 요청 분석·Tool 선택 불가 | High | 계정 제공 목록과 텍스트·구조화 출력·Tool 왕복 호출 |
| Opinet 실제 인증/호출 | 핵심 후보 검색 불가 | High | 실제 키로 반경/상세 호출 |
| Kakao 장거리 상세경로 | 검색 지점 생성 불가 | High | 대구→서울 `summary=false` 호출 |
| 좌표 변환 | 두 API 연결 실패 | Critical | 경도/위도→KATEC 실험 |
| 검색 지점 전략 | 호출량 과다/후보 누락 | High | 몇 개 지점으로 후보 확보 가능한지 실험 |
| 휴게소 API·주유소 매칭 | 보조정보 오연결 또는 외부 API 조건 미충족 | High | 실호출·페이지 처리·위치/방향 매칭 확인 |
| 실제 연비 오차 | 도달거리 과신 | Medium | 안전 잔량 + 안내 정책 확정 |
| 가격 갱신 시차 | 추천 가격 불일치 | Medium | 가격 기준시각 확인/표시 |
| 추천 정렬 기준 | 결과 설명 어려움 | Medium | 실제 후보 데이터로 비교 |

---

# 14. 함수 설명과 Tool 계약

## 14.1 함수 description 작성 규칙

**새로 작성하거나 수정하는 모든 함수·메서드에는 상세한 docstring을 작성한다.** AI 개발자가 구현 본문을 열지 않고 설명만 읽어도 올바른 입력을 만들고 결과를 사용할 수 있어야 한다. 단순히 “주유소를 검색한다”처럼 함수명을 반복하는 설명은 충분하지 않다.

| 설명 항목 | 반드시 담을 내용 |
|---|---|
| 목적과 책임 | 해결하는 문제, 수행하는 작업의 범위 |
| 호출 시점·선행 조건 | 언제 사용하는지, 먼저 필요한 데이터·설정, 사용하면 안 되는 조건 |
| 입력 | 각 인자의 의미·타입·필수 여부·기본값·유효 범위·단위·좌표계·ID 출처 |
| 처리 규칙 | 주요 계산·정규화·필터·정렬·중복 제거 기준, 원본 응답과 달라지는 부분 |
| 반환 | 타입·필드 의미·단위·nullable 조건·성공 예시, 다음 함수로 전달할 값 |
| 실패와 경계값 | 빈 결과·입력 오류·타임아웃·인증 오류·누락 데이터, 반환 오류와 발생 예외 구분 |
| 부작용·의존성 | 외부 API 호출, DB 쓰기, 이벤트 발행, 캐시·시간 제한·재시도 여부 |
| 사용 예시·한계 | 최소 호출 예시, 결과 해석 방법, 설명만으로 오해할 수 있는 한계 |

해당하지 않는 항목은 ‘없음’으로 명시한다. 단순 계산 함수는 외부 호출·부작용이 없다는 사실과 계산식·단위를 짧게 설명하면 된다. 설명은 한국어로 작성하고 코드의 필드명·타입명은 그대로 사용한다.

Agent에 노출하는 함수에는 추가 규칙을 적용한다.

1. docstring의 사용 계약을 Tool의 `description`에 실제 등록한다. 코드 주석만 쓰고 모델에는 전달하지 않는 상태를 허용하지 않는다.
2. 입력 스키마의 각 필드에도 의미·단위·범위를 설명한다. Pydantic의 `Field(description=...)` 등을 사용하고 실제 직렬화된 Tool 스키마를 확인한다.
3. 비슷한 Tool의 역할 차이를 명시한다. 후보 검색은 ID 확보, 상세 조회는 확보한 ID의 상세정보, 보조 검색은 휴게소 정보 확보에 사용한다.
4. 구현·스키마·오류 정책을 바꾸면 설명과 예시도 함께 수정한다. 구현 설명과 LLM에 전달되는 계약이 서로 달라지지 않게 한다.

참고: [함수 설명 작성에 관한 공식 가이드](https://developers.openai.com/api/docs/guides/function-calling#best-practices-for-defining-functions). 이 원칙을 내부 함수와 GMS Tool 등록 모두에 적용한다.

### 설명 예시 — 후보 검색

아래는 구현한 후보 함수의 설명 계약 예시다. 실제 정규화 모델과 Tool 등록은 `fuelroute/tools.py`에 있다. 성공 데이터는 후보 목록을 `data.items`에 담는 공통 객체 형식을 사용한다.

```python
async def search_gas_stations(
    longitude: float,
    latitude: float,
    fuel_type: FuelType,
    radius_m: int = 5000,
) -> ToolResult[dict]:
    """경로상 한 검색점 주변의 주유소 후보를 Opinet에서 조회한다.

    호출 시점:
        기본 경로와 안전 주행거리를 확보한 뒤, 안전 구간 안에서 선택한
        검색점마다 호출한다. 출발지·목적지 이름만으로 호출할 수 없다.
        OPINET_API_KEY와 검증된 WGS84→KATEC 변환기가 필요하다.

    Args:
        longitude: WGS84 경도(도). 위도와 순서를 바꾸지 않는다.
        latitude: WGS84 위도(도). 검색점의 안전 구간 포함 여부는 호출자가 확인한다.
        fuel_type: 내부 유종 값 gasoline 또는 diesel. Opinet 코드로 변환한다.
        radius_m: 검색점 기준 반경(m), 1~5000, 기본 5000.

    처리 및 반환:
        좌표를 KATEC로 바꾸고 반경 검색 응답을 공통 후보 목록으로 정규화한다.
        성공하면 ok=True이며 data.items에 station_id, name, price_krw_per_l,
        longitude, latitude, center_distance_m을 반환한다.
        필수 식별자·위치를 해석할 수 없으면 INVALID_RESPONSE로 처리한다.
        확인되지 않은 가격은 null이다. center_distance_m은 검색점 기준
        거리이며 출발지에서 실제 운전한 거리나 도달 가능 판정이 아니다.

    실패:
        0건은 EMPTY_RESULT, 잘못된 인자는 INVALID_INPUT, 키 누락은
        CONFIG_ERROR, 인증 거절은 AUTH_FAILED로 ToolResult에 담는다.
        타임아웃·외부 응답 오류도 공통 오류로 변환하며 정상 결과로 위장하지 않는다.
        호출자는 error.retryable과 error.replan_allowed를 구분해 처리한다.

    부작용 및 다음 단계:
        외부 HTTP 요청이 발생한다. DB 저장과 자체 재계획은 하지 않는다.
        공통 실행 래퍼가 타임아웃·재시도 정책과 호출 이벤트를 담당한다.
        station_id를 get_station_details에, 후보 좌표를 경유 경로 조회에 전달한다.
        여러 검색점의 중복 후보 병합과 실제 도달 가능 판정은 상위 서비스 책임이다.

    Example:
        await search_gas_stations(127.0, 37.5, "gasoline", radius_m=3000)
        위 좌표는 입력 형식 예시이며 해당 지역의 실제 응답을 보장하지 않는다.
    """
    ...
```

## 14.2 성공·실패 공통 반환 형식

모든 외부 조회 Tool은 `ToolResult[T]`를 반환한다. 성공·실패 모두 `ok`, `tool_name`, `data`, `error`, `meta`를 유지한다. HTTP 성공 코드만으로 Tool 성공을 판정하지 않고 제공사 오류 코드와 필수 필드도 확인한다.

| 필드 | 계약 |
|---|---|
| `ok` | 성공이면 `true`, 실패이면 `false` |
| `tool_name` | 호출한 Tool의 고정 이름 |
| `data` | 성공 시 Tool별 타입의 데이터, 실패 시 `null` |
| `error` | 성공 시 `null`, 실패 시 `code`, `message`, `retryable`, `replan_allowed` |
| `meta` | `provider`, `request_id`, `elapsed_ms`, `fetched_at` |

`request_id`는 독립 실행에도 생성하는 호출 식별자이며 이벤트와 연결한다. `elapsed_ms`는 해당 호출에 걸린 시간, `fetched_at`은 호출 처리 완료 시각(실패 포함, 시간대가 있는 ISO 8601)이다. 데이터 자체의 가격·시설 기준시각은 `data` 안에 따로 둔다. `retryable`은 동일 요청을 일시적 장애 후 재시도할 수 있는지, `replan_allowed`는 입력·검색 지점을 바꾼 재계획이 유효한지를 뜻한다.

성공 예시의 값은 설명용 가상 데이터다.

```json
{
  "ok": true,
  "tool_name": "get_station_details",
  "data": {"station_id": "example-station", "name": "예시 주유소", "price_krw_per_l": 1700},
  "error": null,
  "meta": {"provider": "opinet", "request_id": "example-001", "elapsed_ms": 150, "fetched_at": "2026-11-18T10:00:00+09:00"}
}
```

```json
{
  "ok": false,
  "tool_name": "search_gas_stations",
  "data": null,
  "error": {"code": "EMPTY_RESULT", "message": "해당 검색점에서 후보를 찾지 못했습니다.", "retryable": false, "replan_allowed": true},
  "meta": {"provider": "opinet", "request_id": "example-002", "elapsed_ms": 140, "fetched_at": "2026-11-18T10:01:00+09:00"}
}
```

| 오류 코드 | 동일 요청 재시도 | 처리 |
|---|---|---|
| `INVALID_INPUT`, `CONFIG_ERROR`, `AUTH_FAILED` | 불가 | 입력·설정 수정 필요, 자동 재계획 금지 |
| `EMPTY_RESULT`, `NOT_FOUND`, `NO_ROUTE` | 불가 | 후보·검색점을 바꾸는 재계획 가능. 보조정보만 없으면 미확인으로 계속 |
| `TIMEOUT`, `RATE_LIMITED`, `UPSTREAM_ERROR` | 일시적 장애에 한해 가능 | 제공사 한도·대기 지침 및 재시도 상한 적용, 같은 호출 무한 반복 금지 |
| `INVALID_RESPONSE` | 기본 불가 | 파싱·필수 필드 오류 기록, 부족한 데이터로 판단하지 않음 |

휴게소 API 실패는 핵심 데이터가 충분한 경우 전체 실패로 전파하지 않는다. 예상 가능한 외부 오류는 위 계약으로 변환하고, 프로그래밍 오류까지 빈 성공 응답으로 덮지 않는다.

## 14.3 오늘 구현할 독립 TODO

| TODO / 함수 | 입력 | 성공 데이터·후속 사용 | 완료 기준 |
|---|---|---|---|
| 후보 조회 / `search_gas_stations` | 검색점 WGS84 좌표·유종·반경(m) | 주유소 ID·가격·위치 → 상세 조회·경유 경로 조회 | 정상·0건·입력/인증 오류를 독립 호출로 확인 |
| 상세 정보 / `get_station_details` | 후보 조회에서 얻은 Opinet 주유소 ID·유종 | 주소·유종별 가격·확인 가능한 상세정보 → 후보 평가·설명 | 실제 후보 ID 연결, 가격 누락·존재하지 않는 ID 처리 |
| 보조 정보 / `search_rest_area_info` | 후보/경로 좌표·선택 노선/방향·검색거리(m) | 휴게소 위치·시설·기준일·매칭 상태 → 결과 보조 설명 | 페이지 처리·검색 조건 적용, 미매칭·실패 후 추천 계속 정책 확인 |

오늘은 각 함수의 타입·스키마·설명·공통 응답을 정의하고 Python/CLI에서 개별 호출한다. 상세 조회는 앞서 확보한 ID를 입력 fixture로 전달할 수 있어야 하며, 함수 내부에서 후보 조회나 Agent 전체 실행을 다시 시작하지 않는다. 경로 조회도 별도 함수로 검증해 후보 검색의 입력을 공급한다.

각 TODO에 입력 예시, 호출 방법, 마스킹된 실제 성공 응답, 실패 응답, 다음 단계로 넘길 필드를 남긴다. 실패는 재현용 fixture로 확인할 수 있지만, fixture 검증을 실제 인증·호출 성공으로 기록하지 않는다. 개발 작업의 정의·완료 상태와 AI의 구현·검증 결과는 [`DEVELOPMENT.md`](DEVELOPMENT.md)에 기록한다. 상세 실호출 근거는 [검증 JSON](verification/2026-11-18.json)에 연결한다. 사용자가 앞으로 직접 해야 할 일은 TASKS, 사용자가 직접 실행했다고 확인한 일은 daily에서 관리한다.

---

# 15. 주요 결정과 변경 이유

2026-11-18 기준. 기존 별도 의사결정 문서의 핵심 내용을 이 표와 PRD로 통합했다. 이후 변경은 해당 설계와 이 표를 함께 수정한다.

| 기록 | 결정·상태 | 이유·후속 확인 |
|---|---|---|
| DEC-001 | FuelRoute 도메인 확정 | 명시적인 안전 제약·외부 데이터·재계획·저장이 연결됨. 대안과 기획 이유는 PRD 1.5 |
| DEC-002 | 미래 유가 예측 제외 | 현재 사실 데이터로 추천에 집중. 예측 모델·데이터 검증 전에는 재도입하지 않음 |
| DEC-003 | 연료량·평균 연비 직접 입력 | 차량 제조사·OBD 연동 범위 축소. 입력 기반 추정치임을 안내 |
| DEC-004 | 안전 잔량 하드 제약 | 연료가 거의 0이 되는 후보를 허용하지 않음. 추가 안전계수는 미정 |
| DEC-005 | 5km 제한은 검색점별 반경 | 도로 거리 기반 좌표 보간과 안전 구간 검색점 생성·실제 후보 연결 검증 완료 |
| DEC-006 | 최대 세 검색점으로 연결 실험 | 16곳 확보·3곳 평가, Opinet 6회/Kakao 7회 실측. 추가 확장·전체 경로 커버리지는 후속 |
| DEC-007 | 제약 필터 후 정렬 제안 | LLM 종합 판단·임의 가중합보다 판단 근거가 명확함. 정렬 순서는 실데이터 검증 후 확정 |
| DEC-008 | 전국휴게소정보표준데이터 선정·실호출 검증 | 215건 수집·반경/방향 필터 확인. 동일 주유소 매칭은 미확인 유지 |
| DEC-009 | GMS `gpt-5.4-mini` 현재 계정 검증 | 텍스트·구조화 출력·Tool 왕복 실제 성공, 다른 계정은 별도 검증 |
| DEC-010 | 상세 함수 설명·공통 응답·독립 호출 필수 | AI가 설명만으로 함수 계약을 이해하고 각 TODO를 따로 검증할 수 있도록 함 |
| CHG-001 | 최저가 중심 → 안전하게 도달 가능한 후보 중 추천 | 도달 불가·과도한 우회 방지. FR-003·006·007에 반영 |
| CHG-002 | 현재 위치 5km 중심 → 경로 기반 검색 | 장거리 이동 중 주유 지점을 결정하는 목적. FR-002·004·006에 반영 |
