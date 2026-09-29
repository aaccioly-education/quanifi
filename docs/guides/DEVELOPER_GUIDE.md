# Quanifi — Developer Guide

This guide covers everything needed to clone, test, and extend Quanifi — from setting up the Python environment to loading the demo canvas in Apache NiFi.

---

## Table of Contents

1. [Prerequisites](#1-prerequisites)
2. [Clone and Python Environment](#2-clone-and-python-environment)
3. [Running the Test Suite](#3-running-the-test-suite)
4. [NiFi Installation](#4-nifi-installation)
5. [Configuring NiFi for Quanifi](#5-configuring-nifi-for-quanifi)
6. [Loading the Demo Flow](#6-loading-the-demo-flow)
7. [Updating Absolute Paths After Import](#7-updating-absolute-paths-after-import)
8. [Starting and Accessing NiFi](#8-starting-and-accessing-nifi)
9. [Creating a New Processor](#9-creating-a-new-processor)
10. [Project Structure](#10-project-structure)

---

## 1. Prerequisites

| Component | Version | Notes |
|---|---|---|
| Python | 3.10+ | Must be available before NiFi starts |
| Apache NiFi | 2.9.0 | Exact version required — Python processor API changed in 2.x |
| Java | 21+ | Required to run NiFi (NiFi bundles its own in recent builds) |
| git | any | |

For running tests outside NiFi, you only need Python ≥ 3.10.

---

## 2. Clone and Python Environment

```bash
git clone https://github.com/ramalhoneilson/Quanifi.git
cd Quanifi
```

Create a virtual environment and install all framework dependencies. NiFi will be pointed at this same environment so processors share one set of packages:

```bash
python3 -m venv .venv

.venv/bin/pip install \
  "qiskit>=2.0.0" \
  "qiskit-aer>=0.13.0" \
  qiskit-qasm3-import \
  "cirq>=1.0.0" \
  ply \
  qrisp \
  matplotlib \
  pylatexenc \
  pytest
```

Or with [uv](https://github.com/astral-sh/uv) (faster):

```bash
uv venv .venv
uv pip install --python .venv/bin/python \
  "qiskit>=2.0.0" "qiskit-aer>=0.13.0" qiskit-qasm3-import \
  "cirq>=1.0.0" ply qrisp matplotlib pylatexenc pytest
```

> **Note:** NiFi creates its own per-processor venvs using the packages declared in `ProcessorDetails.dependencies`. Pointing `nifi.python.command` to `.venv/bin/python3` (see §5) lets NiFi reuse the pre-installed packages, which avoids redundant downloads and makes first-use startup near-instant.

---

## 3. Running the Test Suite

Tests live in `tests/` and use pytest. They run entirely outside NiFi — no JVM, no running server.

```bash
# Fast suite: 236 tests, ~5s (skips real Qrisp simulator, incl. Qrisp VQE)
.venv/bin/python -m pytest -m "not slow"

# Full suite: 266 tests, ~5s
.venv/bin/python -m pytest

# Single file
.venv/bin/python -m pytest tests/test_cirq.py -v

# Single test
.venv/bin/python -m pytest tests/test_integration.py::TestCrossFramework::test_qiskit_circuit_to_cirq_simulator -v
```

### Test markers

| Marker | Description | When to use |
|---|---|---|
| *(none)* | Unit + integration tests, Qiskit + Cirq only | Every commit |
| `slow` | Tests that run the real Qrisp simulator | Before merging Qrisp-related changes |

### What the tests cover

| File | Processors under test |
|---|---|
| `test_qiskit.py` | `QiskitHadamardTransform`, `QiskitStatePreparation`, `QiskitGroverCircuit`, `QiskitQFTCircuit`, `QiskitPhaseEstimation`, `QiskitAerSimulator`, `QiskitGroverSearch` |
| `test_cirq.py` | `CirqHadamardTransform`, `CirqPhaseOracle`, `CirqGroverOperator`, `CirqGroverCircuit`, `CirqSimulator` |
| `test_cirq_qft_qpe.py` | `CirqQFTCircuit`, `CirqPhaseEstimation` |
| `test_qrisp.py` | `QrispSimulator`, `QrispGroverSearch` |
| `test_integration.py` | Full pipelines — Qiskit-only, Cirq-only, and all cross-framework combinations |
| `test_reporting.py` | `QiskitCircuitReport`, `QuantumDistributionComparison`, `QiskitStatevectorSimulator` |

### How the test harness works

Because `nifiapi.*` is a JVM-backed package that only exists inside a running NiFi process, `tests/conftest.py` installs lightweight stubs into `sys.modules` before any processor is imported. Every test then instantiates the processor class directly, creates a `MockContext` and `MockFlowFile`, calls `processor.transform(ctx, ff)`, and asserts on the returned `FlowFileTransformResult`.

---

## 4. NiFi Installation

Download the NiFi 2.9.0 binary from the Apache archive:

```
https://archive.apache.org/dist/nifi/2.9.0/nifi-2.9.0-bin.zip
```

Extract it:

```bash
unzip nifi-2.9.0-bin.zip -d ~/
# Result: ~/nifi-2.9.0/
```

No further installation steps are needed. NiFi runs in-place from the extracted directory.

> **Java:** NiFi 2.9.0 ships with an embedded JDK on macOS and Linux. If yours does not, install Java 21 first (`brew install openjdk@21` on macOS).

---

## 5. Configuring NiFi for Quanifi

Edit `$NIFI_HOME/conf/nifi.properties`. Two values must be set; everything else can stay at its default:

```properties
# Point NiFi at the Python interpreter that has the framework packages installed.
# Replace <path-to-quanifi> with the absolute path where you cloned this repo.
nifi.python.command=<path-to-quanifi>/.venv/bin/python3

# Register the quanifi processor directory as a named extension source.
# NiFi polls this directory for new/changed .py files.
nifi.python.extensions.source.directory.quanifi=<path-to-quanifi>/nifi_extensions
```

**Example** (replace `/Users/yourname/projects/quanifi` with your actual path):

```properties
nifi.python.command=/Users/yourname/projects/quanifi/.venv/bin/python3
nifi.python.extensions.source.directory.quanifi=/Users/yourname/projects/quanifi/nifi_extensions
```

The key on the left of the `=` can be anything after the last `.` — NiFi scans all properties whose name starts with `nifi.python.extensions.source.directory`. Using a named suffix (`quanifi`) instead of `default` lets you add other extension directories alongside it.

No other properties need to change for a local single-user development setup. NiFi defaults to HTTPS on port 8443 and generates a self-signed certificate on first start.

---

## 6. Loading the Demo Flow

`flow.json.gz` in the root of this repository is an export of the full NiFi canvas with all demo pipelines pre-wired and configured. Loading it gives you:

| Pipeline | Processors |
|---|---|
| CSV demo | `GetFile → SplitRecord → UpdateAttribute → PutFile` |
| Grover (Qiskit, split) | `GenerateFlowFile → QiskitGroverCircuit → QiskitAerSimulator → QiskitCircuitReport` |
| Grover (Qrisp) | `GenerateFlowFile → QrispGroverSearch → QiskitCircuitReport` |
| Grover cross-compare | Both Grover pipelines feed `QuantumDistributionComparison` |
| Grover all-in-one | `GenerateFlowFile → QiskitGroverSearch → QiskitAerSimulator → QiskitCircuitReport` |

**To load the flow:**

1. Stop NiFi if it is running:
   ```bash
   $NIFI_HOME/bin/nifi.sh stop
   ```

2. Back up the existing flow file (optional but recommended):
   ```bash
   cp $NIFI_HOME/conf/flow.json.gz $NIFI_HOME/conf/flow.json.gz.bak
   ```

3. Copy the repo's flow file into NiFi's `conf/` directory:
   ```bash
   cp <path-to-quanifi>/flow.json.gz $NIFI_HOME/conf/flow.json.gz
   ```

4. Start NiFi:
   ```bash
   $NIFI_HOME/bin/nifi.sh start
   ```

5. Proceed to §7 to update the machine-specific paths before running any processors.

---

## 7. Updating Absolute Paths After Import

The flow was exported from a specific machine. Several processor properties contain absolute paths that must be updated to match your environment. After loading and starting NiFi, open the canvas and update the following:

| Processor | Property | What to set |
|---|---|---|
| `GetFile` | Input Directory | Absolute path to `<quanifi>/demo/input/` |
| `PutFile` (CSV output) | Directory | Absolute path to `<quanifi>/demo/output/` |
| `PutFile` (processed) | Directory | Absolute path to `<quanifi>/demo/processed/` |
| `QiskitCircuitReport` × 3 | Reports Directory | Absolute path to `<quanifi>/reports/` |
| `QuantumDistributionComparison` | Reports Directory | Absolute path to `<quanifi>/reports/` |
| `QuantumDistributionComparison` | State Directory | Absolute path to `<quanifi>/reports/tmp/quanifi_compare_state/` (created automatically) |

**How to edit a property in NiFi:**
1. Right-click the processor on the canvas → **Configure**.
2. Select the **Properties** tab.
3. Click the value field of the property you want to change, type the new path, click **OK**.
4. Click **Apply**.

---

## 8. Starting and Accessing NiFi

```bash
# Start
$NIFI_HOME/bin/nifi.sh start

# Check status (shows PID when running)
$NIFI_HOME/bin/nifi.sh status

# Stop
$NIFI_HOME/bin/nifi.sh stop
```

NiFi takes 30–60 seconds to start. Once ready, open:

```
https://localhost:8443/nifi
```

Your browser will warn about a self-signed certificate — accept the exception and continue.

**First-time login:** NiFi generates a random username and password on first start and prints them to the application log:

```bash
grep "Generated Username\|Generated Password" $NIFI_HOME/logs/nifi-app.log
```

To set a known username and password instead, run:

```bash
$NIFI_HOME/bin/nifi.sh set-single-user-credentials <username> <password>
```

Then restart NiFi.

### Verifying processor discovery

After starting, navigate to a blank area of the canvas, click **+** (Add Processor), and search for `Qiskit` or `Cirq`. All processors from `nifi_extensions/` should appear. If they don't:

1. Confirm `nifi.python.extensions.source.directory.quanifi` points to the correct directory.
2. Check `$NIFI_HOME/logs/nifi-app.log` for Python import errors.
3. Confirm `.venv/bin/python3` has all required packages (`python3 -c "import qiskit, cirq, qrisp"`).

### Processor first-use delay

The first time you drag a processor onto the canvas, NiFi creates an isolated venv for it and runs `pip install` for the packages listed in `ProcessorDetails.dependencies`. The processor shows **Initializing** for 20–60 seconds. Subsequent restarts reuse the cached venv.

If you point `nifi.python.command` at the pre-populated `.venv` (as recommended in §5), the install step resolves almost immediately because the packages are already present.

---

## 9. Creating a New Processor

See [CREATING_PROCESSORS.md](CREATING_PROCESSORS.md) for the full skeleton, pitfall list, and available validators.

Quick checklist:
1. Create `nifi_extensions/MyProcessor.py` — file name must match the class name exactly.
2. Check syntax before touching NiFi: `python3 -c "import ast; ast.parse(open('nifi_extensions/MyProcessor.py').read()); print('OK')"`.
3. Run the test suite: `.venv/bin/python -m pytest -m "not slow"`.
4. Restart NiFi — it picks up the new file on start.
5. If you changed `PropertyDescriptor` definitions in an existing processor: delete `$NIFI_HOME/work/python/extensions/<ProcessorName>/` before restarting so NiFi rebuilds the venv with the new property metadata.

---

## 10. Project Structure

```
quanifi/
│
├── nifi_extensions/          # All NiFi Python processors (one class per file)
│   ├── Qiskit*.py            # Qiskit circuit builders, simulators, report
│   ├── Cirq*.py              # Cirq circuit builders and simulator
│   ├── Qrisp*.py             # Qrisp all-in-one search and simulator
│   ├── Quantum*.py           # Framework-agnostic: QuantumDistributionComparison
│   ├── reporting.py          # Shared HTML report helpers (not a processor)
│   └── QuanifiReport.py      # Re-export shim for reporting.py
│
├── tests/
│   ├── conftest.py           # nifiapi stubs, MockContext, MockFlowFile
│   ├── test_qiskit.py        # Qiskit processor unit tests
│   ├── test_cirq.py          # Cirq processor unit tests
│   ├── test_qrisp.py         # Qrisp processor tests (marked slow)
│   ├── test_integration.py   # End-to-end pipeline + cross-framework tests
│   └── test_reporting.py     # HTML report processor tests
│
├── demo/
│   ├── input/team.csv        # Sample CSV for the classic data-pipeline demo
│   └── output/               # Populated by NiFi's PutFile processor
│
├── reports/                  # HTML reports written at runtime by NiFi processors
│   └── tmp/                  # Transient state files (QuantumDistributionComparison)
│
├── flow.json.gz              # Exported NiFi canvas — load into $NIFI_HOME/conf/ (see §6)
│
├── README.md                 # Processor API reference and example pipelines
├── DEVELOPER_GUIDE.md        # This file
├── CREATING_PROCESSORS.md    # Processor skeleton, validators, pitfall list
└── pyproject.toml            # Python project metadata; dev deps (pytest)
```
