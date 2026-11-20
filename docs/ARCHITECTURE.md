# ARCHITECTURE — FuelRoute Agent

> **상태:** 전체 Agent·계획 CRUD·대화/계획/로그 FastAPI 연결 완료 / 기능 UI는 F3018부터 진행
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
- `fuelroute/core/config.py`: `.env`보다 OS 환경변수를 우선하고 빈 값을 미설정으로 처리한다. 키는 `SecretStr`로 보관하며 `doctor`는 누락 변수명만 출력한다.
- `fuelroute/api/app.py`: `GET /health`, 대화 `POST /api/chat`, 계획 CRUD `/api/plans`, 실행 로그 조회·삭제 `/api/logs`를 등록한다.
- `fuelroute/core/contracts.py`: `ToolResult[T]`의 성공/실패 필드 불변조건과 완료 시각의 시간대를 검증한다.
- `fuelroute/calculations/driving_range.py`와 `schemas/driving.py`: 연료 입력의 단위·범위·유한성을 검증하고 안전거리를 계산한다. LLM용 Tool에도 설명과 입력 스키마를 등록했다.
- `fuelroute/cli/gms_probe.py`: GMS ChatOpenAI 텍스트·구조화 조건 추출·단일 로컬 Tool 왕복을 실제 계정에서 검증했다.
- `fuelroute/clients/{opinet,kakao_routes,rest_area}.py`: Opinet KATEC 반경/ID 상세, Kakao 기본/경유 상세경로, 휴게소 한 페이지를 독립 조회한다. 원본 응답과 공통 메타를 반환하고 키는 마스킹한다. 요청별 타임아웃은 기본 20초, 자동 재시도는 0회다.
- `fuelroute/tools/fuel_tools.py`와 `schemas/stations.py`: `search_gas_stations`, `get_station_details`, `search_rest_area_info`를 공통 응답·입력 설명과 함께 등록했다. WGS84↔KATEC 변환, 가격·시각 정규화, 휴게소 전체 페이지·반경/방향 필터를 실제 응답으로 확인했다. 휴게소와 동일 주유소임을 추측하지 않는다.
- 설치 버전은 `requirements.txt`, 개발 도구는 `requirements-dev.txt`, 전체 Windows/Python 3.11.9 환경은 `requirements.lock.txt`에 기록했다.

