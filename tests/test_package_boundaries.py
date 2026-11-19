"""공통 계약·계산이 상위 실행 계층이나 통신 구현에 의존하지 않는지 검사한다."""

import ast
from pathlib import Path

import pytest


@pytest.mark.parametrize(
    ("package", "allowed"),
    [
        ("core", {"core"}),
        ("schemas", {"core", "schemas"}),
        ("calculations", {"core", "schemas", "calculations"}),
        ("clients", {"core", "schemas", "clients"}),
        ("storage", {"core", "schemas", "storage"}),
    ],
)
def test_lower_layers_keep_dependency_direction(package: str, allowed: set[str]) -> None:
    """하위 패키지의 중첩 import까지 읽어 상위 실행·조회 계층과의 결합을 막는다.

    package는 core/schemas/calculations/clients/storage이고 allowed는 허용한 내부 패키지다.
    저장소 소스를 AST로 읽으며 함수를 실행하거나 API·DB를 호출하지 않는다. 상대
    import는 의존성 검사를 우회할 수 있어 거절한다. core/스키마/순수 계산에서 HTTP·
    LLM SDK 직접 import도 금지한다. 위반 시 파일·모듈명이 포함된 assertion이 발생한다.
    예: calculations가 agent.execution을 import하면 실패한다. 동적 import는 검사하지
    않으며 일반 실행 동작은 기존 기능 테스트가 담당한다. 반환·쓰기 부작용은 없다.
    """
    root = Path(__file__).resolve().parents[1] / "fuelroute" / package
    network_modules = {"httpx", "httpx2", "requests", "openai", "langchain_openai", "urllib"}
    for path in root.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                assert node.level == 0, f"{path}: 상대 import 대신 명시적인 절대 경로 사용"
                modules = [node.module or ""]
            elif isinstance(node, ast.Import):
                modules = [alias.name for alias in node.names]
            else:
                continue
            for module in modules:
                parts = module.split(".")
                if parts[0] == "fuelroute":
                    assert len(parts) >= 3 and parts[1] in allowed, (path, module)
                if package in {"core", "schemas", "calculations", "storage"}:
                    # URL 구문 분석/인코딩은 통신이 아니므로 공통 설정·정제에서 허용한다.
                    if module == "urllib.parse":
                        continue
                    assert parts[0] not in network_modules, (path, module)
