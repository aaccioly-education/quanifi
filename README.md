# Quanifi

Quanifi provides reusable Python processors for building, composing, executing,
testing, and reporting quantum-computing workflows on Apache NiFi.

The framework supports processors based on Qiskit, Cirq, Qrisp, PennyLane,
pyQuil, Amazon Braket, Microsoft Q#/QDK, IBM Quantum, IQM, and Quantum
Inspire. Processors exchange circuits through a shared FlowFile attribute
contract and, where supported, OpenQASM 2.0.

This repository contains framework code, tests, examples, and user-facing
documentation. Research campaigns, paper-specific analysis, raw experimental
results, and internal development material are maintained separately.

## Layout

- `nifi_extensions/`: reusable NiFi Python processors
- `tests/`: processor and framework tests
- `docs/`: component reference and development guides
- `guides/`: introductory example flows
- `demo/`: small example inputs and utilities
- `web/`: optional report browser

## Development

Create the development environment with `uv sync --frozen --extra dev --extra iqm --python 3.12`, then run:

```bash
just test
```

See `docs/README.md` for the documentation index.

## pyQuil examples

[Grover, VQE and QAOA canvases](demo/pyquil/index.html) demonstrate the modular
pyQuil components. See the [processor guide](docs/guides/PYQUIL_COMPONENTS.md)
for import instructions, properties and local execution.
