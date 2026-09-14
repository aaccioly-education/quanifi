#!/usr/bin/env python3
"""Block until NiFi can actually run a window, or fail loudly.

Readiness is not an open port, and it is not an HTTP 200 on the UI. Both of
those are true of a NiFi that will never run anything:

  * 2026-08-30 10:53 -- Jetty served the UI and `/access/token` issued tokens
    while the Flow Controller never finished initializing. Every
    `/flow/process-groups/*` call hung until the caller's 240 s timeout. The
    18:30 window logged in successfully and then died in lane discovery.
  * The same evening, three further starts came up with the UI answering and
    most Python processors stuck in "Initializing runtime environment".
    `gen2-window` found its lanes and skipped all six.

Both states pass a `curl -o /dev/null -w %{http_code}` check, which is why the
old gate in `just nifi-start` reported success and its three-attempt retry never
fired. The honest signal is the one the window's own guard uses: every processor
reports `validationStatus == VALID`.

**Why this waits on the log before touching the API.** Polling
`/flow/process-groups/*` *during* startup is itself enough to wedge NiFi -- on
2026-08-30 three starts died under exactly that polling, and the one start that
came up healthy (all 260 processors valid) was the one left alone. An earlier
version of this script polled the API every 15 s from the moment the web server
answered, so it destroyed the thing it was measuring, and then told `nifi-start`
to kill a NiFi that had been initializing perfectly well (142 -> 69 invalid, then
torn down). So: phase 1 reads only records written after the current
``nifi.sh start`` and makes no requests at all. It requires one successful load
record for every Python processor in the offline flow, followed by a quiet
minute. Phase 2 makes one authenticated pass only after that positive
completion evidence.

Exit status is the contract: 0 means a window may be launched, non-zero means it
must not be.

    tools/nifi_ready.py --log /path/to/nifi-app.log
"""
import argparse
import datetime
import gzip
import json
import os
import re
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

#: Default instance. A second NiFi (2.10.0 on 8444) now exists, so this is
#: overridable with --base-url: pointed at the wrong port, the confirmation
#: pass would authenticate against the OTHER instance and report ITS processors
#: as ready, which looks exactly like success.
BASE = "https://127.0.0.1:8443/nifi-api"
HOST_HEADER = "localhost:8443"
CTX = ssl.create_default_context()
CTX.check_hostname = False
CTX.verify_mode = ssl.CERT_NONE

#: Per-call ceiling. Short on purpose: a wedged Flow Controller accepts the
#: connection and never answers, so one call has to be given up on quickly and
#: counted, rather than inheriting the caller's minutes-long timeout.
CALL_TIMEOUT = 30

LOADED = re.compile(r"Successfully loaded Python Processor")
#: A processor whose module fails to import never initializes and never
#: complains again -- the ImportError appears once, in whichever hourly log file
#: was current, and the processor then sits in "Initializing runtime
#: environment" forever. On 2026-08-30 a stale __pycache__/batch_prep.pyc
#: shadowed a new function and took 108 processors down exactly this way.
BROKEN = re.compile(r"ImportError|ModuleNotFoundError|cannot import name")


def _set_target(base_url):
    """Point every request at one instance.

    The single-user certificate is issued for localhost, so the Host header
    carries the name while the connection goes to the literal address.
    """
    global BASE, HOST_HEADER
    BASE = base_url
    HOST_HEADER = "localhost:%s" % (urllib.parse.urlsplit(base_url).port or 443)


def log(message):
    print("%s  %s" % (datetime.datetime.now().strftime("%H:%M:%S"), message),
          flush=True)


def call(path, token=None, method="GET", body=None, raw=False):
    headers = {"Host": HOST_HEADER}
    data = None
    if token:
        headers["Authorization"] = "Bearer " + token
    if body is not None:
        data = body.encode()
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    req = urllib.request.Request(BASE + path, data=data, headers=headers,
                                 method=method)
    with urllib.request.urlopen(req, context=CTX, timeout=CALL_TIMEOUT) as response:
        out = response.read().decode()
    return out if raw else (json.loads(out) if out else {})


def login(user, password):
    return call("/access/token", method="POST", raw=True,
                body=urllib.parse.urlencode({"username": user,
                                             "password": password}))


def python_processor_count(flow_path):
    """Count processors that must emit a successful Python-load log record."""
    with gzip.open(flow_path, "rt", encoding="utf-8") as handle:
        root = json.load(handle)["rootGroup"]

    def groups(group):
        yield group
        for child in group.get("processGroups", []):
            yield from groups(child)

    return sum(
        processor.get("bundle", {}).get("artifact") == "python-extensions"
        for group in groups(root)
        for processor in group.get("processors", [])
    )


