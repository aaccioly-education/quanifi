# Component reference

Every NiFi processor Quanifi ships, one row each, generated from each
processor's own `ProcessorDetails.description` in
[`nifi_extensions/`](../nifi_extensions/) (69 processors as of 2026-07-04;
`pauli_dsl.py` and `reporting.py` are shared helper modules, not processors).
This supersedes the "Processors" section in the root [README.md](../README.md),
which predates the amplitude-estimation, Braket, molecular-Hamiltonian, and
differential/mutation-testing processors below.

For the cross-processor attribute contracts (`circuit.*`, `sim.*`, `ae.*`,
`vqe.*`, …) these components share, see
[`guides/CREATING_PROCESSORS.md`](guides/CREATING_PROCESSORS.md).

## Circuit builders

### Qiskit

| Component | Description |
|---|---|
| `QiskitAnsatz` | Builds a parameterised variational ansatz (efficient_su2, real_amplitudes, or two_local) and attaches it to the FlowFile as base64 QPY in the `ansatz.qpy_b64` attribute. In chain mode the incoming Hamiltonian content passes through unchanged; in standalone mode the ansatz is written as the circuit content with the `circuit.*` contract. |
| `QiskitHadamardTransform` | Applies a Hadamard gate to every qubit of a circuit. If the incoming FlowFile already carries a circuit, H gates are appended to all its qubits; if empty, a fresh n-qubit circuit is created. |
| `QiskitStatePreparation` | Prepares a state circuit from a classical description: uniform superposition, GHZ, a specific basis state, or an arbitrary normalized amplitude vector (custom mode). |
| `QiskitPhaseOracle` | Builds a phase oracle for a target bitstring (−1 phase on `\|target⟩`, identity elsewhere). Standalone outputs a bare oracle; compose mode appends it to an existing circuit. Mirrors `CirqPhaseOracle` for cross-framework use. |
| `QiskitGroverOperator` | Reads an oracle circuit, prepends the uniform superposition H⊗n, and applies the full Grover operator (oracle + diffuser) N times via `qiskit.circuit.library.grover_operator`. Mirrors `CirqGroverOperator`. |
| `QiskitGroverCircuit` | Builds a full Grover search circuit for a target bitstring, unmeasured, in QPY or OpenQASM 3. |
| `ReadoutBaselineCircuit` | Builds the readout-error baseline for the Grover N-M hardware matrix: an X gate on every qubit whose target bit is 1, then measurement — no superposition, no entanglement, no two-qubit gates, emitted as OpenQASM 2.0. On ideal hardware every shot returns the target, so any departure is pure state-preparation-and-measurement error for those physical qubits, isolated from the builders' gate error. Sets `grover.kind = "readout"` so the batch submitter's case-major ordering ranks it first. |
| `QiskitQFTCircuit` | Applies the Quantum Fourier Transform or its inverse. Standalone creates a fresh n-qubit QFT circuit; compose mode appends it to an existing circuit. |
| `QiskitPhaseEstimation` | Builds a QPE circuit using Qiskit's `PhaseEstimation` library block. Standalone demos a builtin unitary (T/S/Z); compose mode loads the unitary from the FlowFile. |
| `QiskitQuantumArithmetic` | Builds a reversible arithmetic circuit (`a + b`) from Qiskit's adder library — `cdkm` (Cuccaro ripple-carry), `vbe` (VBE ripple-carry), or `draper` (Fourier-basis) — with the operands encoded into the input registers and no measurement. Publishes the `arithmetic.*` contract (expected result, expected result bits, result-qubit indices) so an oracle can score the run against the classical answer. |

### Cirq

