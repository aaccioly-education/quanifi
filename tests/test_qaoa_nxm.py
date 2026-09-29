"""The QAOA NxM matrix: 5 fixed-angle builders x 7 counts engines, all scored
by QuantumQAOAEvaluator. Demonstrates that every row (builder) is one
unitary and every cell (builder x engine) statistically agrees with the
exact distribution -- the acceptance criterion for the whole extraction.

No NiFi/JVM -- conftest.py stubs nifiapi.*. PyquilSimulator is the one slow
engine (about 13.6 ms/shot); its column runs at 256 shots and is marked
``slow``.
"""

import json
import re

import pytest

from conftest import MockContext, MockFlowFile, result_to_flowfile_merged
from qaoa_reference import (
    EXPECTATION_TOL,
    HELLINGER_MAX,
    H_NXM,
    NXM_BETAS,
    NXM_EMIN,
    NXM_GAMMAS,
    NXM_GROUND,
    NXM_LAYERS,
    energies,
    fidelity,
    hellinger,
    qasm2_state,
    reference_probabilities,
    reference_state,
    run,
    terms_of,
)

BUILDERS = [
    "QiskitQAOACircuit",
    "CirqQAOACircuit",
    "PennylaneQAOACircuit",
    "QrispQAOACircuit",
    "PyquilQAOACircuit",
]
FAST_ENGINES = [
    "QiskitAerSimulator",
    "CirqSimulator",
    "QrispSimulator",
    "PennylaneSimulator",
    "BraketSimulator",
    "QSharpSimulator",
]
SLOW_ENGINES = [("PyquilSimulator", "256")]
SEED = "11"

TERMS, N = terms_of(H_NXM, 3)
REF_STATE = reference_state(TERMS, N, NXM_BETAS, NXM_GAMMAS)
REF_PROBS = reference_probabilities(TERMS, N, NXM_BETAS, NXM_GAMMAS)
_E = energies(TERMS, N)
EXPECTED_SAMPLED_EXPECTATION = float(
    sum(p * _E[int(k, 2)] for k, p in REF_PROBS.items())
)


def _hamiltonian_flowfile():
    from QiskitHamiltonian import QiskitHamiltonian

    res = QiskitHamiltonian().transform(
        MockContext(**{"Hamiltonian": H_NXM, "Num Qubits": "3"}), MockFlowFile()
    )
    assert res.relationship == "success", res.attributes
    return result_to_flowfile_merged(res, MockFlowFile())


def _build_row(builder):
    ff = _hamiltonian_flowfile()
    res = run(
        builder,
        {
            "Layers": str(NXM_LAYERS),
            "Betas": str(NXM_BETAS),
            "Gammas": str(NXM_GAMMAS),
        },
        ff,
    )
    assert res.relationship == "success", (builder, res.attributes)
    return result_to_flowfile_merged(res, ff)


@pytest.fixture(scope="module")
def rows():
    return {b: _build_row(b) for b in BUILDERS}


def _run_cell(
    row_ff, builder, engine, shots, hellinger_max, expectation_tol, optimal_prob_tol
):
    props = {"Shots": shots}
    if engine != "BraketSimulator":
        props["Random Seed"] = SEED
    sim = run(engine, props, row_ff)
    assert sim.relationship == "success", (builder, engine, sim.attributes)
    merged = result_to_flowfile_merged(sim, row_ff)
    ev = run("QuantumQAOAEvaluator", {}, merged)
    assert ev.relationship == "success", (builder, engine, ev.attributes)
    a = ev.attributes

    assert merged.getAttribute("sim.bit_order") == "q0_left"
    assert merged.getAttribute("builder.component") == builder
    assert merged.getAttribute("sim.component") == engine
    assert a["qaoa.builder"] == builder
    assert a["qaoa.engine"] == engine

    counts = json.loads(sim.contents)
    h = hellinger(counts, REF_PROBS)
    assert h <= hellinger_max, "{}x{}: hellinger {} > {}".format(
        builder, engine, h, hellinger_max
    )

    assert a["qaoa.best_measurement"] == NXM_GROUND
    assert abs(float(a["qaoa.best_value"]) - NXM_EMIN) <= 1e-9
    assert abs(float(a["qaoa.exact_minimum"]) - NXM_EMIN) <= 1e-9
    assert float(a["qaoa.approximation_ratio"]) == 1.0

    sampled = float(a["qaoa.sampled_expectation"])
    assert abs(sampled - EXPECTED_SAMPLED_EXPECTATION) <= expectation_tol

    assert (
        abs(float(a["qaoa.optimal_probability"]) - REF_PROBS[NXM_GROUND])
        <= optimal_prob_tol
    )

    if engine == "BraketSimulator":
        assert merged.getAttribute("run.seed") is None
    else:
        assert merged.getAttribute("run.seed") == SEED