def read_new_log(path, inode, offset):
    """Read only records written by this start, tolerating log rotation."""
    try:
        current_inode = os.stat(path).st_ino
        size = os.path.getsize(path)
    except OSError:
        return [], inode, offset
    if current_inode != inode or size < offset:
        inode, offset = current_inode, 0
    with open(path, "r", encoding="utf-8", errors="replace") as handle:
        handle.seek(offset)
        lines = handle.readlines()
        offset = handle.tell()
    return lines, inode, offset


def wait_for_quiet(path, quiet_seconds, deadline, expected_loaded,
                   start_inode=0, start_offset=0, stall_seconds=120):
    """Passively wait for *this start* to load every Python processor.

    Silence is not completion: the old implementation treated zero (or one)
    load records followed by a quiet minute as readiness and then queried the
    API while NiFi was still starting.  That query can wedge the Flow
    Controller.  This implementation never contacts NiFi until the log records
    one successful load for every Python processor in the offline flow.
    """
    loaded = broken = 0
    inode, offset = int(start_inode), int(start_offset)
    completed_at = None
    last_reported = None
    last_progress = time.time()
    while time.time() < deadline:
        lines, inode, offset = read_new_log(path, inode, offset)
        for line in lines:
            if LOADED.search(line):
                loaded += 1
            elif BROKEN.search(line):
                broken += 1
        if broken:
            log("%d import failure(s) in this startup. Processors whose module "
                "fails to import never initialize. Grep the log for ImportError, "
                "and check for a stale __pycache__." % broken)
            return False
        if loaded != last_reported:
            last_reported = loaded
            last_progress = time.time()
            log("initializing: %d/%d Python processors loaded"
                % (loaded, expected_loaded))
        if loaded >= expected_loaded:
            if completed_at is None:
                completed_at = time.time()
            if time.time() - completed_at >= quiet_seconds:
                log("all %d Python processors loaded; log quiet for %ds"
                    % (expected_loaded, quiet_seconds))
                return True
        else:
            completed_at = None
            if time.time() - last_progress >= stall_seconds:
                log("STALLED: no Python processor loaded for %ds at %d/%d; "
                    "retrying without probing the API"
                    % (stall_seconds, loaded, expected_loaded))
                return False
        time.sleep(5)
    return False


#: Patterns indicating a processor whose Python/runtime environment failed to
#: load or is still initializing, rather than normal canvas configuration errors.
HALF_LOADED_PATTERN = re.compile(
    r"initializing runtime environment"
    r"|failed to initialize"
    r"|failed to load"
    r"|failed to communicate with python"
    r"|python process"
    r"|modulenotfounderror"
    r"|importerror"
    r"|cannot import name",
    re.IGNORECASE,
)


def categorize_processor(processor):
    """Categorize a processor into ('valid', 'half_loaded', 'unconfigured').

    Returns (category, details).
    - 'valid': processor reports validationStatus == 'VALID'
    - 'half_loaded': runtime environment still initializing, validating, or failed
    - 'unconfigured': runtime environment loaded OK, but canvas wiring/properties incomplete
    """
    comp = processor.get("component", {})
    status = comp.get("validationStatus")
    if status == "VALID":
        return "valid", None

    if status == "VALIDATING":
        return "half_loaded", ["Processor is still validating"]

    errors = comp.get("validationErrors") or []
    if not errors:
        return "half_loaded", [f"Validation status is {status} with no explanation"]

    # Check for half-loaded / runtime loading failures
    loading_errors = [e for e in errors if HALF_LOADED_PATTERN.search(e)]
    if loading_errors:
        return "half_loaded", loading_errors

    # If scheduledState is RUNNING, a non-valid processor cannot run
    state = comp.get("state") or comp.get("scheduledState")
    if state == "RUNNING":
        return "half_loaded", [f"Processor is RUNNING but invalid: {'; '.join(errors)}"]

    # Otherwise, it is an unconfigured component (e.g. stopped on canvas, missing wire/property)
    return "unconfigured", errors


def audit_processors(token):
    """(half_loaded, unconfigured, valid_count, total) over every processor on the canvas.

    Walks the whole tree. Differentiates between processors that failed to load
    or are still initializing ('half_loaded') and processors that loaded fine
    but are simply unconfigured on the canvas ('unconfigured').
    """
    half_loaded = []
    unconfigured = []
    valid_count = 0
    total = 0
    stack = ["root"]
    while stack:
        gid = stack.pop()
        flow = call("/flow/process-groups/%s" % gid,
                    token=token)["processGroupFlow"]["flow"]
        for processor in flow["processors"]:
            total += 1
            cat, details = categorize_processor(processor)
            comp = processor.get("component", {})
            pname = comp.get("name")
            ptype = comp.get("type")
            pid = comp.get("id")
            if cat == "valid":
                valid_count += 1
            elif cat == "half_loaded":
                half_loaded.append((pname, ptype, pid, gid, details))
                log(f"HALF-LOADED PROCESSOR: '{pname}' ({ptype}, {pid}) in group {gid}: {details}")
            else:
                unconfigured.append((pname, ptype, pid, gid, details))
                log(f"UNCONFIGURED PROCESSOR: '{pname}' ({ptype}, {pid}) in group {gid}: {details}")
        stack.extend(child["id"] for child in flow["processGroups"])
    return half_loaded, unconfigured, valid_count, total


