"""Unit tests for the five fixed-angle QAOA builders (the NxM rows):
QiskitQAOACircuit, CirqQAOACircuit, PennylaneQAOACircuit, QrispQAOACircuit,
PyquilQAOACircuit. No NiFi/JVM -- conftest.py stubs nifiapi.*.
"""

import itertools
import json

import pytest

import qaoa_contract as qc
from conftest import MockFlowFile, result_to_flowfile_merged
from qaoa_reference import (
    ANGLE_SETS,
    H_3BODY,
    H_ASYM,
    chain,
    energies,
    fidelity,
    hamiltonian_ff,
    qasm2_state,
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

FRAMEWORK = {
    "QiskitQAOACircuit": "qiskit",
    "CirqQAOACircuit": "cirq",
    "PennylaneQAOACircuit": "pennylane",
    "QrispQAOACircuit": "qrisp",
    "PyquilQAOACircuit": "pyquil",
}


# ---------------------------------------------------------------------------
# Hamiltonian test cases (shared by exact-equivalence and contract tests)
# ---------------------------------------------------------------------------


def _case_h_asym():
    terms, n = terms_of(H_ASYM)
    return terms, n, hamiltonian_ff(H_ASYM)


def _case_h_3body():
    terms, n = terms_of(H_3BODY)
    return terms, n, hamiltonian_ff(H_3BODY)


def _case_maxcut_default():
    from pauli_dsl import wire_to_terms
    from MaxCutProblem import MaxCutProblem
    from conftest import MockContext

    res = MaxCutProblem().transform(MockContext(), MockFlowFile())
    assert res.relationship == "success", res.attributes
    terms, n = wire_to_terms(res.contents.decode("utf-8"))
    ff = result_to_flowfile_merged(res, MockFlowFile())
    return terms, n, ff


def _case_idle_qubits():
    terms, n = terms_of("Z0", 3)
    return terms, n, hamiltonian_ff("Z0", n="3")


def _case_single_qubit():
    terms, n = terms_of("Z0 - 0.25", 1)
    return terms, n, hamiltonian_ff("Z0 - 0.25", n="1")


HAMILTONIAN_CASES = {
    "h_asym": _case_h_asym,
    "h_3body": _case_h_3body,
    "maxcut_default": _case_maxcut_default,
    "idle_qubits": _case_idle_qubits,
    "single_qubit": _case_single_qubit,
}


# ---------------------------------------------------------------------------
# Exact equivalence with the independent reference
# ---------------------------------------------------------------------------


class TestExactEquivalence:
    @pytest.mark.parametrize("builder", BUILDERS)
    @pytest.mark.parametrize("layers,betas,gammas", ANGLE_SETS)
    @pytest.mark.parametrize("case_name", sorted(HAMILTONIAN_CASES))
    def test_matches_reference_state(self, case_name, layers, betas, gammas, builder):
        terms, n, ff = HAMILTONIAN_CASES[case_name]()
        res = run(
            builder,
            {"Layers": str(layers), "Betas": str(betas), "Gammas": str(gammas)},
            ff,
        )
        assert res.relationship == "success", res.attributes
        assert res.contents == res.attributes["circuit.qasm2"].encode()

        profile = qc.qasm2_profile(res.attributes["circuit.qasm2"])
        assert profile["num_qubits"] == n

        state = qasm2_state(res.attributes["circuit.qasm2"])
        ref = reference_state(terms, n, betas, gammas)
        assert fidelity(state, ref) >= 1 - 1e-9


# ---------------------------------------------------------------------------
# Independent energy cross-check via PyquilExpectation
# ---------------------------------------------------------------------------


class TestIndependentEnergy:
    @pytest.mark.parametrize("builder", BUILDERS)
    def test_expectation_matches_reference_energy(self, builder):
        import numpy as np

        layers, betas, gammas = ANGLE_SETS[1]
        terms, n = terms_of(H_ASYM)
        ff = hamiltonian_ff(H_ASYM)
        built = chain(
            builder,
            {"Layers": str(layers), "Betas": str(betas), "Gammas": str(gammas)},
            ff,
        )
        ev = run("PyquilExpectation", {}, built)
        assert ev.relationship == "success", ev.attributes
        value = json.loads(ev.contents)["expectation"]

        ref = reference_state(terms, n, betas, gammas)
        e = energies(terms, n)
        expected = float(np.sum(e * np.abs(ref) ** 2))
        assert abs(value - expected) < 1e-8


# ---------------------------------------------------------------------------
# Attribute contract
# ---------------------------------------------------------------------------


class TestContract:
    @pytest.mark.parametrize("builder", BUILDERS)
    def test_attribute_contract(self, builder):
        ff = hamiltonian_ff(H_ASYM)
        res = run(
            builder, {"Layers": "2", "Betas": "[0.3,0.4]", "Gammas": "[0.5,0.6]"}, ff
        )
        assert res.relationship == "success", res.attributes
        a = res.attributes

        assert a["circuit.format"] == "qasm2"
        assert a["circuit.qasm2"] == res.contents.decode("utf-8")
        assert a["circuit.qasm3"] == ""
        assert a["circuit.cirq_json"] == ""
        assert a["circuit.framework"] == FRAMEWORK[builder]
        assert a["builder.framework"] == FRAMEWORK[builder]
        assert a["builder.component"] == builder
        assert a["circuit.bit_order"] == "q0_left"
        assert a["circuit.algorithm"] == "qaoa"
        assert a["circuit.num_parameters"] == "0"
        assert a["circuit.t_count"] == "0"
        assert a["circuit.marked_state"] == ""
        assert a["circuit.num_iterations"] == ""
        assert a["circuit.error"] == ""
        assert a["mime.type"] == "text/plain"
        assert a["report.type"] == "circuit"

        assert a["hamiltonian.json"] == bytes(ff.getContentsAsBytes()).decode("utf-8")
        assert json.loads(a["qaoa.betas"]) == [0.3, 0.4]
        assert json.loads(a["qaoa.gammas"]) == [0.5, 0.6]
        assert a["qaoa.layers"] == "2"
        assert a["qaoa.mixer_type"] == "RX"
        assert a["qaoa.error"] == ""
        for key in qc.EVALUATOR_KEYS:
            assert a[key] == ""


# ---------------------------------------------------------------------------
# Stale-attribute blanking through a merged chain
# ---------------------------------------------------------------------------


class TestStaleBlanking:
    @pytest.mark.parametrize("builder", BUILDERS)
    def test_stale_presentation_attrs_are_blanked(self, builder):
        base = hamiltonian_ff(H_ASYM)
        stale = {
            "circuit.svg": "old",
            "circuit.qasm3": "old",
            "circuit.cirq_json": "old",
            "circuit.marked_state": "old",
            "qaoa.best_measurement": "old",
        }
        attrs = base.getAttributes()
        attrs.update(stale)
        upstream = MockFlowFile(
            content=bytes(base.getContentsAsBytes()), attributes=attrs
        )

        res = run(builder, {}, upstream)
        assert res.relationship == "success", res.attributes
        merged = result_to_flowfile_merged(res, upstream)

        for key in (
            "circuit.qasm3",
            "circuit.cirq_json",
            "circuit.marked_state",
            "qaoa.best_measurement",
        ):
            assert merged.getAttribute(key) == ""

        if builder == "CirqQAOACircuit":
            assert merged.getAttribute("circuit.svg") not in ("", "old")
        else:
            assert merged.getAttribute("circuit.svg") == ""


# ---------------------------------------------------------------------------
# Expression language (FLOWFILE_ATTRIBUTES scope)
# ---------------------------------------------------------------------------


class TestExpressionLanguage:
    @pytest.mark.parametrize("builder", BUILDERS)
    def test_betas_gammas_layers_from_attributes(self, builder):
        base = hamiltonian_ff(H_ASYM)
        attrs = base.getAttributes()
        attrs.update({"b": "[0.3,0.4]", "g": "[0.5,0.6]", "p": "2"})
        ff = MockFlowFile(content=bytes(base.getContentsAsBytes()), attributes=attrs)

        res = run(builder, {"Layers": "${p}", "Betas": "${b}", "Gammas": "${g}"}, ff)
        assert res.relationship == "success", res.attributes
        assert json.loads(res.attributes["qaoa.betas"]) == [0.3, 0.4]
        assert json.loads(res.attributes["qaoa.gammas"]) == [0.5, 0.6]
        assert res.attributes["qaoa.layers"] == "2"


# ---------------------------------------------------------------------------
# Rebuild path: any builder's output feeds any other builder
# ---------------------------------------------------------------------------


class TestRebuildPath:
    @pytest.mark.parametrize(
        "first,second", list(itertools.product(BUILDERS, BUILDERS))
    )
    def test_cross_framework_rebuild_matches(self, first, second):
        ff = hamiltonian_ff(H_ASYM)
        props = {"Layers": "1", "Betas": "0.37", "Gammas": "0.81"}
        built = chain(first, props, ff)
        res = run(second, props, built)
        assert res.relationship == "success", (first, second, res.attributes)

        s1 = qasm2_state(built.getAttribute("circuit.qasm2"))
        s2 = qasm2_state(res.attributes["circuit.qasm2"])
        assert fidelity(s1, s2) >= 1 - 1e-9


# ---------------------------------------------------------------------------
# Determinism
# ---------------------------------------------------------------------------


class TestDeterminism:
    @pytest.mark.parametrize("builder", BUILDERS)
    def test_two_runs_produce_identical_qasm2(self, builder):
        ff = hamiltonian_ff(H_3BODY)
        props = {"Layers": "2", "Betas": "[0.3,0.4]", "Gammas": "[0.5,0.6]"}
        res1 = run(builder, props, ff)
        res2 = run(builder, props, ff)
        assert res1.relationship == res2.relationship == "success"
        assert res1.attributes["circuit.qasm2"] == res2.attributes["circuit.qasm2"]


# ---------------------------------------------------------------------------
# Failures
# ---------------------------------------------------------------------------


def _ff_non_diagonal():
    return hamiltonian_ff("X0 X1")


def _ff_identity_only():
    return hamiltonian_ff("0.5", "1")


def _ff_no_hamiltonian():
    return MockFlowFile()


def _ff_default():
    return hamiltonian_ff(H_ASYM)


def _ff_wide_wire():
    label = "Z" + "I" * 16
    raw = json.dumps({"num_qubits": 17, "terms": [[label, 1.0, 0.0]]})
    return MockFlowFile(
        content=raw.encode(), attributes={"hamiltonian.format": "sparse_pauli_op_json"}
    )


FAILURE_CASES = [
    ("non_diagonal_hamiltonian", _ff_non_diagonal, {}, "diagonal"),
    ("identity_only_hamiltonian", _ff_identity_only, {}, "no Z terms"),
    ("no_hamiltonian", _ff_no_hamiltonian, {}, None),
    ("layers_zero", _ff_default, {"Layers": "0"}, None),
    ("layers_17", _ff_default, {"Layers": "17"}, None),
    ("layers_two", _ff_default, {"Layers": "two"}, None),
    ("betas_wrong_length", _ff_default, {"Betas": "[0.1,0.2]"}, None),
    ("betas_nan", _ff_default, {"Betas": "[NaN]"}, None),
    ("betas_abc", _ff_default, {"Betas": "abc"}, None),
    ("wide_wire", _ff_wide_wire, {}, "Hamiltonian qubits"),
]


class TestFailures:
    @pytest.mark.parametrize("builder", BUILDERS)
    @pytest.mark.parametrize(
        "label,ff_factory,props,needle",
        FAILURE_CASES,
        ids=[c[0] for c in FAILURE_CASES],
    )
    def test_failure_preserves_content(self, builder, label, ff_factory, props, needle):
        ff = ff_factory()
        res = run(builder, props, ff)
        assert res.relationship == "failure", (builder, label, res.attributes)
        assert res.contents == bytes(ff.getContentsAsBytes())
        assert res.attributes["qaoa.error"]
        if needle:
            assert needle in res.attributes["qaoa.error"]


# ---------------------------------------------------------------------------
# Qrisp specifics
# ---------------------------------------------------------------------------


class TestQrispSpecifics:
    def test_single_qreg(self):
        ff = hamiltonian_ff(H_ASYM)
        res = run("QrispQAOACircuit", {}, ff)
        assert res.relationship == "success", res.attributes
        qasm = res.attributes["circuit.qasm2"]
        assert qasm.count("qreg ") == 1

    def test_xy_mixer_qasm2_profile_and_hamming_weight_invariant(self):
        import numpy as np
        from math import comb

        terms, n = terms_of("Z0 - Z1 + 0.5 Z1 Z2 + 0.3 Z0 Z2", 3)
        ff = hamiltonian_ff("Z0 - Z1 + 0.5 Z1 Z2 + 0.3 Z0 Z2", n="3")
        res = run(
            "QrispQAOACircuit",
            {
                "Mixer Type": "XY",
                "Layers": "2",
                "Betas": "[0.3,0.4]",
                "Gammas": "[0.5,0.6]",
            },
            ff,
        )
        assert res.relationship == "success", res.attributes
        profile = qc.qasm2_profile(res.attributes["circuit.qasm2"])
        assert profile["num_qubits"] == n

        state = qasm2_state(res.attributes["circuit.qasm2"])
        probs = np.abs(state) ** 2
        by_weight = {}
        for i in range(2**n):
            k = bin(i).count("1")
            by_weight[k] = by_weight.get(k, 0.0) + probs[i]
        for k in range(n + 1):
            assert abs(by_weight.get(k, 0.0) - comb(n, k) / 2**n) < 1e-9

    def test_xy_mixer_matches_qrisps_own_measurement(self):
        import qrisp_qaoa
        from qrisp import QuantumVariable

        spec = "Z0 - Z1 + 0.5 Z1 Z2 + 0.3 Z0 Z2"
        terms, n = terms_of(spec, 3)
        betas, gammas = [0.3, 0.4], [0.5, 0.6]
        ff = hamiltonian_ff(spec, n="3")
        res = run(
            "QrispQAOACircuit",
            {
                "Mixer Type": "XY",
                "Layers": "2",
                "Betas": str(betas),
                "Gammas": str(gammas),
            },
            ff,
        )
        assert res.relationship == "success", res.attributes
        state = qasm2_state(res.attributes["circuit.qasm2"])
        from_qasm = {
            format(i, "0{}b".format(n)): float(abs(state[i]) ** 2) for i in range(2**n)
        }

        qv = QuantumVariable(n)
        qrisp_qaoa.prepare(qv, terms, betas, gammas, "XY")
        native = qv.get_measurement()
        for key, p in from_qasm.items():
            assert abs(p - native.get(key, 0.0)) < 1e-4

    def test_xy_mixer_runs_on_qsharp_simulator(self):
        ff = hamiltonian_ff(H_ASYM)
        built = chain("QrispQAOACircuit", {"Mixer Type": "XY"}, ff)
        res = run("QSharpSimulator", {"Shots": "64", "Random Seed": "11"}, built)
        assert res.relationship == "success", res.attributes


# ---------------------------------------------------------------------------
# Rows are distinct implementations (documentational)
# ---------------------------------------------------------------------------


def test_gate_counts_differ_between_qrisp_and_qiskit_rows():
    ff = hamiltonian_ff(H_3BODY)
    props = {"Layers": "2", "Betas": "[0.3,0.4]", "Gammas": "[0.5,0.6]"}
    qrisp_res = run("QrispQAOACircuit", props, ff)
    qiskit_res = run("QiskitQAOACircuit", props, ff)
    assert qrisp_res.relationship == qiskit_res.relationship == "success"
    assert json.loads(qrisp_res.attributes["circuit.gate_counts"]) != json.loads(
        qiskit_res.attributes["circuit.gate_counts"]
    )
