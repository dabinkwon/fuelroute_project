# FuelRoute 프론트엔드

F3018 대화·추천·계획 관리 대시보드다. 별도 빌드 없는 HTML/CSS/JavaScript이며 정적 파일은
`dist/`에 둔다. API 키는 사용하지 않고 모든 데이터 요청은 FastAPI Backend로 보낸다.

## 화면 구조

- 최상단 전체 너비: 자연어 주행 조건 입력, 추천 결과, 계획 날짜/선택 시각, 추천 채택·저장
- 두 번째 줄: 안전거리, 실제 도달거리, 추가 우회시간을 같은 너비의 3열로 표시
- 세 번째 줄: 넓은 주행 조건과 좁은 추천 균형 카드
- 저장한 플랜 모달: `/api/plans` 목록, revision 기반 일정·메모 수정, 선택 삭제
- 추천 근거 모달: 가격·거리·제약 근거를 현재 추천과 함께 표시
- 반응형: 데스크톱 6열 기반 단일 흐름, 태블릿 2열, 모바일 단일 열과 하단 메뉴

기본 입력은 계기판의 주행 가능 거리와 안전 여유(km)다. L 연료량이나 연비를 필수로 받지
않는다. 알 수 없는 가격·시간·비용은 만들지 않고 미확인/대시로 표시한다.
출발·종료 시각은 계획 정렬과 겹치는 일정 안내를 위한 선택값이며 추천 계산에는 사용하지
않는다. 메모도 선택값이고 최대 1000자 일반 텍스트로 저장한다.

## 로컬 실행

저장소 루트의 Git Bash 터미널 두 개에서 Backend와 정적 서버를 각각 실행한다.

```bash
source .venv/Scripts/activate
python -m uvicorn fuelroute.api.app:app --reload --host 127.0.0.1 --port 8000
```

```bash
source .venv/Scripts/activate
python -m http.server 5173 --bind 127.0.0.1 --directory frontend/dist
```

`http://127.0.0.1:5173`에 접속한다. 로컬 5173 화면은 `app.js`에서 8000 Backend를 선택하며,
FastAPI CORS는 `127.0.0.1:5173`과 `localhost:5173`을 허용한다.

## Backend 주소와 배포

운영 Backend가 별도 HTTPS 도메인에 배포되면 `dist/config.js`의
`window.FUELROUTE_API_BASE`에 origin만 설정한다. 키·인증 헤더·개별 Endpoint 경로는 넣지
않는다. 빈 문자열은 같은 origin을 뜻한다.

- 운영 프론트 주소: https://fuelroute-348.netlify.app
- Netlify 사이트 ID: `21ab4da2-503e-4dfe-bcc8-346b74f64a7d`
- 배포 대상: `frontend/dist`
- 빌드 명령: 없음

현재 운영 URL에는 이전 소개 화면이 배포돼 있다. 새 기능 UI는 공개 Backend URL을 설정하고
사용자 화면 확인·기능 검증을 마친 뒤 같은 Netlify 사이트에 재배포한다. `.env`, Python 소스,
로컬 DB는 정적 배포 대상에 포함하지 않는다.

```bash
netlify deploy --prod --no-build --dir frontend/dist --site 21ab4da2-503e-4dfe-bcc8-346b74f64a7d
```