| Component | Description |
|---|---|
| `CirqAnsatz` | Builds a parameterised Cirq variational ansatz (efficient_su2, real_amplitudes, two_local) with sympy free parameters, attached as Cirq JSON in `ansatz.cirq_json`. |
| `CirqHadamardTransform` | Applies H⊗n. Standalone creates a fresh circuit producing the uniform superposition; compose mode appends the H layer to an incoming Cirq/qasm2 circuit. |
| `CirqStatePreparation` | Prepares a state circuit (uniform superposition, GHZ, basis state, or custom amplitude vector). Cross-framework mirror of `QiskitStatePreparation`. |
| `CirqPhaseOracle` | Builds a phase oracle for a target bitstring. Standalone outputs a bare oracle; compose mode appends to an existing Cirq circuit. Feeds `CirqGroverOperator` or amplitude estimation/amplification. |
| `CirqGroverOperator` | Reads an oracle circuit, prepends H⊗n, and applies the full Grover operator N times — equivalent to Classiq's `power(reps, grover_operator(oracle, hadamard_transform))`. |
| `CirqGroverCircuit` | Builds a Grover search circuit for a target bitstring, in Cirq JSON or OpenQASM 2.0. |
| `CirqQFTCircuit` | Applies the QFT (or inverse) using Cirq's built-in `cirq.qft`. Standalone or compose (appends to an existing circuit). |
| `CirqPhaseEstimation` | QPE using Cirq: X on target → H on phase register → controlled-U^(2^k) → inverse QFT. Standalone uses a builtin Z-rotation (T/S/Z) as U; compose mode uses the incoming circuit as U. |
| `CirqQuantumArithmetic` | Builds `a + b` in Cirq as either a hand-written Cuccaro MAJ/UMA ripple-carry adder (`ripple_carry`) or a Fourier-basis adder (`qft`), emitting qasm2 on the same `arithmetic.*` contract as the Qiskit and Qrisp builders. Cirq ships no high-level adder, so this is a reuse artifact in the same sense as `CirqGroverOperator`: the algorithm is transcribed, not imported. |

### Qrisp

