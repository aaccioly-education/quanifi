# NiFi flow configuration guide

This is a hands-on, click-by-click guide to building and running every processor
in [`nifi_extensions/`](../../nifi_extensions/) on the NiFi canvas. It is organized as
**24 flows**, ordered so you can work through them one at a time and exercise
every component at least once. Standalone-friendly processors get their own
small flow; processors that only make sense chained together (the three `*VQE`
pipelines, Cirq's oracle+operator pair, the PennyLane QML lane, the comparison /
assertion pair, …) are grouped into a single combined flow instead of one flow
per processor — per the "category, not name" rule in
[nifi_extensions/AGENTS.md](../../nifi_extensions/AGENTS.md).

> Companion docs: [CREATING_PROCESSORS.md](CREATING_PROCESSORS.md) (how
> processors are built), [DATA_DRIVEN_TESTING.md](DATA_DRIVEN_TESTING.md) (the
> attribute-externalization contract used in Flow 24), and
> [HANDOFF.md](../planning/HANDOFF.md) (design rationale for VQE).

## Before you start

1. **Start NiFi** (`~/projects/nifi-2.9.0/`) and open the canvas in
   a browser (`https://localhost:8443/nifi` or whatever your `nifi.properties`
   sets).
2. **Reports directory** — most flows end in `QuanifiReport` /
   `QiskitCircuitReport` / `QiskitStatevectorSimulator`, which append HTML "run
   cards" to files under `~/projects/quanifi/reports/`. Make sure
   that directory (and `reports/tmp/` for the comparison processors' pairing
   state) exists and is writable by the NiFi process. Open the resulting
   `.html` files directly in a browser and refresh after each run — new cards
   are appended at the top.
3. **General recipe** (applies to every flow below):
   - Drag the **Processor** icon onto the canvas, search for the class name
     (e.g. `QiskitGroverCircuit`), add it.
   - Double-click it → **Properties** tab → set the values from the tables
     below (leave anything not mentioned at its default).
   - Drag from one processor's edge to the next to create a **Connection**;
     in the dialog, tick the relationship(s) named in "Wire" below
     (almost always `success`).
   - For every relationship a processor can emit that you are **not** wiring
     to something downstream (typically `failure`, and the unused side of
     `pass`/`fail` or `success`/`waiting`), open the processor's
     **Settings** tab (or the connection dialog) and tick **automatically
     terminate** — otherwise NiFi refuses to start it.
   - Select every processor in the flow and press the **▶ Start** button (or
     right-click → Start).
   - To inspect a FlowFile, right-click the connection → **List queue** → the
     little "i" icon → **View** (content) / **Attributes** tab.
   - First placement of a processor triggers NiFi to build an isolated venv
     and install `ProcessorDetails.dependencies` — this can take a minute; watch
     the bulletin board (top-right bell icon) for errors.

---

# Qiskit

## Flow 1 — Smoke test: `QiskitQuantumHelloWorld`

**Components:** `QiskitQuantumHelloWorld`
**What it proves:** NiFi can load the processor, its isolated venv has Qiskit,
and you can read FlowFile content/attributes. No upstream wiring needed.

1. Add `QiskitQuantumHelloWorld`.
2. Properties: **Qubit Count** = `2` (default; try `3` later to see a 3-letter
   GHZ string).
3. Add a `GenerateFlowFile` processor upstream (any standard NiFi processor;
   content/schedule don't matter — it's just the trigger) → connect its
   `success` to `QiskitQuantumHelloWorld`.
4. Wire `QiskitQuantumHelloWorld`'s `success` to a funnel (or any processor you
   auto-terminate) so you can list the queue.
5. Start everything. List the queue on the final connection: content is the
   measured GHZ bitstring (e.g. `00` or `11`); attributes carry
   `quantum.bitstring` and `quantum.qubits`.

---

## Flow 2 — Qiskit Grover circuit → simulator → circuit report

**Components:** `QiskitGroverCircuit` → `QiskitAerSimulator` → `QiskitCircuitReport`
**What it proves:** the Tier-1 "circuit builder → simulator → report" shape
that most Qiskit/Cirq flows below reuse, plus the Grover-specific HTML report.

1. **`QiskitGroverCircuit`**
   | Property | Value |
   |---|---|
   | Marked State | `11` |
   | Num Iterations | `1` |
   | Insert Barriers | `false` |
   | Output Format | `qasm3` |

   Wire `success` → `QiskitAerSimulator`.

2. **`QiskitAerSimulator`**
   | Property | Value |
   |---|---|
   | Shots | `1024` |
   | Noise Model | `none` (try `depolarizing` on a second run to see the noise panel) |

   Leave the noise/backend properties at default for an ideal run. Wire
   `success` → `QiskitCircuitReport`.

