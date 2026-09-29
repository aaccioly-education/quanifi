"""
Unit tests for the QAOA solver family:

    <any>Hamiltonian -> <Framework>QAOA -> QuanifiReport

QAOA has no separate ansatz stage (the ansatz is derived from the cost
Hamiltonian), so each solver consumes the framework-neutral
sparse_pauli_op_json wire content directly. The canonical test problem is
MaxCut on a triangle, H = 0.5 (Z0Z1 + Z1Z2 + Z0Z2): exact minimum -0.5,
attained by every 2-vs-1 partition (6 of the 8 basis states).
No running NiFi / JVM — conftest.py stubs nifiapi.*.
"""

import importlib
import json

import numpy as np
import pytest

import qaoa_contract as qc
from qaoa_contract import EVALUATOR_KEYS
from QiskitHamiltonian import QiskitHamiltonian
from QiskitQAOA import QiskitQAOA

from conftest import (
    MockContext,
    MockFlowFile,
    result_to_flowfile,
    result_to_flowfile_merged,
)
import qaoa_reference as qref

TRIANGLE = "0.5 Z0 Z1 + 0.5 Z1 Z2 + 0.5 Z0 Z2"
TRIANGLE_MIN = -0.5
TRIANGLE_OPTIMA = {"001", "010", "100", "011", "101", "110"}

# Solver framework -> processor class name, used by assert_solver_contract to
# check "Shots" is not among the descriptor names (the clean-break property).
_SOLVER_CLASS = {
    "qiskit": "QiskitQAOA",
    "cirq": "CirqQAOA",
    "pennylane": "PennylaneQAOA",
    "qrisp": "QrispQAOA",
    "pyquil": "PyquilQAOA",
}


def triangle_flowfile():
    res = QiskitHamiltonian().transform(
        MockContext(**{"Hamiltonian": TRIANGLE, "Num Qubits": "0"}), MockFlowFile()
    )
    assert res.relationship == "success"
    return result_to_flowfile(res)


def assert_solver_contract(res, framework, layers, ham_content):
    """The shared qaoa.* / circuit.* contract every (train-only) solver emits."""
    assert res.relationship == "success", res.attributes
    a = res.attributes
    assert res.contents.decode("utf-8") == a["circuit.qasm2"]
    assert a["circuit.format"] == "qasm2"
    qc.qasm2_profile(a["circuit.qasm2"])  # must not raise
    assert a["qaoa.framework"] == framework
    assert a["qaoa.layers"] == str(layers)
    betas = json.loads(a["qaoa.betas"])
    gammas = json.loads(a["qaoa.gammas"])
    assert len(betas) == layers and len(gammas) == layers
    assert json.loads(a["qaoa.optimal_parameters"]) == betas + gammas
    assert a["report.type"] == "circuit"
    assert not any(k.startswith("sim.") for k in a)
    for k in EVALUATOR_KEYS:
        assert a[k] == "", (k, a[k])
    assert a["hamiltonian.json"] == ham_content

    cls_name = _SOLVER_CLASS[framework]
    cls = getattr(importlib.import_module(cls_name), cls_name)
    names = [d.name for d in cls().descriptors]
    assert "Shots" not in names


def assert_triangle_evaluated(ev):
    """The evaluator's output on a triangle-MaxCut solver run."""
    assert ev.relationship == "success", ev.attributes
    a = ev.attributes
    assert abs(float(a["qaoa.exact_minimum"]) - TRIANGLE_MIN) < 1e-9
    assert float(a["qaoa.approximation_ratio"]) >= 0.99
    assert a["qaoa.best_measurement"] in TRIANGLE_OPTIMA
    assert float(a["qaoa.optimal_probability"]) >= 0.9
    assert a["report.type"] == "simulation"


# ---------------------------------------------------------------------------
# pauli_dsl diagonal helpers
# ---------------------------------------------------------------------------


