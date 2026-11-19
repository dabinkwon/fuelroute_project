"""계층 사이에 전달하는 명시적인 입력·출력 데이터 계약: coordinates."""

from pydantic import BaseModel, ConfigDict, Field


class Coordinate(BaseModel):
    """경도·위도 순서의 유한한 WGS84 좌표(도). KATEC 입력과 혼용 금지."""

    model_config = ConfigDict(allow_inf_nan=False, extra="forbid")
    longitude: float = Field(ge=-180, le=180, description="WGS84 경도(도)")
    latitude: float = Field(ge=-90, le=90, description="WGS84 위도(도)")

    def as_query(self) -> str:
        """Kakao 요청용 '경도,위도' 문자열을 반환한다. 외부 호출·저장은 없다.

        검증된 자기 좌표를 사용한다. 예: longitude=127, latitude=37 → '127.0,37.0'.
        좌표계 변환이나 주소 해석은 하지 않는다.
        """
        return f"{self.longitude},{self.latitude}"
