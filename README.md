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

## QAOA examples

[Ten single-processor lanes and a 5×7 N×M matrix](demo/qaoa/index.html) cover
every `<Fw>QAOA` solver and `<Fw>QAOACircuit` builder against every counts
simulator. See the [QAOA components guide](docs/guides/QAOA_COMPONENTS.md)
for the chain, property tables, attribute contracts and the 0.1.0→0.2.0
migration notes.

## License

Quanifi is free software under the [GNU Affero General Public License v3](LICENSE).
If you deploy it as a network service, note that AGPL section 13 requires you to
offer your users the corresponding source.

A [commercial license](COMMERCIAL.md) is available for use that cannot comply
with the AGPL, such as embedding Quanifi in a closed-source product or offering
it as a hosted service without publishing your source.

The name "Quanifi" is not covered by the AGPL grant; see [NOTICE](NOTICE).
Contributions are accepted under the terms in [CONTRIBUTING.md](CONTRIBUTING.md).
