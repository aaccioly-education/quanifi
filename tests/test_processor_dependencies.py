"""Guard the qiskit ceiling in every processor's declared dependencies.

NiFi does not run the project venv for Python processors: it builds a
*per-extension* venv under `nifi-2.9.0/work/python/extensions/<Name>/<version>/`
and pip-installs each processor's `ProcessorDetails.dependencies` there.  An
unbounded `qiskit>=2.0.0` therefore resolves to whatever is newest on PyPI at
the moment that venv is first created -- independently of `uv.lock`.

Qiskit 2.5.0 started shipping wheels whose `_accelerate` extension bundles
mimalloc, and mimalloc's per-thread init (`mi_thread_init`) segfaults
(SIGSEGV, KERN_INVALID_ADDRESS at 0x18) when several freshly created py4j
connection threads make their first qiskit-Rust allocation at the same instant
-- which is exactly what one wave of FlowFiles does.  That killed the
QiskitQuantumArithmetic / QrispQuantumArithmetic / QuantumMutator processes
mid-run with exit code 139 and silently dropped every in-flight FlowFile.

Versions <= 2.4.2 do not bundle mimalloc and are unaffected, so every
processor pins `<2.5` until an upstream fix lands.  This test fails if a new or
edited processor reintroduces an unbounded (or >=2.5-allowing) qiskit spec.
"""
import pathlib
import re

import pytest

EXTENSIONS_DIR = pathlib.Path(__file__).resolve().parent.parent / "nifi_extensions"

# Matches a qiskit requirement (not qiskit-aer / qiskit-ibm-runtime / ...) in a
# dependencies list: the name must be followed by a version spec or the closing
# quote, never by a hyphen.
QISKIT_REQ = re.compile(r'"(qiskit(?:\[[^\]]+\])?)(?![-\w])([^"]*)"')

# Requirement strings only ever appear inside a `dependencies = [...]` block.
DEPENDENCIES_BLOCK = re.compile(r"dependencies\s*=\s*\[(.*?)\]", re.DOTALL)

MAX_EXCLUSIVE = "<2.5"


def _dependency_blocks(path):
    return DEPENDENCIES_BLOCK.findall(path.read_text())


def _processor_files():
    return sorted(p for p in EXTENSIONS_DIR.glob("*.py")
                  if not p.name.startswith("_"))


@pytest.mark.parametrize("path", _processor_files(), ids=lambda p: p.name)
def test_qiskit_dependency_is_capped_below_2_5(path):
    """No processor may declare a qiskit dependency that allows >= 2.5."""
    for block in _dependency_blocks(path):
        for name, spec in QISKIT_REQ.findall(block):
            assert MAX_EXCLUSIVE in spec, (
                "{}: dependency '{}{}' allows qiskit >= 2.5, whose bundled "
                "mimalloc segfaults NiFi's py4j worker threads. Pin it with "
                "'{}' (e.g. \"qiskit>=2.0.0,{}\").".format(
                    path.name, name, spec, MAX_EXCLUSIVE, MAX_EXCLUSIVE))


def test_guard_actually_sees_qiskit_dependencies():
    """Sanity: the regex must find real qiskit pins, or the guard is vacuous."""
    found = [p.name for p in _processor_files()
             for block in _dependency_blocks(p)
             if QISKIT_REQ.search(block)]
    assert len(found) > 20, "expected many qiskit-dependent processors, got {}".format(found)


def test_guard_rejects_an_unbounded_spec():
    """Sanity: an unbounded spec must not slip past the regex."""
    assert QISKIT_REQ.findall('dependencies = ["qiskit>=2.0.0"]') == [("qiskit", ">=2.0.0")]
    # qiskit-aer and friends are different distributions and are not capped.
    assert QISKIT_REQ.findall('["qiskit-aer>=0.13.0"]') == []


# Packages that drag qiskit in transitively.  A processor that depends on one of
# these gets whatever qiskit their resolver picks unless it also names qiskit
# itself -- which is how QrispSimulator and QrispIQMDevice ended up on 2.5.2
# while every directly-declaring processor was safely on 2.4.x.
QISKIT_PULLERS = ("qrisp", "pennylane-qiskit", "iqm-client")

REQ_NAME = re.compile(r'"([A-Za-z0-9_.-]+)(?:\[[^\]]+\])?[^"]*"')


def _requirement_names(block):
    return {n.lower() for n in REQ_NAME.findall(block)}


@pytest.mark.parametrize("path", _processor_files(), ids=lambda p: p.name)
def test_transitive_qiskit_is_capped_too(path):
    """A processor pulling qiskit indirectly must still name a capped qiskit."""
    for block in _dependency_blocks(path):
        names = _requirement_names(block)
        pullers = sorted(n for n in names if n in QISKIT_PULLERS
                         or n.startswith("qiskit-"))
        if not pullers:
            continue
        specs = QISKIT_REQ.findall(block)
        assert specs, (
            "{}: depends on {} which pull qiskit transitively, but never names "
            "qiskit itself -- the per-extension venv will resolve whatever is "
            "newest, including the >= 2.5 builds that segfault. Add "
            "\"qiskit>=2.0.0,{}\".".format(path.name, pullers, MAX_EXCLUSIVE))
        for name, spec in specs:
            assert MAX_EXCLUSIVE in spec, (
                "{}: '{}{}' must be capped with '{}'".format(
                    path.name, name, spec, MAX_EXCLUSIVE))
