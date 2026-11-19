# Tool 실행 계약 — 2026-11-19 km 입력 반영

`ToolRegistry`는 아래 일곱 함수를 등록한다. 입력·출력의 전체 타입·단위·필수값·설명·실패 코드는 [TOOL_CONTRACTS.json](TOOL_CONTRACTS.json)이 기준이다. 다음 Bash 명령으로 같은 명세를 확인할 수 있다. HTTP·LLM 호출은 없다.

## 원본 PDF와 실행 책임 구분

2026-11-19 대조한 요구사항 PDF p.9·14는 외부 API Tool과 로컬 서버 API Tool을 구분하며, 모델의 판단이 필요 없는 작업은 실행 흐름이 직접 수행하도록 한다. 현재 경로·후보·상세·휴게소 조회 네 개는 외부 API Tool이고, 안전거리·비용 평가·정렬 세 개는 순수 계산의 검증용 등록이다. `provider=local`은 HTTP를 사용하지 않는다는 뜻이며 로컬 서버 API Tool을 구현했다는 뜻은 아니다.

전체 Agent를 연결할 때는 필요한 외부 조회 Tool만 모델에 노출하고 계산·제약 판정·정렬·계획 저장·로그 수집은 코드에서 직접 실행한다. 기존 일곱 개의 등록·독립 검증을 모델의 일곱 Tool 선택으로 자동 연결하지 않는다. 이 모델 노출 분리는 [2일차 TASKS](TASKS.md#plan-2026-11-19)의 미완료 작업이다. 로컬 서버 API Tool인 RAG·캘린더는 선택 심화에 해당한다.

## 명세와 독립 확인

안전거리의 기본 입력은 `remaining_range_km`와 `reserve_range_km`이며 `180 - 30 = 150km`로 계산한다. 연비를 요구하지 않고 L를 역산하지 않는다. `RangeConditions`의 `anyOf`는 km 두 필드 또는 기존 L/L/km/L 세 필드의 조건부 필수를 표현하며 런타임 검증도 수행한다. 안전 여유는 0 이상/주행 가능 거리 미만이고 임의 기본값은 없다. km와 L 연료량을 섞으면 `INVALID_INPUT`이다. km 방식에 선택 연비가 있어도 차감식은 바꾸지 않는다.

```bash
python -m fuelroute tool-schemas
```

| Tool | 주요 입력 | 성공 `data` / 다음 단계 |
|---|---|---|
| `calculate_safe_range` | 주행 가능 거리·안전 여유 km; 기존 L/연비 방식도 별도 지원 | `safe_range_km` → 검색 범위·도달 판정 |
| `get_driving_route` | WGS84 출발·목적 좌표, 선택 단일 경유 좌표 | `distance_m`, `duration_s`, `legs` → 기본·접근·경유 비용 평가 |
| `search_gas_stations` | WGS84 검색점, 유종, 반경 m | `items[].station_id`, 위치·가격 → 상세·경로 조회 |
| `get_station_details` | 확보한 Opinet ID, 유종 | ID·위치·가격·가격 기준시각 → 평가·설명 |
| `search_rest_area_info` | WGS84 위치, 반경 m, 선택 노선·방향 | 시설·기준일·`match_status=unverified` → 보조 설명 |
| `assess_candidate_routes` | 정규화한 기본·접근·단일 경유 비용, 안전거리·우회 상한 | 도달·추가 거리/시간, `eligible`, `reason_codes` → 추천 정렬 |
| `rank_candidates` | 평가 목록, 유종, 안전거리·우회 상한, 정렬 기준 | `recommended`, `ranked_candidates`, `excluded_candidates`, `scope` → 최종 설명 |

모든 등록 Tool은 `ok/tool_name/data/error/meta` 공통 JSON 객체를 반환한다. 원래 Python 계산 함수가 숫자나 모델을 반환하더라도 등록 Tool에서는 출력 스키마에 맞춰 `data`에 넣는다. 경로 Tool은 원본 상세 좌표를 LLM에 넘기지 않는다. 검색점 생성에 필요한 상세 좌표는 계획 실행기의 내부 데이터로 사용한다.

입력의 누락·추가 필드·범위 오류는 `INVALID_INPUT`, 성공 데이터의 타입·필수 필드·경로 거리 합 오류는 `INVALID_RESPONSE`다. 외부 조회에는 `CONFIG_ERROR`, `AUTH_FAILED`, `TIMEOUT`, `RATE_LIMITED`, `UPSTREAM_ERROR`도 있다. 후보/휴게소 0건은 `EMPTY_RESULT`, 상세 ID 없음은 `NOT_FOUND`, 경로 없음은 `NO_ROUTE`다. 가능한 코드는 각 Tool 명세의 `error_codes`에 있다. 재시도·재계획 여부는 실제 응답의 플래그와 실행 정책을 함께 확인한다. 현재 실행기는 동일 실패 요청을 자동 재시도하지 않는다.

후보 제외 사유는 Tool 호출 실패와 구분한다. 예를 들어 `UNREACHABLE`, `DETOUR_TIME_EXCEEDED`, `DETOUR_DISTANCE_EXCEEDED`, `MISSING_PRICE`는 정상 평가의 제외 근거다. 적합 후보가 없더라도 `rank_candidates`는 성공 데이터에 `recommended=null`과 제외 목록을 반환한다. 휴게소 보조정보 실패만으로 주유 후보를 제외하지 않는다.

LangChain `StructuredTool`에는 JSON schema와 상세 description을 전달하고 실행 wrapper가 Pydantic으로 같은 입력을 검사한다. `ainvoke(dict)`와 객체형 `ToolCall.args`를 사용한다. JSON 객체 대신 문자열을 넘기면 SDK가 wrapper 실행 전에 거절할 수 있다. `ToolMessage`를 받은 실행기는 본문의 `ok`를 확인해야 한다. 등록만으로 HTTP·LLM·DB 호출은 없고, `AsyncClient`의 종료는 호출자 책임이다. [LangChain 공식 Tool 문서](https://docs.langchain.com/oss/python/langchain/tools)

실제 응답 기반 추천 연결과 기존 비용의 로컬 재판정은 [추천 검증 기록](verification/2026-11-18-recommendation.json)에 구분했다. 재계획·캐시·오류 분기는 모의 응답으로 검증했으며 전체 LangGraph·HTTP API·화면 연결은 후속 단계다.
