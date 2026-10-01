# Quanifi

Quanifi provides reusable Python processors for building, composing, executing,
testing, and reporting quantum-computing workflows on Apache NiFi.

The framework supports processors based on Qiskit, Cirq, Qrisp, PennyLane,
pyQuil, Amazon Braket, Microsoft Q#/QDK, IBM Quantum, IQM, and Quantum
Inspire. Processors exchange circuits through a shared FlowFile attribute
contract and, where supported, OpenQASM 2.0.

This repository contains framework code, tests, examples, and user-facing
documentation. Research campaigns, study-specific analysis, raw experimental
results, and internal development material are maintained separately.

## Quick start (Docker)

> **Docker users: only a subset of processors is installed by default.**
> The full catalogue includes processors that are not in the quickstart image.
> Add the processors you need to `docker/processors.txt`, then run
> `docker compose up -d --build nifi`. You can also select a custom list or all
> processors through `.env`. [Enable additional processors](docs/guides/DOCKER_QUICKSTART.md#adding-processors-to-the-image).

```bash
git clone https://github.com/saeg/quanifi.git quanifi
cd quanifi
docker compose up
```

Wait for `(healthy)` in `docker compose ps`, then open
<https://localhost:8443/nifi> and accept the self-signed certificate.
Use these default credentials on your first login:

| Username | Password |
| --- | --- |
| `admin` | `quanifi-demo-password` |

On the canvas, right-click **Quanifi quickstart — Qiskit Grover** and choose
**Start** to run the demo: **Start here → Build Grover circuit → Simulate circuit → View results**.
It searches for `10` using two qubits and one Grover iteration. Open
`reports/quickstart/qiskit-grover.html` on your computer to see the result.

If port `8443` is already in use, add `QUANIFI_NIFI_PORT=18443` to a local
`.env` file beside `compose.yaml`, then run `docker compose up` again and open
<https://localhost:18443/nifi>. The login credentials are the same.

The optional report browser (`docker compose --profile web up`) is available
at <http://localhost:8080/>. Its default email is `admin@example.com` and its
password is `quanifi-demo-password`.

These are demo defaults. NiFi credentials can be overridden with
`QUANIFI_NIFI_USERNAME` and `QUANIFI_NIFI_PASSWORD`; the report browser uses
`QUANIFI_ADMIN_EMAIL` and `QUANIFI_ADMIN_PASSWORD`.
See [`docs/guides/DOCKER_QUICKSTART.md`](docs/guides/DOCKER_QUICKSTART.md) for
configuration, connecting reports to the browser, and troubleshooting.

## Documentation

Full interactive documentation, processor guides, and tutorials are published at:
**[https://saeg.github.io/quanifi/](https://saeg.github.io/quanifi/)**

- **[Component Catalogue](https://saeg.github.io/quanifi/components.html)** — available processors across Qiskit, Cirq, Qrisp, PennyLane, and pyQuil
- **[Flow Configuration Guide](https://saeg.github.io/quanifi/guides/nifi-flow-configuration-guide.html)** — processor properties, contracts, and canvas design
- **[Developer Guide](https://saeg.github.io/quanifi/guides/developer-guide.html)** — setup, development workflow, and testing
- **[Interactive Tutorials](https://saeg.github.io/quanifi/tutorials/index.html)** — step-by-step algorithms (Deutsch-Jozsa, Grover, QAOA, etc.)
- **[Canvas Screenshots Gallery](https://saeg.github.io/quanifi/screenshots/index.html)** — visual snapshots of all process groups

Local markdown sources and HTML pages are located in [`docs/`](docs/) (see [`docs/README.md`](docs/README.md)).

## Layout

- `nifi_extensions/`: reusable NiFi Python processors
- `tests/`: processor and framework tests
- `docs/`: component reference and development guides
- `guides/`: introductory example flows
- `demo/`: small example inputs and utilities
- `web/`: optional report browser
- `docker/`: container images (quickstart NiFi image, report browser)

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

## Citation

If you use Quanifi or its high-level components in your academic work or research, please cite our paper:

> Neilson Carlos Leite Ramalho, Higor Amario de Souza, Anthony Accioly, Valter Vieira de Camargo, and Marcos Lordello Chaim. (2026). *NxM-Version Programming for Quantum Software: High-Level Components across Frameworks and Engines*. arXiv:2609.33255 [quant-ph]. <https://arxiv.org/abs/2609.33255>

BibTeX entry:

```bibtex
@misc{ramalho2026nxmversionprogrammingquantumsoftware,
      title={NxM-Version Programming for Quantum Software: High-Level Components across Frameworks and Engines}, 
      author={Neilson Carlos Leite Ramalho and Higor Amario de Souza and Anthony Accioly and Valter Vieira de Camargo and Marcos Lordello Chaim},
      year={2026},
      eprint={2609.33255},
      archivePrefix={arXiv},
      primaryClass={quant-ph},
      url={https://arxiv.org/abs/2609.33255}, 
}
```

## License

Quanifi is free software under the [GNU Affero General Public License v3](LICENSE).
If you deploy it as a network service, note that AGPL section 13 requires you to
offer your users the corresponding source.

A [commercial license](COMMERCIAL.md) is available for use that cannot comply
with the AGPL, such as embedding Quanifi in a closed-source product or offering
it as a hosted service without publishing your source.

The name "Quanifi" is not covered by the AGPL grant; see [NOTICE](NOTICE).
Contributions are accepted under the terms in [CONTRIBUTING.md](CONTRIBUTING.md).
