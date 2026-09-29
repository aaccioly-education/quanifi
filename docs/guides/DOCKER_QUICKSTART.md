# Docker quick start

A fresh clone plus `docker compose up` builds and starts a lean NiFi 2.9.0
image with eight quantum processors pre-installed, a stopped demo canvas
already loaded, and no network calls to the NiFi API during startup. Starting
the canvas runs one Qiskit Grover circuit on a local Aer simulator and writes
an HTML report to your host. The default canvas has four processors in a
single sequence; no comparison matrix or consensus configuration is needed.

The image measurements below were collected with the previous advanced 3×3
example (still available separately), not the simple demo. They were collected
on an Apple Silicon Mac using Docker Desktop. They are reference measurements, not performance guarantees.

## Prerequisites

- Docker Desktop with Compose v2, and `git`. No Python, no NiFi, no quantum
  libraries needed on the host to run the demo through the NiFi UI.
- **Memory**: give Docker Desktop at least **4 GB** (Settings → Resources →
  Memory on macOS/Windows); **6 GB or more** is more comfortable if you also
  run the report browser (`--profile web`) or the test profile at the same
  time. On the reference machine the container used **1.36 GiB idle** (right
  after `healthy`, before running the demo) and **2.64 GiB after** the demo
  (nine simulated circuits, incl. Qrisp's jax/jaxlib import, the single
  largest consumer at ~570 MB). A run capped to exactly 4 GB (`docker update
  --memory 4g --memory-swap 4g`) still passed, peaking at 2.38 GiB (60% of
  the cap).
- **Disk**: a few GB free — the runtime image (**3.9 GB on disk, 1.37 GB
  compressed** on the reference machine), the NAR unpack inside the
  container's writable layer (~1 GB, done once per fresh container), and
  Docker's build cache.
- Network access during the build: the base image, the pinned `uv` image and
  every dependency wheel are pulled from Docker Hub / ghcr.io / PyPI. The
  build is not reproducible offline.

## Quick start

```bash
git clone <this repo> quanifi
cd quanifi
docker compose up
```

Wait for `(healthy)` in `docker compose ps`, or the `[quanifi] READY` line in
the logs (`docker compose logs -f nifi | grep quanifi` — the foreground
`docker compose up` is otherwise verbose, since the official image tails
`nifi-app.log`). On the reference machine (Apple Silicon, arm64) the first
build took **~1 minute** on a fast connection, and a fresh container reached
`healthy` in **~30 seconds**; build time depends on network speed, and startup time depends on host resources.

Open <https://localhost:8443/nifi>, accept the self-signed certificate, and
log in with:

- **Username**: `admin`
- **Password**: `quanifi-demo-password`

## Run the demo

1. On the canvas, right-click **Quanifi quickstart — Qiskit Grover** and choose
   **Start**. Double-click the group to see this single flow:

   **Start here → Build Grover circuit → Simulate circuit → View results**

2. Each step has one purpose:
   - **Start here** sends an empty input to start the flow.
   - **Build Grover circuit** uses Qiskit to search for `10` among `00`, `01`,
     `10`, and `11`, using two qubits and one Grover iteration.
   - **Simulate circuit** runs the circuit locally using Qiskit Aer (1,024 shots).
   - **View results** writes the circuit and measurement results to HTML.
3. Open `reports/quickstart/qiskit-grover.html` on your computer. The expected
   top result is **10**, with probability **1.0** on the ideal simulator.
   Bitstrings use qubit 0 on the left.

The trigger runs immediately when started and then once per day while left
running. For another immediate run, stop **Start here**, leave the other three
processors running, then right-click **Start here → Run Once**. Each run adds
one result card to the report.

To try another search, stop **Build Grover circuit**, edit its **Marked State**
to another two-bit value (for example `01`), and start it again before triggering
the flow. Keep **Num Iterations** at `1` for this two-qubit example. Failed inputs
are retained in the connections to the failure funnel for inspection.

### Headless check

After NiFi is healthy, the following starts the flow, checks the new report for
`10` with probability at least 0.99, and stops the flow again:

```bash
python3 tools/quickstart_smoke.py --base-url https://127.0.0.1:8443/nifi-api
```

Use your configured host port in the URL (for example `18443`). Add
`--check-only` to confirm the canvas is present and stopped without running it.
The smoke check expects the default target `10`.

### Existing installations