class TestDiagonalHelpers:

    def test_diagonal_values_zz(self):
        from pauli_dsl import diagonal_values

        # Z0 Z1: +1 on aligned (00, 11), -1 on anti-aligned (01, 10).
        v = diagonal_values([("ZZ", [0, 1], 1.0)], 2)
        assert v.tolist() == [1.0, -1.0, -1.0, 1.0]

    def test_is_diagonal(self):
        from pauli_dsl import is_diagonal

        assert is_diagonal([("ZZ", [0, 1], 1.0), ("", [], 0.5)])
        assert not is_diagonal([("XX", [0, 1], 1.0)])

    def test_diagonal_values_rejects_x_terms(self):
        from pauli_dsl import diagonal_values, PauliDSLError

        with pytest.raises(PauliDSLError):
            diagonal_values([("X", [0], 1.0)], 1)


# ---------------------------------------------------------------------------
# QiskitQAOA
# ---------------------------------------------------------------------------


class TestQiskitQAOA:

    def test_triangle_maxcut(self):
        ff = triangle_flowfile()
        ham_content = bytes(ff.getContentsAsBytes()).decode("utf-8")
        res = QiskitQAOA().transform(
            MockContext(
                **{"Layers": "2", "Max Iterations": "150", "Random Seed": "11"}
            ),
            ff,
        )
        assert_solver_contract(res, "qiskit", 2, ham_content)
        merged = result_to_flowfile_merged(res, ff)
        _engine_res, _merged2, ev = qref.evaluate(
            merged, "QiskitAerSimulator", {"Shots": "1024", "Random Seed": "11"}
        )
        assert_triangle_evaluated(ev)

    def test_energy_consistency(self):
        ff = qref.hamiltonian_ff(qref.H_ASYM)
        res = QiskitQAOA().transform(
            MockContext(
                **{"Layers": "2", "Max Iterations": "150", "Random Seed": "11"}
            ),
            ff,
        )
        assert res.relationship == "success", res.attributes
        a = res.attributes
        assert a["qaoa.energy_method"] == "sampled_statevector"
        assert a["qaoa.energy_shots"] == "1024"

        terms, n = qref.terms_of(qref.H_ASYM)
        state = qref.qasm2_state(a["circuit.qasm2"])
        probs = np.abs(state) ** 2
        e = qref.energies(terms, n)
        e_state = float(np.dot(probs, e))
        sigma = float(np.sqrt(np.dot(probs, (e - e_state) ** 2)))
        optimal_value = float(a["qaoa.optimal_value"])
        tolerance = 6.0 * sigma / np.sqrt(1024) + 1e-9
        assert abs(e_state - optimal_value) <= tolerance, (
            e_state,
            optimal_value,
            tolerance,
        )

    def test_round_trip_builder(self):
        from QiskitQAOACircuit import QiskitQAOACircuit

        ff = triangle_flowfile()
        res = QiskitQAOA().transform(
            MockContext(
                **{"Layers": "2", "Max Iterations": "150", "Random Seed": "11"}
            ),
            ff,
        )
        assert res.relationship == "success", res.attributes
        merged = result_to_flowfile_merged(res, ff)

        rebuilt = QiskitQAOACircuit().transform(
            MockContext(
                **{
                    "Layers": "${qaoa.layers}",
                    "Betas": "${qaoa.betas}",
                    "Gammas": "${qaoa.gammas}",
                }
            ),
            merged,
        )
        assert rebuilt.relationship == "success", rebuilt.attributes
        assert rebuilt.attributes["circuit.qasm2"] == res.attributes["circuit.qasm2"]

    def test_non_diagonal_routes_to_failure(self):
        ham = QiskitHamiltonian().transform(
            MockContext(**{"Hamiltonian": "X0 X1", "Num Qubits": "0"}), MockFlowFile()
        )
        res = QiskitQAOA().transform(MockContext(), result_to_flowfile(ham))
        assert res.relationship == "failure"
        assert "diagonal" in res.attributes["qaoa.error"]
        assert res.contents == ham.contents

    def test_missing_hamiltonian_routes_to_failure(self):
        res = QiskitQAOA().transform(MockContext(), MockFlowFile())
        assert res.relationship == "failure"
        assert "qaoa.error" in res.attributes

    def test_bad_initial_parameters_length(self):
        ff = triangle_flowfile()
        res = QiskitQAOA().transform(
            MockContext(**{"Layers": "2", "Initial Parameters": "0.1, 0.2"}), ff
        )
        assert res.relationship == "failure"
        assert "2p" in res.attributes["qaoa.error"]
        assert res.contents == bytes(ff.getContentsAsBytes())

    def test_identity_only_hamiltonian_is_rejected(self):
        ham = QiskitHamiltonian().transform(
            MockContext(**{"Hamiltonian": "0.5", "Num Qubits": "1"}), MockFlowFile()
        )
        res = QiskitQAOA().transform(MockContext(), result_to_flowfile(ham))
        assert res.relationship == "failure"
        assert "no Z terms" in res.attributes["qaoa.error"]
        assert res.contents == ham.contents

    def test_molecule_hamiltonian_is_rejected_as_non_diagonal(self):
        # Interchange boundary check: the molecular (X/Y-bearing) Hamiltonian
        # rides the same wire format but is not a QAOA problem.
        from MoleculeHamiltonian import MoleculeHamiltonian

        ham = MoleculeHamiltonian().transform(
            MockContext(**{"Compute Reference Energies": "false"}), MockFlowFile()
        )
        res = QiskitQAOA().transform(MockContext(), result_to_flowfile(ham))
        assert res.relationship == "failure"
        assert "diagonal" in res.attributes["qaoa.error"]
        assert res.contents == ham.contents

    def test_bad_random_seed_routes_to_failure(self):
        ff = triangle_flowfile()
        res = QiskitQAOA().transform(MockContext(**{"Random Seed": "-1"}), ff)
        assert res.relationship == "failure"
        assert "qaoa.error" in res.attributes
        assert res.contents == bytes(ff.getContentsAsBytes())

    def test_bad_optimizer_routes_to_failure(self):
        ff = triangle_flowfile()
        res = QiskitQAOA().transform(MockContext(**{"Optimizer": "BOGUS"}), ff)
        assert res.relationship == "failure"
        assert "qaoa.error" in res.attributes
        assert res.contents == bytes(ff.getContentsAsBytes())


