# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- **Qrisp QIRO (`QrispQIRO`)**: Quantum-Informed Recursive Optimization (Finzgar et al., PRX Quantum 5, 020327) for Maximum Independent Set, as a train-only solver with the `QrispQAOA` contract (`MaxIndependentSetProblem -> QrispQIRO -> <counts simulator> -> QuantumQAOAEvaluator -> QuanifiReport`).
  - Uses Qrisp's own QIRO components unmodified (`create_max_indep_replacement_routine`, `create_max_indep_cost_operator_reduced`, `qiro_rx_mixer`, `qiro_init_function`); the `run_qiro` loop is re-implemented only to seed every stage from a local NumPy generator, use exact probabilities, and record a step trace.
  - Emits the final stage's trained circuit as portable OpenQASM 2.0 over all n qubits, keeps `hamiltonian.json` as the original problem, and adds `qiro.*` attributes (step trace, fixed/removed vertices, stage costs, `qiro.trace_svg`).
  - Logic lives in `nifi_extensions/qrisp_qiro.py`; added to `docker/processors-qrisp.txt` (now 29 processors).
- **QuanifiReport**: `QIRO Steps` section (trace SVG, step table, fixed/removed summary), rendered only when `qiro.steps` is present.

## [0.2.0] - 2026-10-08

### Added
- **Qrisp Phase Oracle (`QrispPhaseOracle`)**:
  - Implements phase oracle construction using Qrisp's `tag_state` with canonical `q0_left` bit ordering.
  - Supports both standalone mode (synthesizing a fresh marking oracle) and compose mode (appending the oracle to an upstream circuit).
- **Qrisp Grover Operator (`QrispGroverOperator`)**:
  - Supports the multi-box Grover architecture by consuming an upstream OpenQASM 2.0 oracle FlowFile, preparing uniform superposition $H^{\otimes n}$, and applying $k$ iterations of oracle and native Qrisp `diffuser`.
  - Enables cross-framework interchangeability (e.g., Qrisp oracle with Qiskit/Cirq Grover operator, or Qrisp Grover operator with Aer simulator).
- **Qrisp Amplitude Amplification (`QrispAmplitudeAmplification`)**:
  - Generalized Quantum Amplitude Amplification (QAA) synthesizing the amplification loop $Q = A S_0 A^\dagger S_f$.
- **Qrisp Quantum Teleportation (`QrispTeleportation`)**:
  - Three-qubit quantum teleportation circuit incorporating message state preparation $|\psi(\theta, \phi)\rangle$, Bell pair generation, Bell measurement, classical feed-forward corrections, and uncomputation fidelity verification (`teleport.fidelity = 1.0`).
- **Qrisp Statevector Simulator (`QrispStatevectorSimulator`)**:
  - Evaluates exact statevectors of unmeasured OpenQASM 2.0 circuits via `QuantumCircuit.statevector_array()`.
  - Outputs canonical `q0_left` probability distribution JSON and generates a styled HTML report card with state amplitudes and phases.
- **Qrisp Expectation Value Evaluator (`QrispExpectation`)**:
  - Analytical and sampled evaluation of expectation values $\langle \psi | H | \psi \rangle$ for Pauli observables and Hamiltonians parsed via `pauli_dsl.py`.
- **Comprehensive Qrisp Capability Test Suite**:
  - Added `tests/test_qrisp_expansion.py` validating all 6 new components, diagonal phase actions, fidelity, and cross-framework pipelines.

### Changed
- **Default Canvas Demo**:
  - Simplified the startup canvas demo to use the decomposed Qiskit multi-box Grover pipeline (`QiskitPhaseOracle` → `QiskitGroverOperator` → `QiskitAerSimulator` → `QuanifiReport`), removing the crowded legacy demo.
- **Docker Processor Defaults**:
  - Added multi-box Grover processors and QAOA family processors (`*PhaseOracle`, `*GroverOperator`, `*QAOA*`) to `docker/processors.txt`.
- **NiFi Authentication**:
  - Set default development credentials to user `quanifi` and password `quanifipassword` to comply with Apache NiFi's password security policy while remaining easy to remember.
- **Documentation**:
  - Updated `docs/COMPONENTS.md` with alphabetical catalog entries for all new processors.
  - Updated Table I (*Capabilities of the framework-specific processors*) in the paper repository to mark all Qrisp capabilities.

### Fixed
- **`QrispVQE` Energy Precision**:
  - Made energy precision configurable via the `Energy Precision` property so users can reach chemical accuracy (0.0016 Ha).
- **`QrispVQE` Dependency Isolation**:
  - Decoupled `QrispVQE` from importing `QrispAnsatz` directly by isolating shared logic in `qrisp_ansatz.py`.

---

## [0.1.0] - 2026-08-20

### Added
- Initial framework-specific processor library for Apache NiFi:
  - Qiskit, Cirq, Qrisp, PennyLane, and pyQuil components.
  - Circuit builders, all-in-one algorithms (VQE, QAOA, Shor, Grover, Deutsch–Jozsa, Bernstein–Vazirani, SWAP test), and simulators.
  - Cross-framework interoperability via OpenQASM 2.0 wire format.
  - Hardware integration with IBM Quantum (via Qiskit), IQM Resonance (via Qrisp), Quantum Inspire, and cloud providers.
