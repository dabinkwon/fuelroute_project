"""환경변수·로컬 .env 설정과 제공사별 필수값 검사. 비밀키는 SecretStr로 보관한다."""

from pathlib import Path
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """저장소 루트에서 .env를 읽고 OS 환경변수를 우선 적용한다.

    API별 키가 없어도 서버·오프라인 테스트를 실행할 수 있다. 실제 호출 직전
    missing()으로 필요한 항목만 검사한다. 키는 SecretStr로 보관하며 repr에 노출하지 않는다.
    """

    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", env_ignore_empty=True, extra="ignore"
    )

    gms_api_key: SecretStr | None = None
    gms_base_url: str | None = None
    gms_model: str | None = None
    opinet_api_key: SecretStr | None = None
    kakao_rest_api_key: SecretStr | None = None
    data_go_kr_service_key: SecretStr | None = None
    http_timeout_seconds: float = Field(default=20, gt=0, le=120, allow_inf_nan=False)
    database_path: Path = Field(
        default=Path("data/fuelroute.sqlite3"),
        description="로컬 SQLite 파일, 세션 기능 사용 시 생성",
    )

    @field_validator("database_path", mode="before")
    @classmethod
    def file_database(cls, value: object) -> object:
        """대화 맥락에는 스레드·프로세스 간 재사용할 실제 SQLite 파일 경로를 요구한다.

        기본값은data/fuelroute.sqlite3이며 설정만으로 파일을 생성하지 않는다. 빈 경로나
        :memory:는ValueError로 거절해 CLI에서CONFIG_ERROR로 안내한다. 다른 경로 검사는
        저장소의 파일 열기 단계가 담당한다. HTTP·DB·로그 부작용은 없다.
        """
        if str(value).strip() in {"", ":memory:"}:
            raise ValueError("SQLite 파일 경로를 설정하세요.")
        return value

    @field_validator(
        "gms_api_key",
        "opinet_api_key",
        "kakao_rest_api_key",
        "data_go_kr_service_key",
        "gms_base_url",
        "gms_model",
        mode="before",
    )
    @classmethod
    def strip_empty(cls, value: object) -> object:
        """환경변수 문자열의 공백을 제거하고 빈 값은 미설정(None)으로 바꾼다.

        입력은 설정 원본값이며 문자열 외 타입은 그대로 검증기로 넘긴다.
        외부 호출·저장·예외 변환은 없고, 예: '  ' → None이다.
        """
        return (value.strip() or None) if isinstance(value, str) else value

    @field_validator("gms_base_url")
    @classmethod
    def validate_gms_url(cls, value: str | None) -> str | None:
        """GMS 호출 주소가 공식 HTTPS 호스트를 향하는지 검증한다.

        콘솔의 전체 /chat/completions 주소를 입력한 경우 SDK가 붙일 말단 경로를
        제거해 API base로 정규화한다. 다른 경로는 보존하며 직접 제공사 호출·URL 내
        키·사용자 인증정보는 거절한다. 미설정은 None, 잘못된 값은 ValueError다.
        외부 호출·설정 파일 변경은 없다. 예: .../v1/chat/completions → .../v1.
        """
        if value is not None:
            url = urlsplit(value)
            if (
                url.scheme != "https"
                or url.hostname != "gms.ssafy.io"
                or url.username
                or url.password
                or url.query
                or url.fragment
                or url.port not in (None, 443)
            ):
                raise ValueError("GMS 콘솔의 HTTPS API 주소를 사용하세요.")
            endpoint = "/chat/completions"
            if url.path.rstrip("/").endswith(endpoint):
                value = value.rstrip("/")[: -len(endpoint)]
        return value

    def missing(self, provider: str) -> list[str]:
        """지정 제공사의 호출에 필요한 미설정 환경변수 이름만 반환한다.

        provider는 gms/opinet/kakao/kakao_local/rest_area 또는 local이다. kakao_local은
        같은 REST 키를 쓰지만 검색 권한·쿼터는 길찾기와 별도로 확인한다. 반환 목록이
        비어도 인증 성공을 뜻하지 않는다. 미지원 이름은 KeyError, 부작용은 없다.
        """
        required = {
            "local": (),
            "gms": ("gms_api_key", "gms_base_url", "gms_model"),
            "opinet": ("opinet_api_key",),
            "kakao": ("kakao_rest_api_key",),
            "kakao_local": ("kakao_rest_api_key",),
            "rest_area": ("data_go_kr_service_key",),
        }
        return [name.upper() for name in required[provider] if getattr(self, name) is None]

    def secret_values(self) -> list[str]:
        """외부 응답에서 제거할 설정된 인증키 목록을 내부 정제용으로 반환한다.

        호출자는 이 목록을 출력·로그·저장하지 않는다. 미설정 키는 제외하며
        SecretStr을 실제 문자열로 해제하는 유일한 용도는 요청 인증과 응답 정제다.
        """
        return [
            key.get_secret_value()
            for key in (
                self.gms_api_key,
                self.opinet_api_key,
                self.kakao_rest_api_key,
                self.data_go_kr_service_key,
            )
            if key is not None
        ]
