import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PURE_MODULES = (ROOT / "src" / "contracts.py", ROOT / "src" / "formatting.py")
INTEGRATION_IMPORTS = {"dotenv", "httpx", "telethon"}


def _top_level_imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text())
    imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(alias.name.partition(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.add(node.module.partition(".")[0])
    return imports


def test_contract_and_formatting_modules_do_not_import_integration_clients() -> None:
    for module_path in PURE_MODULES:
        assert _top_level_imports(module_path).isdisjoint(INTEGRATION_IMPORTS)


def test_pure_modules_do_not_depend_on_runtime_entrypoint() -> None:
    for module_path in PURE_MODULES:
        assert "src.main" not in module_path.read_text()
