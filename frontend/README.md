# FuelRoute 프론트엔드

카카오 앱 대표 도메인 등록을 위해 먼저 배포하는 정적 서비스 소개 페이지다. HTML/CSS로 동작하며 백엔드·API 인증키를 사용하지 않는다. 안전거리 수치는 `(15 - 3) * 12 = 144km`의 명시적인 계산 예시다. 실제 주유소 검색·지도·추천이 연결된 화면으로 표시하지 않는다.

## 소스와 배포

- 화면: `dist/index.html`, `dist/styles.css`
- Netlify 설정: 상위 `netlify.toml` (`base=frontend`, `publish=dist`)
- 기존 Sites 연결 이력: `.openai/hosting.json`
- 기존 Python 서버: 상위 `fuelroute/`
- 배포 방식: Netlify 정적 호스팅. `dist/`의 HTML/CSS만 공개한다.

미리 보기(저장소 루트에서 실행):

```powershell
.\.venv\Scripts\python.exe -m http.server 5173 --directory frontend/dist
```

`http://127.0.0.1:5173`으로 접속한다. `.env`나 Python 소스를 이 정적 폴더에 복사하지 않는다. 공개 페이지에는 비밀키를 넣지 않으며 향후 REST API 호출은 Backend에서 수행한다.

실제 배포 URL과 카카오 등록 기준은 상위 README에서 관리한다. `.openai/hosting.json`은 이전 Sites 배포 이력이며 Netlify의 사이트 ID와 혼용하지 않는다.

## Netlify 운영 배포

사용자 요청에 따라 Netlify에 실제 배포했다. Vercel 이전 준비는 취소하고 사용하지 않는 Vercel 설정 파일을 정리했다.

- 운영 주소: https://fuelroute-348.netlify.app
- 사이트 ID: `21ab4da2-503e-4dfe-bcc8-346b74f64a7d`
- 사이트 이름: `fuelroute-348`
- 운영 배포 ID: `6afd3677cf50d903c64b1785`
- 빌드 명령: 없음. CLI `--no-build` 사용
- 업로드 폴더: `frontend/dist`의 `index.html`, `styles.css`

canonical/og:url은 실제 운영 주소로 설정했다. 인증 없는 HTTPS 요청에서 HTML·CSS의 HTTP 200을 확인했다. HTML에는 Netlify의 기본 호스팅 주석·스크립트가 추가되며 본문과 CSS가 배포 소스와 일치하는지 확인했다. 카카오 앱 대표 도메인에는 새 운영 주소를 사용한다.

기존 로그인된 Netlify CLI를 사용해 저장소 루트의 PowerShell에서 재배포한다.

```powershell
$publishPath = (Resolve-Path frontend/dist).Path
netlify deploy --prod --no-build --dir $publishPath --site 21ab4da2-503e-4dfe-bcc8-346b74f64a7d
```

새 개발 환경에서는 먼저 `netlify login`이 필요하다. CLI의 로컬 연결 상태 `.netlify/`는 Git에서 제외하며, 위 사이트 ID로 기존 사이트를 재사용한다. 배포 대상 폴더만 전송하므로 `.env`, Python 소스, Sites 설정은 업로드하지 않는다.