3. **`QiskitCircuitReport`**
   | Property | Value |
   |---|---|
   | Reports Directory | `~/projects/quanifi/reports` |
   | Flow Name | `qiskit-grover` |

   Auto-terminate `success` (it's the terminal node).

4. Start all three. Open
   `~/projects/quanifi/reports/qiskit-grover.html` — you should see
   a card with the circuit diagram (QASM3), the marked state/iteration count,
   and a measurement-count bar chart dominated by `11`.

---

## Flow 3 — Qiskit Hadamard transform

**Components:** `QiskitHadamardTransform` → `QiskitAerSimulator` → `QuanifiReport`
**What it proves:** standalone-vs-compose mode (run it bare first, then chained
onto an existing circuit) and the generic `QuanifiReport` "simulation" card.

1. **`QiskitHadamardTransform`**
   | Property | Value |
   |---|---|
   | Qubit Count | `3` |
   | Output Format | `qasm3` |

   Feed it from a `GenerateFlowFile` with **empty content** (standalone mode —
   it builds a fresh 3-qubit H⊗3 circuit). Wire `success` → `QiskitAerSimulator`.
2. **`QiskitAerSimulator`** — Shots `1024`, Noise Model `none`. Wire `success`
   → `QuanifiReport`.
3. **`QuanifiReport`** — Reports Directory (default), Flow Name = `qiskit-hadamard`.
   Auto-terminate `success`.
4. Start, then open `qiskit-hadamard.html`. Counts should be roughly uniform
   across all 8 three-bit strings (uniform superposition).
5. **Try compose mode**: place a fresh `QiskitGroverCircuit` (Flow 2 settings)
   *before* `QiskitHadamardTransform` instead of `GenerateFlowFile` — now it
   appends `H` to every qubit of the Grover circuit it receives (it detects the
   incoming `circuit.format` and non-empty content).

---

## Flow 4 — Qiskit QFT circuit

**Components:** `QiskitQFTCircuit` → `QiskitAerSimulator` → `QuanifiReport`

1. **`QiskitQFTCircuit`**
   | Property | Value |
   |---|---|
   | Qubit Count | `3` |
   | Inverse | `false` |
   | Approximation Degree | `0` |
   | Do Swaps | `true` |
   | Insert Barriers | `false` |
   | Output Format | `qasm3` |

   Feed from `GenerateFlowFile` (empty content → standalone mode, fresh 3-qubit
   QFT). Wire `success` → `QiskitAerSimulator`.
2. **`QiskitAerSimulator`** — Shots `1024`. Wire `success` → `QuanifiReport`.
3. **`QuanifiReport`** — Flow Name = `qiskit-qft`. Auto-terminate `success`.
4. Start; open `qiskit-qft.html`. Then flip **Inverse** to `true` and re-run to
   compare QFT vs QFT† — note `circuit.qft_inverse` in the Circuit Attributes
   table changes accordingly.

---

## Flow 5 — Qiskit Phase Estimation

**Components:** `QiskitPhaseEstimation` → `QiskitAerSimulator` → `QuanifiReport`

1. **`QiskitPhaseEstimation`**
   | Property | Value |
   |---|---|
   | Phase Register Size | `3` |
   | Builtin Unitary | `T` |
   | Insert Barriers | `false` |
   | Output Format | `qasm3` |

   Feed from `GenerateFlowFile` with empty content (standalone mode — it
   builds a demo QPE estimating the `T` gate's eigenphase 1/8 with the
   eigenstate `|1⟩` prepared). Wire `success` → `QiskitAerSimulator`.
2. **`QiskitAerSimulator`** — Shots `1024`. Wire `success` → `QuanifiReport`.
3. **`QuanifiReport`** — Flow Name = `qiskit-qpe`. Auto-terminate `success`.
4. Start; open `qiskit-qpe.html`. The **Decoded Result** panel reads the phase
   register off `sim.top_result` and shows the answer directly: with
   `Builtin Unitary = T` and a 3-qubit phase register, *Estimated phase (φ)* =
   `0.1250 (= 1/8)`, readout bits `001`. (`sim.top_result` itself is the full
   4-qubit string `1100` — the 4th qubit is the `|1⟩` eigenstate; the decoder
   extracts just the phase bits.) Switch **Builtin Unitary** to `S` (expect
   `1/4`, bits `010`) or `Z` (expect `1/2`, bits `100`) and re-run to see the
   decoded phase shift.

   *Note:* the circuit builder no longer self-measures — measurement is the
   simulator's job. Earlier builds called `measure_all()` in both stages, which
   produced a garbled doubled readout like `1100 1100`.

---

## Flow 6 — Qiskit Amplitude Amplification

**Components:** `QiskitAmplitudeAmplification` → `QiskitAerSimulator` → `QuanifiReport`

1. **`QiskitAmplitudeAmplification`**
   | Property | Value |
   |---|---|
   | Marked State | `11` |
   | Num Iterations | `1` |
   | Insert Barriers | `false` |
   | Output Format | `qasm3` |

   Feed from `GenerateFlowFile` with empty content — **standalone mode**: it
   builds its own phase oracle for `Marked State` and amplifies it. (You can
   later feed it a `circuit.format`-bearing oracle, e.g. from
   `QiskitGroverCircuit` with `Num Iterations = 0`, to exercise **compose
   mode** instead — `circuit.mode` flips from `standalone` to `compose` and
   `circuit.marked_state` is no longer set.) Wire `success` → `QiskitAerSimulator`.
2. **`QiskitAerSimulator`** — Shots `1024`. Wire `success` → `QuanifiReport`.
3. **`QuanifiReport`** — Flow Name = `qiskit-amplitude-amplification`.
   Auto-terminate `success`.
4. Start; open the report. `circuit.mode = standalone` and
   `circuit.marked_state = 11` should appear in the Circuit Attributes table,
   and the histogram should peak at `11`.

---

## Flow 7 — Qiskit State Preparation → Statevector Simulator

**Components:** `QiskitStatePreparation` → `QiskitStatevectorSimulator`
**What it proves:** the exact-statevector lane (no shots/noise — it's its own
self-reporting terminal node, unlike the shot-based simulators above).

1. **`QiskitStatePreparation`**
   | Property | Value |
   |---|---|
   | State Type | `ghz` |
   | Qubit Count | `3` |
   | Output Format | `qasm3` |

   (`Target Basis State` / `Amplitudes` are ignored for `ghz`; try `basis` with
   `Target Basis State = 3` or `custom` with `Amplitudes = [1,0,0,1]` on later
   runs.) Feed from `GenerateFlowFile`. Wire `success` →
   `QiskitStatevectorSimulator`.
2. **`QiskitStatevectorSimulator`**
   | Property | Value |
   |---|---|
   | Reports Directory | `~/projects/quanifi/reports` |
   | Flow Name | `qiskit-statevector` |
   | Probability Threshold | `0.001` |
   | Max States | `32` |

   Auto-terminate `success` — there is no downstream `QuanifiReport` here; this
   processor writes its own HTML directly.
3. Start both. Open `qiskit-statevector-html` (file name is
   `{flow_name}-statevector.html`, i.e.
   `reports/qiskit-statevector-statevector.html`). For a 3-qubit GHZ state you
   should see exactly two rows, `000` and `111`, each at probability ≈ 0.5,
   with amplitude/phase columns.

---

## Flow 8 — Qiskit Grover Search (all-in-one)

**Components:** `QiskitGroverSearch` → `QuanifiReport`
**What it proves:** the "all-in-one / solver" category — a single processor
that builds *and* simulates, emitting both `circuit.*`-style and `grover.*`
algorithm-specific attributes (no separate simulator needed).

1. **`QiskitGroverSearch`**
   | Property | Value |
   |---|---|
   | Marked State | `101` |
   | Num Iterations | `2` |
   | Shots | `1024` |
   | Insert Barriers | `false` |

   Feed from `GenerateFlowFile`. Wire `success` → `QuanifiReport`.
2. **`QuanifiReport`** — Flow Name = `qiskit-grover-search`. Note: this
   processor does **not** set `report.type`, so `QuanifiReport` falls back to
   its `_default` card (raw JSON content + an "All Attributes" table) rather
   than the simulation layout — that's expected; look for `grover.top_result`
   and `grover.top_probability` in the attributes table, which should show
   `101` dominating. Auto-terminate `success`.
3. Start both and inspect the report.

---

## Flow 9 — Qiskit VQE pipeline (one combined flow)

**Components:** `QiskitHamiltonian` → `QiskitAnsatz` → `QiskitVQE` → `QuanifiReport`
**Why one flow:** per `nifi_extensions/AGENTS.md` §"Variational pipeline", VQE
is *decomposed* — the Hamiltonian and ansatz stages are reusable building
blocks, but `QiskitVQE` is a **strict solver** that refuses to run without both
of them on the FlowFile (`failure` + `vqe.error` otherwise). Testing any one in
isolation from the others isn't meaningful, so they're documented as a single
chain.

1. **`QiskitHamiltonian`**
   | Property | Value |
   |---|---|
   | Hamiltonian | `Z0 + Z1 + 0.5 X0 X1` |
   | Num Qubits | `0` (auto-derive → 2) |

   Feed from `GenerateFlowFile`. Wire `success` → `QiskitAnsatz`; auto-terminate
   `failure` (only triggers on a DSL parse error).
2. **`QiskitAnsatz`**
   | Property | Value |
   |---|---|
   | Ansatz Type | `efficient_su2` |
   | Reps | `2` |
   | Entanglement | `full` |
   | Num Qubits | `2` (ignored — **chain mode** activates because the incoming
   FlowFile already carries `hamiltonian.format` + `hamiltonian.num_qubits`, so
   the qubit count comes from the Hamiltonian and its content passes through
   untouched) |

   Wire `success` → `QiskitVQE`; auto-terminate `failure`.
3. **`QiskitVQE`**
   | Property | Value |
   |---|---|
   | Optimizer | `COBYLA` |
   | Max Iterations | `100` |
   | Shots | `1024` |
   | Initial Parameters | `random` |

   This is the strict solver — it requires `hamiltonian.format =
   sparse_pauli_op_json` *and* `ansatz.qpy_b64` on the FlowFile (both supplied
   by the two upstream stages) and routes to `failure`/`vqe.error` if either is
   missing. Wire `success` → `QuanifiReport`; auto-terminate `failure`.
4. **`QuanifiReport`** — Flow Name = `qiskit-vqe`. Auto-terminate `success`.
5. Start all four. Open `qiskit-vqe.html`; look for `vqe.optimal_value`,
   `vqe.converged`, `vqe.num_iterations`, and the trained circuit's measurement
   histogram (it uses the `simulation` card layout because `QiskitVQE` sets
   `report.type = simulation`).
6. **To see the strict-solver failure path**, temporarily disconnect
   `QiskitHamiltonian → QiskitAnsatz` and feed `QiskitVQE` directly from
   `GenerateFlowFile`: it routes to `failure` with `vqe.error = "no Hamiltonian
   on the FlowFile..."`. Reconnect afterwards.

---

# Cirq

## Flow 10 — Cirq Grover circuit → simulator

**Components:** `CirqGroverCircuit` → `CirqSimulator` → `QuanifiReport`
**What it proves:** the Cirq equivalent of Flow 2's shape, plus
`circuit.svg`/Cirq-JSON content (which `QuanifiReport` prefers over QASM).

1. **`CirqGroverCircuit`**
   | Property | Value |
   |---|---|
   | Marked State | `11` |
   | Num Iterations | `1` |
   | Insert Barriers | `false` |
   | Output Format | `cirq_json` |

   Feed from `GenerateFlowFile`. Wire `success` → `CirqSimulator`.
2. **`CirqSimulator`**
   | Property | Value |
   |---|---|
   | Shots | `1024` |
   | Noise Model | `none` |
   | Error Probability | `0.01` (ignored when Noise Model = none) |
   | Damping Gamma | `0.05` (ignored unless `amplitude_damp`) |
   | Readout Error Rate | `0.0` |

   Wire `success` → `QuanifiReport`; auto-terminate `failure` (only fires for
   an unsupported `circuit.format`).
3. **`QuanifiReport`** — Flow Name = `cirq-grover`. Auto-terminate `success`.
4. Start; open `cirq-grover.html`. The card should show the SVG circuit
   diagram (Cirq renders `circuit.svg`, preferred over QASM) and a histogram
   peaking at `11`. Re-run with **Output Format = `qasm2`** on
   `CirqGroverCircuit` to see the alternate serialisation path (and
   `circuit.qasm2` show up instead).

---

## Flow 11 — Cirq Phase Oracle + Grover Operator (one combined flow)

**Components:** `CirqPhaseOracle` → `CirqGroverOperator` → `CirqSimulator` → `QuanifiReport`
**Why one flow:** `CirqGroverOperator` is explicitly designed to consume the
oracle that `CirqPhaseOracle` produces (it prepends the H⊗n layer and applies
oracle+diffuser); neither is a meaningful standalone test on its own.

1. **`CirqPhaseOracle`**
   | Property | Value |
   |---|---|
   | Marked State | `101` |
   | Insert Barriers | `false` |
   | Output Format | `cirq_json` |

   Feed from `GenerateFlowFile` with empty content (standalone mode — bare
   oracle on a fresh 3-qubit register). Wire `success` → `CirqGroverOperator`.
2. **`CirqGroverOperator`**
   | Property | Value |
   |---|---|
   | Num Iterations | `2` |
   | Insert Barriers | `false` |
   | Output Format | `cirq_json` |

   It reads the oracle off the FlowFile content (per `circuit.format`,
   defaulting to `cirq_json`), prepends `H⊗n`, and applies oracle+diffuser
   `Num Iterations` times. Wire `success` → `CirqSimulator`.
3. **`CirqSimulator`** — Shots `1024`, Noise Model `none`. Wire `success` →
   `QuanifiReport`; auto-terminate `failure`.
4. **`QuanifiReport`** — Flow Name = `cirq-grover-operator`. Auto-terminate
   `success`.
5. Start all four; open the report. `circuit.marked_state = 101` should be
   forwarded all the way through (both downstream processors re-emit it when
   present), and the histogram should peak at `101`.

---

## Flow 12 — Cirq Hadamard transform

**Components:** `CirqHadamardTransform` → `CirqSimulator` → `QuanifiReport`

1. **`CirqHadamardTransform`** — Qubit Count `3`, Output Format `cirq_json`.
   Feed from `GenerateFlowFile` with empty content (standalone — fresh H⊗3).
   Wire `success` → `CirqSimulator`.
2. **`CirqSimulator`** — Shots `1024`. Wire `success` → `QuanifiReport`.
3. **`QuanifiReport`** — Flow Name = `cirq-hadamard`. Auto-terminate `success`.
4. Start; counts should be roughly uniform over all 8 three-bit strings.
5. **Compose mode**: insert a `CirqGroverCircuit` (Flow 10 settings) before it
   — it now appends `H` to the existing circuit's qubits and forwards
   `circuit.marked_state`.

---

## Flow 13 — Cirq QFT circuit

**Components:** `CirqQFTCircuit` → `CirqSimulator` → `QuanifiReport`

1. **`CirqQFTCircuit`**
   | Property | Value |
   |---|---|
   | Qubit Count | `3` |
   | Inverse | `false` |
   | Do Swaps | `true` |
   | Insert Barriers | `false` |
   | Output Format | `cirq_json` |

   Feed from `GenerateFlowFile` (empty content → standalone). Wire `success` →
   `CirqSimulator`.
2. **`CirqSimulator`** — Shots `1024`. Wire `success` → `QuanifiReport`.
3. **`QuanifiReport`** — Flow Name = `cirq-qft`. Auto-terminate `success`.
4. Start; toggle **Inverse** between runs and watch `circuit.qft_inverse` flip
   in the report's attribute table.

---

## Flow 14 — Cirq Phase Estimation

**Components:** `CirqPhaseEstimation` → `CirqSimulator` → `QuanifiReport`

1. **`CirqPhaseEstimation`**
   | Property | Value |
   |---|---|
   | Phase Register Size | `3` |
   | Builtin Unitary | `T` |
   | Insert Barriers | `false` |
   | Output Format | `cirq_json` |

   Feed from `GenerateFlowFile` with empty content (standalone — builtin `T`
   gate, expected dominant readout `001`). Wire `success` → `CirqSimulator`.
2. **`CirqSimulator`** — Shots `1024`. Wire `success` → `QuanifiReport`.
3. **`QuanifiReport`** — Flow Name = `cirq-qpe`. Auto-terminate `success`.
4. Start; open the report and confirm the `001`/`010`/`100` readouts as you
   cycle **Builtin Unitary** through `T`/`S`/`Z`. You can also feed it a
   circuit from `CirqGroverCircuit` upstream to exercise **compose mode**
   (the incoming circuit becomes the unitary; `circuit.qpe_builtin` then reads
   `compose`).

---

## Flow 15 — Cirq VQE pipeline (one combined flow)

**Components:** `CirqHamiltonian` → `CirqAnsatz` → `CirqVQE` → `QuanifiReport`
**Why one flow:** same rationale as Flow 9 — `CirqVQE` is a strict solver that
requires both upstream stages.

1. **`CirqHamiltonian`** — Hamiltonian `Z0 + Z1 + 0.5 X0 X1`, Num Qubits `0`.
   Feed from `GenerateFlowFile`. Wire `success` → `CirqAnsatz`; auto-terminate
   `failure`.
2. **`CirqAnsatz`**
   | Property | Value |
   |---|---|
   | Ansatz Type | `efficient_su2` |
   | Reps | `2` |
   | Entanglement | `full` |
   | Num Qubits | `2` (ignored — chain mode picks up `hamiltonian.num_qubits`) |

   Wire `success` → `CirqVQE`; auto-terminate `failure`.
3. **`CirqVQE`**
   | Property | Value |
   |---|---|
   | Optimizer | `COBYLA` |
   | Max Iterations | `100` |
   | Shots | `1024` |
   | Initial Parameters | `random` |

   Strict solver: requires `hamiltonian.format = sparse_pauli_op_json` (content
   parsed via `wire_to_terms`) *and* `ansatz.cirq_json` on the FlowFile. Wire
   `success` → `QuanifiReport`; auto-terminate `failure`.
4. **`QuanifiReport`** — Flow Name = `cirq-vqe`. Auto-terminate `success`.
5. Start; open `cirq-vqe.html`. Compare `vqe.optimal_value` /
   `vqe.elapsed_seconds` against the Qiskit run from Flow 9 — same Hamiltonian
   expression, two independently hand-rolled solvers (`CirqVQE` is
   `cirq.Simulator` + `scipy.optimize` by hand vs. `qiskit_algorithms.VQE`).

---

# Qrisp

## Flow 16 — Qrisp Grover Search (all-in-one)

**Components:** `QrispGroverSearch` → `QuanifiReport`

1. **`QrispGroverSearch`**
   | Property | Value |
   |---|---|
   | Marked State | `11` |
   | Num Iterations | `0` (let Qrisp pick the optimal count automatically) |
   | Shots | `1024` |

   Feed from `GenerateFlowFile`. Wire `success` → `QuanifiReport`.
2. **`QuanifiReport`** — Flow Name = `qrisp-grover`. Note: like
   `QiskitGroverSearch`, this all-in-one does **not** set `report.type`, so you
   get the `_default` card; check `grover.framework = qrisp`,
   `sim.top_result`, `sim.top_probability` and `circuit.num_iterations`
   (= `"auto"` when `Num Iterations = 0`) in the attribute table. Auto-terminate
   `success`.
3. Start both; the content is a JSON probability distribution (Qrisp returns
   probabilities, not raw counts) peaking at `11`.

---

## Flow 17 — Qrisp QFT circuit → simulator

**Components:** `QrispQFTCircuit` → `QrispSimulator` → `QuanifiReport`
**Note:** `QrispQFTCircuit` always emits `circuit.format = qasm2` — the *only*
format `QrispSimulator` accepts (anything else routes it to `failure` with
`sim.error`), so this pairing is the path of least resistance.

1. **`QrispQFTCircuit`**
   | Property | Value |
   |---|---|
   | Qubit Count | `3` |
   | Inverse | `false` |
   | Do Swaps | `true` |

   Feed from `GenerateFlowFile`. Wire `success` → `QrispSimulator`.
2. **`QrispSimulator`** — Shots `1024`. Wire `success` → `QuanifiReport`;
   auto-terminate `failure` (fires only on an unsupported `circuit.format` —
   won't happen here since the upstream always emits `qasm2`).
3. **`QuanifiReport`** — Flow Name = `qrisp-qft`. Auto-terminate `success`.
4. Start; open the report and toggle **Inverse**, watching
   `circuit.qft_inverse` change.

---

## Flow 18 — Qrisp Phase Estimation (all-in-one)

**Components:** `QrispPhaseEstimation` → `QuanifiReport`
**Note:** unlike its Qiskit/Cirq counterparts, this processor has **no compose
mode** — it always builds its own demo QPE for a builtin unitary and emits its
own `qpe.*` result attributes plus a probability-distribution content (no
simulator needed downstream).

1. **`QrispPhaseEstimation`**
   | Property | Value |
   |---|---|
   | Phase Register Size | `3` |
   | Builtin Unitary | `T` |
   | Shots | `1024` |

   Feed from `GenerateFlowFile`. Wire `success` → `QuanifiReport`.
2. **`QuanifiReport`** — Flow Name = `qrisp-qpe`. No `report.type` is set here
   either, so expect the `_default` card; look at `qpe.top_phase`,
   `qpe.top_probability`, `qpe.precision` (≈ `0.125` for `T`) in the attribute
   table. Auto-terminate `success`.
3. Start; cycle **Builtin Unitary** through `T`/`S`/`Z` (expected `qpe.top_phase`
   ≈ `0.125`/`0.25`/`0.5`) between runs.

---

## Flow 19 — Qrisp VQE pipeline (one combined flow)

**Components:** `QrispHamiltonian` → `QrispAnsatz` → `QrispVQE` → `QuanifiReport`
**Why one flow:** same strict-solver rationale as Flows 9/15. Note Qrisp's
ansatz "carrier" is a *spec*, not a circuit (`ansatz.format = qrisp_spec`) —
`QrispVQE` rebuilds the callable `ansatz_function(qv, theta)` from
`ansatz.type`/`ansatz.reps`/`ansatz.entanglement` via `QrispAnsatz`'s
`_build_ansatz_function`.

1. **`QrispHamiltonian`** — Hamiltonian `Z0 + Z1 + 0.5 X0 X1`, Num Qubits `0`.
   Feed from `GenerateFlowFile`. Wire `success` → `QrispAnsatz`; auto-terminate
   `failure`.
2. **`QrispAnsatz`**
   | Property | Value |
   |---|---|
   | Ansatz Type | `efficient_su2` |
   | Reps | `2` |
   | Entanglement | `full` |
   | Num Qubits | `2` (ignored — chain mode reads `hamiltonian.num_qubits`) |

   Wire `success` → `QrispVQE`. (No `failure` relationship is declared on this
   processor — nothing to auto-terminate.)
3. **`QrispVQE`**
   | Property | Value |
   |---|---|
   | Optimizer | `COBYLA` |
   | Max Iterations | `100` |
   | Shots | `1024` |
   | Initial Parameters | `random` |

   Strict solver: requires `hamiltonian.format = sparse_pauli_op_json` *and*
   `ansatz.format = qrisp_spec`. Wire `success` → `QuanifiReport`;
   auto-terminate `failure`.
4. **`QuanifiReport`** — Flow Name = `qrisp-vqe`. Auto-terminate `success`.
5. Start all four; open `qrisp-vqe.html`. `QrispVQE` calls Qrisp's first-class
   `VQEProblem.run(...)` — compare `vqe.optimal_value` /
   `vqe.elapsed_seconds` against Flows 9 and 15 for a three-way cross-framework
   comparison on the *identical* Hamiltonian expression and ansatz spec.

---

# PennyLane (QML lane)

## Flow 20 — Feature embedding → variational ansatz → expectation (QNode chain)

**Components:** `PennylaneFeatureEmbedding` → `PennylaneVariationalAnsatz` →
`PennylaneExpectation` → `QuanifiReport`
**Why one flow:** these three chain end to end and only make sense as a
pipeline — PennyLane is QNode-centric (its native output is expectation values
/ probabilities, not raw shot counts), and `circuit.format = qasm2` is the glue
between every stage.

1. **`PennylaneFeatureEmbedding`**
   | Property | Value |
   |---|---|
   | Embedding | `angle` |
   | Features | `[0.1, 0.5, 0.9]` (used as a fallback only — feed real content below) |
   | Rotation | `Y` |

   Feed from a `GenerateFlowFile` whose **content** is a JSON array of numbers,
   e.g. `[0.2, 0.4, 0.6]` (this is preferred over the `Features` property
   fallback — set `GenerateFlowFile`'s "Custom Text" / content accordingly and
   `mime.type = application/json` if you like). One qubit is allocated per
   feature → 3 qubits here. Wire `success` → `PennylaneVariationalAnsatz`;
   auto-terminate `failure`.
2. **`PennylaneVariationalAnsatz`**
   | Property | Value |
   |---|---|
   | Ansatz | `strongly_entangling` |
   | Num Qubits | `3` (ignored — **compose mode** activates because the
   incoming `circuit.format = qasm2`; qubit count is taken from the embedding
   circuit) |
   | Num Layers | `2` |
   | Weight Seed | `42` |

   Wire `success` → `PennylaneExpectation`; auto-terminate `failure`.
3. **`PennylaneExpectation`**
   | Property | Value |
   |---|---|
   | Observable | `Z` |
   | Shots | *(leave blank for an exact analytic result, or set e.g. `1024` to sample)* |

   This is the PennyLane execution lane (`sim.framework = pennylane`) —
   strictly requires `circuit.format = qasm2` (both upstream processors emit
   it). Wire `success` → `QuanifiReport`; auto-terminate `failure`.
4. **`QuanifiReport`** — Flow Name = `pennylane-qml`. It uses the `simulation`
   card layout (`report.type = simulation`). Auto-terminate `success`.
5. Start all four. Open `pennylane-qml.html`: instead of plain shot counts you
   should see `sim.observable = Z`, `sim.expectations` (a per-qubit `⟨Z⟩` JSON
   map), plus the usual `sim.top_result`/`sim.top_probability` derived from the
   probability distribution. Try **Observable = `X`** or **`Y`** to see the
   expectation values change, and compare standalone vs. compose mode on
   `PennylaneVariationalAnsatz` by disconnecting the embedding stage and
   feeding it directly from `GenerateFlowFile` (it then builds a fresh
   `Num Qubits`-sized ansatz with no embedding in front).

---

## Flow 21 — PennyLane dataset loader (standalone generator)

**Components:** `PennylaneDatasetLoader`
**Why separate:** it's a *source* processor. Its natural consumer is the
training lane: wire it straight into `PennylaneVariationalClassifier`
(Flow 33), which reads the whole record array from the FlowFile content. To
feed the *circuit* lane instead (Flow 20), bridge one record's `features`
array into FlowFile content with standard NiFi `SplitJson` +
`EvaluateJsonPath`, the same pattern Flow 24 uses for test cases.

1. **`PennylaneDatasetLoader`**
   | Property | Value |
   |---|---|
   | Mode | `synthetic` |
   | Num Samples | `20` |
   | Num Features | `3` |
   | Class Separation | `2.0` |
   | Seed | `0` |

   Feed from `GenerateFlowFile`. Wire `success` to a funnel/auto-terminated
   processor; auto-terminate `failure`.
2. Start, list the queue: content is a JSON array of 20
   `{"features": [...], "label": 0|1}` records; attributes show
   `dataset.count = 20`, `dataset.num_features = 3`, `dataset.num_classes = 2`,
   `dataset.framework = pennylane`.
3. Switch **Mode** to `inline` and set **Records** to a literal JSON array
   (e.g. `[{"features":[0,0,0],"label":0},{"features":[1,1,1],"label":1}]`) to
   exercise the second code path — `dataset.mode` should now read `inline` and
   `dataset.count = 2`.

---

# Cross-framework comparison & equivalence

## Flow 22 — Unitary equivalence: Qiskit vs. Cirq Grover circuit

**Components:** `QuanifiUnitary` (×2) → `QuantumUnitaryComparison` → `QuanifiReport`
**What it proves:** that two circuit-producing processors from *different*
frameworks build mathematically equivalent unitaries (up to global phase) —
exercising `QuanifiUnitary`'s Qiskit→Cirq qubit-order normalisation
(`reverse_bits()`) and the pairing/state-persistence machinery in
`QuantumUnitaryComparison`.

1. **Branch A** — reuse/duplicate the `QiskitGroverCircuit` from Flow 2 with
   **Marked State = `11`, Num Iterations = `1`, Output Format = `qasm3`**, and
   set its **Num Iterations property to `0`** so the resulting circuit is just
   the oracle+state-prep layer (a deterministic, *unmeasured* circuit — the
   form `QuanifiUnitary` needs). Wire `success` → a `QuanifiUnitary` instance
   ("Unitary A").
2. **Branch B** — a `CirqGroverCircuit` with the **same** `Marked State = 11`,
   `Num Iterations = 0`, `Output Format = cirq_json`. Wire `success` → a second
   `QuanifiUnitary` instance ("Unitary B").
3. **Both `QuanifiUnitary` instances**
   | Property | Value |
   |---|---|
   | Max Qubits | `12` |

   Each requires `circuit.format` on the FlowFile (fails clearly with
   `unitary.error_type = missing_circuit_format_attribute` otherwise — both
   branches supply it). Wire each `success` → the **same**
   `QuantumUnitaryComparison` instance; auto-terminate `failure` on both.
4. **`QuantumUnitaryComparison`**
   | Property | Value |
   |---|---|
   | State Directory | `~/projects/quanifi/reports/tmp/quanifi_unitary_state` |
   | Comparison Label | `grover-2q-equiv` |
   | Framework Label | `${unitary.source_format}` (default — auto-detects `qasm3` vs `cirq_json`) |
   | Tolerance | `1e-9` |

   It pairs FlowFiles by **Comparison Label** (both branches must use the same
   value/instance) and persists the first arrival to `State Directory` until
   the second shows up. Wire `success` → `QuanifiReport`. The `failure`
   relationship covers *both* "first of pair, waiting" *and* real errors
   (`not_a_unitary_flowfile`, `json_parse_failure`, `state_load_failure`) — per
   its docstring, just auto-terminate it (you can inspect `equiv.status =
   waiting` on those FlowFiles if curious before terminating).
5. **`QuanifiReport`** — Flow Name = `unitary-equivalence`. Note: this
   processor doesn't set `report.type`, so it lands in the `_default` card —
   look for `equiv.equivalent = true`, `equiv.process_fidelity ≈ 1.0000000000`,
   and `equiv.global_phase_degrees` in the attributes table. Auto-terminate
   `success`.
6. Start everything, run **both branches once** (so a pair completes), then
   open the report. `equiv.equivalent` should read `true` — Qiskit and Cirq
   build the same Grover oracle+state-prep unitary (modulo the
   `reverse_bits()` qubit-order fix `QuanifiUnitary` applies to Qiskit
   circuits).

---

## Flow 23 — Distribution comparison + assertion gate (one combined flow)

**Components:** `QuantumDistributionComparison` → `QuantumAssertion` → `QuanifiReport`
**Why one flow:** `QuantumAssertion` is explicitly designed to sit immediately
downstream of `QuantumDistributionComparison` (it reads `compare.*` attributes
and is the natural pass/fail gate on top of it) — testing it without a real
comparison upstream means hand-crafting `compare.*` attributes yourself.

1. **Branch A** — `QiskitGroverCircuit` (`Marked State = 11`,
   `Num Iterations = 1`, `Output Format = qasm3`) → `QiskitAerSimulator`
   (`Shots = 1024`). This is the same shape as Flow 2's first two stages — you
   can reuse those instances and fan their `success` out to a second
   destination.
2. **Branch B** — `CirqGroverCircuit` (`Marked State = 11`,
   `Num Iterations = 1`, `Output Format = cirq_json`) → `CirqSimulator`
   (`Shots = 1024`). Likewise reusable from Flow 10.
3. Wire **both** branches' final `success` into a single
   **`QuantumDistributionComparison`** instance.
   | Property | Value |
   |---|---|
   | Reports Directory | `~/projects/quanifi/reports` |
   | Flow Name | `grover-qiskit-vs-cirq` |
   | State Directory | `~/projects/quanifi/reports/tmp/quanifi_compare_state` |
   | Comparison Label | `grover-comparison` |
   | Framework Label | `${grover.framework}` (default; falls back to
   auto-labels `run-1`/`run-2` since these simulator FlowFiles don't actually
   carry `grover.framework` — that's fine for a quick smoke test, but if you
   want clean `qiskit`/`cirq` labels in the report, override this to a literal
   per-branch value, e.g. by adding an `UpdateAttribute` on each branch setting
   `grover.framework` to `qiskit`/`cirq` before the comparison) |

   Per its docstring, **auto-terminate `failure`** — it carries both the
   benign "first of pair, waiting" case (`compare.status = waiting`) and real
   parse errors. Wire `success` → `QuantumAssertion`.
4. **`QuantumAssertion`**
   | Property | Value |
   |---|---|
   | Hellinger Threshold | `0.10` |
   | Check Ground Truth | `true` |
   | Reports Directory | `~/projects/quanifi/reports` |
   | Flow Name | `grover-qiskit-vs-cirq` |

   It overrides the default relationships entirely — there is **no**
   `success`/`failure` here, only **`pass`** and **`fail`**. Wire both to a
   `QuanifiReport` (or to separate funnels if you'd rather just watch the
   queues). Note `Check Ground Truth = true` requires a `test.expected`
   attribute to actually assert anything; since these FlowFiles don't carry
   one, that half of the check is silently skipped and the verdict rests on the
   Hellinger-distance check alone.
5. **`QuanifiReport`** — Flow Name = `grover-assertion`. Look for
   `assert.verdict` (`PASS`/`FAIL`/`DISAGREE`), `assert.hellinger`,
   `assert.top_result_a`/`assert.top_result_b` in the attributes (`_default`
   card — `QuantumAssertion` doesn't set `report.type`). Auto-terminate
   `success`.
6. Start everything and trigger **both branches once**. Because Qiskit and
   Cirq use different bit-order conventions for `11` on a 2-qubit Grover
   search, you may see `compare.agreement = false` on the *raw* label
   comparison even though the underlying physics agrees — that's the
   "Gotchas for the (future) vote" bit-order caveat called out in
   [DATA_DRIVEN_TESTING.md](DATA_DRIVEN_TESTING.md); it's a good opportunity to
   see `assert.verdict = DISAGREE` / `FAIL` and read the `assert.reason` text.

---

## Flow 24 — Data-driven test case generation

**Components:** `QuantumTestCaseSource`
**Why standalone here:** wiring its output into the parametrized Grover
branches end-to-end requires three *standard* NiFi processors
(`SplitJson` → `EvaluateJsonPath` → fan-out to `${grover.*}`-driven branches) —
that full recipe is already documented as the canvas-wiring diagram in
[DATA_DRIVEN_TESTING.md](DATA_DRIVEN_TESTING.md) §3. Run it standalone first to
see what it produces, then follow that doc to wire it up for real differential
runs (which would ultimately feed Flow 23's comparison/assertion pair, closing
the loop).

1. **`QuantumTestCaseSource`**
   | Property | Value |
   |---|---|
   | Test Matrix | `{"grover.marked_state": ["00", "11", "000"], "grover.num_iterations": ["1", "2"]}` (the default — a parameter-axes spec; its Cartesian product yields 6 cases) |
   | Case ID Prefix | `case` |
   | Partition Label | *(leave blank)* |
   | Run ID | *(leave blank — auto-generates a timestamp-based id per trigger)* |

   Feed from `GenerateFlowFile`. Wire `success` to a funnel; auto-terminate
   `failure` (fires on bad JSON or a zero-case expansion).
2. Start, list the queue: content is a JSON array of 6 row objects, each
   stamped with `test.case_id` (`case-000` … `case-005`), `test.run_id`
   (shared across the batch), and the two axis attributes per row; FlowFile
   attributes show `testsource.count = 6`, `testsource.mode = axes`.
3. **Try the explicit-table form** — switch **Test Matrix** to a JSON array,
   e.g.
   `[{"grover.marked_state": "0000", "test.partition": "all-zeros", "test.expected": "0000"}, {"grover.marked_state": "1", "test.partition": "n=1", "test.expected": "1"}]`
   — now `testsource.mode = table`, `testsource.count = 2`, and each row
   carries its own `test.partition`/`test.expected` (a per-row ground-truth
   oracle that `QuantumAssertion` in Flow 23 can later check via
   `Check Ground Truth = true`).

---


# Problem encoders (data in, solution out)

These two framework-agnostic processors are the "no Pauli strings" entry
point: raw problem data becomes a Hamiltonian in the shared wire format
(`hamiltonian.format = sparse_pauli_op_json`), which any `*QAOA` (or `*VQE`)
solver consumes. Swapping the solver framework is a one-processor change.

## Flow 25 — MaxCut: graph edges → QAOA → report

**Components:** `MaxCutProblem` → `QiskitQAOA` → `QuanifiReport`
**What it proves:** a user goes from an edge list to an optimal graph
partition without writing a Hamiltonian: the encoder emits the MaxCut Ising
operator whose minimum eigenvalue is exactly `-max_cut`.

1. **`MaxCutProblem`**
   | Property | Value |
   |---|---|
   | Edges | `[[0, 1], [1, 2], [0, 2]]` (a triangle; default) |
   | Num Nodes | `0` (derive from the edges) |

   Feed from a `GenerateFlowFile` trigger. To drive it from data instead,
   put the JSON edge list in the FlowFile *content*: content overrides the
   property, which is what makes data-driven runs (Flow 24 style) work.
   Wire `success` → `QiskitQAOA`; auto-terminate `failure`.
2. **`QiskitQAOA`** — defaults are fine (Layers `2`). Wire `success` →
   `QuanifiReport`; auto-terminate `failure`.
3. **`QuanifiReport`** — Flow Name = `maxcut-qaoa`. Auto-terminate `success`.
4. Start all three. In `maxcut-qaoa.html` the QAOA panel shows
   `qaoa.exact_minimum = -2.0` (the triangle's max cut is 2),
   `qaoa.approximation_ratio ≥ 0.99`, and a `qaoa.best_measurement` that is
   any 3-bit string except `000`/`111` (all balanced partitions of a triangle
   cut 2 edges).
5. Swap `QiskitQAOA` for `CirqQAOA`, `QrispQAOA`, or `PennylaneQAOA`: the
   wire format is byte-identical, so no reconfiguration is needed.

---

## Flow 26 — QUBO matrix → QAOA (framework-swappable)

**Components:** `QuboToHamiltonian` → `QiskitQAOA` → `QuanifiReport`
**What it proves:** QUBO is the lingua franca of combinatorial optimization
(portfolio selection, scheduling, routing all reduce to it); this one adapter
makes any such problem solvable by every QAOA processor. The ground bitstring
*is* the binary solution vector.

1. **`QuboToHamiltonian`**
   | Property | Value |
   |---|---|
   | QUBO Matrix | `[[-1, 2], [0, -1]]` (default) |

   For this Q, `f(00) = 0`, `f(01) = f(10) = -1`, `f(11) = 0`: pick exactly
   one of the two variables. As with Flow 25, a JSON 2D array in the FlowFile
   content overrides the property. Wire `success` → `QiskitQAOA`.
2. **`QiskitQAOA`** → **`QuanifiReport`** (Flow Name = `qubo-qaoa`), as in
   Flow 25.
3. Expect `qaoa.exact_minimum = -1.0` and `qaoa.best_measurement` = `01` or
   `10`. Read the bitstring left to right: character *i* is variable *i*
   (`sim.bit_order = q0_left`).

---

# Entanglement & factoring demos

The learner ladder: Bell states (first entanglement), teleportation (first
protocol), Shor (the famous one). All three are all-in-one processors that
need only a `GenerateFlowFile` trigger.

## Flow 27 — Bell state

**Components:** `QiskitBellState` → `QuanifiReport`
**What it proves:** entanglement in one box: two qubits, two outcomes, perfect
correlation.

1. **`QiskitBellState`**
   | Property | Value |
   |---|---|
   | Bell State | `phi_plus` |
   | Shots | `1024` |

   Wire `success` → `QuanifiReport` (Flow Name = `bell`); auto-terminate
   `failure`.
2. Start. The report's counts chart shows only `00` and `11`, roughly 50/50,
   and `bell.correlation = 1.0000`: every shot landed on the correlated pair.
3. Try the other three states: `psi_plus`/`psi_minus` flip the pair to
   `01`/`10`; the correlation stays 1.0.

---

## Flow 28 — Quantum teleportation

**Components:** `QiskitTeleportation` → `QuanifiReport`
**What it proves:** a real dynamic-circuit protocol: mid-circuit measurement
plus classically conditioned corrections, self-verified.

1. **`QiskitTeleportation`**
   | Property | Value |
   |---|---|
   | Theta | `1.0471975512` (pi/3) |
   | Phi | `0.7853981634` (pi/4) |
   | Shots | `1024` |

   Wire `success` → `QuanifiReport` (Flow Name = `teleport`); auto-terminate
   `failure`.
2. Start. This processor sets no `report.type`, so the report falls back to
   the all-attributes card: look for `teleport.fidelity = 1.0000`. The counts
   keys read `c0 c1 c2` left to right; the first two bits (the Bell
   measurement) are random across all four values, the third (the
   verification readout on the teleported qubit) is always `0`: the state
   arrived intact regardless of which correction branch fired.
3. Change Theta/Phi to any angles: fidelity stays 1.0. That invariance under
   the message state is the protocol working, not a lucky input.

---

## Flow 29 — Shor's algorithm

**Components:** `QrispShor` → `QuanifiReport`
**What it proves:** the most famous quantum algorithm as a one-property box,
via Qrisp's native `shors_alg` (the only integrated framework that ships Shor
as a high-level call).

1. **`QrispShor`**
   | Property | Value |
   |---|---|
   | Number To Factor | `15` |

   Wire `success` → `QuanifiReport` (Flow Name = `shor`); wire `failure` to a
   funnel so you can inspect `shor.error`.
2. Start. The all-attributes card shows `shor.factor_1 = 3`,
   `shor.factor_2 = 5` (a couple of seconds on the local simulator). Try
   `21` (slower). A prime input (e.g. `13`) routes to `failure` with a clear
   `shor.error`: that is the input validation, not a bug.

---

# Amplitude estimation

## Flow 30 — Bernoulli amplitude estimation across frameworks

**Components:** `QiskitAmplitudeEstimation` → `QuanifiReport`
**What it proves:** the estimation counterpart to sampling: all four
frameworks implement the same Bernoulli problem
(`A = Ry(2·asin(√p))`), so their canonical estimates land on the same grid
`sin²(π·y/2^m)` and are directly comparable.

1. **`QiskitAmplitudeEstimation`**
   | Property | Value |
   |---|---|
   | Probability | `0.2` |
   | Evaluation Qubits | `3` |
   | Method | `canonical` |
   | Shots | `1024` |

   Wire `success` → `QuanifiReport` (Flow Name = `qae`); auto-terminate
   `failure`.
2. Start. The Amplitude Estimation panel shows `ae.estimate = 0.14644661`
   (the m = 3 grid point nearest 0.2) and `ae.mle ≈ 0.2` (the off-grid
   maximum-likelihood refinement).
3. Switch **Method** to `iterative`: `ae.estimate ≈ 0.2` directly, within
   `Epsilon Target`.
4. Swap the processor for `CirqAmplitudeEstimation` or
   `PennylaneAmplitudeEstimation` with the same Probability/Evaluation
   Qubits: the canonical estimate is *identical* (same grid), which is the
   N-version pseudo-oracle for this component. `QrispAmplitudeEstimation` is
   the iterative counterpart (Qrisp's native IQAE): expect `≈ 0.2`.

---

# Real hardware execution

Both gateways consume the shared circuit contract, so a circuit built by
*any* framework's builder (Qiskit, Cirq, Qrisp, PennyLane) runs on real
hardware through them: set the builder's **Output Format** to `qasm2` and
wire it in. Every default below works offline; credentials only matter when
you point at a real device.

## Flow 31 — Any circuit → Amazon Braket (local or QPU)

**Components:** any circuit builder (e.g. `QiskitGroverCircuit`) →
`BraketDevice` → `QuanifiReport`
**What it proves:** the qasm2 interchange reaching hardware: the same wiring
runs offline today and on an IonQ/Rigetti/IQM QPU when you paste an ARN.

1. **`QiskitGroverCircuit`** — Marked State = `11`, Num Iterations = `1`,
   **Output Format = `qasm2`**. Wire `success` → `BraketDevice`.
2. **`BraketDevice`**
   | Property | Value |
   |---|---|
   | Device | `local` |
   | Shots | `1024` |
   | Wait For Results | `true` |

   Wire `success` → `QuanifiReport` (Flow Name = `braket-hw`); auto-terminate
   `failure`.
3. Start. Counts concentrate on `11`; the attributes carry
   `hw.provider = aws-braket`, `hw.device = local`.
4. For real hardware: set **Device** to a Braket ARN (e.g.
   `arn:aws:braket:::device/quantum-simulator/amazon/sv1`, or a QPU ARN),
   have AWS credentials in the standard chain (env vars or `~/.aws`), and,
   for QPUs with long queues, set **Wait For Results** = `false`: the
   FlowFile continues immediately with `hw.task_arn` + `hw.status` instead
   of blocking the canvas. Missing credentials route to `failure` with a
   clear `hw.error`.
5. Cross-framework check: replace the builder with `CirqQFTCircuit` or
   `PennylaneFeatureEmbedding` (both emit qasm2): a Cirq- or PennyLane-built
   circuit on AWS hardware, no translation step on the canvas.

## Flow 32 — Any circuit → IBM Quantum (fake backend or real)

**Components:** any circuit builder → `QiskitRuntimeSampler` → `QuanifiReport`
**What it proves:** the same gateway idea for IBM: one processor, offline
noisy rehearsal and real-QPU submission.

1. Reuse the builder from Flow 31 (Output Format = `qasm2`), wired into
   **`QiskitRuntimeSampler`**:
   | Property | Value |
   |---|---|
   | Backend | `fake_manila` |
   | Shots | `2000` |

   Wire `success` → `QuanifiReport` (Flow Name = `ibm-hw`); auto-terminate
   `failure`.
2. Start. `fake_manila` runs *locally* with the real backend's calibrated
   noise model: a Bell/Grover pair now shows a few percent leakage into the
   wrong states — that is what the hardware will do to you, rehearsed
   offline. Attributes carry `hw.mode = local-fake`.
3. For real hardware: set **Backend** = `least_busy` (or a name like
   `ibm_brisbane`) and put your IBM Quantum token in **Token** (or use a
   saved account). The circuit is transpiled to the backend ISA
   automatically. `hw.mode` flips to `cloud`.

## Flow 34 — Any circuit → IQM Resonance (async submit + REST polling)

*(Numbered 34 so the existing flow numbers keep pointing at the same
diagrams; it belongs to this hardware section, not to the QML one below.)*

**Components:** any circuit builder → `QrispIQMDevice` → `IQMJobPoller`
(self-looping) → `QuanifiReport`
**What it proves:** the third gateway, and the one shape the other two don't
show — **submission and collection as separate canvas steps**. Qrisp's
Backend Interface is asynchronous, so the submit node never blocks on an IQM
queue; a poller drives the job to completion against the IQM Resonance REST
API and re-queues itself while the job is still waiting.

1. Reuse the builder from Flow 31 (**Output Format = `qasm2`** — Qrisp's
   importer is the qasm2 one), wired into **`QrispIQMDevice`**:
   | Property | Value |
   |---|---|
   | Device Instance | `local` |
   | Shots | `1024` |
   | Wait For Results | `true` |

   Wire `success` → `QuanifiReport` (Flow Name = `iqm-hw`); auto-terminate
   `failure`. Start it: `local` runs Qrisp's own statevector simulator, so
   the whole lane is exercised offline with no token. Attributes carry
   `hw.provider = iqm-resonance`, `hw.mode = local-sim`.

2. Now switch to the asynchronous shape, which is what a real QPU wants.
   Set **`QrispIQMDevice`**:
   | Property | Value |
   |---|---|
   | Device Instance | `garnet` |
   | API Token | `#{iqm.resonance.token}` |
   | Shots | `100` |
   | Wait For Results | `false` |

   It now returns immediately with `hw.job_id`, `hw.status` and
   `hw.server_url` instead of counts — there is no `report.type` yet,
   because there are no results yet.

3. **`IQMJobPoller`** between it and the report:
   | Property | Value |
   |---|---|
   | API Token | `#{iqm.resonance.token}` |
   | Job ID | `${hw.job_id}` |
   | Poll Timeout Seconds | `60` |
   | Poll Interval Seconds | `5` |
   | Max Poll Interval Seconds | `60` |

   Set its **Run Schedule** to `30 sec`. Wire `success` → `QuanifiReport`,
   auto-terminate `failure`, and — the important one — wire **`pending` back
   into `IQMJobPoller` itself**. Each pass polls `GET /api/v1/jobs/{id}` for
   up to 60 s, obeying the server's `Retry-After` header; a job still in the
   queue leaves on `pending` and comes round again. A three-hour queue costs
   a few hundred cheap passes and zero parked threads.

4. When the job reaches `completed`, the poller fetches
   `GET /api/v1/jobs/{id}/artifacts/measurement_counts`, normalises to the
   canonical `q0_left` order, and emits the usual `sim.*` +
   `report.type = simulation`. `QuanifiReport` renders the counts chart plus
   a **Hardware Execution** panel (`hw.provider`, `hw.device`, `hw.job_id`,
   `hw.status`, `hw.poll_attempts`) — that panel is vendor-neutral, so
   Flows 31 and 32 get it too.

5. Credentials: create a Parameter Context `IQM Quantum Credentials` with a
   **sensitive** parameter `iqm.resonance.token`, assign it to the process
   group, and set both **API Token** properties to exactly
   `#{iqm.resonance.token}` (a sensitive property may reference one sensitive
   Parameter and nothing else). The ready-made group is built by
   `tools/add_iqm_group.py`.

---

# QML training

## Flow 33 — Dataset → variational classifier → training report

**Components:** `PennylaneDatasetLoader` → `PennylaneVariationalClassifier` →
`QuanifiReport`
**What it proves:** the QML lane end to end: machine learning on a quantum
circuit with zero equations, honest train/validation split, and a loss curve
in the report.

1. **`PennylaneDatasetLoader`**
   | Property | Value |
   |---|---|
   | Mode | `synthetic` |
   | Num Samples | `24` |
   | Num Features | `2` |
   | Seed | `7` |

   Feed from `GenerateFlowFile`. Wire `success` →
   `PennylaneVariationalClassifier`; auto-terminate `failure`.
2. **`PennylaneVariationalClassifier`**
   | Property | Value |
   |---|---|
   | Embedding | `angle` |
   | Ansatz | `strongly_entangling` |
   | Layers | `2` |
   | Epochs | `25` |
   | Learning Rate | `0.2` |

   The Embedding/Ansatz values use the same vocabulary as
   `PennylaneFeatureEmbedding` and `PennylaneVariationalAnsatz` (Flow 20), so
   the model trained here is the same circuit family those builders emit.
   Wire `success` → `QuanifiReport`; auto-terminate `failure`.
3. **`QuanifiReport`** — Flow Name = `qml-training`. Auto-terminate `success`.
4. Start all three. The card uses the `training` layout: a loss curve that
   visibly decreases, plus `train.train_accuracy` / `train.val_accuracy`
   (≥ 0.8 on the separable synthetic blobs) and the model summary
   (`train.embedding`, `train.ansatz`, `train.num_qubits`).
5. Variations: **Embedding** = `iqp` with **Ansatz** = `basic_entangler`
   trains a different circuit family; **Embedding** = `amplitude` packs the
   features into `ceil(log2(n))` qubits (watch `train.num_qubits` drop).
   Switch the loader's **Mode** to `inline` to train on your own records.

---

# Quick-reference: which flow exercises which processor

| Processor | Flow(s) |
|---|---|
| QiskitQuantumHelloWorld | 1 |
| QiskitGroverCircuit | 2, 22, 23 |
| QiskitAerSimulator | 2, 3, 4, 5, 6, 23 |
| QiskitCircuitReport | 2 |
| QiskitHadamardTransform | 3 |
| QiskitQFTCircuit | 4 |
| QiskitPhaseEstimation | 5 |
| QiskitAmplitudeAmplification | 6 |
| QiskitStatePreparation | 7 |
| QiskitStatevectorSimulator | 7 |
| QiskitGroverSearch | 8 |
| QiskitHamiltonian | 9 |
| QiskitAnsatz | 9 |
| QiskitVQE | 9 |
| CirqGroverCircuit | 10, 22, 23 |
| CirqSimulator | 10, 11, 12, 13, 14, 23 |
| CirqPhaseOracle | 11 |
| CirqGroverOperator | 11 |
| CirqHadamardTransform | 12 |
| CirqQFTCircuit | 13 |
| CirqPhaseEstimation | 14 |
| CirqHamiltonian | 15 |
| CirqAnsatz | 15 |
| CirqVQE | 15 |
| QrispGroverSearch | 16 |
| QrispQFTCircuit | 17 |
| QrispSimulator | 17 |
| QrispPhaseEstimation | 18 |
| QrispHamiltonian | 19 |
| QrispAnsatz | 19 |
| QrispVQE | 19 |
| PennylaneFeatureEmbedding | 20 |
| PennylaneVariationalAnsatz | 20 |
| PennylaneExpectation | 20 |
| PennylaneDatasetLoader | 21 |
| QuanifiUnitary | 22 |
| QuantumUnitaryComparison | 22 |
| QuantumDistributionComparison | 23 |
| QuantumAssertion | 23 |
| QuantumTestCaseSource | 24 |
| MaxCutProblem | 25 |
| QuboToHamiltonian | 26 |
| QiskitQAOA | 25, 26 |
| CirqQAOA / QrispQAOA / PennylaneQAOA | 25, 26 (as swap-ins) |
| QiskitBellState | 27 |
| QiskitTeleportation | 28 |
| QrispShor | 29 |
| QiskitAmplitudeEstimation | 30 |
| CirqAmplitudeEstimation / PennylaneAmplitudeEstimation / QrispAmplitudeEstimation | 30 (as swap-ins) |
| BraketDevice | 31 |
| QiskitRuntimeSampler | 32 |
| QrispIQMDevice | 34 |
| IQMJobPoller | 34 |
| PennylaneVariationalClassifier | 33 |
| QuanifiReport | 3, 4, 5, 6, 7 (self-reporting peers aside), 8, 9, 10–15, 17–34 |
</content>
