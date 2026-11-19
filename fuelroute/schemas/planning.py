"""계층 사이에 전달하는 명시적인 입력·출력 데이터 계약: planning."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from fuelroute.schemas.requests import PlanningRequest

Action = Literal[
    "base_route",
    "safe_range",
    "search_points",
    "search_candidates",
    "station_details",
    "candidate_routes",
    "evaluate_constraints",
    "rank_candidates",
    "rest_area_info",
]


class ProposedStep(BaseModel):
    """모델이 고르는 작업 이름과 짧은 선택 이유. 인자·수치·코드 실행은 허용하지 않는다."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    action: Action = Field(
        description="허용된 작업 ID; 같은 작업을 중복하거나 새 작업을 만들지 않음"
    )
    reason: str = Field(min_length=1, max_length=200, description="이 작업이 필요한 이유 한 문장")


class PlanProposal(BaseModel):
    """GMS 계획 출력. 외부 호출과 순수 계산을 구분한 전체 작업 순서만 제안한다."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    reason: str = Field(min_length=1, max_length=300, description="전체 계획의 목적과 순서 요약")
    steps: list[ProposedStep] = Field(
        min_length=1,
        max_length=9,
        description="의존 작업을 앞에 배치한 아홉 작업; 실행은 하지 않음",
    )


class PlanStep(BaseModel):
    """실행기가 참조할 고정 작업 계약. 입력 참조 문자열은 설명용이며 eval하지 않는다."""

    model_config = ConfigDict(extra="forbid")
    action: Action = Field(description="한 계획 안에서 유일한 작업 ID")
    reason: str = Field(description="모델의 짧은 작업 선택 이유; 실행 지시로 해석하지 않음")
    kind: Literal["external_tool", "local"] = Field(description="외부 조회 또는 일반 코드 작업")
    tool_name: str | None = Field(description="등록된 외부 조회 Tool 이름; 일반 코드는 null")
    depends_on: list[Action] = Field(description="성공 결과가 먼저 필요한 작업 ID")
    inputs_from: list[str] = Field(description="검증된 request 또는 앞선 작업 결과의 논리적 출처")
    run_when: Literal[
        "once", "per_search_point", "per_candidate", "per_priced_candidate", "if_recommended"
    ] = Field(description="반복·조건부 실행 범위; 실제 반복에는 request의 후보/검색점 상한 적용")
    failure_policy: Literal["stop", "replan_if_empty", "exclude_candidate", "optional"] = Field(
        description="중단·0건 재계획 판단·후보 제외·보조정보 미확인; 핵심 인증/설정/한도는 중단"
    )


class WorkPlan(BaseModel):
    """Agent의 실행 전 작업계획. 저장할 주유 계획(F3015)이나 실행 성공 결과와 다르다."""

    model_config = ConfigDict(extra="forbid")
    status: Literal["ready", "needs_user_input", "not_required"] = Field(
        description="작업 순서 준비, 입력 보완 필요, 추천 계획 불필요"
    )
    reason: str = Field(description="계획 생성 또는 생략의 짧은 이유")
    request: PlanningRequest | None = Field(
        description="사용자 값과 기존 실행 정책으로 만든 요청; 계획이 준비되지 않았으면 null"
    )
    steps: list[PlanStep] = Field(description="검증된 작업 순서; 보류/생략이면 빈 목록")
    warnings: list[str] = Field(
        description="위치 수준·현재 데이터·미판정 시간 조건 등 해석상 주의점"
    )
