# Interchangeable Grover — canvas guide

Click-by-click instructions for the **decomposed Grover pipeline** using the
Qiskit pair (`QiskitPhaseOracle` → `QiskitGroverOperator`) and for the two
**cross-framework assemblies** that mix Qiskit and Cirq stages in one search.
Follows the same conventions as
[NIFI_FLOW_CONFIGURATION_GUIDE.md](NIFI_FLOW_CONFIGURATION_GUIDE.md) (general
recipe, auto-terminating unused relationships, etc.); the Cirq-only twin of
Flow A is that guide's Flow 11.

**Why these exist:** every stage holds exactly one concern — the *oracle* holds
**Marked State**, the *operator* holds **Num Iterations**, the *simulator*
holds **Shots** — and the property names match across frameworks, so any stage
can be swapped for its counterpart, even mid-pipeline across frameworks.

Two contract rules make the mixing work:

1. **OpenQASM 2 is the cross-framework wire format.** Within one framework use
   the native format (`qasm3`/`qpy` for Qiskit, `cirq_json` for Cirq); the
   moment a connection crosses frameworks, set the upstream processor's
   **Output Format = `qasm2`**.
2. **Canonical bit order (`q0_left`).** All simulators emit counts keys and
   `sim.top_result` with qubit 0 as the leftmost character — the same order you
   type **Marked State** in. Non-palindromic targets like `110` work
   everywhere; expect `sim.top_result == circuit.marked_state` always.

Before you start: restart NiFi after pulling the processors
(`cd ~/projects/nifi-2.9.0 && bin/nifi.sh restart`). First canvas placement of
each new processor installs its pip dependencies (~30 s, watch the bulletin
board).

---

## Flow A — Pure Qiskit, decomposed

**Components:** `GenerateFlowFile` → `QiskitPhaseOracle` →
`QiskitGroverOperator` → `QiskitAerSimulator` → `QuanifiReport`

1. **`GenerateFlowFile`** — empty content, any schedule (it is just the
   trigger). Wire `success` → `QiskitPhaseOracle`.
2. **`QiskitPhaseOracle`**
   | Property | Value |
   |---|---|
   | Marked State | `110` |
   | Insert Barriers | `false` |
   | Output Format | `qasm3` |

   Standalone mode (no incoming `circuit.format`): emits a bare 3-qubit oracle
   that flips the sign of \|110⟩ only. Wire `success` → `QiskitGroverOperator`;
   auto-terminate `failure` (or route it to a funnel — it fires on a malformed
   Marked State with a `circuit.error` attribute explaining why).
3. **`QiskitGroverOperator`**
   | Property | Value |
   |---|---|
   | Num Iterations | `2` |
   | Insert Barriers | `false` |
   | Output Format | `qasm3` |

   Reads the oracle off the FlowFile content (per `circuit.format`), prepends
   H⊗n, and applies oracle+diffuser `Num Iterations` times via Qiskit's
   `grover_operator`. Forwards `circuit.marked_state`. Wire `success` →
   `QiskitAerSimulator`; auto-terminate `failure` (fires when no oracle is on
   the FlowFile — e.g. wired directly to `GenerateFlowFile`).
4. **`QiskitAerSimulator`** — Shots `1024`, Noise Model `none`. Wire
   `success` → `QuanifiReport`; auto-terminate `failure`.
5. **`QuanifiReport`** — Flow Name = `qiskit-grover-decomposed`.
   Auto-terminate `success`.
6. Start all five; open
   `reports/qiskit-grover-decomposed-report.html`. The histogram peaks at
   `110` and `sim.top_result = 110` — *not* bit-reversed; the simulators
   normalise key order (`sim.bit_order = q0_left`).

**Iteration count:** optimal is ⌊π/4·√2ⁿ⌋ for one marked state — `1` for 2
qubits, `2` for 3, `3` for 4. Too many iterations *overshoots* and the peak
degrades again; that is Grover physics, not a bug.

---

## Flow B — Cirq oracle, Qiskit amplification + simulation

Same canvas as Flow A with **one box swapped**: replace `QiskitPhaseOracle`
with `CirqPhaseOracle`.

| Processor | Property | Value |
|---|---|---|
| `CirqPhaseOracle` | Marked State | `110` |
| | Insert Barriers | `false` |
| | **Output Format** | **`qasm2`** ← the cross-framework hop |
| `QiskitGroverOperator` | Num Iterations | `2` |
| | Output Format | `qasm3` (back inside Qiskit) |
| `QiskitAerSimulator` | Shots | `1024` |
| `QuanifiReport` | Flow Name | `xfw-cirq-oracle-qiskit-grover` |