def count_invalid(token):
    """Backward-compatible helper returning (half_loaded_count, total)."""
    half_loaded, unconfigured, valid_count, total = audit_processors(token)
    return len(half_loaded), total


def main(argv=None):
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--log", required=True,
                        help="nifi-app.log to watch during phase 1")
    parser.add_argument("--flow", required=True,
                        help="offline flow used to derive the expected Python count")
    parser.add_argument("--start-inode", type=int, default=0,
                        help="log inode captured immediately before nifi.sh start")
    parser.add_argument("--start-offset", type=int, default=0,
                        help="log byte offset captured immediately before start")
    parser.add_argument("--timeout-minutes", type=int, default=12,
                        help="give up after this long (default 12)")
    parser.add_argument("--quiet-seconds", type=int, default=60,
                        help="no newly loaded processor for this long means "
                             "initialization has finished (default 60)")
    parser.add_argument("--stall-seconds", type=int, default=120,
                        help="retry after this long without load progress; no API "
                             "request is made (default 120)")
    parser.add_argument("--confirm-attempts", type=int, default=3,
                        help="authenticated checks after the log goes quiet")
    parser.add_argument("--base-url", default=BASE,
                        help="nifi-api base URL of the instance being probed. "
                             "Must match the instance whose --log and --flow "
                             "are given: a mismatch silently confirms the other "
                             "instance instead (default %(default)s)")
    parser.add_argument("--strict", action="store_true", default=False,
                        help="require all processors on canvas to be fully configured (default False)")
    args = parser.parse_args(argv)
    _set_target(args.base_url)

    use_nifi2 = "8444" in args.base_url
    user = (os.environ.get("NIFI2_USER") if use_nifi2 else None) or os.environ.get("NIFI_USER")
    password = (os.environ.get("NIFI2_PASSWORD") if use_nifi2 else None) or os.environ.get("NIFI_PASSWORD")
    if not user or not password:
        missing = "NIFI2_USER / NIFI2_PASSWORD" if use_nifi2 else "NIFI_USER / NIFI_PASSWORD"
        raise SystemExit("%s is not set -- run through `just` so .env is loaded" % missing)

    deadline = time.time() + args.timeout_minutes * 60

    # --- phase 1: passive. No requests; polling here is what wedges NiFi. ----
    expected_loaded = python_processor_count(args.flow)
    if expected_loaded <= 0:
        log("flow contains no Python processors; refusing an unsafe API probe")
        return 3
    if not wait_for_quiet(args.log, args.quiet_seconds, deadline,
                          expected_loaded, args.start_inode,
                          args.start_offset, args.stall_seconds):
        if time.time() >= deadline:
            log("TIMED OUT waiting for initialization to settle")
            return 1
        return 3

    # --- phase 2: one authenticated pass, now that nothing is initializing --
    for attempt in range(1, args.confirm_attempts + 1):
        try:
            token = login(user, password)
            half_loaded, unconfigured, valid_count, total = audit_processors(token)
        except (urllib.error.URLError, OSError, TimeoutError, KeyError) as exc:
            log("confirmation %d/%d did not answer (%s)"
                % (attempt, args.confirm_attempts, type(exc).__name__))
            if attempt == args.confirm_attempts:
                log("WEDGED: the API will not answer even though initialization "
                    "has finished. The Flow Controller is not serving; only a "
                    "restart clears it.")
                return 2
            time.sleep(20)
            continue

        if not half_loaded:
            if unconfigured:
                log("NOTE: %d processor(s) unconfigured on canvas (missing connections/properties), but runtime loaded OK:"
                    % len(unconfigured))
                for name, ptype, pid, gid, errs in unconfigured:
                    log("  - '%s' (%s): %s" % (name, ptype, "; ".join(errs)))
                if getattr(args, "strict", False):
                    log("STRICT MODE: failing because %d processor(s) are unconfigured." % len(unconfigured))
                    return 3
                log("READY: all %d processors loaded (%d fully valid, %d unconfigured)"
                    % (total, valid_count, len(unconfigured)))
            else:
                log("READY: all %d processors valid" % total)
            return 0

        log("STALLED: %d of %d processors failed to initialize runtime environment."
            % (len(half_loaded), total))
        return 3

    return 1


if __name__ == "__main__":
    sys.exit(main())
