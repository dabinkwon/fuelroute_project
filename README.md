# FuelRoute Agent

> **현재 상태:** GMS·외부 API 3종 검증, 추천 정책·유한한 재계획·일곱 Tool 계약 구현 완료<br>
> **구현 상태:** 추천 CLI와 F3005 분석·자동 좌표 변환, F3006 작업 계획 실호출 성공. 전체 실행·Memory·로깅·API·화면 연결은 후속

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

현재 구조:

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
│   ├── main.py         # FastAPI /health
│   ├── __main__.py     # python -m fuelroute 검증 CLI
│   ├── agent.py        # State·make_graph와 F3005 분석·F3006 계획 노드
│   ├── agent_gms.py    # GMS 구조화 요청 분석·작업 순서 제안
│   ├── request_analysis.py # 원문 근거·단위·필수값 검증
│   ├── geocoding.py    # Kakao Local 주소·장소 검색과 모호성 처리
│   ├── task_plan.py    # F3006 계획 스키마·입력 보존·의존 순서 검증
│   ├── config.py       # .env와 OS 환경변수
│   ├── contracts.py    # ToolResult 공통 성공·실패
│   ├── services.py     # 안전 주행거리 계산
│   ├── coordinates.py  # WGS84 ↔ KATEC TM128, 검색용 직선거리
│   ├── routing.py      # 도로 누적거리와 검색점 보간
│   ├── route_spike.py  # 후보 연결·실제 도달거리·우회비용 검증
│   ├── recommendation.py # 제약 재판정·가격/시간 우선 추천
│   ├── planning.py     # 최대 2회 재계획·캐시·평가 예산
│   ├── route_contracts.py # 정규화한 거리·시간·구간 비용
│   ├── tool_registry.py # 일곱 Tool 등록·입출력·실패 계약
│   ├── tools.py        # 후보·ID 상세·휴게소 정규화 조회, LangChain 등록
│   ├── gms.py          # GMS 응답·구조화 출력·Tool 왕복 검증
│   └── probes.py       # 외부 데이터 API의 독립 호출 검증
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
python -m uvicorn fuelroute.main:app --reload
```

`http://127.0.0.1:8000/health`와 `/docs`를 사용할 수 있다. 현재 HTTP 경로는 `/health`만 구현했다. 추천은 아래 CLI에서 실행하며 Chat·추천 HTTP API·계획 CRUD 연결은 후속 단계다.

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

`probes.py`는 인증·원본 필드 확인용이고 `tools.py`는 정규화된 개별 조회용이다. 실제 호출 성공 기록과 HTTP 모의 테스트 결과를 구분한다. 주유소와 휴게소가 같은 시설인지는 아직 확인하지 않으며 보조정보로만 표시한다.

### 구현한 세 조회 Tool의 개별 호출

```bash
python -m fuelroute stations --point 127.035092567 37.494343243 --fuel-type gasoline --radius-m 5000
# 위 응답 data.items의 station_id를 사용한다. 아래 ID는 독립 조회 형식의 예시다.
python -m fuelroute station-detail --station-id A0010207 --fuel-type gasoline
python -m fuelroute rest-search --point 128.806648 37.758206 --radius-m 3000 --direction 하행
```

`stations`는 WGS84 좌표를 KATEC로 변환하고 `data.items`에 ID·가격·위치를 반환한다. `station-detail`은 해당 ID로 상세·가격 기준시각을 반환한다. `rest-search`는 전체 휴게소 페이지를 읽고 반경·노선·방향을 필터한다. 휴게소 `match_status=unverified`는 가까운 참고 정보라는 뜻이며 같은 주유소라는 의미가 아니다. 전체 Agent 추천 흐름은 후속 단계다.

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

