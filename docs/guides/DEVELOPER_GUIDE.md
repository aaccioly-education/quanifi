# Quanifi developer guide

## Set up a checkout

Install Python 3.12, Git, [uv](https://docs.astral.sh/uv/), and
[just](https://just.systems/). Then run:

```bash
git clone https://github.com/saeg/quanifi.git
cd quanifi
uv sync --frozen --extra dev --extra docs --extra iqm --python 3.12
```

The lockfile fixes the tested dependency versions. NiFi installs each Python
processor in its own environment; the host `.venv` is for tests and tools.
The host environment is not automatically shared with NiFi's processor environments.

## Run and check the code

```bash
just test
just test-file tests/test_qiskit.py
just lint
just format
just docs
```

`just test` runs the full offline suite. Use `.venv/bin/python -m pytest -m
"not slow"` for a shorter run and `--collect-only -q` to inspect the current
inventory. Timing depends on hardware and dependency versions. Tests marked
`slow` include real simulator and optimization workloads.

Tests use NiFi API stubs in `tests/conftest.py`, instantiate the real processor
classes, and check their outputs without a JVM. The test suite does not require
provider credentials. The Docker smoke check additionally exercises a running
NiFi instance; see the [Docker guide](DOCKER_QUICKSTART.md).

`just lint` runs the repository's declared correctness checks. `just format`
formats Python files with Black; review its diff before committing. Documentation
is generated from Markdown sources and processor metadata by `just docs`.

## Run NiFi with Docker

Follow the [Docker quick start](DOCKER_QUICKSTART.md). It includes login details,
a four-step Qiskit Grover example, and instructions for selecting additional
processors. After editing processor code, rebuild with:

```bash
docker compose up -d --build nifi
```

Wait for healthy status before using the NiFi API. Existing configuration volumes
retain their flows; rebuilding does not overwrite saved canvases.

## Use a standalone NiFi installation

Install Apache NiFi and its required Java and Python versions following the
[official administration guide](https://nifi.apache.org/nifi-docs/administration-guide.html).
Configure the Python executable and extension directory in `conf/nifi.properties`:

```properties
nifi.python.command=<absolute-path-to-python>
nifi.python.extensions.source.directory.quanifi=<absolute-path-to-quanifi>/nifi_extensions
```

NiFi resolves `ProcessorDetails.dependencies` independently for each processor.
Configure provider credentials through sensitive properties or parameter contexts,
not committed source files. See [Creating processors](CREATING_PROCESSORS.md) for
component metadata, relationships, and dependency declarations.

## Documentation and examples

Edit Markdown and generator inputs, then run `just docs` to update the published
HTML. The [catalogue](../COMPONENTS.md) is generated from processor descriptions;
edit the relevant `ProcessorDetails.description` to correct an entry.

- [Flow configuration](NIFI_FLOW_CONFIGURATION_GUIDE.md)
- [Interchangeable Grover circuits](INTERCHANGEABLE_GROVER_FLOW.md)
- [pyQuil examples](PYQUIL_COMPONENTS.md)
- [QAOA examples](QAOA_COMPONENTS.md)

Use a separate process group and output directory when trying an example on an
existing NiFi instance. Start only the group intended for that run.