| Component | Description |
|---|---|
| `QrispAnsatz` | Declares a parameterised Qrisp variational ansatz (efficient_su2, real_amplitudes, two_local) as a spec in `ansatz.*` attributes — a Qrisp ansatz is a callable, not a serialisable circuit, so `QrispVQE` reconstructs it from the spec. |
| `QrispHadamardTransform` | Applies H⊗n using Qrisp. Standalone or compose (appends to an incoming qasm2 circuit). Always emits OpenQASM 2.0. Cross-framework mirror of `QiskitHadamardTransform`. |
| `QrispStatePreparation` | Prepares a state circuit (uniform superposition, GHZ, basis state, or custom amplitude vector), emitting OpenQASM 2.0. Cross-framework mirror of `QiskitStatePreparation`. |
| `QrispQFTCircuit` | Applies the QFT (or inverse) using Qrisp's built-in QFT primitive, output as qasm2. |
| `QrispPhaseEstimation` | QPE using Qrisp's QPE primitive (`iter_spec=True`). Builtin unitaries T/S/Z; target register prepared in `\|1⟩`. Sets `qpe.top_phase`; exports the circuit as qasm2. |
| `QrispQuantumArithmetic` | Arithmetic on Qrisp `QuantumFloat` registers (add / subtract / multiply, signed or unsigned). `Implementation` selects the adder: `default` (Qrisp's own operator overloading), or the named `cuccaro`, `fourier`, `gidney` (temporary-AND, measurement-based uncompute) and `qcla` (carry-lookahead) adders. The widest implementation choice of the three frameworks, which is why it supplies most of the algorithm families in the arithmetic study. |

*(Qrisp has no standalone `GroverOperator`/`PhaseOracle`/`GroverCircuit` — see `QrispGroverSearch` under Algorithms.)*

### PennyLane (circuit-emitting)

| Component | Description |
|---|---|
| `PennylaneFeatureEmbedding` | Encodes a classical feature vector into a circuit via a PennyLane embedding template (angle/amplitude/iqp/basis), emitted as OpenQASM 2.0 — the classical→quantum data-loading step of a QML pipeline. |
| `PennylaneVariationalAnsatz` | Builds a PennyLane variational ansatz (`StronglyEntanglingLayers` or `BasicEntanglerLayers`) with reproducible seeded weights, emitted as OpenQASM 2.0. Standalone or appended after an embedding to compose a full QML model circuit. |
| `PennylaneGroverCircuit` | Builds a Grover search circuit for a target bitstring with `qml.GroverOperator`, emitted as OpenQASM 2.0. The third builder in the N-M version matrix: for the 4-qubit case it emits 88 two-qubit gates against Qiskit's 56 and Cirq's 112, so the three are bit-identical on simulators but should separate on hardware. |
| `PennylaneQuantumArithmetic` | Builds `a + b` with PennyLane's `qml.OutAdder`, on the same `arithmetic.*` contract as the Qiskit, Cirq and Qrisp builders. Alone among the four it is **out-of-place**: the sum lands in its own `(n+1)`-wide register rather than over B, because `OutAdder` is PennyLane's only native quantum+quantum adder. It is the confirmatory experiment's third framework lane, chosen because its circuit is built from *wires* rather than operand values and is therefore identical for every operand pair — the property every Qrisp adder lacks. |

### pyquil (Rigetti)

| Component | Description |
|---|---|
| `PyquilGroverCircuit` | Builds a Grover search circuit directly in pyquil primitives (Program/H/X/CNOT/RZ) — an independent N-version implementation, not translated from Qiskit or Cirq. pyquil ships no multi-controlled-Z helper, so it is hand-decomposed (native CZ/CCZ identities for n≤3, an exact ancilla-free Walsh-Hadamard phase-polynomial construction for n≥4). Emitted as OpenQASM 2.0 via `quil_qasm.to_qasm2`, the interchange format every other engine in this repo accepts. |

## Simulators

| Component | Description |
|---|---|
| `QiskitAerSimulator` | Reads a circuit (QPY or OpenQASM 3), adds measurement, runs it on Qiskit's `AerSimulator`, writes shot counts as JSON. Optional gate/readout noise model. |
| `QiskitStatevectorSimulator` | Computes the exact statevector of an unmeasured circuit via `qiskit.quantum_info.Statevector`. Outputs probability distribution as JSON plus an HTML card with amplitude/phase per basis state. |
| `CirqSimulator` | Reads a circuit (Cirq JSON or qasm2), adds measurement, runs it on Cirq's wave-function simulator, writes shot counts as JSON. Optional noise channel and readout error. |
| `CirqStatevectorSimulator` | Exact statevector via `cirq.Simulator().simulate()`. Cross-framework mirror of `QiskitStatevectorSimulator`. |
| `QrispSimulator` | Reads a qasm2 circuit, adds measurement, runs it on Qrisp's native statevector simulator, writes shot counts as JSON. No noise-model support (ideal only). |
| `PennylaneSimulator` | Reads a qasm2 circuit, samples it on PennyLane's `default.qubit` device, writes shot counts as JSON. Ideal (noiseless) only — for QML-native outputs (expectations, analytic probabilities) use `PennylaneExpectation` instead. |
| `BraketSimulator` | Reads a circuit (OpenQASM 3, or qasm2 via Qiskit's parser), samples it on the Amazon Braket `LocalSimulator` (no AWS account/network needed), writes shot counts as JSON. MSB-first bit order (Cirq convention). Noiseless only. |
| `QSharpSimulator` | Reads OpenQASM 2/3, normalizes it to a measured circuit, and samples it with the Microsoft QDK sparse or Clifford simulator. Supports seeded sparse-simulator Pauli noise and emits canonical q0-left counts. |
| `PyquilSimulator` | Reads a circuit (OpenQASM 2 or 3, re-based onto Quil-native gates via Qiskit's parser), samples it on Rigetti's pure-Python PyQVM (no external `quilc`/`qvm` server), and writes shot counts as JSON. Seeded and reproducible; supports pyquil's six post-gate Kraus noise channels (relaxation, dephasing, depolarizing, phase_flip, bit_flip, bitphase_flip) via `ReferenceDensitySimulator`. |

## Hardware execution

Gateways from the shared circuit contract (`circuit.format = qasm2`/`qasm3`) to
real quantum hardware. Because they consume the interchange format, a circuit
built by *any* framework's builder runs on real QPUs through them.

| Component | Description |
|---|---|
| `BraketDevice` | Runs a circuit on an Amazon Braket device: a QPU / managed-simulator ARN (IonQ, Rigetti, IQM, SV1) or `local` for the offline `LocalSimulator`. `Wait For Results = false` submits asynchronously and continues with `hw.task_arn`, so hours-long QPU queues never block the canvas. Credentials via the standard AWS chain. |
| `QiskitRuntimeSampler` | Samples a circuit via IBM Qiskit Runtime `SamplerV2`. `Backend = fake_<name>` runs the calibrated noisy fake backend locally (offline, no account); `least_busy` or a backend name submits to real IBM hardware (token required). Transpiles to the backend ISA before submission. |

## Algorithms (all-in-one solvers)

| Component | Framework | Description |
|---|---|---|
| `QiskitGroverSearch` | Qiskit | Full Grover's search for a target bitstring: phase oracle → `grover_operator` → N iterations → `AerSimulator` → measurement counts. |
| `QrispGroverSearch` | Qrisp | Grover's search via Qrisp: `tag_state()` oracle → `grovers_alg()` → measurement probability distribution as JSON. |
| `QiskitVQE` | Qiskit | Variational Quantum Eigensolver: reads a Hamiltonian and an ansatz, minimises energy with a classical optimizer. Emits `vqe.*` results. |
| `CirqVQE` | Cirq | Hand-rolled VQE: simulates a sympy-parameterised ansatz, computes the Hamiltonian expectation from the statevector, minimises with `scipy.optimize`. Strict solver — requires upstream Hamiltonian + ansatz. |
| `QrispVQE` | Qrisp | VQE via Qrisp's `VQEProblem`: trains the ansatz spec with the chosen optimizer, emits `vqe.*` results. |
| `QiskitQAOA` | Qiskit | QAOA via `qiskit_algorithms.QAOA`: optimizes the p-layer ansatz against a diagonal cost Hamiltonian, emits `qaoa.*` results (optimal value, best bitstring, approximation ratio). |
| `CirqQAOA` | Cirq | Hand-rolled QAOA: alternating `exp(-i·gamma·H)` cost layers and Rx mixer layers, optimized with `scipy`. |
| `QrispQAOA` | Qrisp | QAOA via Qrisp's `QAOAProblem`. |
| `PennylaneQAOA` | PennyLane | QAOA via `qml.qaoa` layers with analytic expectations on `default.qubit`, optimized with `scipy`. |
| `QiskitAmplitudeEstimation` | Qiskit | QAE on a Bernoulli problem (`A = Ry(2·asin(√p))`) via `qiskit-algorithms`. `canonical` = QPE-based grid estimate + MLE refinement; `iterative` = epsilon-convergent, no phase register. Emits `ae.*`. |
| `CirqAmplitudeEstimation` | Cirq | Canonical QPE-based QAE built from Cirq primitives, same Bernoulli problem. Emits `ae.*`. |
| `QrispAmplitudeEstimation` | Qrisp | QAE via Qrisp's native IQAE, converging to `Epsilon Target` at confidence `Alpha`. Emits `ae.*`. |
| `PennylaneAmplitudeEstimation` | PennyLane | QAE composed from PennyLane's `QuantumPhaseEstimation` template. Emits `ae.*`. |
| `QiskitAmplitudeAmplification` | Qiskit | Generalised Grover amplitude amplification via `qiskit-algorithms`' `AmplificationProblem`/`Grover.construct_circuit()` (Brassard et al., 2000). `Q = A·S₀·A†·Sꜰ` applied N times. |
| `QrispShor` | Qrisp | Shor's factoring algorithm via Qrisp's native `shors_alg`: configure `Number To Factor` (small odd composites, e.g. 15 or 21), get `shor.factor_1`/`shor.factor_2`. Educational scale. |
| `QiskitBellState` | Qiskit | Prepares and measures one of the four Bell states (H + CNOT + local flips); `bell.correlation` is the probability mass on the correlated pair (1.0 ideal). The canonical entanglement demo. |
| `QiskitTeleportation` | Qiskit | Teleports `Rz(Phi)Ry(Theta)\|0>` from qubit 0 to qubit 2 using a Bell pair, a mid-circuit Bell measurement, and classically conditioned corrections (dynamic circuit). Self-verifying: `teleport.fidelity = P(target = 0)`, ideally 1.0. |
| `QiskitQuantumHelloWorld` | Qiskit | Runs a GHZ-state circuit and writes the measurement bitstring to the FlowFile content — the smoke-test processor. |

## Hamiltonians & problem encoders

| Component | Description |
|---|---|
| `QiskitHamiltonian` | Builds a problem Hamiltonian (`SparsePauliOp`) from a compact Pauli sum (e.g. `Z0 + Z1 + 0.5 X0 X1`) or JSON, serialised with `hamiltonian.format = sparse_pauli_op_json` so `QiskitVQE`/`QiskitQAOA` can consume it. No quantum execution. |
| `CirqHamiltonian` | Same wire format, tagged `hamiltonian.framework=cirq`, shared with `QiskitHamiltonian`. |
| `QrispHamiltonian` | Same wire format, tagged `hamiltonian.framework=qrisp`. |
| `MaxCutProblem` | Encodes a weighted graph (JSON edge list, e.g. `[[0,1],[1,2,0.5]]`) as the MaxCut Ising Hamiltonian in the framework-neutral wire format. The minimum eigenvalue equals `-max_cut`, so QAOA results read in cut units. No Pauli strings required. |
| `QuboToHamiltonian` | Encodes a QUBO matrix (JSON 2D array; minimize `x^T Q x` over binary `x`) as the equivalent Ising Hamiltonian. The ground bitstring is the binary solution vector — the universal adapter for combinatorial problems (portfolio, scheduling, routing). |
| `MoleculeHamiltonian` | Builds a molecular qubit Hamiltonian from a geometry string (e.g. `H 0 0 0; H 0 0 0.735`) using `pyscf` + OpenFermion (Jordan-Wigner). Emits the same framework-neutral format plus chemistry attributes (formula, bond distance, HF/FCI reference energies) so any `*VQE` solver can consume it. |

## PennyLane QML lane

PennyLane is treated as a separate QML lane rather than a fourth circuit-and-counts
framework: its native outputs are expectation values/probabilities, and its
focus is data encoding + training rather than gate-level circuits. `PennylaneFeatureEmbedding`
and `PennylaneVariationalAnsatz` (listed under Circuit builders above) emit qasm2
and *are* interchangeable with the other frameworks' simulators; the three below are not.

| Component | Description |
|---|---|
| `PennylaneDatasetLoader` | Emits a labelled classical dataset as a JSON array of records (`{"features": [...], "label": k}`). `synthetic` generates a seeded two-class Gaussian-blob dataset offline; `inline` validates a dataset given via the `Records` property. Feeds `SplitJson` → `PennylaneFeatureEmbedding`. |
| `PennylaneExpectation` | Runs a qasm2 circuit on a PennyLane device and emits QML-native outputs: per-qubit Pauli expectation values (`sim.expectations`) and the probability distribution. The expectation-value lane PennyLane is built around, as opposed to shot-count simulators. |
| `PennylaneVariationalClassifier` | Trains a variational quantum classifier on the labelled JSON dataset in the FlowFile content (from `PennylaneDatasetLoader`). `Embedding` (angle / iqp / amplitude) and `Ansatz` (strongly_entangling / basic_entangler) use the same vocabulary as the builder processors. Emits `train.*` attributes, the loss history, and `report.type = training` for the loss-curve report card. |

## Differential & mutation-testing infrastructure

Cross-framework infrastructure for data-driven differential testing (N frameworks
× M simulators) and mutation testing (inject a mutant on one branch of a K-way
consensus flow, see if it's caught).

| Component | Description |
|---|---|
| `QuantumTestCaseSource` | Expands a compact test-matrix spec (explicit JSON table or parameter axes) into a JSON array of test-case rows, each carrying `test.case_id`/`test.run_id`/`test.partition`. Feeds `SplitJson` to fan out one FlowFile per case. Setting `Arithmetic Suite` (`exhaustive:2`, `boundary:3`) instead generates arithmetic operand pairs through the same generator the headless study uses, tagging each row with the boundary conditions (`carry_out`, `max_a`, …) that input covers. `Case Input Mode` adds two modes that state the operand pairs explicitly instead of generating them — `inline-arithmetic-json` (the `Arithmetic Cases` property) and `flowfile-content` (a reviewed file placed by a stock `FetchFile`) — validated by one strict parser that derives all ground truth, with `Expected Case Count` / `Expected Case Set SHA-256` / `Expected Manifest SHA-256` as fail-closed campaign guards. Default `legacy-auto` reproduces the historical precedence exactly. See [DATA_DRIVEN_TESTING.md](guides/DATA_DRIVEN_TESTING.md) §2a. |
| `QuantumBatchResultExpander` | Expands a polled hardware batch into a JSON array of one row per circuit, each carrying that circuit's counts and the ground-truth attributes recorded at submission, so per-circuit hardware results can be scored against a known answer. Normalises counts to the canonical `q0_left` order (Qiskit/IBM return them MSB-first; the IQM serializer does not), because getting that wrong does not raise, it reports 0% success and looks like a dead device. Feed into `SplitJson`. |
| `QuantumSuccessProbabilityOracle` | Ground-truth oracle for circuits with a known answer: marginalises counts onto the result register, reports the success probability with a Wilson score interval, and in paired mode runs a two-proportion z-test against a reference run, emitting `agree` / `negligible-difference` / `diverge` / `no-test`. Unlike the distributional oracles it scores against a *specification*, not against another branch, so a wrong answer and a merely different distribution are distinguishable. Pure-Python statistics, no scipy. |
| `QuantumDistributionComparison` | Pairs two measurement distributions and computes Hellinger distance, Total Variation, and Fidelity (effect sizes), plus a Pearson chi-squared homogeneity test (significance). Emits a two-gate verdict (consistent / negligible-difference / disagree). Pairing via a static Comparison Label. |
| `QuantumUnitaryComparison` | Compares two unitary matrices (from `QuanifiUnitary`) for equivalence up to global phase via process fidelity `F = \|tr(U_A† · U_B)\|² / d²`. `F = 1` means equivalence on every possible input, not just `\|0...0⟩`. |
| `QuantumAssertion` | Assertion gate: receives `QuantumDistributionComparison` output, applies a Hellinger threshold check (differential oracle) plus an optional ground-truth check against `test.expected`. Routes to pass/fail. |
| `QuantumConsensusOracle` | K-way majority oracle: collects K framework distributions sharing a slot key, takes an endian-agnostic majority vote, emits `assert.verdict = PASS / FAIL / DISAGREE`. Generalises `QuantumDistributionComparison` + `QuantumAssertion` to K branches; feeds `MutationScoreReport`. |
| `QuantumDistributionOracle` | K-way oracle for degenerate/multi-peaked outputs (GHZ, W states) where top-1 voting is a coin flip: votes on **distribution similarity** instead, via pairwise Hellinger distance gated by a chi-squared homogeneity test across every branch pair sharing a slot key. Requires **Multiple Comparison Correction** (`none` / `holm` / `benjamini-hochberg`, no default — an unset value leaves the processor INVALID) to correct the per-pair p-values for the K(K-1)/2 tests a slot runs; `none` reproduces the original uncorrected `p < alpha` rule byte-for-byte. Emits `consensus.correction`, `consensus.pairwise_tests`, `consensus.significant_pairs_raw`, `consensus.significant_pairs_corrected` alongside the existing `consensus.*`/`assert.*` attributes. Ground truth is checked as expected **support** (a set of bitstrings), not a single winner, so a degenerate but correct output is not penalised. Shares `multiple_comparisons.py` with the version-matrix and mutation experiment scripts. |
| `QuantumMutator` | Gate-level (Layer-A) mutation operator: reads a qasm2/qasm3 circuit, applies one syntactic mutation (`gate.add`, `gate.remove`, `gate.replace` same-arity, `rotation.perturb`, and `carry.break` — deletes a gate on the carry-out qubit named by `arithmetic.result_qubits`, so the fault only manifests on operand pairs that actually carry), emits the mutant with `mut.*` bookkeeping. Placed on one branch of a K-branch consensus flow so a killed mutant surfaces as a single-branch `DISAGREE`. |
| `MutationScoreReport` | Aggregates per-case verdicts into a mutation survival rate, grouped by mutation operator, keyed on `test.run_id`. Excludes control rows from the score and reports control dissents (real cross-framework discrepancies) separately. |
| `Generation2JobAdapter` | Audited blinding boundary for the Generation-2 experiment. Marginalises expanded hardware counts onto each implementation's result register and removes expected answers and mutation truth before oracle processing. |
| `Generation2EnsembleBuilder` | Fail-closed constructor for Generation-2 clean and one-faulty-version ensembles. Requires exactly the three preregistered versions and rejects duplicate, incomplete, old-generation, or truth-bearing input. |
| `Generation2PseudoOracle` | Reference-free modal-majority oracle used identically by NiFi and CLI. Emits `clean`, a localized `suspect`, or `abstain`; decisions carry a reproducible digest before truth is revealed. |
| `Generation2TruthEvaluator` | Post-freeze unblinding stage. Joins decisions to the separately stored sealed truth and distinguishes correct localization, wrong attribution, misses, false alarms, clean decisions, and abstentions. |
| `Generation2CalibrationEvaluator` | Evaluates complete 21-circuit qualification and 84-circuit clean-calibration jobs, enforcing aggregate case correctness and the preregistered 25/28 repeated-mode voter eligibility rule. Preparatory outputs remain outside confirmatory metrics. |
| `Generation2Reporter` | Writes Generation-2 evaluation artifacts as provenance-preserving JSON, CSV, Markdown, and HTML files from one common record set. |

## Reporting & utility

| Component | Description |
|---|---|
| `QuanifiReport` | Generic HTML report writer. Reads `report.type` to select which sections to render (circuit diagram, counts, statevector, comparison metrics, …) and appends a card to a local HTML file. |
| `QuanifiUnitary` | Computes the 2ⁿ×2ⁿ unitary matrix of a circuit, written as JSON. Connects any circuit builder to a downstream consumer such as `QuantumUnitaryComparison`. Normalises Qiskit circuits to Cirq qubit-ordering so unitaries are directly comparable across frameworks. |
| `QiskitCircuitReport` | **Deprecated** — use `QuanifiReport`. Legacy Qiskit-only reporter (SVG circuit diagram, QASM 3 source, counts bar chart). Kept only for backward compatibility with existing saved flows. |

## Summary

| Category | Count |
|---|---|
| Circuit builders | 28 (Qiskit 10, Cirq 9, Qrisp 6, PennyLane 2, pyquil 1) |
| Simulators | 8 |
| Hardware execution | 2 |
| Algorithms (all-in-one solvers) | 18 |
| Hamiltonians & problem encoders | 6 |
| PennyLane QML lane | 5 (2 shared with Circuit builders above, 3 unique) |
| Differential & mutation-testing infrastructure | 9 |
| Reporting & utility | 3 |
| **Total processors documented here** | **75** |

`nifi_extensions/` currently holds more processor modules than this table lists
(the hardware batch submitters and pollers added in 2026-08 are documented in
the provider-specific processor documentation
rather than here).
