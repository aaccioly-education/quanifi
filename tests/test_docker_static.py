"""Static checks on the Docker quickstart's repo-level files: the moved web
Dockerfile, the .dockerignore allowlist shape, compose.yaml's service
contract, and that no personal email leaked into a Docker-facing file.
Host-only (not in docker/test-files.txt): uses PyYAML."""

import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]

# Files explicitly allowed into the Docker build context.
EXPECTED_ALLOW_LINES = [
    "docker/",
    "nifi_extensions/*.py",
    "tools/_harness.py",
    "tools/build_grover_examples.py",
    "tools/nifi_ready.py",
    "tools/quickstart_smoke.py",
    "tests/*.py",
    "demo/",
    "web/",
    "manage.py",
    "pyproject.toml",
    "uv.lock",
]


def test_root_dockerfile_moved():
    assert not (ROOT / "Dockerfile").exists()
    web_dockerfile = ROOT / "docker/web/Dockerfile"
    assert web_dockerfile.exists()
    content = web_dockerfile.read_text()
    assert "COPY manage.py" in content
    assert (ROOT / "manage.py").exists()


def test_dockerignore_is_an_allowlist():
    lines = (ROOT / ".dockerignore").read_text().splitlines()
    effective = [ln for ln in lines if ln.strip() and not ln.strip().startswith("#")]

    assert effective[0] == "*"

    allow_lines = [ln[1:] for ln in effective if ln.startswith("!")]
    allowed_set = set(EXPECTED_ALLOW_LINES) | {"LICENSE", "NOTICE"}
    assert set(allow_lines) <= allowed_set, set(allow_lines) - allowed_set
    for expected in EXPECTED_ALLOW_LINES:
        assert expected in allow_lines, expected

    last_allow_index = max(i for i, ln in enumerate(effective) if ln.startswith("!"))
    redeny_lines = [ln for ln in effective if ln.startswith("**/")]
    assert redeny_lines, "expected at least one secret-shaped re-deny pattern"
    for ln in redeny_lines:
        assert effective.index(ln) > last_allow_index, ln


def test_compose_services():
    data = yaml.safe_load((ROOT / "compose.yaml").read_text())
    services = data["services"]

    for name, svc in services.items():
        assert "container_name" not in svc, name
        if name == "db":
            assert "image" in svc
        else:
            assert "image" not in svc, name

    assert "profiles" not in services["nifi"]
    assert services["test"]["profiles"] == ["test"]
    assert services["db"]["profiles"] == ["web"]
    assert services["web"]["profiles"] == ["web"]

    port_re = re.compile(r"^127\.0\.0\.1:\$\{QUANIFI_[A-Z_]+:-\d+\}:\d+$")
    saw_a_port = False
    for name, svc in services.items():
        for port in svc.get("ports", []):
            saw_a_port = True
            assert port_re.match(str(port)), "{}: bad port {!r}".format(name, port)
    assert saw_a_port

    assert "NIFI_WEB_PROXY_HOST" in services["nifi"]["environment"]
    assert services["nifi"]["restart"] == "on-failure:5"


def test_no_personal_email():
    for rel in (
        "compose.yaml",
        "web/env.example",
        "web/config/settings.py",
        "web/reports/tests.py",
    ):
        text = (ROOT / rel).read_text()
        assert "@gmail.com" not in text, rel


def test_test_files_exist():
    lines = (ROOT / "docker/test-files.txt").read_text().splitlines()
    found_any = False
    for raw in lines:
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        found_any = True
        assert (ROOT / line).exists(), line
    assert found_any