실제 요청 근거: [Opinet 반경 검색](https://www.opinet.co.kr/user/custapi/openApiInfoDtl.do?apiId=3), [Opinet ID 상세](https://www.opinet.co.kr/user/custapi/openApiInfoDtl.do?apiId=1), [Kakao 길찾기](https://developers.kakaomobility.com/en/guide/navi-api/directions), [휴게소 표준데이터](https://www.data.go.kr/data/15025446/standard.do). 2026-11-18 사용자 확인값인 계정별 호출 한도·휴게소 API 활용기간은 [TASKS 완료 기록](TASKS.md#completed-2026-11-18)과 [검증 기록](verification/2026-11-18.json)에 남겼다. 공개 이용조건·현재 적용 범위는 [API_USAGE](API_USAGE.md)에 기록했다. 개별 계약과 상용 재배포 권한 확보를 뜻하지 않는다.

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

### 구현 파일의 책임 경계

2026-11-19에 기존 기능을 유지하면서 `fuelroute/`를 책임별 패키지로 재구성했다. `agent/`는 실행 흐름, `calculations/`는 순수 계산, `clients/`는 제공사 통신, `tools/`는 정규화·등록, `schemas/`는 공유 계약, `core/`는 설정·오류·공통 결과, `api/`는 HTTP 진입점, `cli/`는 명령·진단을 맡는다.

하위 의존성은 `core → core`, `schemas → core·schemas`, `calculations → core·schemas·calculations`, `clients → core·schemas·clients`로 제한한다. 공유 모델이 필요해 계산에서 Agent 실행기를 가져오는 구조를 피한다. `schemas/execution.py`의 결과를 `calculations/constraints.py`에서 판정하므로 판정은 HTTP·LLM 없이 독립 실행된다. 상위 Agent와 Tool은 이 계약·계산·클라이언트를 조합한다. 검증용 GMS 왕복 실행기는 `cli/gms_probe.py`에 유지한다.

서버 진입점은 `fuelroute.api.app:app`이다. `python -m fuelroute` 명령·공개 JSON·일곱 Tool 설명/입출력 스키마와 API 호출 정책은 유지했다. [이동표·검증](DEVELOPMENT.md#package-layout)을 참고한다.

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
remaining_range_km
reserve_range_km
current_fuel_l?  # 기존 L 방식
avg_efficiency_kmpl?  # km 방식에서는 선택
reserve_fuel_l?  # 기존 L 방식
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

계획은 로그 삭제와 독립적으로 남는다. 세션 로그 삭제 시 이벤트는 함께 삭제하고 계획의 세션 참조는 `NULL`로 바꾼다. 시각은 시간대가 있는 ISO 8601 기준으로 처리하고 화면에서 KST로 표시한다. F3015에서 입력·추천·근거 스냅샷의 저장과 목록/상세 조회를, F3016에서 revision 기반 수정·삭제를 SQLite Repository로 연결했다. 핵심 추천 입력 변경은 저장 추천을 자동 재실행하지 않고 `revalidation_required`로 표시한다. 선택 일정 시각은 정렬·충돌 안내용이며 메모와 함께 추천 판단을 바꾸지 않는다. HTTP 연결은 F3017 범위다.

실행 가능한 설계는 [`storage.sql`](storage.sql)에 기록했다. `tests/test_storage_design.py`와 실제 조회 스냅샷을 넣은 메모리 SQLite 실험에서 이벤트 순서 중복·수정 금지, 로그 삭제 시 이벤트 제거·계획 보존을 확인했다. F3010의 conversation_memory, F3013의 실행 로그, F3015의 fuel_plans Repository를 연결했다. `fuel_plans`는 입력·추천 근거를 JSON 스냅샷으로 보관하고 사용자 `memo`를 최대 1000자의 일반 텍스트로 별도 저장한다. 기존 DB에 열이 없으면 연결 시 빈 메모 열을 추가한다. `planned_start_at/end_at`이 모두 있는 경우에만 반개구간으로 충돌을 판단하고, 시각이 부족하면 값을 만들지 않고 `not_comparable`로 구분한다. 충돌은 저장을 막지 않는다.

---

# 6. API 설계와 구현

`GET /health`, F3014의 `GET/DELETE /api/logs`, F3017의 Chat·계획 Endpoint와 위치 선택기의
`GET /api/locations/resolve`를 구현했다.

## GET `/api/locations/resolve`

- **기능:** 선택한 시·도/시·군·구 안의 동·주소·장소명을 Kakao Local 후보로 조회
- **입력:** `query` 1~200자. Frontend가 상위 지역과 상세 검색어를 공백으로 조합한다.
- **출력:** `LocationMatchSet`의 최대 5개 WGS84 후보. 복수 후보는 자동 확정하지 않는다.
- **오류:** 잘못된 입력 422, 0건 404, 인증·통신·제공사 실패 503

전국 시·도→시·군·구 목록은 Frontend 정적 selector가 제공한다. Kakao Local은 행정구역 트리
열거 API로 가정하지 않고 상세 동·주소·장소 검색에만 사용한다. 확정 후보의 경도·위도는 기존
Chat 문장에 포함하며 API 키·제공사 오류 원문은 브라우저로 전달하지 않는다.

## POST `/api/chat`

- **기능:** 사용자 요청을 Main Agent에 전달
- **관련 요구사항:** FR-001 ~ FR-009, FR-011, FR-012
- **Request 예시:**

```json
{
  "session_id": null,
  "message": "대구에서 서울 가는데 휘발유고 주행 가능180km야. 안전 여유30km는 남기고 싶어."
}
```

- **Response 핵심 필드 예시:**

```json
{
  "session_id": "string",
  "execution_id": "string",
  "status": "recommended",
  "answer": "string",
  "needs_user_input": false,
  "missing_fields": [],
  "event_count": 12,
  "log_delivery_failures": 0,
  "elapsed_ms": 15138.479,
  "analysis": {},
  "judgement": {},
  "replanning": {},
  "final_response": {}
}
```

실제 응답은 `ChatResponse`이며 분석·판정·재계획·최종 응답과 실행 로그 ID/건수, 전체
`elapsed_ms`를 typed 필드로 반환한다. 내부 Tool 원문·캐시·모델 추론은 포함하지 않는다.

## Plans

| Method | Endpoint | 기능 | 관련 요구사항 |
|---|---|---|---|
| POST | `/api/plans` | 계획 저장 | FR-010 |
| GET | `/api/plans` | 계획 목록 | FR-010 |
| GET | `/api/plans/{plan_id}` | 계획 상세 | FR-010 |
| PATCH | `/api/plans/{plan_id}` | 계획 수정 | FR-010 |
| DELETE | `/api/plans/{plan_id}` | 계획 삭제 | FR-010 |

계획 시간 충돌은 저장을 막지 않고 응답의 `conflicts`에 포함한다. 선택 출발·종료 시각은
계획 정렬과 일정 겹침 안내에만 쓰며 시각이 부족하면 `conflict_check=not_comparable`이다.
`memo`는 선택 일반 텍스트이고 추천 계산에 사용하지 않는다. PATCH는 예상 revision을 요구한다.

## Logs

| Method | Endpoint | 기능 | 관련 요구사항 |
|---|---|---|---|
| GET | `/api/logs` | 세션 로그 목록 | FR-011 |
| GET | `/api/logs/{session_id}` | 세션 로그 상세 | FR-011 |
| DELETE | `/api/logs/{session_id}` | 로그 삭제 | FR-011 |

**로그 수정 Endpoint는 만들지 않는다.** F3014 구현은 `api/logs.py`에서 목록 limit1~100,
한 SQLite snapshot의 상세, 삭제를 제공한다. 없는 ID는404, 잘못된 입력은422, 저장소 장애는
503이며 `{error:{code,message,request_id}}`로 반환한다. DELETE는 이벤트를 CASCADE하고
계획 행이 있으면 FK `ON DELETE SET NULL`로 실행 참조만 지운다. `/health`는 DB와 독립적이다.

## 화면 구성과 연결

| 화면/영역 | 주요 내용 | 연결 API |
|---|---|---|
| 주행 조건·대화 | 시·도/시·군·구/상세 후보 출발·목적지, 유종, 거리 조건, 추가 요청 | `/api/locations/resolve`, `/api/chat` |
| 추천 결과 | 가격·안전거리·우회시간·제외 사유·휴게소 보조정보 | `/api/chat` 응답 |
| 실행상태·이력 | 단계·Tool 성공/실패·재계획·로그 조회/삭제 | `/api/logs` 계열 |
| 계획 관리 | 저장·목록·상세·수정·삭제, 시간 충돌 안내 | `/api/plans` 계열 |

세 필수 조회 TODO의 개별 검증은 Python 함수 또는 CLI로 수행하며, 검증용 공개 디버그
Endpoint는 추가하지 않는다.

## 구현한 HTTP 입출력 계약

실제 조회 Tool과 저장 모델을 바탕으로 아래 계약을 구현했다. 상세 JSON schema는 `/docs`와
`/openapi.json`에서 확인한다.

| Endpoint | 입력 | 성공 응답·상태 |
|---|---|---|
| `GET /api/locations/resolve` | 상위 행정구역을 포함한 `query` | 200, 상세 위치 후보 최대 5개와 유일 결과 여부 |
| `POST /api/chat` | `message: string`, `session_id: string 또는 null` | 200, 대화/실행 ID, 분석·판정·재계획·최종 응답, 이벤트 건수·로그 저장 실패 수·전체 경과시간(ms) |
| `POST /api/plans` | 주행 입력 스냅샷·검증된 추천 스냅샷·`trip_date`·선택 계획 시작/종료시각·메모 | 201, `plan: FuelPlan`, `conflicts: [{plan_id, reason}]`; 충돌을 이유로 저장을 거절하지 않음 |
| `GET /api/plans` | 선택 `trip_date: YYYY-MM-DD` | 200, `{items: FuelPlan[]}` |
| `GET /api/plans/{plan_id}` | 저장한 계획 ID | 200, `FuelPlan` |
| `PATCH /api/plans/{plan_id}` | 변경할 필드만; 날짜·조건이 바뀌면 기존 추천은 재검증 필요 상태로 전환, 시각·메모만 바뀌면 기존 상태 유지 | 200, `{plan: FuelPlan, conflicts: []}` |
| `DELETE /api/plans/{plan_id}` | 저장한 계획 ID | 204, 본문 없음 |
| `GET /api/logs` | 선택 세션 상태 | 200, `{items: ExecutionSession[]}` |
| `GET /api/logs/{session_id}` | 세션 ID | 200, `{session: ExecutionSession, events: ExecutionEvent[]}`; sequence 오름차순 |
| `DELETE /api/logs/{session_id}` | 세션 ID | 204, 본문 없음; 저장한 계획은 유지 |

- `TripContext`의 좌표는 `{longitude: number, latitude: number, label?: string}`인 WGS84, 기본 범위 입력은 주행 가능/안전 여유(km)다. km 방식에 L/연비는 필수가 아니며 기존 L 방식만 별도로 허용한다. 필수값 누락은 임의 보충하지 않고 Chat의 `needs_user_input=true`/`missing_fields`로 안내한다.
- `FuelPlan`은 5장의 저장 구조를 따르며 추천 주유소는 `StationDetails`, 실제 도달·우회 거리(km)/시간(min), 추천 근거, 보조정보 `RestAreaInfo[]|null`을 담는다. HTTP 직전 도메인 계산에서 m/s→km/min 변환을 명시한다.
- `ExecutionEvent`는 11장의 세션/순서/Tool/요청ID/시각과 마스킹된 payload를 사용한다. 원본 인증 URL·헤더·내부 예외 trace는 응답에 포함하지 않는다.
- HTTP 오류는 `{error: {code, message, request_id}}`로 통일한다. 잘못된 입력은 422, 없는 계획/로그는 404, 일시 외부 장애로 핵심 판단 불가 시 503이다. 처리하지 못한 예외는 원문·타입·stack trace·요청 인증정보를 복사하지 않고 고정 `INTERNAL_ERROR` 500으로 변환한다. Chat 성공 응답의 `elapsed_ms`는 LLM·외부 Tool·계산·최종 응답을 포함한 서버 실측 전체 경과시간이며 SLA가 아니다. 후보 없음·추가 입력·보조정보 실패는 대화 상태로 표현하고 UI가 `warnings`와 근거를 표시한다.
- 화면 상태: 요청 중은 처리 중 표시, 필수값 누락은 입력 보완, 추천 없음은 근거 안내, 휴게소만 실패하면 보조정보 미확인 표시다. 로그 화면은 수정 버튼을 제공하지 않는다.
- 계획 시간 충돌은 시작/종료가 모두 있는 반개구간을 비교한다. 구체 시각이 부족하면
  `not_comparable`로 구분하며 날짜만으로 충돌을 만들거나 저장 실패를 반환하지 않는다.

F3017은 위 Backend 계약을 연결했다. Frontend의 실제 호출·상태 표시·브라우저 검증은
F3018~F3019에서 수행한다.

### F3018 대화·계획 대시보드 — 화면 확인 중

`frontend/dist`는 별도 빌드 없는 HTML/CSS/JavaScript다. 왼쪽 큰 카드에서 시·도→시·군·구와
선택 상세 후보, 유종, 주행 가능 거리, 안전 여유를 먼저 받고 추가 자연어 요청은 선택으로 둔다.
상세 후보는 상위 지역을 포함해 조회하며 선택 좌표를 Chat 요청 문장에 넣는다. 추천 후보를
보여주고, 사용자가 날짜·선택 시각·메모를 확인한 뒤 추천을 채택하면 Chat 응답의
검증된 좌표·조건·주유소·m/s 비용·근거를 `PlanCreate`의 km/min 스냅샷으로 바꿔
`POST /api/plans`에 저장한다. 추천이 없거나 필수 비용이 누락되면 저장 payload를 만들지 않는다.

저장 목록은 GET, 일정·메모 변경은 현재 revision의 PATCH, 삭제는 DELETE를 사용한다. 실제
변경 필드만 PATCH해 변경 없는 저장의 422를 프론트에서 예방한다. 날짜·조건 변경 후
`revalidation_required`를 별도 배지로 표시하고 시각·메모만 바꾸면 기존 추천 상태를 유지한다.
본문은 전체 너비 주유 플랜→안전거리/도달거리/우회시간 3열→주행 조건/추천 균형 순서다.
저장 플랜과 추천 근거는 상단 탭·사이드 메뉴에서 여는 네이티브 `dialog` 모달로 분리해 본문
카드 분할을 줄인다. session ID만 브라우저 localStorage에 보관하며
키·원본 Tool 응답·계획 데이터는 저장하지 않는다. 로컬 5173은 Backend 8000을 사용하고 운영
origin은 공개 `config.js`의 빈 `FUELROUTE_API_BASE` 한 곳에서 설정한다. 기능 UI 운영 배포는
공개 Backend 주소 설정과 사용자 화면 확인 뒤 진행한다.

### F3019 실행 과정·상태 모달

상단 탭과 사이드 메뉴의 실행 과정 버튼은 `/api/logs?limit=50` 목록과 선택
`/api/logs/{execution_id}` 상세를 네이티브 `dialog` 모달에 표시한다. Chat 요청 중에는 로컬
상태를 `Agent 실행 중`으로 표시하고, 응답의 `execution_id`를 받은 뒤 실제 저장 로그를 읽어
교체한다. 로그 전달 실패 수가 있으면 정상 응답과 부분 로그 실패를 구분한다.

상세 타임라인은 서버의 연속 `sequence` 순서를 그대로 사용한다. 단계 시작/완료, Tool
요청/성공/실패, 제약 판정, 재계획 실행/상한 종료, 최종 응답을 다른 표시로 구분한다. Tool
실패는 정제 오류 코드와 재시도/재계획 가능 여부만 보여주며 키·URL·입력값·원본 응답·내부
예외는 표시하지 않는다. 로그 삭제는 기존 `DELETE /api/logs/{execution_id}`를 호출하고 저장
계획은 유지한다. API가 streaming 실행 ID를 미리 주지 않으므로 실제 세부 이벤트는 요청 완료
후 표시하며, 실행 중에는 확정되지 않은 가상 단계를 만들지 않는다.

---

# 7. 핵심 비즈니스 로직

## 7.1 안전 주행 가능 거리 — 확정

```text
safe_range_km = remaining_range_km - reserve_range_km
# 예: 180km - 30km = 150km
```

검증:

```text
remaining_range_km > 0
0 <= reserve_range_km < remaining_range_km
```

두 값은 유한한 km이며 안전 여유에 임의 기본값을 넣지 않는다. km 입력에 연비는 필요 없고 L를 역산하지 않는다. 기존 L/연비는 `(현재L-안전L)*연비(km/L)`로 계산한다. km 범위와 L 연료량 혼용은 거절한다. km 방식의 선택 연비로 차감식을 바꾸지 않는다. 두 방식 모두 사용자 입력 기반 추정치이며 별도 안전계수는 추가하지 않는다.

---

## 7.2 접근 가능한 주유소 판별 — 구현 완료

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

저장된 실제 장거리 응답에서 80/100/130km 좌표를 추출했다. 실호출에서는 검색점 세 곳에서 후보 16곳을 확보했다. 추천 실행기의 추가 검색점은 10장의 유한한 재계획 정책을 따른다. [실험 기록](verification/2026-11-18-route-spike.json)을 참고한다.

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

이 계산은 사용자가 연비를 별도로 제공했을 때 검토하는 선택 설계다. 기본 km 입력에서 연비를 요구하거나 L 소비량·연료비를 역산하지 않는다. 현재 구현은 실제 추가 거리/시간과 조회 가격을 사용한다.

필요하면 다음 근사식을 사용할 수 있다.

```text
extra_fuel_l = detour_distance_km / avg_efficiency_kmpl
extra_fuel_cost = extra_fuel_l × reference_fuel_price
```

하지만 `reference_fuel_price`를 무엇으로 둘지 아직 확정하지 않았다.

따라서 MVP 추천 로직의 필수 계산으로 확정하지 않는다.

---

## 7.6 추천 방식 — 확정·구현 완료

`recommendation.py`의 `rank_candidates`가 상세정보·유종·가격·실제 접근 비용을 확인하고 안전거리와 사용자 우회시간/거리 상한을 먼저 적용한다. 입력의 과거 `eligible` 값만 신뢰하지 않고 현재 제약으로 다시 판정한다. 상한과 같은 값은 허용한다. 상세/비용/가격 누락·ID 불일치와 제공사 실패도 제외 사유로 보존한다.

적합 후보의 사전식 정렬 기준은 다음과 같다.

| 선택 | 우선순위 |
|---|---|
| `price` 기본값 | 가격 원/L → 추가 시간 초 → 추가 거리 m → 주유소 ID |
| `detour_time` | 추가 시간 초 → 추가 거리 m → 가격 원/L → 주유소 ID |

가중치·가상 주유량·총 주유비·추가 연료비를 생성하지 않는다. 주유 예산·원하는 주유 시점은 현재 수치 제약 입력에 포함하지 않는다. 정렬 결과에 `ranking_fields`, 제외 목록과 `scope=evaluated_candidates_only`를 담는다. 적합 후보가 없으면 `recommended=null`이다. 부분 평가한 집합에서의 추천이며 전체 경로 최저가를 보장하지 않는다.

기존 실제 비용의 로컬 재판정에서 가격 우선은 불정주유소, 시간 우선은 북상주IC주유소/충전소를 선택했고 5분 상한에서는 모두 제외했다. 새 추천 실호출도 별도로 검증했다. [추천 검증 기록](verification/2026-11-18-recommendation.json)을 따른다.

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

대구→서울 샘플은 총 292,235m / 14,525초였으며 `sections[].roads[].vertexes`를 확인했다. 교통과 요청 시점에 따라 결과는 달라진다. 거리(m)·시간(s)을 보존하며 경유 요청은 기본 요청과 같은 옵션으로 보낸다. N km 검색점 추출·실제 후보 우회 판정도 7장의 구현과 연결 실험에서 확인했다.

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

[Naver 공식 좌표계 정의](https://api-fin.ncloud-docs.com/docs/ai-naver-mapsreversegeocoding-gc)와 [TMAP의 WGS84↔KATECH 기준점](https://tmap-skopenapi.readme.io/reference/자동차-경로안내)을 대조했다. 서울·부산·대전 세 기준점의 최대 차이는 2.9405m로 검증 허용치 5m 이내다. 실제 Kakao 경로 좌표→변환→Opinet 후보/상세 주소로 지역 연결도 확인한다. 이 좌표계 변환은 pyproj가 처리한다. 2026-11-19 추가한 지명/주소→WGS84 위치 해석은 별도 Kakao Local 검색이며, 사용자가 카카오맵을 활성화한 뒤 실제 성공을 확인했다.

---

# 10. Agent 구조 설계안

## F3005~F3008 구현 상태 — 2026-11-19

첨부 `step3_agent.py`의 `State`·노드·`make_graph` 구조와 `step4_agent_gms.py`의 모델 함수 주입을 참고했다. `analyze` 명령의 추천 의도는 `START→analyze→resolve_locations→END`, 인사/설명 등은 분석 뒤 종료한다. F3006의 `plan` 명령은 위치 해석 뒤 `build_plan→END`를 연결한다. 비추천 의도는 위치 검색 없이 `build_plan`에서 계획 생략을 기록한다. 분석·계획·위치 검색 함수를 각각 주입할 수 있다. F3008 판정·F3009 재계획은 아래 구현 계약을 따른다. 조건 맥락은 아래 F3010을 따르며 최종 문장은 후속이다.

`ExtractedRequest`는 명시된 값과 원문 `quote`를 받고, `analyze_conditions`는 원문 존재·숫자와 단위·좌표 범위·상대 날짜/시각·유종/정렬 별칭을 검사한다. 원문과 대조할 수 없는 값은 `unverified_fields`, 사용자 범위 오류는 `invalid_fields`, 필수값 부재는 `missing_fields`로 반환한다. 기준·선호·제약은 `condition_groups`로 구분한다. 같은 숫자가 다른 의미로 사용된 경우 등 의미 분류의 정확성은 모델에 의존하므로 원문 대조가 모든 해석 오류를 보장하지는 않는다.

추천의 내부 필수 입력은 출발/목적 WGS84 좌표·유종·주행 가능 거리·안전 여유(km)다. `range_input_mode=distance`이면 연비를 요구하지 않는다. 명시적 L 연료량만 있으면 `fuel` 모드로 기존 세 값을 요구한다. 두 방식 혼용은 입력 보완 대상으로 둔다. 사용자에게 좌표 입력을 필수로 요구하지 않고, 지명/주소만 있으면 Kakao Local 주소 검색 후 0건일 때만 장소 검색으로 보완한다. 유일한 결과만 채우고 여러 후보는 상세 주소·장소명을 다시 묻는다. 유효한 입력 좌표와 입력 오류는 검색 결과로 덮어쓰지 않는다. `coordinate_sources`·`location_results`에 출처·후보·실패를 남기고 지역/도로 수준 결과는 `warnings`로 표시한다.

위치 검색은 위치당 최대 2회·요청당 최대 4회이며 동일 실행 내 동일 지명은 캐시한다. 빈 결과·잘못된 개별 지명 외의 제공사 장애/권한/한도 오류가 나면 다음 위치도 반복 조회하지 않는다. 좌표 검색 뒤 누락 목록·질문·계획 준비 상태를 다시 계산한다. L/리터·km/L·분/min·km 단위만 현재 추출 검사에서 채택하고 다른 단위는 확인을 요청한다. 날짜 미언급 시 날짜를 채우지 않으며 상대 날짜는 선택 기준일 또는 실행일 KST를 사용한다.

누락·잘못된 사용자 값은 성공 결과 안의 `needs_user_input`으로 안내하고, GMS 설정/인증/통신·모델 출력 형식 오류는 공통 실패로 반환한다. 위치 검색 실패는 해당 `location_results`의 공통 오류와 입력 보완 상태로 남긴다. `ready_for_planning`은 입력 준비 상태이며 시간 제약 충족이나 추천 성공 판정이 아니다. 단독 분석에는 맥락을 쓰지 않으며 F3010의 선택 세션 ID를 지정하면 아래 맥락 계약을 적용한다. 최종 문장·이벤트·로그 저장은 후속이다. 한 회차 조회는 아래 F3007 계약으로 연결했다. [분석 기록](DEVELOPMENT.md#f3005-implementation), [위치 해석 기록](DEVELOPMENT.md#f3005-geocoding)을 따른다.

### F3006 작업 계획 계약

`agent/planning.py`는 요청 분석 결과를 재검사하는 `prepare_plan`과 모델 제안을 검증하는 `finalize_plan`을 분리한다. 준비된 추천만 `agent.llm.llm_plan`으로 GMS를 1회 더 호출한다. 모델에는 검증 조건·기존 실행 기본값·허용 작업·실제 외부 조회 Tool의 설명/입력 스키마/다음 필드를 전달한다. 원본 위치 응답·인증정보는 전달하지 않는다. `calculate_safe_range`·제약 평가·정렬은 일반 코드 작업이며 모델의 외부 조회 Tool 목록에서는 제외한다.

| 필드 | 의미 |
|---|---|
| `State.plan.status` | `ready`: 작업 순서 준비, `needs_user_input`: 입력 보완, `not_required`: 추천 계획 불필요 |
| `request` | 기존 `PlanningRequest`로 검증한 좌표·유종·연료·우회 상한·정렬과 실행 기본값. 보류/생략이면 `null` |
| `steps` | 작업 ID·선택 이유·외부 조회/일반 코드·Tool 이름·선행 작업·입력 출처·실행 범위·실패 정책. 보류/생략이면 빈 목록 |
| `warnings` | 지역 수준 좌표 안내와 미판정 시각·미래 가격 예측 제외 등 해석상 한계 |

추천 계획의 작업은 기본 경로(`base_route`)·안전거리(`safe_range`) → 검색점 → 후보 → 상세 → 접근/경유 경로 → 제약 판정 → 정렬 → 추천 후보 휴게소 보조정보다. 독립적인 기본 경로/안전거리 순서는 바꿀 수 있으나 각 의존성은 먼저 충족해야 한다. 아홉 단계의 누락·중복·역순·허용되지 않은 작업/인자 추가는 `INVALID_RESPONSE`이며 재호출이나 임의 복구를 하지 않는다. 조건·Tool 이름·입력 출처·정렬·예산은 모델이 바꾸지 못한다. 입력 참조는 논리적 출처 설명이며 문자열을 코드로 평가하지 않는다.

상세 조회는 후보 ID를 사용하고 가격이 확인된 후보만 실제 경로를 평가하도록 계획한다. 후보 검색 0건은 재계획 판단 대상으로 남기고, 핵심 조회의 인증/설정/한도는 중단한다. 휴게소는 정렬한 추천 후보가 있을 때만 조회하며 실패하면 보조정보 미확인으로 남긴다. 이 단계는 실행 조건·정책을 보관하며 한 회차의 반복/호출은 F3007에서 연결했으며 재계획 반복은 F3009에서 연결한다. 안전 잔량·유종·사용자 상한은 완화하지 않는다.

`plan`의 GMS 상한은 분석 1회+계획 1회다. 미준비·비추천 의도는 계획 호출 0회이며 자동 Local 검색은 별도 최대 4회다. `plan` 명령은 경로·주유소·휴게소 Tool을 실행하지 않고 `run` 명령이 한 회차 실행한다. `graph_nodes`는 구성한 노드 목록이고 `analyzer_mode`/`planner_mode`는 의존성 선택이므로 실제 호출 이력으로 해석하지 않는다. 실행 이력은 후속 이벤트로 관리한다.

날짜·출발/도착 시각은 `analysis.conditions`에 유지한다. `plan.request`는 현재 추천 실행기 입력이므로 날짜·시각 적합성은 아직 계산하지 않는다. 기본 경로의 상세 좌표는 검색점 생성에 필요하지만 현재 등록 `get_driving_route`는 정규화 비용만 반환한다. F3007의 `ToolRegistry.route_context(include_path=True)`는 `probe_route` 한 번의 응답을 `DrivingRoute` 비용과 내부 `RoutePath`로 분리한다. 실행기는 이 경로로 검색점을 만들고 원본 경로를 모델/공개 결과에 전달하지 않는다. 작업 계획은 F3015의 저장할 최종 주유 계획과 구분한다. [구현·검증 기록](DEVELOPMENT.md#f3006-implementation), [실호출 근거](verification/2026-11-19-task-plan.json)를 따른다.

### F3007 조회 실행 계약

F3007 당시 그래프는 `START→analyze→resolve_locations→build_plan→execute_plan→END`였다. 아래는 독립 최초 회차 계약이며 현재 run의 판정·재계획은 F3008/F3009 절을 따른다. `execute_work_plan`은 요청을 재검증하고 아홉 작업의 ID·순서·의존성·고정 Tool/입력 출처/실패 정책이 F3006 계약과 같은지 HTTP 전에 확인한다. 문자열 참조를 코드로 실행하지 않으며 고정 핸들러만 사용한다. 입력 보완/비추천은 실행기 호출0회다. Kakao/Opinet 필수 설정도 첫 경로 호출 전에 검사하고 휴게소 키는 선택 의존성으로 취급한다.

| 결과 | 계약 |
|---|---|
| `State.results` / `data.results` | `WorkResults`: 계획 작업 ID별 경로 비용·안전거리·검색점·후보·상세·후보 경로·기존 판정/정렬·휴게소 출력 |
| `State.execution` / `data.execution` | `PlanExecution`: 처리 상태·단계·공통 호출 결과·미평가 ID·캐시 사용·경고·재계획 필요 여부 |
| `calls` | 공개 입력과 `ToolResult` 전체 계약. 인증/요청 URL/원본 경로는 제외하고 가격 기준시각·메타 완료 시각을 구분 |
| `steps` | 실제 단계 처리 상태. `completed`는 처리 완료이며 각 조회의 성공 여부는 `calls[].result.ok`로 확인 |
| `tool_call_counts` | 제공사별 논리 Tool 시도 수. 설정 사전 실패는 HTTP0회, 휴게소는 페이지 수만큼 HTTP가 발생할 수 있음 |

후보는 검색점 간 주유소 ID로 합치고 최초 조회 가격→ID 순 일부만 상세 조회한다. 평가 상한은 한 회차 `min(max_candidates,max_evaluations)`다. 상세 ID·유종과 출력 타입을 검사하고 가격 없는 후보의 경로를 생략한다. 동일 인자의 접근/경유 비용은 요청 내 캐시하며 반환 비용과 request_id를 후보별로 연결한다. 캐시·상세 경로는 실행 종료 시 폐기하고 다른 사용자/실행과 공유하지 않는다.

개별 상세/경로의 없음·타임아웃·잘못된 응답은 해당 후보의 제외 근거로 남기고 다른 후보를 진행한다. 기본 경로/검색의 치명적 실패와 핵심 인증/설정/한도/입력 오류는 중단한다. `execute_work_plan`은 `stopped/error`와 확보한 부분 결과를 보존한다. 외부 `execute_request`는 핵심 실패를 공통 `ok=false,data=null,error`로 전달하고 CLI는 종료1이다. 정상 처리 중의 개별 후보/보조 실패는 성공 데이터에 남긴다. 로깅 이벤트·저장은 아직 연결하지 않았다.

검색 전체0건/전체 제외는 `no_recommendation,replan_needed=true`이고 자동 반복은 없다. 기존 안전/우회 판정과 정렬 함수를 재사용하며 전체 경로 최저가나 도착시각 충족을 주장하지 않는다. 추천 후보가 있을 때만 휴게소를 조회하고 보조 실패는 경고로 표시한다. `match_status=unverified`, 미확인 시설 `null`, 데이터 기준일을 보존한다. 판단 노드/시각 제약·그래프 재계획·Memory·최종 문장은 후속 F3008~F3011 범위다. [F3007 기록](DEVELOPMENT.md#f3007-implementation), [실호출 근거](verification/2026-11-19-tool-execution.json), [호출 상한](API_USAGE.md)을 따른다.

### F3008 판정 계약

`run`은 실행 뒤 `judge→enrich`를 추가한다. `calculations/constraints.py`의 `judge_execution`은 분석/계획의 위치·유종·주행 조건과 실제 기본 경로의 출발/목적을 대조하고 비용을 재계산한다. 과거 `eligible`·안전거리·우회 캐시를 신뢰하지 않는다. 상세 가격/유종·독립 접근과 경유 첫 구간 중 큰 거리·동일 기본/경유 차이를 확인한다. 비용 미확인은0으로 보완하지 않고 불충족과 구분한다.

`TripJudgement`는 후보별 `ConstraintCheck`의 비교값·단위·출처와 호출 ID, 여유km·선택 연비가 있는 경우의 추정L·예상 도착을 반환한다. 상한과 같으면 통과하며 음수 우회는 원 값 보존 후 상한 비교에0을 쓴다. 실패/미확인 후보는 확정 추천에서 제외하고 평가한 집합 안에서 사용자 가격/시간 우선을 유지한다. `apply_judgement`는 결과 복사본의 안전거리·평가·정렬을 갱신한다.

선택 시간 입력은 `trip_date`(출발/계획 날짜), `arrival_date`(명시 도착 날짜), `departure_time`, `arrival_deadline`, `refuel_stop_min`이다. 도착 상한이 없으면 시간 제약을 만들지 않는다. 도착 날짜만 있으면 시각을 묻고23:59로 채우지 않는다. 날짜 없는 정상 순서의 두 시각은 `same_day_clock`, 날짜 둘이 명시되면 KST datetime 차이인 `explicit_dates`로 비교한다. 역순 시각은 익일을 추측하지 않고 날짜를 요구한다. 주행 예상초+명시 정차초를 예산과 비교하고 정차시간 미언급은0으로 대신하지 않는다.

기본 입력 준비는 `analysis.ready_for_planning`, 최종 제약 상태는 `judgement.status`다. 상태는 `recommended/no_recommendation/needs_user_input/unavailable/not_required`이며 입력 보완 질문은 `missing_fields/questions`에 있다. ETA는 주행 응답과 정차 예상값에 따른 추정치이고 미래 교통을 보장하지 않는다. 시간 판정 자체는 HTTP/LLM0회다.

기본 그래프 실행기는 보조 조회를 미룬 뒤 확정 판정의 후보만 `enrich_rest_area`에 넘긴다. 다른 후보의 보조정보는 지우고 같은 ID의 과거 보조 호출은 성공/실패 모두 재사용한다. 직접 `execute_work_plan`의 기본값은 기존 한 회차 계약을 유지하며 판정 그래프는 `PlanExecutionSession(defer_rest_info=True)`를 사용한다. 시간 제약으로 후보가 모두 제외되면 보조 HTTP0회다. 그래프 재계획은 아래 F3009에서 연결했으며 조건 맥락은 F3010에서 연결했고 최종 문장·이벤트/로그 저장은 후속이다. [F3008 기록](DEVELOPMENT.md#f3008-judgement), [추출·재판정 근거](verification/2026-11-19-constraint-judgement.json)를 따른다.

### F3009 그래프 재계획 계약

생산 `run`은 `execute_plan→judge→replan` 이후 추가 회차가 있으면 `replan→judge`로 돌아가고, 종료하면 `enrich→END`로 간다. `agent/execution.py`의 `PlanExecutionSession`은 요청당 하나 생성하며 최초 `start` 한 번과 추가 `replan` 한 번씩을 제공한다. 최초 실행을 중첩 호출하거나 기존 `recommend_route`의 반복기를 그래프 안에 다시 넣지 않는다. GMS는 분석/계획 최대2회 그대로다.

`agent/replanning.py`의 `replan_stop_reason`은 비추천 의도·필수 입력/시간 보완·핵심 실패·추천 발견·전체 평가 예산·추가 회차 상한을 먼저 확인한다. `decide_replan`은 아직 평가하지 않은 실제 후보 ID를 우선하고, 없으면 기존 상세 경로로 만든 안전 구간의 미사용 앞쪽 점을 한 곳 고른다. 검색은0건만 새 점 대상으로 삼고 개별 상세/경로 실패는 다른 후보로 진행한다. 같은 실패 요청을 재시도하거나 입력·안전/우회/시간 제약을 바꾸지 않는다.

최초 회차0·추가1~2회, 기본 회차3곳/전체9곳이며 실패한 평가도 예산을 쓴다. 추가 회차에는 남은 평가 예산만 선택한다. 기본 경로·안전거리·기존 후보/상세/경로는 유지하고, 동일 좌표/유종/반경 검색과 같은 경로 인자의 성공/실패는 캐시한다. 기하/캐시는 요청 종료 시 폐기하며 공개 상태에 원본 경로를 넣지 않는다. 매 회차 실제 비용으로 F3008을 재판정하고 판정 코드/경고를 제공사 오류나 중복 문장으로 누적하지 않는다.

| 출력 | 의미 |
|---|---|
| `data.replanning.replan_count` | 완료한 추가 회차0~2, 최초 회차 제외 |
| `rounds` | 회차별 트리거·새 검색점·신규 평가ID·적합 후보 수·논리 호출 증가량 |
| `stop_reason` | recommendation_found / needs_user_input / not_required / core_error / evaluation_budget_exhausted / replan_limit_reached / search_space_exhausted / judgement_unavailable |
| `next_input` | 입력 보완 또는 상한·검색 범위 소진 시 확인할 내용 |
| `execution.calls/steps[].round_index` | 최초0/추가1~2회차의 호출·단계 연결 |

종료한 `execution.replan_needed`는false이고 `judgement.replan_needed`는 조건상 대안 탐색 필요 여부다. 정상적인 후보 미발견은 공통 성공의 `no_recommendation`이며 전체 경로에 후보가 없다는 뜻이 아니다. 핵심 실패의 공개 응답은 기존처럼 `ok=false/data=null/error`이고 원 그래프 State에는 부분 결과·종료 근거를 보존한다. 입력 보완/인사는 실제 실행 회차가 없어 rounds가 비어 있다.

`execute_request`의 모델/실행기 주입을 유지한다. 사용자 주입 executor만 있으면 임의 재계획은 비활성화하며 같은 소유권의 replanner를 함께 명시해야 반복한다. 생산 경로는 내부 Session.start/replan을 연결한다. 그래프 자체도 주입 콜백 전에 횟수/평가 상한을 검사하고 누적 결과·진행 없는 출력을 거절한다. 보조 조회는 마지막 확정 후보에만 최대1회이며 실패가 추천을 지우지 않는다.

F3011을 포함한 기본 전체 호출 상한은 GMS3·Local4·Opinet14·Mobility19·휴게소1논리조회/최대10HTTP다. GMS는 분석1+계획1+최종 문단1이며 재계획에는 추가되지 않는다. [검증 기록](DEVELOPMENT.md#f3009-replanning)과 [실제 상한 종료](verification/2026-11-19-graph-replanning.json)를 따른다. 조건 맥락은 F3010, 최종 문장은 아래 F3011 계약을 따르며 이벤트/로그 저장은 후속이다.

### F3010 세션 맥락 계약

`analyze_request`/`plan_request`/`execute_request`의 선택 `session_id`는 영문·숫자·_·- 최대64자다. 같은 ID만 저장본을 읽으며 ID가 없으면 기존 단독 요청이고 DB를 만들지 않는다. 기본 파일은 `Settings.database_path`=`data/fuelroute.sqlite3`, 선택 환경변수는DATABASE_PATH다. 파일 기반 SQLite를 쓰며 :memory:는 거절한다. ID와 DB 경로는 별개로 취급하고 SQL은 파라미터 바인딩을 쓴다.

`State.session_id/memory`는 현재 ID·읽은 버전·직전 요청/응답 요약을, `analysis.conditions`는 통합 조건을 담는다. GMS에는 검증 조건·보완 대상·직전 요약을 참고 데이터 JSON으로 전달한다. 현재 원문에 실제 있는 값/단위/quote만 추출하고 과거 값을 새 Mention으로 복사하지 않는다. `agent/memory.py`가 미언급 값만 이어받고 변경한 값의 무효/미확인은 기존 정상값으로 숨기지 않는다. 병합 뒤 필수값·숫자·여유 관계·단위 방식을 재검사한다.

새 지명은 이전 좌표/출처를 비우고 Local로 다시 해석한다. 이미 확인한 지명이 유지되면 좌표·지역 수준 안내를 재사용한다. 새 직접 좌표는 이전 지명을 지운다. km와L량을 바꾸면 반대 방식의 연료량만 비우고 역산하지 않는다. 원문에 명시된 새 여행/초기화와 조건 제거만 반영한다. 도착 상한을 없애면 도착 날짜도 비워 날짜만 남아 같은 제약을 재요구하지 않게 한다. 인사/설명은 추천 없이 기존 여행을 보존한다.

`schemas/memory.py`의 ConversationMemory는 ID/revision/검증 조건/오류·보완 필드/좌표 출처·지역 안내/직전 요약/UTC 갱신 시각을 가진다. 요청 요약은 변경한 필드명 중심 최대1000자, 응답 요약은 상태·추천ID·제외 코드·보완 대상 중심 최대2000자다. 전체 발언·Tool 응답·실행 캐시·경로·가격·시설 원문·인증은 보관하지 않는다. 메모리 입력/저장 전 설정된 비밀값을 정제하며 원본 가격과 경로는 후속 요청에서 다시 조회한다. 요약용 LLM은 호출하지 않는다.

`storage/conversations.py`는 대화 테이블만 만들고 동기 SQLite I/O는 작업 스레드에서 처리한다. 연결은 매 작업 후 닫는다. 대화/로그/계획은 별도 테이블이며 맥락 테이블은 로그/계획 CRUD 완료를 뜻하지 않는다. save는 예상 revision과 같은 행만 갱신하는 CAS다. 그래프가 완료하면 새 revision을 저장하고 data.session에ID/버전/상속 필드/저장 여부/최신 요약을 반환한다. `analysis.field_sources`는 current(이번 원문/위치 해석) 또는 memory(이전 검증 조건)다.

손상/파일 오류는MEMORY_ERROR, 동시 변경은MEMORY_CONFLICT다. 모르는 필드/다른ID/버전 불일치는 모델 호출 전에 거절하며 자동 덮어쓰기·재시도는 없다. 분석/계획 모델 실패는 저장본을 유지하고 그래프가 종료한 핵심 Tool 실패는 확인한 입력과 짧은 오류 코드를 저장한다. 이때 외부 응답의 기존 공통error/data=null 계약은 유지한다. 이는 로컬 세션 분리이며 HTTP 사용자 인증은 후속 서버 범위다.

실제 GMS4회로 같은 세션3턴의 부분 변경과 다른 세션 분리를 확인했고 데이터 API는0회였다. 두 턴의 전체 추천/가격 재조회·모든 분기는 모의 전송으로 구분했다. [개발 기록](DEVELOPMENT.md#f3010-memory), [실제 분석·SQLite 근거](verification/2026-11-19-conversation-memory.json)를 따른다.

### F3011 최종 응답 계약

`agent/response.py`는 마지막 `ResponseContext`에서 가격·도달·안전거리·우회 비용·정렬·주요 제외·휴게소 보조정보·평가 범위를 한국어 `ResponseFact`로 만든다. 추천/미발견은 `judge_execution`으로 원본 정규화 비용을 다시 판정해 State와 불일치하면 `INVALID_RESPONSE`로 거절한다. 가격 기준시각과 Tool 처리 완료시각을 구분하고, 시간대 없는 시각은 KST로 추측하지 않는다. 휴게소 시설의 `None`은 미확인, 동일 시설/방향은 `unverified`, 위치 거리는 도로 거리가 아닌 직선거리로 표시한다.

`agent.llm.llm_response_outline`에는 사실 ID·분류·정제된 본문만 전달한다. GMS는 모든 ID를 정확히 한 번 포함하는 문단 구조만 반환하고 자유 본문·숫자·추천을 만들지 않는다. `render_response`가 누락·중복·새 ID와 결과 문장 선두 여부를 검사하고 코드 생성 문장만 연결한다. 추천/미발견의 설명 모델 오류는 동일 사실의 `fallback`으로 복구하며 입력 보완·인사·핵심 오류는 최종 모델을 호출하지 않는다. 프로그래밍 오류와 선행 판정 모순은 fallback으로 숨기지 않는다.

그래프는 `...→judge/replan→enrich→respond→END`로 끝난다. `State.answer/final_response`와 외부 결과의 `data.answer/final_response`에 답변·근거·질문·주의·추천 ID·평가 범위·출처 request ID·생성 모드를 반환한다. `run --text`는 답변만 표시하고 기본 JSON 계약은 유지한다. 전체 GMS 상한은 분석1+준비된 계획1+최종 문단1이며 재계획·코드 판정·템플릿 응답은 모델을 추가 호출하지 않는다. [개발 기록](DEVELOPMENT.md#f3011-response), [실호출 기록](verification/2026-11-19-final-response.json)을 따른다.

## 전체 Agent 연결 설계

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

## 재계획 실행 정책 — 확정·서비스 구현 완료

`agent/replanning.py`의 `recommend_route`는 명시적 조건을 받는 실행기다. 아래는 독립 CLI의 정책이다. F3009 그래프는 위 Session/판정 계약으로 같은 예산·검색점 정책을 적용한다. 선택 대화 맥락은 F3010을 따르고 최종 문장은 후속이다.

- 재계획은 최대 2회, 최초 실행 포함 최대 3회차다. `max_replans=0`이면 최초 회차만 실행한다.
- 검색 결과가 없거나 평가한 후보가 전부 제외되면 이미 조회한 미평가 후보를 먼저 사용한다. 미평가 후보가 없을 때만 안전 구간의 앞쪽 검색점을 회차당 한 곳 추가한다. 기본 139/119/94km 이후 추가 지점은 69/44km다.
- 기본 회차당 최대 3곳, 전체 최대 9곳을 평가한다. 설정 허용 범위는 회차당 1~10곳, 전체 1~30곳이다. 적합 후보를 찾으면 즉시 종료한다.
- 기존 안전 잔량·유종·우회 상한을 완화하지 않는다. 가격/시간 정렬은 평가한 후보 집합에 적용한다. 후보 평가 순서는 검색 가격→ID이며 시간 우선 옵션도 전체 후보의 최단 우회를 보장하지 않는다.
- 동일 실행에서 기본·접근·경유 경로, 상세, 검색 결과를 요청별 캐시하고 주유소 ID를 중복 제거한다. 호출 종료 시 캐시를 폐기한다. DB나 사용자 사이에 결과를 공유하지 않는다.
- 인증·설정·호출 제한·잘못된 입력은 즉시 중단한다. 검색 오류는 `EMPTY_RESULT`만 검색점 변경 대상으로 삼고, 개별 후보 상세/경로 실패는 제외 사유로 남긴다. 동일 실패 요청의 자동 재시도는 없다.
- 기본 상한은 Opinet 14회(검색 5+상세 9), Kakao 19회(기본 1+접근/경유 18), GMS 0회다. 실제 호출만 기록하고 캐시 사용 수를 별도로 반환한다.

종료 사유는 `recommendation_found`, `evaluation_budget_exhausted`, `search_space_exhausted`, `replan_limit_reached`로 구분한다. 적합 후보가 없으면 `status=no_recommendation`과 부분 평가·제외 근거를 반환한다. 상한 안에서 후보를 찾지 못했다는 뜻이며 주유소가 전체 경로에 없다는 단정은 하지 않는다. 기존 `route-spike`는 재계획 없는 기술 검증 명령으로 별도 유지한다.

독립 실행기의 재계획·상한·캐시·인증/한도 중단은 모의 검증했고 실제 추천은 최초 회차 성공을 확인했다. 이후 F3009 그래프에서는 실제2회/9곳 상한 종료도 확인했다. [검증 기록](verification/2026-11-18-recommendation.json)과 [호출 예산](API_USAGE.md)을 따른다.

---

# 11. 이벤트 및 로깅

F3012에서 실제 LangGraph와 Tool 호출 이력에 연결한 이벤트:

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

`agent/events.py`의 `EventBus`는 발행자가 구독자를 모르는 인프로세스 발행/구독 경계다. `ExecutionEventStream`은 요청마다 별도 영숫자 실행 ID와1부터 증가하는 sequence를 만들고 LangGraph 노드를 관찰한다. 구독자가 없으면 발행만 마치며, 구독자 예외는 `delivery_failures`에 더하고 다음 구독자/메인 실행을 계속한다. 별도 큐·재시도·전역 싱글턴은 없다.

`agent/logging.py`의 `LoggingAgent`는 EventBus 구독자다. 이벤트를 설정 비밀값으로 다시 정제·재검증한 후 `storage/logs.py`의 Repository에 전달하고 LLM으로 요약하지 않는다. Repository는 작업 스레드의 짧은 SQLite 연결과 `BEGIN IMMEDIATE`를 사용한다. 첫 sequence1에서 실행 세션을 만들고 기존 이벤트 수+1만 append하며 마지막 execution 완료 이벤트로 상태/종료시각을 갱신한다. 이벤트 행 UPDATE는 trigger가 거절한다. 저장 오류는 `LOG_STORE_ERROR`지만 EventBus 경계에서 메인 실행과 격리된다. 저장소 읽기 primitive는 F3013 검증용이며 HTTP 목록·상세·삭제는 F3014 서비스가 담당한다.

**수집·저장에는 LLM을 사용하지 않는다.**

이벤트에는 `event_id`, `execution_id`, `sequence`, `event_type`, `step`, `tool_name?`, `request_id?`, `elapsed_ms?`, `payload`, `created_at`을 포함한다. `payload`에는 상태·결과 개수·오류 코드·판정/재계획의 공개 결과와 입력 필드명만 남긴다. 사용자 원문·좌표/가격값·경로 기하·키/URL/헤더·LLM 내부 추론은 수집하지 않는다. 누적 실행 결과의 같은 request ID는 재발행하지 않으며 `TOOL_REQUESTED` 뒤 `TOOL_SUCCEEDED` 또는 `TOOL_FAILED`로 연결한다. `data.event_stream`은 실행 ID·발행 건수·구독 실패 수를 반환하고 실제 이벤트 본문은 응답에 복제하지 않는다.

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
| 계기판 주행 가능 거리 추정 오차 | 도달거리 과신 | Medium | 사용자 지정 안전 여유(km)와 추정치 안내 |
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

아래는 구현한 후보 함수의 설명 계약 예시다. 실제 정규화 모델과 Tool 등록은 `fuelroute/tools/fuel_tools.py`와 `schemas/stations.py`에 있다. 성공 데이터는 후보 목록을 `data.items`에 담는 공통 객체 형식을 사용한다.

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

각 TODO에 입력 예시, 호출 방법, 마스킹된 실제 성공 응답, 실패 응답, 다음 단계로 넘길 필드를 남긴다. 실패는 재현용 fixture로 확인할 수 있지만, fixture 검증을 실제 인증·호출 성공으로 기록하지 않는다. 기존 개발 TASK 이력과 AI의 상세 구현·검증 결과는 [`DEVELOPMENT.md`](DEVELOPMENT.md)에 기록한다. 상세 실호출 근거는 [검증 JSON](verification/2026-11-18.json)에 연결한다. 날짜별 할 일·완료 체크·사용자가 직접 확인한 값은 [TASKS](TASKS.md)에서 함께 관리한다.

---

## 14.4 최종 Tool 레지스트리 — 구현 완료

`ToolRegistry`는 안전거리 계산·기본/접근/경유 경로·후보·상세·보조정보·비용 평가·추천 정렬의 일곱 Tool을 등록한다. Python 원 함수와 등록 Tool의 반환을 구분하고 공통 JSON 응답·입력/출력 검증·실패 코드·다음 단계 필드를 명시한다. [실행 계약](TOOL_CONTRACTS.md), [전체 JSON schema](TOOL_CONTRACTS.json)를 따른다. `python -m fuelroute tool-schemas`로 HTTP 없이 같은 명세를 출력한다.

PDF p.9·14 대조에 따라 모델에 노출할 조회 Tool과 실행 흐름이 직접 처리할 계산·저장을 구분한다. 등록된 안전거리·비용 평가·정렬은 검증용 순수 계산 함수이며 외부 API/로컬 서버 API Tool과 같은 분류로 취급하지 않는다. 전체 Agent 연결 시 필요한 외부 조회만 모델에 노출하고 계산·판정·정렬·계획 저장·로그 수집은 코드에서 직접 수행하는 작업을 TASKS에 배정했다. 기존 등록 스키마를 바꾸거나 Agent 연결이 이미 완료됐다고 기록하지 않는다.

경로 Tool은 `DrivingRoute`의 거리(m)·시간(초)·구간 비용만 반환한다. 구간 거리 합과 전체 거리의 일치를 검증하고 원본 상세 좌표는 모델에 전달하지 않는다. `FuelTools.registered_tools`는 기존 세 조회 Tool만 제공하는 호환 진입점으로 유지한다. 전체 Agent의 Tool 선택과 결과 설명 연결은 후속 단계다.

# 15. 주요 결정과 변경 이유

2026-11-18 기준. 기존 별도 의사결정 문서의 핵심 내용을 이 표와 PRD로 통합했다. 이후 변경은 해당 설계와 이 표를 함께 수정한다.

| 기록 | 결정·상태 | 이유·후속 확인 |
|---|---|---|
| DEC-001 | FuelRoute 도메인 확정 | 명시적인 안전 제약·외부 데이터·재계획·저장이 연결됨. 대안과 기획 이유는 PRD 1.5 |
| DEC-002 | 미래 유가 예측 제외 | 현재 사실 데이터로 추천에 집중. 예측 모델·데이터 검증 전에는 재도입하지 않음 |
| DEC-003 | 계기판 주행 가능 거리·안전 여유(km) 직접 입력으로 변경 — 2026-11-19 | 사용자 요청. L/연비 없이 계산하며 기존 L 입력은 호환 지원. 차량 자동 수집 없음 |
| DEC-004 | 안전 여유 하드 제약 | 주행 가능-안전 여유(km) 범위를 넘는 후보 제외. 기존 L 방식도 안전 잔량 유지 |
| DEC-005 | 5km 제한은 검색점별 반경 | 도로 거리 기반 좌표 보간과 안전 구간 검색점 생성·실제 후보 연결 검증 완료 |
| DEC-006 | 최초 최대 세 검색점, 재계획 최대 두 점 추가 | 기존 후보를 먼저 재사용. 기본 최대 Opinet 14회/Kakao 19회이며 전체 경로 커버리지는 보장하지 않음 |
| DEC-007 | 제약 필터 후 가격 우선, 선택 시간 우선 정렬 확정 | 임의 가중치 없이 사전식 비교. 실제 비용의 로컬 재판정과 추천 실호출 완료 |
| DEC-008 | 전국휴게소정보표준데이터 선정·실호출 검증 | 215건 수집·반경/방향 필터 확인. 동일 주유소 매칭은 미확인 유지 |
| DEC-009 | GMS `gpt-5.4-mini` 현재 계정 검증 | 텍스트·구조화 출력·Tool 왕복 실제 성공, 다른 계정은 별도 검증 |
| DEC-010 | 상세 함수 설명·공통 응답·독립 호출 필수 | AI가 설명만으로 함수 계약을 이해하고 각 TODO를 따로 검증할 수 있도록 함 |
| CHG-001 | 최저가 중심 → 안전하게 도달 가능한 후보 중 추천 | 도달 불가·과도한 우회 방지. FR-003·006·007에 반영 |
| CHG-002 | 현재 위치 5km 중심 → 경로 기반 검색 | 장거리 이동 중 주유 지점을 결정하는 목적. FR-002·004·006에 반영 |
