#!/usr/bin/env python3
"""Log-only readiness monitor for the Quanifi quickstart NiFi container.

Contract:
  - It reads only ``nifi-app.log`` records written *after this start* (via
    ``tools/nifi_ready.py``'s log primitives). It never calls the NiFi REST
    API: querying the API while the Flow Controller is still starting is
    exactly the py4j startup wedge this monitor exists to detect instead of
    trigger.
  - It writes a status file (``watch``/``reset``); the Docker ``HEALTHCHECK``
    only reads that file (``check``), never the log or the API directly.
  - ``check`` exits 0 iff the status file's state is ``ready``.

Stdlib only. Python 3.11 (the base image's ``/usr/bin/python3``).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

# ``nifi_ready`` lives at ``tools/nifi_ready.py`` in the repo, and is copied to
# the same directory as this file in the image (``/opt/quanifi/bin``).
try:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import nifi_ready
except ImportError:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))
    import nifi_ready

LOADED = nifi_ready.LOADED
BROKEN = nifi_ready.BROKEN
FC_START = re.compile(r"Starting Flow Controller")
APP_STARTED = re.compile(r"Started Application in")
PROGRESS = re.compile(
    r"Installing dependencies|Successfully installed requirements|"
    r"Creating Python Virtual Environment|"
    r"Successfully created Python Virtual Environment|"
    r"Launching Python Process"
)

DEFAULT_STATUS_PATH = "/tmp/quanifi/status.json"


@dataclass
class Progress:
    expected: int
    loaded: int = 0
    broken: list = field(default_factory=list)
    fc_started_at: Optional[float] = None
    app_started_at: Optional[float] = None
    last_progress_at: Optional[float] = None
    completed_at: Optional[float] = None


def consume(progress: Progress, lines, now: float) -> None:
    """Fold new log lines into ``progress``, in place."""
    for line in lines:
        if BROKEN.search(line):
            progress.broken.append(line.strip()[:300])
            continue
        if LOADED.search(line):
            progress.loaded += 1
            progress.last_progress_at = now
            if progress.loaded == progress.expected and progress.completed_at is None:
                progress.completed_at = now
            continue
        if PROGRESS.search(line):
            progress.last_progress_at = now
            continue
        if FC_START.search(line):
            progress.fc_started_at = progress.fc_started_at or now
            continue
        if APP_STARTED.search(line):
            progress.app_started_at = progress.app_started_at or now
            continue


@dataclass
class Config:
    stall_seconds: float
    quiet_seconds: float
    startup_timeout_seconds: float
    missing_types: list = field(default_factory=list)


def decide(
    progress: Progress,
    now: float,
    started_at: float,
    cfg: Config,
    installer_running: bool = False,
):
    """Return (state, message). ``state`` is one of ready/starting/stalled/
    broken/timeout."""
    if installer_running:
        progress.last_progress_at = now

    if progress.broken:
        return "broken", "Python import failure in this start: {}".format(
            progress.broken[0]
        )
    if cfg.missing_types:
        return "broken", (
            "canvas uses processor types not baked into this image: {}".format(
                ", ".join(cfg.missing_types)
            )
        )
    if progress.loaded >= progress.expected and progress.app_started_at is not None:
        anchor = max(
            progress.completed_at or progress.app_started_at, progress.app_started_at
        )
        if now - anchor >= cfg.quiet_seconds:
            return "ready", "all {} Python processors loaded".format(progress.expected)
        return "starting", "{}/{} Python processors loaded".format(
            progress.loaded, progress.expected
        )
    if (
        progress.fc_started_at is not None
        and progress.loaded < progress.expected
        and now - max(progress.fc_started_at, progress.last_progress_at or 0)
        >= cfg.stall_seconds
    ):
        return "stalled", (
            "no Python processor loaded for {}s after 'Starting Flow Controller' "
            "({}/{}): the py4j startup wedge".format(
                cfg.stall_seconds, progress.loaded, progress.expected
            )
        )
    if now - started_at >= cfg.startup_timeout_seconds:
        return "timeout", "NiFi did not finish starting within {}s".format(
            cfg.startup_timeout_seconds
        )
    return "starting", "{}/{} Python processors loaded".format(
        progress.loaded, progress.expected
    )


def installer_running() -> bool:
    """True if a ``pip install`` process is currently running (a first-use
    dependency install with ``QUANIFI_PREBAKE=false`` counts as progress, not
    silence)."""
    proc_dir = "/proc"
    if not os.path.isdir(proc_dir):
        return False
    for entry in os.listdir(proc_dir):
        if not entry.isdigit():
            continue
        try:
            with open(os.path.join(proc_dir, entry, "cmdline"), "rb") as handle:
                cmdline = handle.read().replace(b"\x00", b" ")
        except OSError:
            continue
        if b"pip install" in cmdline:
            return True
    return False


def auto_resume_enabled(props_path) -> bool:
    """Parse ``nifi.flowcontroller.autoResumeState`` from ``nifi.properties``.
    Defaults to True (NiFi's own default) when the file or key is absent."""
    try:
        text = Path(props_path).read_text(encoding="utf-8")
    except OSError:
        return True
    for line in text.splitlines():
        if line.strip().startswith("nifi.flowcontroller.autoResumeState="):
            value = line.split("=", 1)[1].strip()
            return value.lower() == "true"
    return True


def recovery_allowed(auto_recover: bool, auto_resume: bool, used: int, limit: int):
    if not auto_recover:
        return False, "QUANIFI_AUTO_RECOVER is off"
    if auto_resume:
        return False, "autoResumeState=true: a restart would resume RUNNING components"
    if used >= limit:
        return False, "recovery limit reached ({}/{})".format(used, limit)
    return True, ""


def write_status(path, **fields) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(fields, indent=2) + "\n")
    os.replace(tmp, path)


