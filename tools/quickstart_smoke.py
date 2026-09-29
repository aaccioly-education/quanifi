#!/usr/bin/env python3
"""Host-side smoke check for the Quanifi quickstart demo.

Run this only *after* `docker compose ps` (or the HEALTHCHECK) reports the
`nifi` service as healthy: it is the one tool in this project that is allowed
to call the NiFi REST API during/after startup, because by then the Flow
Controller has already finished starting (the log-only monitor established
that; see docker/nifi/quanifi_monitor.py).

It checks that the quickstart group is present, stopped and loaded (every
Python processor valid). Optionally (without --check-only) it also starts the
group, waits for the verdict JSON written by the canvas's PutFile step, and
asserts the 3x3 Grover matrix: every cell must read |110>, and
QuantumConsensusOracle must vote PASS with low disagreement.

Stdlib only (+ nifi_ready + build_grover_examples's constants), so it runs
under the macOS system /usr/bin/python3 (3.9) with no venv.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

_TOOLS_DIR = Path(__file__).resolve().parent
if str(_TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(_TOOLS_DIR))

import nifi_ready  # noqa: E402
import build_grover_examples as grover  # noqa: E402

ROOT = _TOOLS_DIR.parent


def evaluate_result(doc, expected=None):
    """Return a list of error strings; an empty list means the demo passed.

    ``doc`` is the verdict document: the RESULT_ATTRIBUTES dict, with
    ``consensus.branches_json`` either already parsed (a list) or still a
    JSON string. This checks the tops *strictly per branch* even though the
    oracle's own verdict is endian-agnostic (a bit-reversed run would still
    vote PASS).
    """
    if expected is None:
        expected = grover.MARKED_STATE
    errors = []

    if doc.get("assert.verdict") != "PASS":
        errors.append(
            "assert.verdict={!r}, expected PASS".format(doc.get("assert.verdict"))
        )

    try:
        branches_count = int(str(doc.get("consensus.branches", "")))
    except (TypeError, ValueError):
        branches_count = None
    if branches_count != 9:
        errors.append(
            "consensus.branches={!r}, expected 9".format(doc.get("consensus.branches"))
        )

    raw_branches = doc.get("consensus.branches_json", "")
    if isinstance(raw_branches, str):
        try:
            branches = json.loads(raw_branches) if raw_branches else []
        except ValueError:
            branches = None
            errors.append("consensus.branches_json is not valid JSON")
    else:
        branches = raw_branches

    if branches is not None:
        if len(branches) != 9:
            errors.append(
                "consensus.branches_json has {} entries, expected 9".format(
                    len(branches)
                )
            )
        expected_labels = {
            "{} and {}".format(comp, engine)
            for _, comp in grover.BUILDERS
            for engine in grover.ENGINES
        }
        actual_labels = {b.get("label") for b in branches}
        if actual_labels != expected_labels:
            errors.append(
                "branch labels {} != expected {}".format(actual_labels, expected_labels)
            )
        for branch in branches:
            if branch.get("top") != expected:
                errors.append(
                    "branch {!r} top={!r}, expected {!r}".format(
                        branch.get("label"), branch.get("top"), expected
                    )
                )
            if branch.get("dissent"):
                errors.append("branch {!r} dissented".format(branch.get("label")))

    try:
        max_hellinger = float(str(doc.get("consensus.max_hellinger", "")))
    except (TypeError, ValueError):
        max_hellinger = None
        errors.append(
            "consensus.max_hellinger={!r} is not a float".format(
                doc.get("consensus.max_hellinger")
            )
        )
    if max_hellinger is not None and max_hellinger > grover.MAX_HELLINGER:
        errors.append(
            "consensus.max_hellinger={} > {}".format(
                max_hellinger, grover.MAX_HELLINGER
            )
        )

    return errors


def put_json(path, token, body):
    data = json.dumps(body).encode("utf-8")
    headers = {
        "Host": nifi_ready.HOST_HEADER,
        "Content-Type": "application/json",
        "Authorization": "Bearer " + token,
    }
    req = urllib.request.Request(
        nifi_ready.BASE + path, data=data, headers=headers, method="PUT"
    )
    with urllib.request.urlopen(
        req, context=nifi_ready.CTX, timeout=nifi_ready.CALL_TIMEOUT
    ) as response:
        out = response.read().decode()
    return json.loads(out) if out else {}


def find_group(token):
    flow = nifi_ready.call("/flow/process-groups/root", token=token)
    groups = flow["processGroupFlow"]["flow"]["processGroups"]
    for group in groups:
        if group["component"]["name"] == grover.GROUP_NAME:
            return group["component"]["id"]
    raise SystemExit(
        "quickstart group {!r} not found on the canvas".format(grover.GROUP_NAME)
    )


def processor_states(token, gid):
    flow = nifi_ready.call("/flow/process-groups/{}".format(gid), token=token)
    processors = flow["processGroupFlow"]["flow"]["processors"]
    return {p["component"]["name"]: p["component"]["state"] for p in processors}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    default_port = os.environ.get("QUANIFI_NIFI_PORT", "8443")
    parser.add_argument(
        "--base-url",
        default="https://127.0.0.1:{}/nifi-api".format(default_port),
    )
    parser.add_argument(
        "--user", default=os.environ.get("QUANIFI_NIFI_USERNAME", "admin")
    )
    parser.add_argument(
        "--password",
        default=os.environ.get("QUANIFI_NIFI_PASSWORD", "quanifi-demo-password"),
    )
    parser.add_argument("--reports-dir", default=str(ROOT / "reports"))
    parser.add_argument("--timeout", type=int, default=600)
    parser.add_argument("--check-only", action="store_true")
    parser.add_argument("--leave-running", action="store_true")
    args = parser.parse_args(argv)

    nifi_ready._set_target(args.base_url)
    try:
        token = nifi_ready.login(args.user, args.password)
    except (urllib.error.URLError, OSError) as exc:
        print("quickstart_smoke: could not log in: {}".format(exc))
        return 1

    half_loaded = unconfigured = valid_count = total = None
    for attempt in range(3):
        half_loaded, unconfigured, valid_count, total = nifi_ready.audit_processors(
            token
        )
        if not half_loaded:
            break
        if attempt < 2:
            time.sleep(10)
    if half_loaded:
        print(
            "quickstart_smoke: {} processor(s) still half-loaded after 3 checks".format(
                len(half_loaded)
            )
        )
        return 2

    gid = find_group(token)
    states = processor_states(token, gid)
    not_stopped = {name: state for name, state in states.items() if state != "STOPPED"}
    if not_stopped:
        print(
            "quickstart_smoke: not every component is STOPPED: {}".format(not_stopped)
        )
        return 3

    if args.check_only:
        print("canvas present, {} processors, all STOPPED".format(len(states)))
        return 0

    results_dir = Path(args.reports_dir) / "quickstart" / "results"
    before = set(results_dir.glob("*")) if results_dir.exists() else set()

    put_json(
        "/flow/process-groups/{}".format(gid), token, {"id": gid, "state": "RUNNING"}
    )

    deadline = time.time() + args.timeout
    doc = None
    new_file = None
    while time.time() < deadline:
        if results_dir.exists():
            candidates = set(results_dir.glob("*")) - before
            for path in candidates:
                try:
                    doc = json.loads(path.read_text(encoding="utf-8"))
                    new_file = path
                    break
                except (OSError, ValueError):
                    continue
        if doc is not None:
            break
        time.sleep(5)

    if not args.leave_running:
        put_json(
            "/flow/process-groups/{}".format(gid),
            token,
            {"id": gid, "state": "STOPPED"},
        )

    if doc is None:
        print(
            "quickstart_smoke: timed out after {}s waiting for a verdict file "
            "in {}; check the failure funnel's queue on the canvas".format(
                args.timeout, results_dir
            )
        )
        return 1

    print("Wrote {}".format(new_file))
    header = ["builder \\ engine"] + grover.ENGINES
    print(" | ".join(header))
    branches = doc.get("consensus.branches_json", "[]")
    if isinstance(branches, str):
        branches = json.loads(branches) if branches else []
    by_label = {b["label"]: b for b in branches}
    for _, bcomp in grover.BUILDERS:
        row = [bcomp]
        for engine in grover.ENGINES:
            branch = by_label.get("{} and {}".format(bcomp, engine))
            row.append(branch["top"] if branch else "-")
        print(" | ".join(row))

    print(
        "verdict={} max_hellinger={} report={}".format(
            doc.get("assert.verdict"),
            doc.get("consensus.max_hellinger"),
            Path(args.reports_dir) / "quickstart" / "grover-3x3.html",
        )
    )

    errors = evaluate_result(doc)
    if errors:
        for err in errors:
            print("FAIL: " + err)
        return 1
    print("PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