첨부 예제처럼 `State`, `make_graph`, 모델 호출 함수를 분리했다. 추천 요청의 현재 그래프는 `START→analyze→resolve_locations→END`이며 다른 의도는 분석 뒤 종료한다. 계획·주유소 조회·Memory·최종 응답·로깅은 작업별 확인 후 연결한다. [분석 기록](docs/DEVELOPMENT.md#f3005-implementation), [자동 변환 기록](docs/DEVELOPMENT.md#f3005-geocoding), [실호출 근거](docs/verification/2026-11-19-geocoding.json)를 참고한다.

<a id="f3006-task-plan"></a>

### F3006 작업 계획 — 구현·검증·커밋 완료

```bash
python -m fuelroute plan --message '대구에서 서울로 가는데 휘발유이고 주행 가능180km, 안전 여유30km로 주유 추천해줘.' --reference-date 2026-11-19
```

기존 분석과 필요한 위치 해석 뒤 `build_plan` 노드를 실행한다. 준비된 추천 요청만 GMS에 작업 순서를 1회 더 제안받으며, 입력이 부족하면 `data.plan.status=needs_user_input`, 인사·설명 등은 `not_required`와 빈 작업 목록을 반환한다. 이 두 경우 계획 모델을 호출하지 않는다. `analyze` 명령은 기존 분석 범위를 유지한다.

준비된 계획은 `status=ready`이며 `data.plan.request`에 검증한 주행 조건과 기존 실행 기본값을, `steps`에 작업 ID·외부 Tool/일반 코드 구분·선행 작업·입력 출처·조건부 실행 범위·실패 정책을 담는다. 기본 경로·안전거리 → 검색점 → 후보 → 상세 → 접근/경유 경로 → 제약 판정 → 정렬 → 추천 후보의 휴게소 보조정보 순서다. 코드가 누락·중복·의존 순서 위반과 임의 작업/인자 삽입을 거절한다. 입력 출처는 후속 실행기를 위한 설명이며 문자열을 코드로 실행하지 않는다.

GMS는 분석 포함 최대 2회, 위치 검색은 별도 최대 4회다. **아직 계획의 길찾기·주유소·휴게소 조회를 실행하지 않는다.** 상세 가격이 없는 후보의 경로 조회와 추천 후보가 없는 경우의 보조 조회를 생략하도록 계획한다. 보조정보 실패는 추천 제외 사유로 사용하지 않는다. 날짜·시각은 분석에 보존하며 미래 가격이나 도착 시각 충족을 보장하지 않는다. 작업 계획은 F3015에서 저장할 최종 주유 계획과 구분한다.

F3006 신규 테스트 25개와 km 전환 테스트 26개를 포함해 전체 215개를 통과했다. 실제 GMS 정상/누락/인사와 km 입력·안전 여유 누락을 확인했다. [상세 기록](docs/DEVELOPMENT.md#f3006-implementation), [실호출 근거](docs/verification/2026-11-19-task-plan.json)를 참고한다. F3006과 km 기본 입력 전환은 사용자 요청으로 작업별 커밋을 마쳤으며 다음 개발 작업은 F3007 Tool 호출·결과 통합이다.

### 추천·재계획 실행

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

현재는 **F3006 작업 계획과 km 기본 입력 전환을 구현·검증하고 작업별 커밋을 마친 단계**다. 다음 개발 작업은 F3007 Tool 호출·결과 통합이다.

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
- 현재 전체 테스트 215개·Ruff·의존성 검사 통과. F3006 시나리오 GMS 4회와 사전 실행 2회, km 전환 GMS 3회를 구분해 기록

아직 완료되지 않은 내용:

- 검색점 전략·후보 평가 범위의 고도화
- 같은 주유소·진행 방향임을 입증하는 휴게소 매칭 고도화
- 공개 데이터 재배포 범위를 확대할 때의 상세 이용허락 조건 재확인 및 공유 일일 한도 집계
- 전체 LangGraph 흐름, SQLite Repository·CRUD, UI 연동 구현

다음에 수행할 일과 완료 체크는 [`docs/TASKS.md`](docs/TASKS.md)의 날짜별 목록을 따른다. 기존 개발 TASK 이력과 상세 구현·검증 근거는 [`docs/DEVELOPMENT.md`](docs/DEVELOPMENT.md)에 보존한다.