`QiskitGroverOperator` detects `circuit.format = qasm2` and parses the
Cirq-emitted oracle with Qiskit's qasm2 loader. Everything downstream is
unchanged. Expect the same `110` peak. (Note the qasm2 export decomposes the
multi-controlled Z into CZ + single-qubit gates, so `circuit.gate_count` /
`circuit.depth` differ from Flow A — the *semantics* are identical, the gate
inventory is not.)

The report's **Circuit Diagram** panel is rendered from the operator's qasm3
via Qiskit's matplotlib drawer and shows the **full** H⊗n + (oracle+diffuser)×2
circuit. The Cirq oracle's own SVG is deliberately blanked by
`QiskitGroverOperator` — NiFi merges attributes downstream, and the report
would otherwise prefer that stale SVG, which depicts only the bare oracle.

---

## Flow C — Qiskit oracle, Cirq amplification + simulation

The reverse assembly: keep `QiskitPhaseOracle`, swap the rest of the chain to
Cirq.

| Processor | Property | Value |
|---|---|---|
| `QiskitPhaseOracle` | Marked State | `110` |
| | **Output Format** | **`qasm2`** ← the cross-framework hop |
| `CirqGroverOperator` | Num Iterations | `2` |
| | Output Format | `cirq_json` (back inside Cirq) |
| `CirqSimulator` | Shots | `1024`, Noise Model `none` |
| `QuanifiReport` | Flow Name | `xfw-qiskit-oracle-cirq-grover` |

Again expect the `110` peak with `sim.top_result = 110`, now with
`sim.framework = cirq` in the report header.

---

## Differential testing in one canvas

Run Flows A–C side by side into **separate** `QuanifiReport` flow names, or
fan one `GenerateFlowFile` into all three chains and feed the simulator
outputs pairwise into `QuantumDistributionComparison` — because all engines
emit canonical-order keys, the distributions are directly comparable
(Hellinger ≈ 0, chi-squared verdict `consistent` for ideal runs).

## Troubleshooting

- **The `original` relationship keeps re-marking itself auto-terminated** —
  that's NiFi framework behaviour for *all* Python processors, not a bug:
  `original` is added by NiFi's Java proxy (it receives the **unmodified
  incoming** FlowFile, for provenance) with auto-terminate as its default, and
  the default is re-applied whenever the Python processor is reinitialised on
  start. Leave it terminated — the *result* (counts + `sim.*`) always travels
  on `success`, so `original` can never block the report.
- **Nothing reaches the report, but the logs are clean** — the simulator
  rejected the circuit and routed it to `failure`, which is auto-terminated,
  so the FlowFile vanished. Typical cause: an upstream Output Format the
  simulator can't read (`CirqSimulator` accepts only `cirq_json`/`qasm2`;
  `QrispSimulator` only `qasm2`). The simulators now log the rejection with
  the offending `circuit.format`, so check the processor bulletin (red square,
  top-right of the box) or `logs/nifi-app.log`.
- **Operator routes to `failure` immediately** — its FlowFile had no circuit:
  check the oracle is upstream and its `success` (not `failure`) is wired in.
  The `circuit.error` attribute on the failed FlowFile names the cause.
- **Peak at the bit-reversed target** — you are running a pre-normalisation
  processor build; restart NiFi so the current `nifi_extensions/` code loads,
  and re-generate stale report cards (`just clean-reports`).
- **Circuit Diagram shows only the small oracle, not the full Grover circuit**
  (Flow B) — same cause: an older build of `QiskitGroverOperator` that didn't
  blank the upstream Cirq SVG. Restart NiFi; no venv wipe needed.
- **`ModuleNotFoundError: No module named 'ply'` from a Cirq processor**
  (Flow C) — Cirq's qasm2 importer needs `ply`, which only the cross-framework
  path exercises. All qasm-importing Cirq processors now declare it, but a
  **dependency change does not reach an already-cached venv**: delete the
  processor's cached environment and restart —
  `rm -rf ~/projects/nifi-2.9.0/work/python/extensions/CirqGroverOperator/`
  (same for `CirqPhaseOracle`, `CirqHadamardTransform`, `CirqQFTCircuit`,
  `CirqPhaseEstimation` if they're already on the canvas). First placement
  re-installs deps (~30 s).
- **Processor missing from the add dialog** — the file failed to parse or
  NiFi wasn't restarted; see the skeleton rules in
  [CREATING_PROCESSORS.md](CREATING_PROCESSORS.md).

These three flows are pinned in CI-style tests:
`tests/test_integration.py::TestCrossFramework` and
`tests/test_bit_order.py::TestGroverNonPalindrome`.
