"use strict";

// 운영 Backend가 별도 도메인에 배포되면 아래 빈 문자열만 HTTPS origin으로 바꾼다.
// 인증키나 Endpoint 경로는 이 공개 파일에 넣지 않는다.
window.FUELROUTE_API_BASE = window.FUELROUTE_API_BASE || "";
