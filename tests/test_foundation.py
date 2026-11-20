"""키 없이 실행하는 설정·안전거리·공통 계약·서버 검증."""

import json
from datetime import datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from fuelroute.api.app import app
from fuelroute.calculations.driving_range import calculate_safe_range
from fuelroute.cli.commands import main
from fuelroute.cli.gms_probe import safe_range_tool
from fuelroute.core.config import Settings
from fuelroute.core.contracts import ToolError, ToolMeta, ToolResult, utc_now

SECRET_ENV_NAMES = {
    "GMS_API_KEY",
    "OPINET_API_KEY",
    "KAKAO_REST_API_KEY",
    "DATA_GO_KR_SERVICE_KEY",
}


def test_secret_files_and_public_env_template_stay_safe() -> None:
    """NF3001의 로컬 비밀 파일 제외와 공개 설정 예시의 빈 키 값을 회귀 검증한다.

    저장소 루트의 `.gitignore`와 `.env.example`을 읽으며 실제 `.env`나 환경변수의 키 값은
    열지 않는다. 네 API 키 항목은 예시 파일에 존재하되 값이 비어 있어야 하고, `.env` 변형과
    SQLite·브라우저/배포 로컬 산출물은 Git 제외 규칙에 포함되어야 한다. 조건 위반은 assertion
    실패로 반환하며 파일·Git·네트워크를 변경하지 않는다. 예: `OPINET_API_KEY=`는 허용하지만
    `OPINET_API_KEY=sample`은 거절한다.
    """
    root = Path(__file__).resolve().parents[1]
    ignored = {
        line.strip()
        for line in (root / ".gitignore").read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }
    assert {".env", ".env.*", "*.sqlite*", "artifacts/", ".netlify/"} <= ignored
    assert "!.env.example" in ignored

    assignments = {}
    for line in (root / ".env.example").read_text(encoding="utf-8").splitlines():
        if line and not line.startswith("#") and "=" in line:
            name, value = line.split("=", 1)
            assignments[name] = value
    assert SECRET_ENV_NAMES <= assignments.keys()
    assert all(assignments[name] == "" for name in SECRET_ENV_NAMES)


def test_settings_serialization_masks_every_configured_api_key() -> None:
    """NF3001의 네 제공사 키가 설정 객체 repr·JSON 직렬화에 평문으로 나오지 않게 한다.

    테스트 전용 고유 문자열을 GMS·Opinet·Kakao·공공데이터 키로 전달한 뒤 Settings의 repr과
    `model_dump_json()`을 검사한다. 외부 호출·환경변수 변경·파일 저장은 없으며 SecretStr의
    마스킹 표현 자체에는 의존하지 않는다. 어느 한 키라도 평문으로 포함되면 assertion으로
    실패한다.
    """
    secrets = {name: f"nf3001-{name.lower()}-secret" for name in SECRET_ENV_NAMES}
    settings = Settings(_env_file=None, **{name.lower(): value for name, value in secrets.items()})
    serialized = f"{settings!r}\n{settings.model_dump_json()}"
    assert all(value not in serialized for value in secrets.values())


def test_env_precedence_and_no_secret_output(tmp_path, monkeypatch, capsys) -> None:
    """임시 .env와 OS 값의 우선순위·doctor 미노출을 검사한다. 실제 키·HTTP는 없다."""
    env = tmp_path / ".env"
    env.write_text("GMS_API_KEY=file-secret\nOPINET_API_KEY=\n", encoding="utf-8")
    monkeypatch.setenv("GMS_API_KEY", "os-secret")
    settings = Settings(_env_file=env)
    assert settings.gms_api_key.get_secret_value() == "os-secret"
    assert settings.missing("opinet") == ["OPINET_API_KEY"]
    assert "os-secret" not in repr(settings)
    monkeypatch.chdir(tmp_path)
    assert main(["doctor"]) == 0
    output = capsys.readouterr().out
    assert "secret" not in output
    assert json.loads(output)["data"]["providers"]["gms"]["configured"] is False


