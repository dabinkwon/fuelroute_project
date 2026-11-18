"""LLM에 원본 상세 좌표 전체를 넘기지 않는 경로 비용 계약."""

from pydantic import BaseModel, ConfigDict, Field, model_validator


class RouteLeg(BaseModel):
    """한 자동차 경로 구간의 거리(m)와 예상 소요시간(초)."""

    model_config = ConfigDict(extra="forbid")
    distance_m: int = Field(ge=0, strict=True, description="자동차 경로 거리(m)")
    duration_s: int = Field(ge=0, strict=True, description="자동차 경로 예상 시간(초)")


class DrivingRoute(RouteLeg):
    """원본 제공사 구조를 제거한 전체 경로와 경유 전후 구간 비용."""

    legs: list[RouteLeg] = Field(min_length=1, description="순서가 보존된 경로 구간 비용")

    @model_validator(mode="after")
    def validate_distance_sum(self) -> "DrivingRoute":
        """구간 거리 합이 전체 경로 거리와 같은지 검증한다. 외부 호출·보정은 없다.

        불일치 시 ValueError를 발생시켜 잘못된 접근·우회 계산을 방지한다.
        예: 전체 1000m에 구간 합이 900m인 응답은 사용하지 않는다.
        """
        if sum(leg.distance_m for leg in self.legs) != self.distance_m:
            raise ValueError("구간 거리 합이 전체 경로 거리와 다릅니다.")
        return self

    @classmethod
    def from_probe_data(cls, data: dict) -> "DrivingRoute":
        """probe_route의 성공 data에서 필요한 거리·시간·구간만 정규화한다.

        원본 요약과 평면 비용이 일치하고 첫 경로가 정상이어야 한다. 누락·타입·합계
        오류는 ValueError/ValidationError다. 0m 접근도 허용하며 좌표·키·원본 메시지는
        반환하지 않는다. HTTP·DB 부작용은 없다. 반환값은 후보 경로 비용 평가에 전달한다.
        """
        try:
            route = data["response"]["routes"][0]
            summary = route["summary"]
            if (
                route.get("result_code") != 0
                or summary["distance"] != data["distance_m"]
                or summary["duration"] != data["duration_s"]
            ):
                raise ValueError("경로 결과·요약 불일치")
            return cls(
                distance_m=data["distance_m"],
                duration_s=data["duration_s"],
                legs=[
                    RouteLeg(distance_m=part["distance"], duration_s=part["duration"])
                    for part in route["sections"]
                ],
            )
        except (KeyError, IndexError, TypeError, AttributeError) as exc:
            raise ValueError("경로 비용의 필수 구조를 확인하세요.") from exc
