from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
PUBLIC_PATHS = [ROOT / "src" / "trading_core", ROOT / ".github", ROOT / "pyproject.toml"]

FORBIDDEN_MATERIAL = [
    re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}"),
    re.compile(r"github_pat_[A-Za-z0-9_]{20,}"),
    re.compile(r"AccountKey=[A-Za-z0-9+/=]{20,}", re.IGNORECASE),
    re.compile(r"SharedAccessSignature=", re.IGNORECASE),
    re.compile(r"DefaultEndpointsProtocol=", re.IGNORECASE),
    re.compile(r"https://[^\s\"']+\.azurewebsites\.net", re.IGNORECASE),
    re.compile(r"/subscriptions/[0-9a-fA-F-]{36}", re.IGNORECASE),
    re.compile(r"Bearer\s+[A-Za-z0-9._-]{20,}", re.IGNORECASE),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
]
CLOUD_IMPORT = re.compile(r"^\s*(?:from|import)\s+(?:azure|github)(?:\.|\s|$)", re.MULTILINE)


def public_files():
    for path in PUBLIC_PATHS:
        if path.is_file():
            yield path
        elif path.exists():
            yield from (candidate for candidate in path.rglob("*") if candidate.is_file())


def test_public_package_contains_no_known_secret_or_production_resource_material():
    violations = []
    for path in public_files():
        text = path.read_text(encoding="utf-8")
        for pattern in FORBIDDEN_MATERIAL:
            if pattern.search(text):
                violations.append(f"{path.relative_to(ROOT)}: {pattern.pattern}")
    assert violations == []


def test_core_has_no_cloud_sdk_imports():
    violations = []
    for path in (ROOT / "src" / "trading_core").rglob("*.py"):
        if CLOUD_IMPORT.search(path.read_text(encoding="utf-8")):
            violations.append(str(path.relative_to(ROOT)))
    assert violations == []