Rebuilding preserves your saved canvas. To add the new example without deleting
existing flows, drag a Process Group onto the NiFi canvas, choose the option to
upload a flow definition, and select `demo/grover/qiskit-grover.json`. The new
group is **Quanifi quickstart — Qiskit Grover**. Do not use `down -v` to upgrade
an installation whose flows or reports you want to keep.

### Advanced example

The old matrix is available in `demo/grover/grover-3x3.json` for users who want
to compare three builders across three simulators. Import it separately when
ready. All its processor types remain included in the image. Its local runner is
`python3 tools/build_grover_examples.py --run`; the default Docker smoke check
checks only the simple Qiskit flow.

## Configuration

Every variable below can be set in the shell or in a `.env` file next to
`compose.yaml`.

| Variable | Default | Effect |
|---|---|---|
| `QUANIFI_NIFI_PORT` | `8443` | Host port mapped to NiFi's HTTPS port. |
| `QUANIFI_NIFI_USERNAME` | `admin` | NiFi single-user login username. |
| `QUANIFI_NIFI_PASSWORD` | `quanifi-demo-password` | NiFi single-user login password. |
| `QUANIFI_SENSITIVE_PROPS_KEY` | `quanifi-quickstart-demo-key` | NiFi encryption key for sensitive properties. Set a private key before storing real credentials; retain it with the existing flow. |
| `QUANIFI_NIFI_HEAP` | `1g` | JVM initial and max heap (`NIFI_JVM_HEAP_INIT`/`_MAX`). |
| `QUANIFI_REPORTS_DIR` | `./reports` | Host directory bind-mounted at the container's `reports/`. |
| `QUANIFI_PROCESSORS` | `docker/processors.txt` | Path (inside the repo) to the processor list to bake in, or `all`. Rebuild after changing. |
| `QUANIFI_PREBAKE` | `true` | Pre-install dependencies at build time. Set `false` (with `QUANIFI_PROCESSORS=all`) to let NiFi install on first use instead — slower, needs network at runtime. |
| `QUANIFI_CANVAS` | `demo/grover/qiskit-grover.json` | Flow definition baked into the image; empty string (`QUANIFI_CANVAS=`) ships no canvas. |
| `QUANIFI_AUTO_RECOVER` | `true` | Auto-restart NiFi on a detected startup wedge (bounded, see below). |
| `QUANIFI_AUTO_RESUME` | `false` | Opt in to NiFi resuming `RUNNING` components after a restart. **Also disables auto-recovery** when set `true` (see below). |
| `QUANIFI_MAX_RECOVERIES` | `3` | Bounded retries for automatic wedge recovery. |
| `QUANIFI_STALL_SECONDS` | `240` | Seconds of no progress after "Starting Flow Controller" before a stall is declared. |
| `QUANIFI_WEB_PORT` | `8080` | Host port mapped to the report browser (`--profile web`). |
| `QUANIFI_ADMIN_EMAIL` | `admin@example.com` | Report browser's bootstrap administrator email. |
| `QUANIFI_ADMIN_PASSWORD` | `quanifi-demo-password` | Report browser's bootstrap administrator password. |
| `REPORT_INGESTION_TOKEN` | `local-ingestion-token` | Bearer token the report browser accepts on `/api/v1/report-runs/`. |
| `DJANGO_SECRET_KEY` | `local-only-development-key` | Report browser's Django secret key. |
| `QUANIFI_DB_PASSWORD` | `local-only-password` | Postgres password for the report browser's database. |

## Stopping and resetting

- `docker compose down` stops the container but **keeps every volume**,
  including the canvas in `conf/`.
- `docker compose down -v` also removes the volumes — the next `up` starts
  from a fresh, unconfigured NiFi, and the canvas is re-seeded.
- After pulling new code: `docker compose up --build`. Because `conf/` is a
  volume, the entrypoint re-applies `nifi.properties` settings on every start
  but **never overwrites an existing `flow.json.gz`** — so a changed demo
  canvas only takes effect after `docker compose down -v`.

## Restarts never resume anything

