"""F3010 세션별 검증 조건·오류 상태·짧은 요약. 전체 Tool 응답은 보관하지 않는다."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from fuelroute.schemas.analysis import FIELD_LABELS, TripConditions


class SessionKey(BaseModel):
    """CLI/내부 API에서 전달하는 로컬 대화 식별자. 파일 경로나 SQL로 사용하지 않는다."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    session_id: str = Field(
        min_length=1,
        max_length=64,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]*$",
        description="같은 대화에 재사용할 영문·숫자·_·- ID, 최대64자; 예:trip-1",
    )


class ConversationMemory(SessionKey):
    """세션의 최신 상태 하나만 저장한다. 가격·원본 경로·인증·전체 대화는 포함하지 않는다."""

    schema_version: Literal[1] = 1
    revision: int = Field(default=0, ge=0, description="저장할 때1 증가하는 낙관적 동시성 버전")
    has_trip: bool = False
    conditions: TripConditions = Field(default_factory=TripConditions)
    range_input_mode: Literal["distance", "fuel"] = "distance"
    invalid_fields: list[str] = Field(default_factory=list, max_length=24)
    unverified_fields: list[str] = Field(default_factory=list, max_length=24)
    coordinate_sources: dict[str, Literal["user", "kakao_address", "kakao_keyword"]] = Field(
        default_factory=dict
    )
    location_notes: dict[str, str] = Field(
        default_factory=dict, description="두 위치의 지역 수준 안내"
    )
    pending_fields: list[str] = Field(default_factory=list, max_length=24)
    last_request_summary: str = Field(default="", max_length=1000)
    last_response_summary: str = Field(default="", max_length=2000)
    updated_at: datetime | None = None

    @field_validator("updated_at")
    @classmethod
    def aware_timestamp(cls, value: datetime | None) -> datetime | None:
        """저장 시각은 시간대가 있는 datetime 또는 미저장 상태 None만 허용한다.

        시간대를 추측·보정하지 않고 naive 값은ValueError다. HTTP·DB·추가 시각 생성은 없다.
        예:최초 revision0의 updated_at=None은 허용한다.
        """
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("메모리 갱신 시각에는 시간대가 필요합니다.")
        return value

    @field_validator("invalid_fields", "unverified_fields", "pending_fields")
    @classmethod
    def known_fields(cls, value: list[str]) -> list[str]:
        """저장된 보완 필드명이 알려진 입력인지 검사하고 중복을 제거한다.

        모르는 필드가 있으면 저장소 손상으로 ValueError다. 이 검사는 임의 필드/명령을
        실행하지 않으며 HTTP·DB·보정은 없다. 예:__class__는 조건명이 아니므로 거절한다.
        """
        if any(field not in FIELD_LABELS for field in value):
            raise ValueError("알 수 없는 대화 조건명입니다.")
        return list(dict.fromkeys(value))
