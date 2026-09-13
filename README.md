# Quanifi

Quanifi provides reusable Python processors for building, composing, executing,
testing, and reporting quantum-computing workflows on Apache NiFi.

The framework supports processors based on Qiskit, Cirq, Qrisp, PennyLane,
pyQuil, Amazon Braket, Microsoft Q#/QDK, IBM Quantum, IQM, and Quantum
Inspire. Processors exchange circuits through a shared FlowFile attribute
contract and, where supported, OpenQASM 2.0.

This repository contains framework code, tests, examples, and user-facing
documentation. Research campaigns, paper-specific analysis, raw experimental
results, internal development plans, and AI-assistant artifacts are maintained
in separate private repositories.

## Layout

- `nifi_extensions/`: reusable NiFi Python processors
- `tests/`: processor and framework tests
- `docs/`: component reference and development guides
- `guides/`: introductory example flows
- `demo/`: small example inputs and utilities
- `web/`: optional report browser

## Development

Create the environment with `uv sync`, then run:

```bash
.venv/bin/python -m pytest --tb=short -q
```

See `docs/README.md` for the documentation index.