# ---------------------------------------------------------------------------
# CirqQAOA
# ---------------------------------------------------------------------------


class TestCirqQAOA:

    def test_triangle_maxcut(self):
        from CirqQAOA import CirqQAOA

        ff = triangle_flowfile()
        ham_content = bytes(ff.getContentsAsBytes()).decode("utf-8")
        res = CirqQAOA().transform(
            MockContext(
                **{"Layers": "2", "Max Iterations": "150", "Random Seed": "11"}
            ),
            ff,
        )
        assert_solver_contract(res, "cirq", 2, ham_content)
        merged = result_to_flowfile_merged(res, ff)
        _engine_res, _merged2, ev = qref.evaluate(
            merged, "QiskitAerSimulator", {"Shots": "1024", "Random Seed": "11"}
        )
        assert_triangle_evaluated(ev)

    def test_energy_consistency(self):
        from CirqQAOA import CirqQAOA

        ff = qref.hamiltonian_ff(qref.H_ASYM)
        res = CirqQAOA().transform(
            MockContext(
                **{"Layers": "2", "Max Iterations": "150", "Random Seed": "11"}
            ),
            ff,
        )
        assert res.relationship == "success", res.attributes
        a = res.attributes
        assert a["qaoa.energy_method"] == "exact_statevector"
        assert a["qaoa.energy_shots"] == ""

        terms, n = qref.terms_of(qref.H_ASYM)
        state = qref.qasm2_state(a["circuit.qasm2"])
        probs = np.abs(state) ** 2
        e = qref.energies(terms, n)
        e_state = float(np.dot(probs, e))
        optimal_value = float(a["qaoa.optimal_value"])
        assert abs(e_state - optimal_value) < 1e-7, (e_state, optimal_value)

    def test_round_trip_builder(self):
        from CirqQAOA import CirqQAOA
        from CirqQAOACircuit import CirqQAOACircuit

        ff = triangle_flowfile()
        res = CirqQAOA().transform(
            MockContext(
                **{"Layers": "2", "Max Iterations": "150", "Random Seed": "11"}
            ),
            ff,
        )
        assert res.relationship == "success", res.attributes
        merged = result_to_flowfile_merged(res, ff)

        rebuilt = CirqQAOACircuit().transform(
            MockContext(
                **{
                    "Layers": "${qaoa.layers}",
                    "Betas": "${qaoa.betas}",
                    "Gammas": "${qaoa.gammas}",
                }
            ),
            merged,
        )
        assert rebuilt.relationship == "success", rebuilt.attributes
        assert rebuilt.attributes["circuit.qasm2"] == res.attributes["circuit.qasm2"]

    def test_non_diagonal_routes_to_failure(self):
        from CirqQAOA import CirqQAOA

        ham = QiskitHamiltonian().transform(
            MockContext(**{"Hamiltonian": "X0 X1", "Num Qubits": "0"}), MockFlowFile()
        )
        res = CirqQAOA().transform(MockContext(), result_to_flowfile(ham))
        assert res.relationship == "failure"
        assert "diagonal" in res.attributes["qaoa.error"]
        assert res.contents == ham.contents

    def test_missing_hamiltonian_routes_to_failure(self):
        from CirqQAOA import CirqQAOA

        res = CirqQAOA().transform(MockContext(), MockFlowFile())
        assert res.relationship == "failure"
        assert "qaoa.error" in res.attributes

    def test_identity_only_hamiltonian_is_rejected(self):
        from CirqQAOA import CirqQAOA

        ham = QiskitHamiltonian().transform(
            MockContext(**{"Hamiltonian": "0.5", "Num Qubits": "1"}), MockFlowFile()
        )
        res = CirqQAOA().transform(MockContext(), result_to_flowfile(ham))
        assert res.relationship == "failure"
        assert "no Z terms" in res.attributes["qaoa.error"]
        assert res.contents == ham.contents

    def test_bad_random_seed_routes_to_failure(self):
        from CirqQAOA import CirqQAOA

        ff = triangle_flowfile()
        res = CirqQAOA().transform(MockContext(**{"Random Seed": "-1"}), ff)
        assert res.relationship == "failure"
        assert "qaoa.error" in res.attributes
        assert res.contents == bytes(ff.getContentsAsBytes())

    def test_bad_optimizer_routes_to_failure(self):
        from CirqQAOA import CirqQAOA

        ff = triangle_flowfile()
        res = CirqQAOA().transform(MockContext(**{"Optimizer": "BOGUS"}), ff)
        assert res.relationship == "failure"
        assert "qaoa.error" in res.attributes
        assert res.contents == bytes(ff.getContentsAsBytes())


