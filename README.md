# FuelRoute Agent

> **현재 상태:** GMS·외부 API 3종 검증, 추천 정책·유한한 재계획·일곱 Tool 계약 구현 완료<br>
> **구현 상태:** F3005~F3017의 전체 추천 흐름, 계획 CRUD, 대화·계획·로그 FastAPI 연결 완료. 기능 UI는 F3018부터 진행

## 단계별 진행 기록

2026-11-18~19에 완료한 작업을 단계별로 묶어 커밋했다. 아래는 정리한 커밋 순서이며, 테스트 수는 각 단계의 파일을 별도 폴더에서 확인한 결과다.

| 단계 | 완료 내용 | 검증·결과 | 커밋 |
|---|---|---|---|
| 1. 기획·개발 규칙 | 요구사항·설계·상세 함수 설명 규칙, 개인 할 일과 개발 기록 분리 | 현재 기획·설계 문서 기록 | `af1d312` |
| 2. 개발 환경·기반 서버 | 의존성·키 템플릿, FastAPI, 공통 응답, 안전거리 계산, SQLite 저장 설계 | 헬스·설정·144km 계산 확인, 저장 설계 테스트 1개 통과 | `e3a9201` |
| 3. 프론트·배포 설정 | 서비스 소개 HTML/CSS, Netlify 설정, 기존 배포 이력 | 정적 파일·대표 URL·배포 설정 확인 | `5f4c195` |
| 4. GMS·외부 API 연동 | GMS 검증, Opinet·Kakao·휴게소 조회, 좌표 변환, 공통 오류 처리 | 모의 테스트 45개 통과, 기존 실호출 기록 보존 | `609bd11` |
| 5. 경로 기반 후보 평가 | 검색점 보간, 안전 구간 후보 검색, 실제 도달·우회비용 판정, CLI | 테스트 84개 통과. 기존 실호출에서 후보 16곳 중 3곳 평가 | `1a6a8e9` |
| 6. 추천·재계획·Tool 계약 | 제약 우선 정렬, 최대 2회 재계획·캐시, 일곱 Tool 등록·스키마 출력 | 테스트 110개·Ruff 통과, 추천 실호출 성공. [검증 기록](docs/verification/2026-11-18-recommendation.json) | `7ad21e9` |
| 7. F3005 요청 분석 | GMS 구조화 추출, 원문·단위·누락 검사, 분석 전용 LangGraph·CLI | 테스트 141개·Ruff 통과, 기존 GMS 실호출 4회. [분석 기록](docs/DEVELOPMENT.md#f3005-implementation) | `e90586c` |
| 8. 지명·주소 자동 좌표 변환 | Kakao Local 조회, 유일한 결과 선택, 모호성·지역 수준 안내, 입력 좌표 보존 | 테스트 164개·Ruff 통과, 기존 GMS 1회·Local 5회 검증. [변환 기록](docs/DEVELOPMENT.md#f3005-geocoding) | `3fe9a09` |
| 9. F3006 작업 계획 수립 | GMS 계획 제안·의존 순서 검증, 계획 노드·CLI, 입력 부족/비추천 시 계획 생략 | 테스트 189개·Ruff 통과, 기존 GMS 검증 근거 보존. [계획 기록](docs/DEVELOPMENT.md#f3006-implementation) | `ba57c23` |
| 10. km 기본 입력 전환 | 계기판 주행 가능 거리·안전 여유, 연비 없는 계산, 분석·계획·기존 실행기·Tool·CLI 연결 | 테스트 215개·Ruff·의존성 검사 통과, 기존 GMS 3회 검증. [km 전환 기록](docs/DEVELOPMENT.md#km-input) | `feef6ba` |
| 11. F3007 조회 실행기 | 계획 순서·공통 결과 통합, 개별 실패 격리, 내부 경로·캐시·보조정보 처리 | 테스트247개·Ruff 통과 | `ac88022` |
| 12. F3007 Agent·CLI 연결 | 분석→계획→실행 노드와 `run` 명령, 지명 해석·입력 보완·핵심 실패 연결 | 테스트255개·Ruff·의존성 검사 통과, 기존 실호출 근거 보존. [실행 기록](docs/DEVELOPMENT.md#f3007-implementation) | `1d55b4d` |
| 13. F3008 제약 판단 | 실제 도달·우회·가격·유종 재판정, 명시 날짜·시각·정차시간과 도착 상한 비교 | 테스트288개·Ruff 통과, 기존 GMS1회 추출·기록 응답 재판정. [판정 기록](docs/DEVELOPMENT.md#f3008-judgement) | `d11e437` |
| 14. 기능별 폴더 재구성 | Agent·계산·API 클라이언트·Tool·스키마·공통 기반·HTTP·CLI 분리 | 전체292개·Ruff 통과, 공개 Tool 계약 보존. [이동 기록](docs/DEVELOPMENT.md#package-layout) | `ac21f80` |
| 15. F3009 그래프 재계획 | 요청별 캐시·미평가 후보 우선·최대2회와 전체 평가 예산 | 전체319개·실제 후보9곳/추가2회 상한 종료. [재계획 기록](docs/DEVELOPMENT.md#f3009-replanning) | `1dc3228` |
| 16. F3010 대화 맥락 | 검증 조건·짧은 요약의 세션별 SQLite 저장과 현재 발언 변경값 병합 | 전체355개·실제 GMS4회/데이터 API0회. [맥락 기록](docs/DEVELOPMENT.md#f3010-memory) | `e3c8777` |
| 17. F3011 최종 응답 | 코드 검증 사실·출처와 GMS 사실 ID 문단 구성, 텍스트 CLI | 전체392개·실제 정상 추천 GMS3/길찾기7/Opinet6/휴게소1회. [응답 기록](docs/DEVELOPMENT.md#f3011-response) | `802124a` |
| 18. F3012 실행 이벤트 | 단계·Tool·판정·재계획·응답 이벤트와 구독 실패 격리 | 전체402개·payload 정제/연속 순서 검증. [이벤트 기록](docs/DEVELOPMENT.md#f3012-events) | `1d662c6` |
| 19. F3013 로그 저장 | Logging Agent 구독, SQLite 순서/불변 저장과 장애 격리 | 전체408개·세션 분리/비밀값 재정제/실제 DB 장애 검증. [저장 기록](docs/DEVELOPMENT.md#f3013-logging) | `40918f4` |
| 20. F3014 로그 API | 최근 목록·일관된 상세·삭제, 공통 오류와 계획 행 보존 | 전체414개·OpenAPI GET/DELETE 및 404/422/503 검증. [API 기록](docs/DEVELOPMENT.md#f3014-log-api) | `ebad540` |
| 21. F3015 계획 저장 | 입력·추천·근거 스냅샷 저장, 목록/상세, 비차단 시간 충돌 | 전체420개·재시작 영속성/알 수 없는 시각/구간 경계 검증. [저장 기록](docs/DEVELOPMENT.md#f3015-plan-storage) | `14a4303` |
| 22. F3016 계획 수정·삭제 | revision 기반 핵심값 수정, 재검증 상태, 선택 삭제 | 전체424개·동시 수정/충돌 재판정/명시 시각 삭제 검증. [CRUD 기록](docs/DEVELOPMENT.md#f3016-plan-update-delete) | `4392797` |
| 23. F3017 Backend API | 대화 Agent·Memory·로그 연결과 계획 CRUD FastAPI/OpenAPI | 전체429개·HTTP 정상/오류/충돌/영속성/`/docs` 검증. [API 기록](docs/DEVELOPMENT.md#f3017-backend-api) | F3017 기능 커밋 |

커밋 정리 과정에서는 실제 API 호출이나 재배포를 수행하지 않았다. 이후 단계도 완료 내용·검증 결과·남은 범위를 같은 형식으로 추가한다. 상세 개발 기록은 [DEVELOPMENT](docs/DEVELOPMENT.md), 실호출 근거는 [검증 JSON](docs/verification/2026-11-18-route-spike.json)에 있다.

커밋과 변경 파일 확인:

```bash
git log --oneline --reverse 062e7b8..HEAD
git show --stat 1a6a8e9
git log --oneline -- README.md
```

## GitLab·GitHub 원격 저장소

같은 `master` 브랜치를 두 저장소에서 관리한다. 기존 GitLab은 `origin`, GitHub는 `github`로 등록했다.

| 원격 이름 | 저장소 |
|---|---|
| `origin` | [GitLab](https://lab.ssafy.com/ekqlsekqls8857/fuelroute_project) |
| `github` | [GitHub](https://github.com/dabinkwon/fuelroute_project) |

로컬 커밋을 두 곳에 올릴 때는 Bash에서 각각 실행한다.

```bash
git remote -v
git push origin master
git push github master
```

## 프론트엔드 선배포

사용자 요청에 따라 Netlify로 프론트엔드를 배포했다. 카카오 앱 대표 도메인에는 아래 새 주소를 사용한다.

- **서비스 주소:** https://fuelroute-348.netlify.app
- **배포 내용:** FuelRoute 소개·이용 흐름·명시적인 안전거리 계산 예시를 담은 HTML/CSS 프론트엔드. 실제 주유소 검색과 Backend 연결은 후속 작업이다.
- **배포 상태:** Netlify 운영 배포 성공. 로그인 없이 접속할 수 있다.
- **소스:** [`frontend/README.md`](frontend/README.md), `frontend/dist/`. Netlify 배포 설정은 `netlify.toml`이다.
- **Netlify 사이트 ID:** `21ab4da2-503e-4dfe-bcc8-346b74f64a7d`, 이름: `fuelroute-348`.
- **확인한 운영 배포 ID:** `6afd3677cf50d903c64b1785`.

`fuelroute` 이름으로 생성을 요청했으며 Netlify가 사용 가능한 이름 `fuelroute-348`을 부여했다. `https://fuelroute`만으로는 일반 인터넷 공개 도메인으로 사용할 수 없다. 별도 도메인을 사용하려면 소유한 전체 도메인과 DNS 연결이 필요하다.

카카오 앱 대표 도메인은 서비스 대표 주소이며 입력할 값은 위 HTTPS 주소다. 대표 도메인과 JavaScript SDK 도메인 등록은 별도 설정이며, REST API 인증키는 계속 Backend 환경변수로 관리한다. [카카오 앱 설정 공식 안내](https://developers.kakao.com/docs/ko/app-setting/app)

이전 Sites 배포와 `frontend/.openai/hosting.json`은 이력으로 유지한다. 로컬 `artifacts`의 Sites 복제본은 2026-11-19 사용자 요청으로 정리했다. 현재 프론트 배포 대상은 Netlify이며, 재배포할 때는 같은 Netlify 사이트 ID를 사용한다. `.env`와 Python Backend는 프론트 배포 산출물에 포함하지 않는다.

인증 없는 HTTPS 요청으로 HTML·CSS의 HTTP 200과 본문 내용을 확인했다. Netlify의 기본 호스팅 주석·스크립트가 HTML에 추가되며 CSS는 로컬 파일과 동일하다. 이 환경에서는 브라우저 시각 검사를 수행하지 않았다.

## 서비스 한 줄 소개

**계기판의 주행 가능 거리(km)에서 안전 여유(km)를 빼 안전하게 도달 가능한 구간을 계산하고, 목적지까지의 경로상에서 접근 가능한 주유소의 가격과 우회 비용을 비교해 합리적인 주유 지점을 추천하는 Agent 기반 플래너.**

## 프로젝트 배경

장거리 운전 중에는 “가장 싼 주유소”가 반드시 가장 좋은 선택은 아니다.

운전자는 동시에 다음을 판단해야 한다.

- 현재 남은 연료로 해당 주유소까지 갈 수 있는가?
- 안전하게 남겨야 할 연료를 침범하지 않는가?
- 목적지 경로에서 얼마나 벗어나는가?
- 더 싼 주유소를 방문하기 위해 추가로 소모하는 시간과 연료가 합리적인가?
- 현재 조건으로 추천 가능한 후보가 없다면 다른 후보를 다시 찾아야 하는가?

FuelRoute는 단순 주변 최저가 검색이 아니라 **도달 가능성 → 사용자 제약 → 우회 비용 → 가격**을 함께 판단하는 것을 목표로 한다.

## 핵심 기능 요약

- 출발지·목적지·유종·주행 가능 거리(km)·남길 안전 여유(km) 입력
- 목적지까지 자동차 경로 조회
- 현재 조건에서의 안전 주행 가능 거리 계산
- 안전하게 도달 가능한 경로 구간에서 주유소 후보 탐색
- 후보별 가격 및 상세정보 조회
- 후보 주유소 경유 시 실제 추가 거리·시간 계산
- 조건을 만족하지 않는 후보 제외
- 조건을 만족하는 후보가 없을 경우 재계획
- 추천 근거와 제외 사유 제공
- 생성한 주유 계획 저장·조회·수정·삭제
- Agent 실행 과정 및 Tool 호출 이력 기록

상세 기획은 [`docs/PRD.md`](docs/PRD.md)를 참고한다.

## 기술 스택

### 원본 프로젝트 명세에서 요구되거나 허용된 기술

| 영역 | 기술 | 상태 |
|---|---|---|
| Language | Python 3.11+ | 3.11.9 검증; pyproj 3.7.2 요구 버전 반영 |
| Agent | LangGraph | 확정 |
| LLM Integration | GMS + LangChain `ChatOpenAI` | 현재 계정의 텍스트·구조화 출력·Tool 왕복 실호출 검증 완료 |
| Backend | FastAPI | 확정 |
| Database | SQLite | 확정 |
| Frontend | HTML/CSS/JavaScript 또는 Vue.js | **미정** |
| External Data | 외부 API 3종 이상 | 필수 조건 |

### 사용할 API

| API | 용도 | 상태 |
|---|---|---|
| GMS LLM | 요청 분석·Tool 선택·추천 설명 | `gpt-5.4-mini` 텍스트·조건 추출·Tool 왕복 실호출 성공 |
| 한국석유공사 Opinet | 주유소 후보·가격·거리·상세정보 | 휘발유/경유 후보·ID 상세 실호출 성공, 인증 쿼리는 `code` |
| Kakao Mobility 길찾기 | 기본 경로·경유 경로·거리·시간·경로 좌표 | 기본·경유·대구→서울 상세경로 실호출 성공 |
| Kakao Local 검색 | 지명·주소·장소명→WGS84 좌표 | 같은 REST 키 사용, 카카오맵 활성화 후 대구·서울 자동 변환·모호성 확인 |
| 공공데이터포털 전국휴게소정보표준데이터 | 휴게소 위치·방향·편의시설 보조정보 | 실제 215건 수집·반경/방향 필터 성공, 동일 주유소 매칭은 미확인 |

> GMS는 LLM 호출 경로이며, 외부 데이터 API 3종은 Opinet·Kakao Mobility·휴게소 API로 충족한다. API 선정과 실제 키 발급·호출 성공은 구분한다.

`gpt-5.4-mini`를 현재 GMS 계정에서 실제 검증했다. 주소·키는 `GMS_BASE_URL`, `GMS_API_KEY`로 분리하며, 전체 `/chat/completions` 주소를 입력해도 SDK base로 정규화한다. 검증 결과는 [`실호출 기록`](docs/verification/2026-11-18.json), 선정 근거는 [`ARCHITECTURE.md`의 GMS 설계](docs/ARCHITECTURE.md#21-gms-llm-호출)를 참고한다.

기술 설계와 검증 항목은 [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md)를 참고한다.

## 프로젝트 구조

현재 구조다. 새 기능은 같은 책임의 폴더 아래에 추가하며, 입력·출력 모델은 `schemas/`에서 공유한다. 상세 이동표는 [개발 기록](docs/DEVELOPMENT.md#package-layout)에 있다.

```text
project-root/
├── AGENTS.md
├── README.md
├── .env.example         # 키가 없는 설정 템플릿
├── requirements.txt    # 직접 의존성의 검증한 버전
├── requirements-dev.txt
├── requirements.lock.txt # Windows/Python 3.11.9 전체 환경 스냅샷
├── pyproject.toml      # pytest·Ruff 설정
├── netlify.toml         # Netlify: frontend/dist만 배포
├── frontend/           # Netlify에 배포한 정적 소개 화면
├── fuelroute/
│   ├── __main__.py          # python -m fuelroute 진입점
│   ├── agent/               # 요청 분석·계획·LangGraph·실행 흐름
│   │   ├── graph.py         # State·분석→계획→실행→판정→보조 연결
│   │   ├── analysis.py      # 원문·단위·누락 조건 검증
│   │   ├── planning.py      # F3006 계획 구성·의존 순서 검증
│   │   ├── execution.py     # 요청별 최초/추가 회차·캐시·결과 통합
│   │   ├── replanning.py    # F3009 재계획 정책·기존 독립 실행기
│   │   ├── llm.py           # GMS 요청 분석·계획 프롬프트
│   │   └── memory.py        # 현재 조건 병합·짧은 대화 요약
│   ├── calculations/        # 외부 호출 없는 계산·판정
│   │   ├── driving_range.py # 안전 주행거리
│   │   ├── coordinates.py   # WGS84↔KATEC
│   │   ├── route_points.py  # 경로 검증·검색점 보간
│   │   ├── detour.py        # 실제 접근거리·우회 비용
│   │   ├── ranking.py       # 가격/시간 우선 추천
│   │   └── constraints.py   # F3008 제약·시간 상한 판정
│   ├── clients/             # GMS·Kakao·Opinet·휴게소 API·공통 HTTP
│   ├── tools/               # fuel_tools.py 정규화 조회·registry.py 등록
│   ├── schemas/             # 분석·계획·실행·판정·경로 등 공유 모델
│   ├── core/                # 설정·공통 응답·오류·비밀값 정제
│   ├── storage/             # 세션별 SQLite 맥락 저장·충돌 검출
│   ├── api/app.py           # FastAPI /health
│   └── cli/                 # 명령 처리·GMS 검증·경로 진단
├── tests/              # 실제 키를 사용하지 않는 단위·HTTP 모의 테스트
├── docs/
│   ├── PRD.md
│   ├── ARCHITECTURE.md
│   ├── DEVELOPMENT.md   # 개발 작업·구현 및 검증 기록
│   └── TASKS.md         # 날짜별 할 일·완료 체크·직접 확인값
```

`.env`와 `.venv`는 로컬에 생성되어 있으며 Git에서 제외한다. 다음 단계에서 LangGraph 전체 흐름, SQLite Repository와 화면 연동을 확장한다. 오늘은 배포 설정·프론트 화면을 변경하지 않았다.

## 로컬 개발 환경 설정 및 실행 방법

모든 명령어는 저장소 루트의 Bash에서 실행한다. 현재 Windows 환경에서는 Git Bash를 사용하며 Python 3.11.9로 확인했다. 아래에서 가상환경을 활성화한 뒤 이후 명령어에도 같은 터미널을 사용한다.

```bash
python -m venv .venv
source .venv/Scripts/activate
python -m pip install -r requirements-dev.txt
# .env가 없는 새 체크아웃에서만 실행한다. 기존 키 파일을 덮어쓰지 않는다.
if [ ! -f .env ]; then
  cp .env.example .env
fi
python -m fuelroute doctor
```

Linux/macOS의 Bash에서는 가상환경 활성화 명령을 `source .venv/bin/activate`로 사용한다. `python3`만 제공되는 환경에서는 최초 생성 명령을 `python3 -m venv .venv`로 실행한다. 활성화 후의 명령은 동일하게 `python -m ...`이다.

실행 의존성만 설치할 때는 `requirements.txt`를 사용한다. 전체 설치 환경의 버전 재현이 필요하면 `requirements.lock.txt`를 사용한다. lock 파일은 개발 도구도 포함한다.

`.env`에 `GMS_API_KEY`, GMS 콘솔의 정확한 `GMS_BASE_URL`, 제공 모델 ID인 `GMS_MODEL`, `OPINET_API_KEY`, `KAKAO_REST_API_KEY`, `DATA_GO_KR_SERVICE_KEY`를 입력한다. 공공데이터 키는 **디코딩된 키**를 넣는다. `doctor`는 설정 유무만 표시하며 키·주소를 출력하거나 인증 성공을 보장하지 않는다.

기본 서버:

```bash
python -m uvicorn fuelroute.api.app:app --reload
```

`http://127.0.0.1:8000/health`와 `/docs`를 사용할 수 있다. 대화는 `POST /api/chat`, 계획은
`/api/plans`의 POST/GET/PATCH/DELETE, 실행 로그는 `/api/logs`의 GET/DELETE로 분리했다.
대화 요청은 새 `session_id`와 `execution_id`를 반환하며, 후속 대화에는 session ID를
재사용하고 실행 과정은 log ID로 조회한다. 예시는 Bash 기준이다.

```bash
curl -X POST http://127.0.0.1:8000/api/chat \
  -H 'Content-Type: application/json' \
  -d '{"message":"대구에서 서울 가는데 휘발유이고 주행 가능180km, 안전 여유30km로 추천해줘."}'
```

### API별 독립 검증

```bash
# GMS: 텍스트 → 완전/누락 조건 추출 → 로컬 계산 Tool 왕복 순서로 확인
python -m fuelroute gms --mode text
python -m fuelroute gms --mode conditions
python -m fuelroute gms --mode tool

# Opinet 공식 반경 검색 문서의 예시 KATEC 좌표. WGS84 경도·위도가 아니다.
python -m fuelroute opinet-nearby --x 314681.7 --y 544837.1 --radius-m 3000
# 실제 검색 결과의 UNI_ID로 example-id를 바꾼다.
python -m fuelroute opinet-detail --station-id example-id

# Kakao 공식 예시의 WGS84 경도·위도. --via LON LAT로 경유 요청도 확인한다.
python -m fuelroute route --origin 127.1076419 37.4024648 --destination 127.1105634 37.3941969
python -m fuelroute rest-area --page 1 --rows 10
```

성공은 종료 코드 `0`, 설정·입력·API 실패는 `1`과 공통 JSON 응답으로 표시한다. GMS 조건 추출·Tool 검증은 여러 번의 LLM 호출을 발생시킨다. 자동 재시도는 하지 않는다. 결과는 기본적으로 파일에 저장하지 않는다.

`clients/`는 제공사 통신·응답 검사, `tools/fuel_tools.py`는 정규화된 개별 조회를 담당한다. GMS 왕복·경로 연결 진단은 `cli/`에서 실행한다. 실제 호출 성공 기록과 HTTP 모의 테스트 결과를 구분한다. 주유소와 휴게소가 같은 시설인지는 아직 확인하지 않으며 보조정보로만 표시한다.

### 구현한 세 조회 Tool의 개별 호출

```bash
python -m fuelroute stations --point 127.035092567 37.494343243 --fuel-type gasoline --radius-m 5000
# 위 응답 data.items의 station_id를 사용한다. 아래 ID는 독립 조회 형식의 예시다.
python -m fuelroute station-detail --station-id A0010207 --fuel-type gasoline
python -m fuelroute rest-search --point 128.806648 37.758206 --radius-m 3000 --direction 하행
```

`stations`는 WGS84 좌표를 KATEC로 변환하고 `data.items`에 ID·가격·위치를 반환한다. `station-detail`은 해당 ID로 상세·가격 기준시각을 반환한다. `rest-search`는 전체 휴게소 페이지를 읽고 반경·노선·방향을 필터한다. 휴게소 `match_status=unverified`는 가까운 참고 정보라는 뜻이며 같은 주유소라는 의미가 아니다. 자연어 추천·판정·재계획은 아래 run 명령으로 연결했다. 세션별 조건 맥락은 아래 F3010, 한국어 최종 답변은 F3011을 따르며 HTTP/화면은 후속이다.

실제 응답의 값·필드·실패 사례는 [`2026-11-18 검증 기록`](docs/verification/2026-11-18.json)에 남겼다. 로컬 상세 원본과 임시 스크립트가 있던 `artifacts`는 2026-11-19 사용자 요청으로 삭제했으며, Git에 기록한 검증 자료는 유지한다. Opinet은 잘못된 키에도 빈 배열을 반환했으므로 0건만으로 인증 성공이나 실패를 단정할 수 없다.

### 경로→후보→실제 도달·우회비용 연결 실험

```bash
python -m fuelroute route-spike --origin 128.6014 35.8714 --destination 126.9780 37.5665 --remaining-range-km 180 --reserve-range-km 30 --fuel-type gasoline --max-detour-min 15 --max-candidates 3
```

위 km 예시는 150km 안전 구간에서 기본 반경 5km의 검색점을 최대 3개 만든다. 과거 L/연비 실호출은 144km 안전 구간으로 검증했다. 도로별 보고 거리와 상세 좌표를 사용하며, 도로 안의 위치는 지오데식 길이 비율로 근사한다. 조회한 후보를 ID로 합친 뒤 일부 후보만 상세·접근·경유 경로로 평가한다. 독립 접근 거리와 경유 첫 구간 거리 중 큰 값으로 안전 잔량을 확인한다.

기본값의 최대 호출은 Opinet 6회·Kakao 7회이며 GMS는 호출하지 않는다. `--max-search-points`는 1~3, `--max-candidates`는 1~10이며 상한을 늘리면 호출 수도 늘어난다. `--max-detour-km`으로 추가 거리 상한도 지정할 수 있다. 전체 경로를 완전 탐색하거나 전역 최저가를 보장하지 않으며 평가하지 않은 후보 수를 함께 반환한다.

실제 검증에서 주유소 16곳 중 3곳을 평가했고 15분 상한을 만족했다. 같은 실제 거리·시간에 5분 상한을 적용하는 로컬 재판정에서는 3곳 모두 제외됐다. [연결 실험 기록](docs/verification/2026-11-18-route-spike.json)과 [API 한도·이용조건](docs/API_USAGE.md)을 참고한다. 후보가 없거나 모두 제외되면 실험 결과의 `status=no_eligible_candidates`로 명시하며, 하위 조회 실패는 호출 기록에 보존한다.

### F3005 요청 분석 — 구현·검증·커밋 완료

```bash
python -m fuelroute analyze --message '대구에서 서울로 가는데 휘발유이고 주행 가능180km, 안전 여유30km로 주유 추천해줘.' --reference-date 2026-11-19
```

한 요청에 GMS를 1회 호출하고, 좌표 없이 지명·주소를 입력하면 Kakao Local 주소 검색→0건일 때 장소 검색으로 자동 변환한다. 위 예시는 출발/도착 좌표를 직접 요구하지 않는다. 주행 가능 거리·안전 여유(km)가 빠졌다면 해당 값만 추가로 묻고, 위치가 여러 곳이면 후보 이름·주소를 보여주며 더 구체적인 위치를 요청한다. 지역명 결과는 지역 단위 검색 좌표라는 안내를 포함한다.

기존 `KAKAO_REST_API_KEY`와 해당 앱의 **카카오맵 사용 설정 ON**이 필요하다. 위치당 최대 2회, 두 위치 최대 4회이며 같은 지명은 한 실행 안에서 재사용한다. 입력한 유효 좌표는 유지하고 인사/설명에는 위치 검색을 호출하지 않는다. `--reference-date`는 상대 날짜 재현용이며 생략하면 실행일 KST를 사용한다. 날짜 미언급 시 계획 날짜를 채우지 않는다. [API 한도·사용 범위](docs/API_USAGE.md)를 따른다.

기본 계산은 **주행 가능180km − 안전 여유30km = 안전 주행150km**다. 계기판을 읽어 입력하며 차량에서 자동 수집하지 않는다. 주행 가능 거리와 목적지까지 거리는 구분한다. [km 전환 검증](docs/verification/2026-11-19-distance-input.json)을 참고한다.

결과는 `data.analysis`에 조건과 기준/선호/제약 분류, 누락·오류·미확인 필드, 추가 질문, `ready_for_planning`을 담는다. 원문 구절과 숫자·단위를 대조하며 알 수 없는 좌표나 연료를 추측하지 않는다. 기본 필수값은 주행 가능 거리·안전 여유(km)이며 연료량이나 연비를 요구하지 않는다. 안전 여유의 임의 기본값도 없다. 기존 L/연비 입력은 별도로 지원하며 혼용은 거절한다. km·L·km/L·분 단위 누락/불일치는 입력 보완 대상으로 둔다. 준비 상태는 입력 검증 결과이며 실제 추천이나 시간 제약 충족을 뜻하지 않는다.

첨부 예제처럼 `State`, `make_graph`, 모델 호출 함수를 분리했다. `analyze` 명령의 추천 그래프는 `START→analyze→resolve_locations→END`이며 다른 의도는 분석 뒤 종료한다. `plan`은 계획까지, `run`은 조회까지 연결한다. 조건 맥락은 아래 F3010에서 연결했으며 최종 문장·로깅은 후속 작업이다. [분석 기록](docs/DEVELOPMENT.md#f3005-implementation), [자동 변환 기록](docs/DEVELOPMENT.md#f3005-geocoding), [실호출 근거](docs/verification/2026-11-19-geocoding.json)를 참고한다.

<a id="f3006-task-plan"></a>

### F3006 작업 계획 — 구현·검증·커밋 완료

```bash
python -m fuelroute plan --message '대구에서 서울로 가는데 휘발유이고 주행 가능180km, 안전 여유30km로 주유 추천해줘.' --reference-date 2026-11-19
```

기존 분석과 필요한 위치 해석 뒤 `build_plan` 노드를 실행한다. 준비된 추천 요청만 GMS에 작업 순서를 1회 더 제안받으며, 입력이 부족하면 `data.plan.status=needs_user_input`, 인사·설명 등은 `not_required`와 빈 작업 목록을 반환한다. 이 두 경우 계획 모델을 호출하지 않는다. `analyze` 명령은 기존 분석 범위를 유지한다.

준비된 계획은 `status=ready`이며 `data.plan.request`에 검증한 주행 조건과 기존 실행 기본값을, `steps`에 작업 ID·외부 Tool/일반 코드 구분·선행 작업·입력 출처·조건부 실행 범위·실패 정책을 담는다. 기본 경로·안전거리 → 검색점 → 후보 → 상세 → 접근/경유 경로 → 제약 판정 → 정렬 → 추천 후보의 휴게소 보조정보 순서다. 코드가 누락·중복·의존 순서 위반과 임의 작업/인자 삽입을 거절한다. 입력 출처는 후속 실행기를 위한 설명이며 문자열을 코드로 실행하지 않는다.

GMS는 분석 포함 최대 2회, 위치 검색은 별도 최대 4회다. **`plan` 명령은 조회를 실행하지 않으며 `run` 명령에서 같은 계획을 실제 조회에 연결한다.** 상세 가격이 없는 후보의 경로 조회와 추천 후보가 없는 경우의 보조 조회를 생략한다. 보조정보 실패는 추천 제외 사유로 사용하지 않는다. 날짜·시각은 분석에 보존하며 미래 가격이나 도착 시각 충족을 보장하지 않는다. 작업 계획은 F3015에서 저장할 최종 주유 계획과 구분한다.

F3006 신규 테스트 25개와 km 전환 테스트 26개를 포함해 전체 215개를 통과했다. 실제 GMS 정상/누락/인사와 km 입력·안전 여유 누락을 확인했다. [상세 기록](docs/DEVELOPMENT.md#f3006-implementation), [실호출 근거](docs/verification/2026-11-19-task-plan.json)를 참고한다. F3006과 km 기본 입력 전환은 사용자 요청으로 작업별 커밋을 마쳤다. 실제 조회 연결은 아래 F3007 절을 따른다.

<a id="f3007-execution"></a>

### F3007 Tool 실행·결과 통합 — 구현·검증·커밋 완료

```bash
python -m fuelroute run --message '대구에서 서울로 가는데 휘발유이고 주행 가능180km, 안전 여유36km야. 우회15분 이내에서 싼 주유소를 추천해줘.' --reference-date 2026-11-19
```

F3007은 `START→analyze→resolve_locations→build_plan→execute_plan→END`까지 연결했다. 현재 `run`은 아래 F3008의 판정/보조 연결 노드를 추가로 거친다. 지명은 자동 해석하고, 입력이 부족하거나 추천 의도가 아니면 실행기를 호출하지 않는다. 검증한 계획 순서대로 기본 경로·안전거리·검색점·후보·상세·접근/경유 경로·기존 제약 판정/정렬·추천 후보의 휴게소 보조정보를 연결한다. 원본 상세 경로는 실행기 안에서만 검색점 생성에 쓰며 공개 결과나 모델에 전달하지 않는다.

결과는 `data.results`의 타입 있는 작업별 출력과 `data.execution`의 호출 이력·단계 처리 상태·미평가 ID·경고·`replan_needed`로 확인한다. 후보 ID와 상세 위치를 다음 조회에 전달하며 가격 기준시각·휴게소 `unverified`·미확인 시설 `null`을 유지한다. 개별 상세/경로 실패는 사유를 남기고 다른 후보를 계속 평가한다. 핵심 인증·설정·한도 오류는 공통 실패와 종료 코드1로 반환한다. 휴게소 조회 실패만으로 추천을 제외하지 않는다.

기본 호출 상한은 GMS2회·Local4회·Opinet6회·길찾기7회·휴게소1논리조회(내부 최대10페이지)다. 실제 수는 좌표 입력·캐시·조기 제외에 따라 줄어든다. `tool_call_counts`는 HTTP 수가 아닌 논리 Tool 시도 수이며 설정 누락 시 HTTP0회일 수도 있다. 실제 검증은 명시적 좌표로 Local0회였고 GMS2·Opinet6·길찾기7·휴게소1회로 후보16곳 중3곳을 평가해 추천했다. 휴게소 참고 정보2건은 동일 시설/방향 미확인으로 표시했다. 이는 해당 호출 시점의 결과다.

신규 테스트40개·전체255개, Ruff·의존성 검사를 통과했다. [구현·검증 기록](docs/DEVELOPMENT.md#f3007-implementation), [실호출 근거](docs/verification/2026-11-19-tool-execution.json), [호출 상한](docs/API_USAGE.md)을 참고한다. 동일 실패 재시도·최종 자연어 응답·DB/이벤트는 후속 작업이다. 그래프 재계획은 아래 F3009에서 연결했다. 도착시각과 제약 근거는 아래 F3008에서 보완했다.

<a id="f3008-judgement"></a>

### F3008 제약 판단·근거 — 구현·검증·커밋 완료

```bash
python -m fuelroute run --message '대구에서 서울로 휘발유 주유 추천해줘. 주행 가능180km, 안전 여유36km이고 우회15분 이내면 좋아. 2026년11월19일 오후11시 출발해서 2026년11월20일 오전4시까지 도착하고 싶어. 주유와 정차에10분 예상해.' --reference-date 2026-11-19
```

현재 `run`은 `analyze→resolve_locations→build_plan→execute_plan→judge→enrich`로 실행한다. `judge`는 추가 API/LLM 없이 원본 비용을 재사용해 안전거리·유종·가격·실제 도달거리·우회 거리/시간·선택 도착 상한을 다시 확인한다. 과거 `eligible`나 계산된0비용을 신뢰하지 않으며 `data.judgement`에 항목별 `passed/failed/unverified/not_requested`, 비교값·단위·출처·호출 ID를 반환한다. `data.results`와 `execution`도 확정 판정을 반영한다.

도착 상한이 있으면 **주행 예상시간+명시한 주유·정차시간**을 출발~도착 예산과 비교한다. 정차0분도 직접 명시해야 하며 기본값을 넣지 않는다. 날짜 없이15시→20시는 같은 날의 시각차로 비교하고 날짜를 생성하지 않는다. 23시→익일1시처럼 자정을 넘으면 출발/도착 날짜를 요구하며 익일을 임의 추측하지 않는다. 누락값은 `judgement.missing_fields/questions`에 표시하고 미확인 후보를 확정 추천에서 제외한다. `analysis.ready_for_planning`은 기본 조회 입력의 준비 상태이며 최종 제약 판정은 `judgement`를 확인한다.

휴게소 조회는 판정 뒤 확정 후보에만 연결한다. 시간 제약으로A에서B로 추천이 바뀌어도A의 보조정보를B에 표시하지 않는다. 선택 연비가 있으면 도달에 필요한 추정L를 계산할 수 있지만 기본 km 입력에는 연비·L를 요구하지 않는다. ETA·소비량은 추정치이며 미래 교통·대기시간·가격을 보장하지 않는다.

전체 테스트288개·Ruff·의존성 검사를 통과했다. 실제 GMS1회로 명시된 익일 날짜/시각·정차10분 추출을 확인하고, 기존 F3007 실제 경로를 새 조회 없이 재판정해 충족/상한 초과/정차시간 누락을 구분했다. [상세 기록](docs/DEVELOPMENT.md#f3008-judgement), [추출·재판정 근거](docs/verification/2026-11-19-constraint-judgement.json)를 참고한다. 그래프 재계획은 아래 F3009 기록을 따른다.

<a id="f3009-replanning"></a>

### F3009 그래프 재계획 — 커밋 완료(`1dc3228`)

`run`은 `analyze→resolve_locations→build_plan→execute_plan→judge→replan`으로 실행한다. 필요하면 `replan→judge`를 최대2회 반복하고 종료 뒤에만 `enrich→END`로 간다. 분석·계획 모델을 다시 부르거나 최초 실행기를 중첩 실행하지 않는다.

- 기존 미평가 후보를 먼저 사용하고, 없을 때 안전 구간의 앞쪽 검색점 한 곳만 추가한다.
- 기본 회차당3곳·전체9곳 평가이며 실패 후보도 예산을 사용한다. 기본 경로·기존 상세·동일 경로의 성공/실패는 요청 안에서 재사용한다.
- 입력 부족·핵심 인증/한도/설정 실패는 재계획하지 않는다. 안전 여유·유종·우회/시간 상한을 자동 완화하지 않는다.
- `data.replanning`의 `replan_count`, `rounds`, `stop_reason`, `next_input`으로 실제 회차·종료 이유·다음 입력 안내를 확인한다. `execution.calls/steps[].round_index`는 최초0/추가1~2다.

신규27개·전체319개 테스트와 실제 상한 종료 검증을 통과했다. 대구→서울 요청에 의도적으로1분 도착 상한을 주어 후보9곳·추가2회 뒤 `evaluation_budget_exhausted`로 종료했다. 실제 GMS2·길찾기19·Opinet12회였고 Local·휴게소는0회다. 후보 재사용·새 검색점에서의 추천 성공 분기는 모의 검증이며 실제 추천 성공으로 합치지 않는다. [상세 기록](docs/DEVELOPMENT.md#f3009-replanning), [실제 실행 근거](docs/verification/2026-11-19-graph-replanning.json)를 참고한다.

<a id="f3010-memory"></a>

### F3010 대화 맥락 — 구현·검증·사용자 확인 완료

`analyze`, `plan`, `run`에 같은 `--session-id`를 주면 검증된 조건·짧은 직전 요청/응답 요약을 SQLite에서 이어받는다. 기본 DB는 `data/fuelroute.sqlite3`이고 선택 `DATABASE_PATH`로 바꿀 수 있다. 키를 추가할 필요는 없다. ID를 생략하면 기존처럼 단독 실행하며 DB를 만들지 않는다.

```bash
python -m fuelroute analyze --session-id trip-demo --message '대구에서 서울로 휘발유 주유 추천해줘. 주행 가능180km야.'
python -m fuelroute analyze --session-id trip-demo --message '안전 여유30km야.'
python -m fuelroute run --session-id trip-demo --message '우회10분 이내로 바꿔서 추천해줘.'
```

미언급 값만 이어받고 잘못된 새 입력을 이전 정상값으로 숨기지 않는다. 새 지명은 이전 좌표를 지운 뒤 다시 해석한다. km/L량 방식을 바꾸면 반대 방식의 연료량은 지우고 필요한 값을 다시 묻는다. `새 여행/초기화`와 `우회 제한 없애줘` 같은 명시 요청도 현재 원문을 검사한다. 인사·설명만 하면 기존 조건으로 추천하지 않고 여행 맥락을 보존한다.

`data.analysis.field_sources`는 이번 요청/이전 맥락을, `data.session`은 ID·저장 버전·상속 필드를 보여준다. **경로·가격·전체 Tool 응답과 실행 캐시는 다음 발언에 저장하지 않는다.** 후속 `run`은 현재 데이터로 다시 조회한다. 같은 세션을 동시에 갱신하면 `MEMORY_CONFLICT`, 손상·저장 실패는 `MEMORY_ERROR`이며 무조건 덮어쓰거나 자동 재시도하지 않는다.

메모리35개·저장소 의존성1개를 추가해 전체355개 테스트를 통과했다. 실제 GMS4회로 같은 세션의 우회/안전 여유 변경과 다른 세션 분리를 확인했고 데이터 API는0회였다. 두 턴의 전체 추천·가격 재조회는 모의 전송으로 별도 검증했다. [상세 기록](docs/DEVELOPMENT.md#f3010-memory), [실제 분석·SQLite 근거](docs/verification/2026-11-19-conversation-memory.json)를 참고한다.

<a id="f3011-response"></a>

### F3011 근거 기반 최종 응답 — 구현·검증 완료

`run`은 마지막 판정과 휴게소 보조 조회 뒤 `respond` 노드를 거쳐 `data.answer`와 구조화된 `data.final_response`를 반환한다. 가격·실제 도달거리·안전거리·우회 거리/시간·정렬·제외 이유·평가 범위·가격 기준시각·휴게소 미확인을 일반 코드가 문장으로 만든다. GMS는 추천/미발견일 때 검증 문장의 ID를 문단으로 묶는 역할만 하며 숫자·추천·시설 내용을 새로 작성하지 않는다. 모든 사실 ID를 정확히 한 번 사용하고 결과 문장을 먼저 두는지 코드가 검사한다.

```bash
python -m fuelroute run --message '대구에서 서울로 가는데 휘발유이고 주행 가능180km, 안전 여유36km야. 우회15분 이내에서 가격 우선으로 골라줘.' --reference-date 2026-11-19
python -m fuelroute run --text --message '안녕' --reference-date 2026-11-19
```

기본 출력은 기존 공통 JSON이고 `--text`는 사용자용 한국어 답변만 표시한다. 추천/미발견의 전체 GMS 상한은 분석1+계획1+최종 문단1회다. 입력 보완·인사·핵심 오류는 최종 모델을 생략한다. 최종 모델의 통신·형식·근거 ID 오류는 확인된 사실의 `fallback` 응답으로 복구하며 핵심 조회 실패의 기존 `ok=false/data=null/error` 계약은 유지한다. 분석기 주입 테스트는 `narrator`를 명시하지 않으면 GMS를 암묵적으로 호출하지 않는다.

신규37개·전체392개 테스트와 Ruff·포맷·의존성 검사를 통과했다. 실제 정상 추천은 GMS3·Kakao Mobility7·Opinet6·휴게소1회였고, 입력 보완·인사와 커밋된 실제 F3009 비용의 추천 미발견 설명도 구분해 검증했다. [상세 기록](docs/DEVELOPMENT.md#f3011-response), [정제된 검증 근거](docs/verification/2026-11-19-final-response.json)를 참고한다.

<a id="f3012-events"></a>

### F3012 실행 이벤트 발행 — 구현·검증 완료

`EventBus`와 요청별 `ExecutionEventStream`이 단계 시작/종료, Tool 요청/성공/실패와 소요시간, 분석·계획·최종 설명의 공개 결정, 제약 판정, 재계획/상한 종료, 최종 응답 생성을 순서대로 발행한다. 실행 ID는 요청마다 분리하고 sequence는1부터 증가한다. Tool 이벤트는 기존 `request_id`로 요청과 결과를 연결한다.

payload는 단계 상태·개수·오류 코드·입력 필드명만 담는다. 사용자 원문·좌표값·가격/경로 원본·키·URL·헤더·모델 내부 추론은 넣지 않는다. 구독자가 없으면 기존 실행이 그대로 동작하고, 한 구독자가 실패해도 다음 구독자와 메인 Agent를 계속 실행한다. `execute_request(..., event_bus=..., execution_id=...)`를 지정하면 `data.event_stream`에 실행 ID·발행 건수·전달 실패 수를 반환한다. SQLite 저장은 F3013 범위다.

<a id="f3013-logging"></a>

### F3013 SQLite 실행 로그 저장 — 구현·검증 완료

`LoggingAgent`는 `EventBus`의 비동기 구독자로 연결해 이벤트를 설정 비밀값으로 다시 정제한 뒤 `SQLiteExecutionLogStore`에 그대로 전달한다. 저장에는 LLM을 사용하지 않고 사실을 요약·재작성하지 않는다. 저장소는 실행별 첫 이벤트부터 세션을 만들고 정확한 `event_count+1` sequence만 append한다. 마지막 `execution/STEP_COMPLETED`에서 `completed` 또는 `failed` 상태와 종료 시각을 기록한다.

이벤트 행은 SQLite trigger로 수정할 수 없고, 중복·순서 간격·종료 뒤 추가를 `LOG_STORE_ERROR`로 거절한다. 서로 다른 실행 ID는 각자 sequence1부터 시작한다. 파일 I/O는 작업 스레드에서 처리하고 연결을 작업마다 닫는다. DB 저장 장애는 EventBus가 격리해 메인 Agent 응답을 유지한다. 저장 연결은 `LoggingAgent(store, settings.secret_values()).connect(bus)`로 명시하며 로그 HTTP 조회·삭제는 F3014에서 제공한다.

<a id="f3014-log-api"></a>

### F3014 실행 로그 조회·삭제 API — 구현·검증 완료

FastAPI에 아래 세 Endpoint를 연결했다. 상세는 한 SQLite snapshot에서 읽어 실행 중 조회에도 세션 event count와 이벤트 목록이 일치한다. 목록은 최근 시작 순서이고 `limit`은1~100이다. 삭제는 세션과 이벤트를 지우지만 연결된 저장 계획이 있으면 계획 행은 유지하고 실행 참조만 `NULL`로 바꾼다. PUT/PATCH 수정 Endpoint는 제공하지 않는다.

```bash
curl -sS 'http://127.0.0.1:8000/api/logs?limit=50'
curl -sS 'http://127.0.0.1:8000/api/logs/EXECUTION_ID'
curl -i -X DELETE 'http://127.0.0.1:8000/api/logs/EXECUTION_ID'
```

없는 로그는404 `LOG_NOT_FOUND`, 잘못된 ID/쿼리는422 `INVALID_INPUT`, 저장소 장애는503 `LOG_STORE_ERROR`다. 오류 본문은 `{error: {code, message, request_id}}`이며 원 입력·DB 경로·예외를 노출하지 않는다. `/health`는 로그 DB와 무관하게 기존 정적200을 유지한다.

### 기존 명시적 조건의 추천·재계획 실행

```bash
python -m fuelroute recommend --origin 128.6014 35.8714 --destination 126.9780 37.5665 --remaining-range-km 180 --reserve-range-km 30 --fuel-type gasoline --max-detour-min 15 --max-replans 2 --max-evaluations 9 --sort-by price
# HTTP·LLM 호출 없이 일곱 Tool의 입력·출력·실패 계약 확인
python -m fuelroute tool-schemas
```

안전·유종·우회 상한을 먼저 적용한 뒤 기본 가격→추가 시간→추가 거리→ID로 정렬한다. `--sort-by detour_time`이면 추가 시간→추가 거리→가격→ID 순서다. 임의 가중치는 사용하지 않는다. 후보 평가 순서는 검색 가격→ID이며 두 정렬 옵션 모두 **평가한 후보 집합** 안에서 비교한다.

추천이 없으면 이미 조회한 미평가 후보를 먼저 사용하고 없을 때 앞쪽 검색점 한 곳을 추가한다. 재계획은 최대 2회이며 `--max-replans 0`이면 최초 회차만 실행한다. `--max-candidates`는 회차당 기본 3곳(1~10), `--max-evaluations`는 전체 기본 9곳(1~30)이다. 연료·우회 상한을 완화하지 않으며 인증·설정·호출 제한 오류는 중단한다.

동일 실행의 경로·상세·검색 결과를 캐시하고 종료 시 폐기한다. 기본 최대 호출은 Opinet 14회·Kakao 19회·GMS 0회다. 상한을 늘리면 호출 수도 늘어난다. 적합 후보가 없으면 `status=no_recommendation`과 종료 사유·제외 근거를 반환한다. 전체 경로 최저가를 보장하지 않는다.

실제 추천에서는 후보 16곳 중 3곳을 평가해 불정주유소를 선택했다. Opinet 6회·Kakao 7회로 최초 회차에서 종료했다. 재계획·캐시·오류 분기는 모의 응답으로 검증했다. [추천 검증 기록](docs/verification/2026-11-18-recommendation.json), [Tool 계약](docs/TOOL_CONTRACTS.md), [전체 JSON schema](docs/TOOL_CONTRACTS.json)를 참고한다.

### 로컬 검증

```bash
python -m pytest -q
python -m ruff check .
python -m ruff format --check .
python -m pip check
```

## 주요 문서

- [`AGENTS.md`](AGENTS.md) — AI 개발자의 문서 확인 순서, 코드 작성·API 사용·검증 규칙
- [`docs/PRD.md`](docs/PRD.md) — 서비스 기획, 기능 요구사항, MVP 범위
- [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) — 기술 설계안, 핵심 계산, 외부 API 의존성
- [`docs/API_USAGE.md`](docs/API_USAGE.md) — 계정 한도, 공개 이용조건·출처, 현재 적용 범위
- [`docs/TOOL_CONTRACTS.md`](docs/TOOL_CONTRACTS.md) — 일곱 Tool 실행 계약과 기계 판독용 JSON schema
- [`docs/DEVELOPMENT.md`](docs/DEVELOPMENT.md) — 기존 개발 TASK 정의·완료 이력과 상세 구현·검증 근거
- [`docs/TASKS.md`](docs/TASKS.md) — 날짜별 개발·직접 확인할 일, 완료 체크와 확인값

기획 선택 이유는 PRD에, 기술 선택 이유와 변경 이력은 ARCHITECTURE에 함께 기록한다.

## 코드 작성 규칙

공통 작업 지침은 [`AGENTS.md`](AGENTS.md)를 따른다.

새로 작성하거나 수정하는 모든 함수에 상세한 docstring을 작성한다. Agent가 호출하는 Tool에는 같은 계약을 `description`과 입력 스키마의 필드 설명으로 전달한다. AI가 설명만 읽어도 **목적, 호출 시점, 입력 타입·단위·좌표계, 반환값, 실패 조건, 부작용, 다음 단계**를 이해할 수 있어야 한다. 구체적인 규칙과 예시는 [`ARCHITECTURE.md` 14장](docs/ARCHITECTURE.md#14-함수-설명과-tool-계약)을 따른다.

## 날짜별 작업 기록

날짜별 개발·직접 확인할 일과 완료 기록은 [TASKS.md](docs/TASKS.md)에서 함께 관리한다. 완료한 항목은 같은 위치에 체크하고 결과·근거를 남긴다. 별도 daily는 사용하지 않는다. 기존 키 입력, GMS 크레딧·API 한도 확인, 배포 확인·카카오 대표 도메인 등록 기록은 [2026-11-18 완료 항목](docs/TASKS.md#completed-2026-11-18)으로 통합했다.

**개발 종료일은 사용자 확정으로 2026-11-20이다.** 11월 18일 F3001~F3004, 19일 F3005~F3014, 20일 F3015~F3019를 완료하고 최종 검증·제출물 정리도 20일 안에 마친다. [TASKS](docs/TASKS.md)는 날짜별 F 번호·완료 체크·짧은 결과를, [DEVELOPMENT](docs/DEVELOPMENT.md)는 실제 변경한 코드·이유·검증·문제 해결의 상세 이력을 기록한다. 두 파일을 유지하되 현재 진행 상태는 TASKS에서만 체크한다. 선택 심화·추가 배포는 필수 완료 후 기한 안에 여유가 있을 때만 진행한다.

## 현재 개발 상태

F3014는 `ebad540`에 커밋했다. 다음 개발 작업은 F3015 계획 저장이다.

완료된 내용:

- 서비스 도메인 및 핵심 문제 정의
- 장거리 주유 추천 시나리오 정의
- 안전 잔량을 포함한 주행 가능 거리 개념 정의
- Opinet 5km 반경 제한에 대한 경로 기반 검색 방향 정의
- Agent 재계획 필요 조건 초안
- 원본 AI 코어 프로젝트의 필수 요구사항을 FuelRoute에 매핑
- GMS 사용 방향 및 외부 데이터 API 3종 선정
- 함수 설명 작성 규칙과 오늘 구현 TODO 정리
- `.venv`·의존성 설치, `.env` 템플릿과 Git 제외 설정
- 기본 FastAPI 서버와 공통 성공·실패 계약, 안전거리 계산
- GMS 및 데이터 API별 독립 검증 CLI 구현
- GMS·Opinet·Kakao·휴게소 API 실제 호출과 상세 설명·입력 스키마가 연결된 개별 조회 Tool 구현
- 독립 기준점 3개로 KATEC 변환 오차 3m 이내 확인, 좌표 변환 후 실제 지역 주유소 반환
- SQLite 저장 설계의 세션 삭제·계획 보존·이벤트 수정 금지 검증
- 공개 API 이용조건·출처·현재 계정 한도와 실험별 호출 상한 기록
- 도로별 거리 기반 검색점 보간, 안전 구간 후보 검색, 실제 접근·경유 비용 평가
- 제약 우선 추천 정렬, 최대 2회 재계획·실행별 캐시·평가 예산 구현
- 일곱 Tool의 상세 설명·입출력·실패 코드·다음 필드를 등록하고 CLI에서 명세 출력
- 실제 추천 성공, 오프라인 테스트 110개·Ruff 검사 통과
- F3005의 기준·선호·제약 추출, 원문 근거/단위 대조, 누락·오류 안내와 분석 전용 그래프 구현·검증·커밋 완료
- 지명·주소 자동 좌표 변환, 후보 모호성·실패 안내, 좌표 출처·지역 수준 경고 추가
- F3005 단계의 오프라인 테스트 164개·Ruff·의존성 검사 통과. 분석 GMS 4회와 자동 변환 검증 GMS 1회·Kakao Local 5회 확인
- F3006 계획 모델·노드, 작업 의존 순서·입력 보존·계획 생략과 km 기본 입력 전환 구현·검증·커밋 완료
- F3006·km 전환 시점의 전체 테스트215개 통과. F3006 시나리오 GMS4회와 사전 실행2회, km 전환 GMS3회를 구분해 기록
- F3007 계획 순서 실행·후보별 실패 격리·경로 기하 분리·보조정보·Agent/CLI 연결 구현·검증·커밋 완료
- F3009의 전체 테스트319개·실제2회 상한 종료 확인.
- F3007 시점의 전체 테스트255개 통과. 실제 GMS2·길찾기7·Opinet6·휴게소1회로 한 회차 실행 확인
- 현재 전체 테스트288개·Ruff·의존성 검사 통과. F3008 시간 필드 추출 GMS1회와 기존 실제 비용의 로컬 재판정 확인
- F3010 전체355개와 F3011 전체392개 테스트 통과. 실제 정상 추천의 근거 기반 한국어 답변과 GMS 문단 구성 확인

아직 완료되지 않은 내용:

- 검색점 전략·후보 평가 범위의 고도화
- 같은 주유소·진행 방향임을 입증하는 휴게소 매칭 고도화
- 공개 데이터 재배포 범위를 확대할 때의 상세 이용허락 조건 재확인 및 공유 일일 한도 집계
- F3018 기능 UI와 Backend 연동, F3019 실행상태 표시

다음에 수행할 일과 완료 체크는 [`docs/TASKS.md`](docs/TASKS.md)의 날짜별 목록을 따른다. 기존 개발 TASK 이력과 상세 구현·검증 근거는 [`docs/DEVELOPMENT.md`](docs/DEVELOPMENT.md)에 보존한다.
