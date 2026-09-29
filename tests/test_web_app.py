"""Host-only checks that the Django report browser (web/) actually runs.
Not part of docker/test-files.txt: these invoke manage.py via subprocess and
fall back to sqlite (no DATABASE_URL), which the Docker test image does not
carry Django for."""

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _env():
    env = {
        k: v
        for k, v in os.environ.items()
        if not k.startswith(("DATABASE_URL", "DJANGO_", "QUANIFI_ADMIN_"))
    }
    env["DJANGO_SECRET_KEY"] = "test-only"
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return env


def test_manage_py_check():
    result = subprocess.run(
        [sys.executable, "manage.py", "check"],
        cwd=str(ROOT),
        env=_env(),
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_web_reports_suite_passes():
    result = subprocess.run(
        [sys.executable, "manage.py", "test", "web.reports", "--noinput"],
        cwd=str(ROOT),
        env=_env(),
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "OK" in result.stderr