# ---------------------------------------------------------------------------
# QrispQAOA — actual-optimization tests marked slow (real Qrisp simulation),
# matching the convention in test_qrisp_vqe.py.
# ---------------------------------------------------------------------------


class TestQrispQAOA:

    @pytest.mark.slow
    def test_triangle_maxcut(self):
        from QrispQAOA import QrispQAOA

        ff = triangle_flowfile()
        ham_content = bytes(ff.getContentsAsBytes()).decode("utf-8")
        res = QrispQAOA().transform(
            MockContext(
                **{"Layers": "2", "Max Iterations": "100", "Random Seed": "11"}
            ),
            ff,
        )
        assert_solver_contract(res, "qrisp", 2, ham_content)
        merged = result_to_flowfile_merged(res, ff)
        _engine_res, _merged2, ev = qref.evaluate(
            merged, "QrispSimulator", {"Shots": "1024", "Random Seed": "11"}
        )
        assert_triangle_evaluated(ev)

    @pytest.mark.slow
    def test_energy_consistency(self):
        from QrispQAOA import QrispQAOA

        ff = qref.hamiltonian_ff(qref.H_ASYM)
        res = QrispQAOA().transform(
            MockContext(
                **{"Layers": "2", "Max Iterations": "100", "Random Seed": "11"}
            ),
            ff,
        )
        assert res.relationship == "success", res.attributes
        a = res.attributes
        assert a["qaoa.energy_method"] == "exact_probabilities"
        assert a["qaoa.energy_shots"] == ""

        terms, n = qref.terms_of(qref.H_ASYM)
        state = qref.qasm2_state(a["circuit.qasm2"])
        probs = np.abs(state) ** 2
        e = qref.energies(terms, n)
        e_state = float(np.dot(probs, e))
        optimal_value = float(a["qaoa.optimal_value"])
        # Qrisp's optimization_routine trains against its own probabilities,
        # rounded to 5 decimals (verified fact 7); looser than the exact
        # frameworks' 1e-7.
        assert abs(e_state - optimal_value) < 1e-4, (e_state, optimal_value)

    @pytest.mark.slow
    @pytest.mark.parametrize("mixer_name", ["RX", "XY"])
    def test_round_trip_builder(self, mixer_name):
        from QrispQAOA import QrispQAOA
        from QrispQAOACircuit import QrispQAOACircuit

        ff = triangle_flowfile()
        res = QrispQAOA().transform(
            MockContext(
                **{
                    "Layers": "2",
                    "Max Iterations": "100",
                    "Random Seed": "11",
                    "Mixer Type": mixer_name,
                }
            ),
            ff,
        )
        assert res.relationship == "success", res.attributes
        assert res.attributes["qaoa.mixer_type"] == mixer_name
        merged = result_to_flowfile_merged(res, ff)

        rebuilt = QrispQAOACircuit().transform(
            MockContext(
                **{
                    "Layers": "${qaoa.layers}",
                    "Betas": "${qaoa.betas}",
                    "Gammas": "${qaoa.gammas}",
                    "Mixer Type": "${qaoa.mixer_type}",
                }
            ),
            merged,
        )
        assert rebuilt.relationship == "success", rebuilt.attributes
        assert rebuilt.attributes["circuit.qasm2"] == res.attributes["circuit.qasm2"]

    def test_initial_parameters_are_canonical(self):
        """The Initial Parameters property is canonical beta-then-gamma; Qrisp's
        own internal ordering (gamma-then-beta) is converted at the boundary
        via qrisp_qaoa.to_qrisp_theta / from_qrisp_theta."""
        import qrisp_qaoa
        from QrispQAOA import QrispQAOA

        # The conversion helpers round-trip independently of any solver run.
        assert qrisp_qaoa.to_qrisp_theta([1.0, 2.0], [3.0, 4.0]) == [3.0, 4.0, 1.0, 2.0]
        assert qrisp_qaoa.from_qrisp_theta([3.0, 4.0, 1.0, 2.0], 2) == (
            [1.0, 2.0],
            [3.0, 4.0],
        )

        ff = triangle_flowfile()
        res = QrispQAOA().transform(
            MockContext(
                **{
                    "Layers": "2",
                    "Max Iterations": "1",
                    "Optimizer": "POWELL",
                    "Initial Parameters": "[0.1,0.2,0.3,0.4]",
                }
            ),
            ff,
        )
        assert res.relationship == "success", res.attributes
        assert len(json.loads(res.attributes["qaoa.optimal_parameters"])) == 4

    def test_non_diagonal_routes_to_failure(self):
        """Strict-contract failures fail fast (no quantum execution) -> not slow."""
        from QrispQAOA import QrispQAOA

        ham = QiskitHamiltonian().transform(
            MockContext(**{"Hamiltonian": "X0 X1", "Num Qubits": "0"}), MockFlowFile()
        )
        res = QrispQAOA().transform(MockContext(), result_to_flowfile(ham))
        assert res.relationship == "failure"
        assert "diagonal" in res.attributes["qaoa.error"]
        assert res.contents == ham.contents

    def test_missing_hamiltonian_routes_to_failure(self):
        from QrispQAOA import QrispQAOA

        res = QrispQAOA().transform(MockContext(), MockFlowFile())
        assert res.relationship == "failure"
        assert "qaoa.error" in res.attributes

    def test_identity_only_hamiltonian_is_rejected(self):
        from QrispQAOA import QrispQAOA

        ham = QiskitHamiltonian().transform(
            MockContext(**{"Hamiltonian": "0.5", "Num Qubits": "1"}), MockFlowFile()
        )
        res = QrispQAOA().transform(MockContext(), result_to_flowfile(ham))
        assert res.relationship == "failure"
        assert "no Z terms" in res.attributes["qaoa.error"]
        assert res.contents == ham.contents

    def test_bad_random_seed_routes_to_failure(self):
        from QrispQAOA import QrispQAOA

        ff = triangle_flowfile()
        res = QrispQAOA().transform(MockContext(**{"Random Seed": "-1"}), ff)
        assert res.relationship == "failure"
        assert "qaoa.error" in res.attributes
        assert res.contents == bytes(ff.getContentsAsBytes())

    def test_bad_optimizer_routes_to_failure(self):
        from QrispQAOA import QrispQAOA

        ff = triangle_flowfile()
        res = QrispQAOA().transform(MockContext(**{"Optimizer": "BOGUS"}), ff)
        assert res.relationship == "failure"
        assert "qaoa.error" in res.attributes
        assert res.contents == bytes(ff.getContentsAsBytes())


