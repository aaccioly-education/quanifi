# Quanifi documentation

All project documentation, grouped by purpose. The AI-assistant entry points
(`AGENTS.md` at the repo root and in `nifi_extensions/`) stay outside `docs/`
because they are path-sensitive.

- [COMPONENTS.md](COMPONENTS.md) — the current, complete reference of all 61
  NiFi processors, by category (circuit builders, simulators, algorithms,
  Hamiltonians, PennyLane QML lane, differential/mutation-testing
  infrastructure, reporting). Supersedes the older, now-incomplete
  "Processors" section in the root [README.md](../README.md).

- **Experiments / replication** — the headless study runners
  (`experiments/differential_study.py`, `resource_metrics.py`,
  `mutation_study.py`, `export_tables.py`) and their `just experiment-*`
  recipes are documented in the root README's
  ["Reproducing the paper numbers"](../README.md#reproducing-the-paper-numbers-experiments-layer)
  section.

## guides/ — how-to

Start here if you want to *do* something.

- [CREATING_PROCESSORS.md](guides/CREATING_PROCESSORS.md) — the canonical
  processor skeleton, available validators, and the three mistakes that make
  NiFi silently skip a file. Read before writing any processor (the
  `/add-processor` skill automates this).
- [NIFI_FLOW_CONFIGURATION_GUIDE.md](guides/NIFI_FLOW_CONFIGURATION_GUIDE.md) —
  click-by-click canvas setup for 33 flows exercising every processor at least
  once.
- [INTERCHANGEABLE_GROVER_FLOW.md](guides/INTERCHANGEABLE_GROVER_FLOW.md) —
  the decomposed Grover pipeline (`*PhaseOracle` → `*GroverOperator` →
  simulator → report) and the two cross-framework Qiskit↔Cirq assemblies,
  including the qasm2 wire-format rule and the canonical `q0_left` bit order.
- [DEVELOPER_GUIDE.md](guides/DEVELOPER_GUIDE.md) — environment setup, NiFi
  installation/configuration, running the test suite, project structure.
- [DATA_DRIVEN_TESTING.md](guides/DATA_DRIVEN_TESTING.md) — the
  attribute-externalization contract that lets one canvas flow run many test
  cases from data.
- [MUTATION_TESTING.md](guides/MUTATION_TESTING.md) — the mutation-testing
  layer: operators, injection modes, verdicts, and scoring.
- [MUTATION_CANVAS_TESTING.md](guides/MUTATION_CANVAS_TESTING.md) — running
  the mutation flows on the NiFi canvas.
- [headless-experiments-architecture.html](guides/headless-experiments-architecture.html)
  — how the paper experiments run without a live NiFi server while still
  exercising the real processor classes and FlowFile contracts.
- [HARDWARE_N_VERSION_EXPERIMENT.html](guides/HARDWARE_N_VERSION_EXPERIMENT.html)
  — illustrated, software-engineer-friendly explanation of the complete
  GHZ-16 N-version experiment on IBM and IQM hardware: calibration, NiFi batch
  orchestration, comparability controls, results, caveats, and conclusions.
- [NX_M_VERSION_TESTING_FLOW.md](NX_M_VERSION_TESTING_FLOW.md) — simplified
  N x M version testing flow diagram (Mermaid) and 24-branch canvas topology without mutation.

## research/ — dissertation material

- [DISSERTATION.md](research/DISSERTATION.md) — the dissertation narrative:
  implement the same algorithm in N frameworks, run across M independent
  simulators, compare distributions (Hellinger = effect size, chi-squared =
  significance).
- [DISSERTATION_EXCERPT.md](research/DISSERTATION_EXCERPT.md) — self-contained
  excerpt of the core technique.
- [QMutBench.md](research/QMutBench.md) — design of the quantum mutation
  benchmark.

## planning/ — roadmap & status

- [PLAN-generation2-nversion-experiment.md](planning/PLAN-generation2-nversion-experiment.md)
  — **canonical experiment plan**: a fresh, fully blinded N-version study run
  independently through first-class NiFi and CLI pipelines on IBM and IQM.

- [quantum_primitives_roadmap.md](planning/quantum_primitives_roadmap.md) —
  **the source of truth** for the per-framework implementation matrix, the
  interchangeable-Grover status, the canonical bit-order resolution, and the
  parked Qrisp decomposed-Grover design.
- [PROJECT_SUMMARY.md](planning/PROJECT_SUMMARY.md) — end-to-end summary and
  setup instructions.
- [HANDOFF.md](planning/HANDOFF.md) — design rationale and open-work handoff
  notes (VQE/QAOA decomposition decisions).
- [OVERNIGHT_TASKS.md](planning/OVERNIGHT_TASKS.md) — batch task notes.
- [ideas.md](planning/ideas.md) — loose ideas backlog.
