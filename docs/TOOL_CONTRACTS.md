# Tool 실행 계약 — 2026-11-19 km 입력 반영

`fuelroute/tools/registry.py`의 `ToolRegistry`는 아래 일곱 함수를 등록한다. 독립 조회는 `tools/fuel_tools.py`, 공유 입력·출력은 `schemas/`, 일반 코드 계산은 `calculations/`에서 구현한다. 입력·출력의 전체 타입·단위·필수값·설명·실패 코드는 [TOOL_CONTRACTS.json](TOOL_CONTRACTS.json)이 기준이다. 다음 Bash 명령으로 같은 명세를 확인할 수 있다. HTTP·LLM 호출은 없다.

## 원본 PDF와 실행 책임 구분

2026-11-19 대조한 요구사항 PDF p.9·14는 외부 API Tool과 로컬 서버 API Tool을 구분하며, 모델의 판단이 필요 없는 작업은 실행 흐름이 직접 수행하도록 한다. 현재 경로·후보·상세·휴게소 조회 네 개는 외부 API Tool이고, 안전거리·비용 평가·정렬 세 개는 순수 계산의 검증용 등록이다. `provider=local`은 HTTP를 사용하지 않는다는 뜻이며 로컬 서버 API Tool을 구현했다는 뜻은 아니다.

계획기에는 외부 조회 Tool 네 개의 설명·입력 스키마만 전달하고 F3007 실행기는 계산·제약 판정·정렬을 일반 코드로 직접 처리한다. 기존 일곱 개의 검증용 등록을 모델의 일곱 Tool 선택으로 연결하지 않는다. 저장·로그 수집은 후속 코드 작업이며 로컬 서버 API Tool인 RAG·캘린더는 선택 심화에 해당한다. [2일차 TASKS](TASKS.md#plan-2026-11-19)에서 전체 진행 상태를 관리한다.

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

실제 응답 기반 추천 연결과 기존 비용의 로컬 재판정은 [추천 검증 기록](verification/2026-11-18-recommendation.json)에 구분했다. 재계획·캐시·오류 분기는 모의 응답으로 검증했으며 F3009까지 조회·판정·재계획 그래프를 연결했고 조건 맥락은 F3010에서 연결했고 최종 문장·HTTP API·화면은 후속이다.

## F3007의 계획 실행 연결

현재 `run`은 조회 뒤 F3008 `judge`에서 제약 근거·확정 추천을 계산하고 `enrich`에서 보조정보를 연결한다. 이 두 단계는 모델에 노출하는 새 Tool이 아니며 기존 공개 Tool 스키마도 변경하지 않는다. 판정은 일반 코드, 보조 연결은 기존 `search_rest_area_info`를 사용한다. 직접 `execute_work_plan` 호출은 기존 한 회차 계약을 유지한다.

`python -m fuelroute run --message ...`는 분석·계획 뒤 조회 네 종류를 실제 호출한다. 기본 경로의 검색용 기하는 `ToolRegistry.route_context(include_path=True)`가 같은 HTTP 응답에서 비용과 분리해 실행기 안에 보관한다. 등록 경로 Tool의 공개 출력 스키마는 그대로 `DrivingRoute`이며 원본 경로를 모델/공개 결과로 보내지 않는다.

실행 결과의 `data.results`는 작업별 타입을 유지하며 `data.execution.calls`에 공개 입력과 공통 결과를 보관한다. 후보별 실패는 다른 후보와 격리하고 핵심 오류는 중단한다. 보조 조회 실패는 경고와 함께 추천을 유지한다. 호출 집계는 논리 Tool 시도 수이므로 실제 HTTP 요청 수와 구분한다. 한 회차 실행의 제약 판정·정렬은 기존 일반 코드 함수를 재사용하며 그래프 재계획은 아래 F3009 계약을 따르고 최종 설명·이벤트/저장·HTTP/화면은 후속이다. [F3007 실호출](verification/2026-11-19-tool-execution.json)을 참고한다.


## F3009의 재계획 연결

새 모델 Tool을 등록하지 않는다. 그래프는 기존 외부 조회 네 종류와 일반 코드 계산을 요청별 세션으로 재사용하며 추가 모델 호출 없이 최대2회·전체 평가 예산에서 종료한다. 공개 일곱 Tool의 직렬화 설명·스키마는 기존 JSON과 같다. 추가 검색은 미평가 후보가 없을 때 한 점만, 상세/경로는 이전에 평가하지 않은 ID만 진행한다. 같은 경로의 성공/실패는 캐시한다.

`data.replanning`은 회차·종료 사유·다음 입력 안내, `execution.calls/steps[].round_index`는 최초0/추가1~2를 반환한다. 기존 ToolResult 최상위 계약은 바꾸지 않는다. 보조 조회는 모든 판정/재계획 종료 뒤 확정 후보 한 곳에만 연결한다. 실제 상한 종료와 모의 성공 분기는 [검증 기록](DEVELOPMENT.md#f3009-replanning)에서 구분한다.


## F3010의 조건 맥락

Memory는 새 Tool이 아니며 기존 일곱 공개 Tool의 설명/스키마는 바꾸지 않는다. 세션에는 검증 조건과 짧은 요청/응답 요약만 저장하고 후보/상세/경로 Tool 응답이나 실행 캐시는 저장하지 않는다. 후속 run은 병합한 조건으로 현재 경로·가격을 조회한다. `analysis.field_sources`와 `data.session`으로 조건의 출처/세션 버전을 구분한다. 같은 요청 안의 F3009 캐시와 다른 요청 사이의 F3010 조건 맥락을 구분한다. [검증 기록](DEVELOPMENT.md#f3010-memory)을 따른다.