def kill_nifi() -> None:
    subprocess.run(["pkill", "-KILL", "-f", "org.apache.nifi"], check=False)


def _python_types_in_flow(flow_path):
    import gzip

    with gzip.open(flow_path, "rt", encoding="utf-8") as handle:
        root = json.load(handle)["rootGroup"]

    types = set()

    def walk(group):
        for proc in group.get("processors", []):
            if proc.get("bundle", {}).get("artifact") == "python-extensions":
                types.add(proc["type"])
        for child in group.get("processGroups", []):
            walk(child)

    walk(root)
    return types


def _missing_types(flow_path, manifest_path):
    if not flow_path or not os.path.exists(flow_path):
        return []
    if not manifest_path or not os.path.exists(manifest_path):
        return []
    try:
        canvas_types = _python_types_in_flow(flow_path)
        manifest = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
        manifest_types = {p["type"] for p in manifest.get("processors", [])}
    except (OSError, ValueError, KeyError):
        return []
    return sorted(canvas_types - manifest_types)


def watch(
    args,
    *,
    clock=time.time,
    sleep=time.sleep,
    kill=kill_nifi,
    installer=installer_running,
) -> int:
    expected = 0
    if args.flow and os.path.exists(args.flow):
        expected = nifi_ready.python_processor_count(args.flow)

    missing = _missing_types(args.flow, args.manifest)
    cfg = Config(
        stall_seconds=float(os.environ.get("QUANIFI_STALL_SECONDS", 240)),
        quiet_seconds=float(os.environ.get("QUANIFI_QUIET_SECONDS", 20)),
        startup_timeout_seconds=float(
            os.environ.get("QUANIFI_STARTUP_TIMEOUT_SECONDS", 1200)
        ),
        missing_types=missing,
    )
    poll = float(os.environ.get("QUANIFI_POLL_SECONDS", 2))

    progress = Progress(expected=expected)
    inode = int(args.start_inode)
    offset = int(args.start_offset)
    started_at = clock()
    last_state = None
    last_loaded = None

    while True:
        now = clock()
        lines, inode, offset = nifi_ready.read_new_log(args.log, inode, offset)
        consume(progress, lines, now)
        state, message = decide(
            progress, now, started_at, cfg, installer_running=installer()
        )

        if state != last_state or progress.loaded != last_loaded:
            print("[quanifi] {}: {}".format(state, message))
            last_state, last_loaded = state, progress.loaded

        write_status(
            args.status,
            state=state,
            message=message,
            expected=expected,
            loaded=progress.loaded,
            recoveries=_read_recoveries(args.recoveries),
            started_at=started_at,
            updated_at=now,
        )

        if state == "ready":
            _write_recoveries(args.recoveries, 0)
            port = os.environ.get("QUANIFI_NIFI_PORT", "8443")
            user = os.environ.get("SINGLE_USER_CREDENTIALS_USERNAME", "see logs")
            print(
                "[quanifi] READY: open https://localhost:{}/nifi (user {})".format(
                    port, user
                )
            )
            return 0

        if state in ("broken", "timeout"):
            print(
                "[quanifi] {}; restart does not help; see "
                "docs/guides/DOCKER_QUICKSTART.md#troubleshooting".format(message)
            )
            return 1

        if state == "stalled":
            used = _read_recoveries(args.recoveries)
            limit = int(os.environ.get("QUANIFI_MAX_RECOVERIES", 3))
            allowed, reason = recovery_allowed(
                os.environ.get("QUANIFI_AUTO_RECOVER", "true").lower() == "true",
                auto_resume_enabled(args.props),
                used,
                limit,
            )
            if allowed:
                _write_recoveries(args.recoveries, used + 1)
                write_status(
                    args.status,
                    state="recovering",
                    message=message,
                    expected=expected,
                    loaded=progress.loaded,
                    recoveries=used + 1,
                    started_at=started_at,
                    updated_at=now,
                )
                print(
                    "[quanifi] WEDGED ({}); recovery {}/{}: stopping NiFi so the "
                    "container restarts. Nothing will resume: the canvas comes "
                    "back STOPPED.".format(message, used + 1, limit)
                )
                kill()
                return 2
            print(
                "[quanifi] WEDGED ({}); automatic recovery skipped: {}. "
                "Run: docker compose restart nifi".format(message, reason)
            )
            return 1

        sleep(poll)