# ---------------------------------------------------------------------------
# PennylaneQAOA
# ---------------------------------------------------------------------------


class TestPennylaneQAOA:

    def test_triangle_maxcut(self):
        from PennylaneQAOA import PennylaneQAOA

        ff = triangle_flowfile()
        ham_content = bytes(ff.getContentsAsBytes()).decode("utf-8")
        res = PennylaneQAOA().transform(
            MockContext(
                **{"Layers": "2", "Max Iterations": "150", "Random Seed": "11"}
            ),
            ff,
        )
        assert_solver_contract(res, "pennylane", 2, ham_content)
        merged = result_to_flowfile_merged(res, ff)
        _engine_res, _merged2, ev = qref.evaluate(
            merged, "QiskitAerSimulator", {"Shots": "1024", "Random Seed": "11"}
        )
        assert_triangle_evaluated(ev)

    def test_energy_consistency(self):
        from PennylaneQAOA import PennylaneQAOA

        ff = qref.hamiltonian_ff(qref.H_ASYM)
        res = PennylaneQAOA().transform(
            MockContext(
                **{"Layers": "2", "Max Iterations": "150", "Random Seed": "11"}
            ),
            ff,
        )
        assert res.relationship == "success", res.attributes
        a = res.attributes
        assert a["qaoa.energy_method"] == "exact_statevector"
        assert a["qaoa.energy_shots"] == ""

        terms, n = qref.terms_of(qref.H_ASYM)
        state = qref.qasm2_state(a["circuit.qasm2"])
        probs = np.abs(state) ** 2
        e = qref.energies(terms, n)
        e_state = float(np.dot(probs, e))
        optimal_value = float(a["qaoa.optimal_value"])
        assert abs(e_state - optimal_value) < 1e-7, (e_state, optimal_value)

    def test_round_trip_builder(self):
        from PennylaneQAOA import PennylaneQAOA
        from PennylaneQAOACircuit import PennylaneQAOACircuit

        ff = triangle_flowfile()
        res = PennylaneQAOA().transform(
            MockContext(
                **{"Layers": "2", "Max Iterations": "150", "Random Seed": "11"}
            ),
            ff,
        )
        assert res.relationship == "success", res.attributes
        merged = result_to_flowfile_merged(res, ff)

        rebuilt = PennylaneQAOACircuit().transform(
            MockContext(
                **{
                    "Layers": "${qaoa.layers}",
                    "Betas": "${qaoa.betas}",
                    "Gammas": "${qaoa.gammas}",
                }
            ),
            merged,
        )
        assert rebuilt.relationship == "success", rebuilt.attributes
        assert rebuilt.attributes["circuit.qasm2"] == res.attributes["circuit.qasm2"]

    def test_non_diagonal_routes_to_failure(self):
        from PennylaneQAOA import PennylaneQAOA

        ham = QiskitHamiltonian().transform(
            MockContext(**{"Hamiltonian": "X0 X1", "Num Qubits": "0"}), MockFlowFile()
        )
        res = PennylaneQAOA().transform(MockContext(), result_to_flowfile(ham))
        assert res.relationship == "failure"
        assert "diagonal" in res.attributes["qaoa.error"]
        assert res.contents == ham.contents

    def test_missing_hamiltonian_routes_to_failure(self):
        from PennylaneQAOA import PennylaneQAOA

        res = PennylaneQAOA().transform(MockContext(), MockFlowFile())
        assert res.relationship == "failure"
        assert "qaoa.error" in res.attributes

    def test_identity_only_hamiltonian_is_rejected(self):
        from PennylaneQAOA import PennylaneQAOA

        ham = QiskitHamiltonian().transform(
            MockContext(**{"Hamiltonian": "0.5", "Num Qubits": "1"}), MockFlowFile()
        )
        res = PennylaneQAOA().transform(MockContext(), result_to_flowfile(ham))
        assert res.relationship == "failure"
        assert "no Z terms" in res.attributes["qaoa.error"]
        assert res.contents == ham.contents

    def test_bad_random_seed_routes_to_failure(self):
        from PennylaneQAOA import PennylaneQAOA

        ff = triangle_flowfile()
        res = PennylaneQAOA().transform(MockContext(**{"Random Seed": "-1"}), ff)
        assert res.relationship == "failure"
        assert "qaoa.error" in res.attributes
        assert res.contents == bytes(ff.getContentsAsBytes())

    def test_bad_optimizer_routes_to_failure(self):
        from PennylaneQAOA import PennylaneQAOA

        ff = triangle_flowfile()
        res = PennylaneQAOA().transform(MockContext(**{"Optimizer": "BOGUS"}), ff)
        assert res.relationship == "failure"
        assert "qaoa.error" in res.attributes
        assert res.contents == bytes(ff.getContentsAsBytes())