@pytest.mark.parametrize(
    "url",
    [
        "https://api.openai.com/v1",
        "http://gms.ssafy.io/v1",
        "https://gms.ssafy.io.evil.test/v1",
        "https://key@gms.ssafy.io/v1",
        "https://gms.ssafy.io/v1?key=secret",
    ],
)
def test_reject_direct_or_unsafe_gms_url(url: str) -> None:
    """직접 제공사 호출과 키가 포함된 URL을 설정에서 거절한다. 부작용은 없다."""
    with pytest.raises(ValidationError):
        Settings(_env_file=None, gms_base_url=url)


def test_safe_range_and_registered_tool_schema() -> None:
    """km와 기존 L 입력의 계산 및 실제 Tool의 단위 설명·조건부 필수 스키마를 확인한다."""
    assert calculate_safe_range(15, 3, 12) == 144
    tool = safe_range_tool()
    schema = tool.args_schema.model_json_schema()
    assert "안전 여유" in tool.description
    assert all(field.get("description") for field in schema["properties"].values())
    assert schema["anyOf"][0]["required"] == ["remaining_range_km", "reserve_range_km"]
    assert tool.invoke({"remaining_range_km": 180, "reserve_range_km": 30}) == 150
    assert (
        tool.invoke({"current_fuel_l": 15, "reserve_fuel_l": 3, "avg_efficiency_kmpl": 12}) == 144
    )


def test_gms_full_endpoint_is_normalized_to_api_base() -> None:
    """콘솔의 전체 호출 주소를 넣어도 SDK가 경로를 중복하지 않게 한다. HTTP 호출은 없다."""
    settings = Settings(
        _env_file=None,
        gms_base_url="https://gms.ssafy.io/gmsapi/api.openai.com/v1/chat/completions/",
    )
    assert settings.gms_base_url == "https://gms.ssafy.io/gmsapi/api.openai.com/v1"


@pytest.mark.parametrize(
    "values",
    [
        (3, 3, 12),
        (3, 4, 12),
        (0, 0, 12),
        (15, -1, 12),
        (15, 3, 0),
        (float("nan"), 3, 12),
        (15, 3, float("inf")),
        (1e308, 0, 1e308),
    ],
)
def test_reject_invalid_fuel_values(values: tuple[float, float, float]) -> None:
    """안전 잔량 침범·음수·0·비유한 입력을 보정하지 않고 거절하는지 검증한다."""
    with pytest.raises(ValueError):
        calculate_safe_range(*values)


def test_result_rejects_ambiguous_outcomes_and_naive_time() -> None:
    """실패 데이터 혼합·성공 null·시간대 없는 기록을 거절한다. 외부 호출은 없다."""
    meta = ToolMeta(provider="test", request_id="id", elapsed_ms=0, fetched_at=utc_now())
    error = ToolError(code="EMPTY_RESULT", message="없음")
    for ok, data, failure in ((True, None, None), (False, {}, error), (True, {}, error)):
        with pytest.raises(ValidationError):
            ToolResult(ok=ok, tool_name="test", data=data, error=failure, meta=meta)
    with pytest.raises(ValidationError):
        ToolResult(
            ok=True,
            tool_name="test",
            data={},
            error=None,
            meta=meta.model_copy(update={"fetched_at": datetime(2026, 11, 18)}),
        )


def test_health_is_available_without_keys() -> None:
    """API 키가 없어도 헬스 체크가 동작하고 인증 상태를 과장하지 않는지 검증한다."""
    with TestClient(app) as client:
        response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "stage": "api-verification"}


def test_cli_missing_keys_and_invalid_input(monkeypatch, tmp_path, capsys) -> None:
    """키 없음·잘못된 좌표는 JSON 실패/종료1로 출력하고 네트워크를 사용하지 않는다."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("OPINET_API_KEY", raising=False)
    assert main(["opinet-detail", "--station-id", "A0010207"]) == 1
    assert json.loads(capsys.readouterr().out)["error"]["code"] == "CONFIG_ERROR"
    assert main(["route", "--origin", "500", "37", "--destination", "127", "37"]) == 1
    assert json.loads(capsys.readouterr().out)["error"]["code"] == "INVALID_INPUT"
