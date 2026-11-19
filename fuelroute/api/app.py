"""FastAPI 서버 진입점. 현재 GET /health를 제공하며 추천·저장 HTTP 연결은 후속 작업이다."""

from fastapi import FastAPI

app = FastAPI(title="FuelRoute Agent", version="0.1.0")


@app.get("/health")
def health() -> dict[str, str]:
    """프로세스 실행 여부를 확인하는 정적인 상태 응답을 반환한다.

    인자·API 키·외부 호출·DB 의존성이 없다. 예: GET /health → status=ok.
    외부 API 인증이나 추천 기능 준비 여부를 보장하는 응답은 아니다.
    """
    return {"status": "ok", "stage": "api-verification"}