# ---------------------------------------------------------------------------
# Cross-framework interchange: any *Hamiltonian feeds any QAOA solver,
# because the wire format (and the property names) are shared.
# ---------------------------------------------------------------------------


class TestQAOAInterchange:

    @pytest.mark.parametrize(
        "ham_cls_name", ["QiskitHamiltonian", "CirqHamiltonian", "QrispHamiltonian"]
    )
    def test_any_hamiltonian_feeds_qiskit_qaoa(self, ham_cls_name):
        ham_cls = getattr(importlib.import_module(ham_cls_name), ham_cls_name)
        ham = ham_cls().transform(
            MockContext(**{"Hamiltonian": TRIANGLE, "Num Qubits": "0"}), MockFlowFile()
        )
        assert ham.relationship == "success"
        ham_ff = result_to_flowfile(ham)
        res = QiskitQAOA().transform(
            MockContext(**{"Layers": "1", "Max Iterations": "80", "Random Seed": "11"}),
            ham_ff,
        )
        assert res.relationship == "success", res.attributes
        merged = result_to_flowfile_merged(res, ham_ff)
        _engine_res, _merged2, ev = qref.evaluate(
            merged, "QiskitAerSimulator", {"Shots": "1024", "Random Seed": "11"}
        )
        assert ev.relationship == "success", ev.attributes
        assert abs(float(ev.attributes["qaoa.exact_minimum"]) - TRIANGLE_MIN) < 1e-9
        assert float(ev.attributes["qaoa.approximation_ratio"]) >= 0.99

    def test_qrisp_hamiltonian_feeds_cirq_qaoa(self):
        from QrispHamiltonian import QrispHamiltonian
        from CirqQAOA import CirqQAOA

        ham = QrispHamiltonian().transform(
            MockContext(**{"Hamiltonian": TRIANGLE, "Num Qubits": "0"}), MockFlowFile()
        )
        ham_ff = result_to_flowfile(ham)
        res = CirqQAOA().transform(
            MockContext(**{"Layers": "1", "Max Iterations": "80", "Random Seed": "11"}),
            ham_ff,
        )
        assert res.relationship == "success", res.attributes
        assert res.attributes["qaoa.framework"] == "cirq"
        merged = result_to_flowfile_merged(res, ham_ff)
        _engine_res, _merged2, ev = qref.evaluate(
            merged, "QiskitAerSimulator", {"Shots": "1024", "Random Seed": "11"}
        )
        assert ev.relationship == "success", ev.attributes
        assert float(ev.attributes["qaoa.approximation_ratio"]) >= 0.99