def test_rows_are_one_unitary(rows):
    for builder, ff in rows.items():
        state = qasm2_state(ff.getAttribute("circuit.qasm2"))
        assert fidelity(state, REF_STATE) >= 1 - 1e-9, builder


@pytest.mark.parametrize("engine", FAST_ENGINES)
@pytest.mark.parametrize("builder", BUILDERS)
def test_cell(rows, builder, engine):
    _run_cell(
        rows[builder],
        builder,
        engine,
        "4096",
        HELLINGER_MAX[4096],
        EXPECTATION_TOL[4096],
        0.04,
    )


@pytest.mark.slow
@pytest.mark.parametrize("builder", BUILDERS)
def test_pyquil_cell(rows, builder):
    engine, shots = SLOW_ENGINES[0]
    _run_cell(
        rows[builder],
        builder,
        engine,
        shots,
        HELLINGER_MAX[256],
        EXPECTATION_TOL[256],
        0.16,
    )


SEEDED_FAST_ENGINES = [e for e in FAST_ENGINES if e != "BraketSimulator"]


@pytest.mark.parametrize("engine", SEEDED_FAST_ENGINES)
def test_seeded_cells_reproduce(rows, engine):
    ff = rows["QiskitQAOACircuit"]
    props = {"Shots": "4096", "Random Seed": SEED}
    r1 = run(engine, props, ff)
    r2 = run(engine, props, ff)
    assert r1.relationship == r2.relationship == "success"
    assert r1.contents == r2.contents


def _mirror_qasm(source, n):
    """Rewrite every gate's qubit index q[i] -> q[n-1-i] (leaves the qreg
    declaration untouched, so only which physical qubit each gate targets
    changes -- the same circuit, wired to the mirror-image qubit)."""

    def repl(match):
        idx = int(match.group(1))
        return "q[{}]".format(n - 1 - idx)

    lines = []
    for line in source.splitlines():
        if line.strip().startswith("qreg"):
            lines.append(line)
        else:
            lines.append(re.sub(r"q\[(\d+)\]", repl, line))
    return "\n".join(lines) + "\n"


def test_matrix_detects_a_mirrored_row(rows):
    """A cell scored only by best_measurement would not catch a systematic
    qubit-order bug (best_measurement is computed from whichever observed
    state has minimum energy, regardless of how much mass it carries). This
    is why every cell is also compared as a *distribution*."""
    ff = rows["QiskitQAOACircuit"]
    original = ff.getAttribute("circuit.qasm2")
    mirrored = _mirror_qasm(original, N)
    attrs = ff.getAttributes()
    attrs["circuit.qasm2"] = mirrored
    mirrored_ff = MockFlowFile(content=mirrored.encode("utf-8"), attributes=attrs)

    sim = run("QiskitAerSimulator", {"Shots": "4096", "Random Seed": SEED}, mirrored_ff)
    assert sim.relationship == "success", sim.attributes
    merged = result_to_flowfile_merged(sim, mirrored_ff)
    ev = run("QuantumQAOAEvaluator", {}, merged)
    assert ev.relationship == "success", ev.attributes

    counts = json.loads(sim.contents)
    h = hellinger(counts, REF_PROBS)
    assert h > 0.3

    assert float(ev.attributes["qaoa.optimal_probability"]) < 0.1
    assert ev.attributes["qaoa.best_measurement"] == NXM_GROUND