def _read_recoveries(path):
    try:
        return int(Path(path).read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return 0


def _write_recoveries(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(str(value))


def check(args) -> int:
    try:
        data = json.loads(Path(args.status).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        print("unreadable status file: {}".format(args.status))
        return 1
    print("{}: {}".format(data.get("state"), data.get("message")))
    return 0 if data.get("state") == "ready" else 1


def reset(args) -> None:
    write_status(args.status, state="starting", message="entrypoint started")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="quanifi_monitor")
    sub = parser.add_subparsers(dest="command", required=True)

    p_watch = sub.add_parser("watch")
    p_watch.add_argument("--log", required=True)
    p_watch.add_argument("--flow", default="")
    p_watch.add_argument("--props", default="")
    p_watch.add_argument("--manifest", default="")
    p_watch.add_argument("--status", default=DEFAULT_STATUS_PATH)
    p_watch.add_argument("--recoveries", default="/tmp/quanifi/recoveries")
    p_watch.add_argument("--start-inode", default="0")
    p_watch.add_argument("--start-offset", default="0")

    p_check = sub.add_parser("check")
    p_check.add_argument("--status", default=DEFAULT_STATUS_PATH)

    p_reset = sub.add_parser("reset")
    p_reset.add_argument("--status", default=DEFAULT_STATUS_PATH)

    args = parser.parse_args(argv)
    if args.command == "watch":
        return watch(args)
    if args.command == "check":
        return check(args)
    if args.command == "reset":
        reset(args)
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