NiFi 2.9.0's `autoResumeState` controls whether components that were
`RUNNING` before a restart resume automatically afterwards. The entrypoint
sets it to `false` on every start (including an automatic wedge-recovery
restart), so **every restart leaves the canvas `STOPPED`**, regardless of
what state it was in before. This closes a real hazard: a NiFi process that
restarts with `autoResumeState=true` re-fires every `RUNNING` group
immediately, which on a canvas that submits to real quantum hardware means
spending quota twice for one restart. Set `QUANIFI_AUTO_RESUME=true` to opt
out of this protection (NiFi's normal behaviour) — doing so also disables
automatic wedge recovery, since a recovery restart with auto-resume enabled
would itself be exactly that hazard.

## Adding processors to the image

> **The Docker quickstart does not install the full processor catalogue.**
> Only the classes in [`docker/processors.txt`](https://github.com/saeg/quanifi/blob/main/docker/processors.txt)
> are included. A processor documented elsewhere may therefore be missing from
> NiFi's Add Processor dialog until you rebuild the image with it enabled.

### Enable selected processors (recommended)

Edit `docker/processors.txt` and add one existing processor class name per line,
without `.py`. For example, append `QiskitQFTCircuit` to enable that processor. Keep the
existing entries so your saved flows continue to work. Then rebuild and recreate
NiFi:

```bash
docker compose up -d --build nifi
```

Wait until NiFi is healthy, refresh the canvas, and find the new class in
**Add Processor**. Existing flows are preserved and start stopped. Adding a class
to the image makes it available; it does not add it to your canvas automatically.
A plain `docker compose restart` does not rebuild the image.

### Choose a separate list using .env

To keep your own selection separate, copy `docker/processors.txt` to
`docker/my-processors.txt`, then add class names to the copy. Add or update this
setting in `.env` beside `compose.yaml`, keeping any existing settings:

```dotenv
QUANIFI_PROCESSORS=docker/my-processors.txt
QUANIFI_PREBAKE=true
```

Run `docker compose up -d --build nifi`. The list must be inside the repository.
Keeping it under `docker/` includes it in the build context; a list elsewhere
must also be permitted by `.dockerignore` and copied into the Dockerfile's
selection stage. List processor classes only, not helper modules.

### Make all processors available

For the complete catalogue, set these values in the same local `.env` file:

```dotenv
QUANIFI_PROCESSORS=all
QUANIFI_PREBAKE=false
```

Then run `docker compose up -d --build nifi`. This copies every processor in
`nifi_extensions/`; NiFi installs dependencies as it loads the processors.
Startup can take substantially longer and requires network access and more
resources. Framework-specific external services, credentials, or hardware access
still need their own configuration. Selecting all processors does not configure
those services.

### Dependency and extension details

- With `QUANIFI_PREBAKE=true` (the default), dependencies are installed during
  the build using `uv.lock` and the overrides in `pyproject.toml`. With pre-baking
  disabled, NiFi installs the dependencies declared by each processor at runtime.
- Helper modules such as `reporting.py` are picked up automatically.
- A processor must not import another processor module: NiFi loads processors
  in isolated module contexts. The image build rejects these imports.
- To implement a new processor class, see [Creating processors](CREATING_PROCESSORS.md),
  then include its class name in your Docker selection and rebuild.

## Report browser (`--profile web`)

```bash
docker compose --profile web up
```

Starts Postgres and the Django report browser, running migrations and the
administrator bootstrap automatically on start. Open
<http://localhost:8080/>, log in with `QUANIFI_ADMIN_EMAIL` /
`QUANIFI_ADMIN_PASSWORD` (defaults above).

QuanifiReport is **not** wired to the web profile by default: its `Reports
API Token` property is *sensitive*, so NiFi will not let it be baked into the
committed `flow.json.gz` in plain text. To wire a QuanifiReport instance on
the canvas to the browser manually, set on that processor:

- `Output Mode` = `Both` (or `Web API` to skip the local HTML file)
- `Reports API URL` = `http://web:8080/api/v1/report-runs/`
- `Reports API Token` = the value of `REPORT_INGESTION_TOKEN` (default
  `local-ingestion-token`)

Both services share the Compose project's network, and `web` is already in
`DJANGO_ALLOWED_HOSTS`.

For live-reloading development on the report browser's own code (not part of
the quick start):

```bash
docker compose -f compose.yaml -f compose.web-dev.yaml --profile web up
```

## Tests in Docker (`--profile test`)

```bash
docker compose --profile test build test
docker compose --profile test run --rm test
```

Runs the modules listed in `docker/test-files.txt` — the tests whose imports
are fully satisfied by the processors in `docker/processors.txt` and their
pinned dependencies — inside a venv built the same way the runtime image's
per-processor venvs are (one venv, the union of every shipped processor's
dependencies, plus pytest). This is deliberately a small subset: a
full-suite test image would need the entire research stack (pyQuil +
rigetti-quax, Braket, the Q#/QDK toolchain, PennyLane, OpenFermion + PySCF,
IQM), several GB beyond what the lean quickstart needs. The full suite
already runs on the host with `just test`.

## What goes into the images

`.dockerignore` is a strict **allowlist**: it starts with `*` (deny
everything), then re-allows only the paths an image actually `COPY`s, then
re-denies a set of secret-shaped patterns (`*token*`, `*key*`, `*credential*`,
`.env*`, certificate/keystore extensions, `db.sqlite3`) even inside an
allowed directory. This matters because this working tree can hold real
provider credential files (IBM/IQM/Open Quantum key and token files) whose
names follow no single convention — the allowlist means a new credential file
dropped anywhere in the repo never reaches a build context by default,
instead of relying on remembering to add it to a denylist.

To audit what a build would actually send, from a clean clone:

```bash
docker build --no-cache --progress=plain -f - . <<'EOF'
FROM busybox:1.36
COPY . /ctx
RUN find /ctx -type f | sort
EOF
```

A previous audit with eight decoy credential files found none in the build
context. Repeat this check after changing the allowlist.

## Troubleshooting

**Wedged start.** NiFi 2.9's Python bridge can occasionally hang during
startup (the "py4j startup wedge") — no more Python processors load and the
Flow Controller never finishes initializing. The log-only monitor
(`docker/nifi/quanifi_monitor.py`) detects this without ever calling the NiFi
API (which would itself risk triggering the same wedge): if `QUANIFI_STALL_SECONDS`
pass with no new "Successfully loaded Python Processor" record and no install
progress after "Starting Flow Controller" was logged, it kills the NiFi JVMs,
the container exits non-zero, and Compose's `restart: on-failure:5` starts it
again — up to `QUANIFI_MAX_RECOVERIES` times. Because `autoResumeState=false`
on every start, a recovered start always comes back with the canvas
`STOPPED`: nothing re-fires.

- Check the current state: `docker compose exec nifi cat /tmp/quanifi/status.json`.
- Watch the monitor's own messages: `docker compose logs -f nifi | grep quanifi`.
- If recovery is exhausted (`recovery limit reached (N/N)`), the health check
  reports `unhealthy` and stays that way; a manual `docker compose restart
  nifi` resets the recovery counter (a fresh container start) and is safe for
  the same reason automatic recovery is safe — the canvas never auto-resumes.

**Port already in use.** The default ports (8443, 8080) may collide with
another NiFi or web service on your machine. Override
`QUANIFI_NIFI_PORT`/`QUANIFI_WEB_PORT`.

**Out of memory.** An exit code of 137 (visible in `docker compose ps` or
`docker inspect`) means the kernel OOM-killed the container. Check
`docker stats` while the demo runs and increase Docker Desktop's memory
limit (see Prerequisites).

**Permission denied writing to `reports/` on Linux.** The container runs as
uid 1000. If your host `reports/` directory is owned by a different uid,
`mkdir -p reports && sudo chown 1000:1000 reports` (or run
`id -u`/`id -g` and match those). The entrypoint logs a warning at startup if
`reports/` is not writable.

**A newly added processor is stuck "Initializing" after
`QUANIFI_PREBAKE=false`.** NiFi is downloading that processor's dependencies
from PyPI on first use; this needs network access from inside the container
and can take a while for a large dependency tree. Watch
`docker compose logs -f nifi` for `Installing dependencies` lines.

**Canvas shows components NiFi calls invalid / the monitor reports
`broken: canvas uses processor types not baked into this image`.** The
`conf/flow.json.gz` volume was seeded by a different image build than the one
now running (for example, you changed `docker/processors.txt` and rebuilt
without `down -v`). Run `docker compose down -v` to get a canvas that matches
the current image, then `docker compose up` again.

**Apple Silicon vs amd64.** The image builds and runs natively on
`linux/arm64`. An `amd64` build was verified under Docker Desktop's Rosetta
translation (`docker buildx build --platform linux/amd64 -f
docker/nifi/Dockerfile --target runtime ...`): it succeeded in **~2m20s** on
the reference machine, and an optional full run under emulation reached
`healthy` in **~1 minute** and passed the previous advanced demo (all 9 cells `110`, PASS). These timings are specific to that machine.
