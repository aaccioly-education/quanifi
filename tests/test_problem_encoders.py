"""Tests for the framework-agnostic problem encoders (MaxCutProblem,
QuboToHamiltonian): wire-format correctness, and end-to-end chains into
QiskitQAOA proving a user can go from raw data to a solved problem with no
Pauli strings involved."""
import json

from conftest import MockContext, MockFlowFile, result_to_flowfile

from MaxCutProblem import MaxCutProblem
from QuboToHamiltonian import QuboToHamiltonian
from QiskitQAOA import QiskitQAOA


def wire_of(res):
    assert res.relationship == "success"
    assert res.attributes["hamiltonian.format"] == "sparse_pauli_op_json"
    return json.loads(res.contents.decode("utf-8"))


class TestMaxCutProblem:

    def test_triangle_wire_terms(self):
        res = MaxCutProblem().transform(MockContext(), MockFlowFile())
        wire = wire_of(res)
        assert wire["num_qubits"] == 3
        terms = {label: re for label, re, _im in wire["terms"]}
        # H = 0.5*(Z0Z1 + Z1Z2 + Z0Z2) - 1.5*I  (dense labels: index 0 rightmost)
        assert terms == {"III": -1.5, "IZZ": 0.5, "ZZI": 0.5, "ZIZ": 0.5}
        a = res.attributes
        assert a["problem.type"] == "maxcut"
        assert a["problem.num_nodes"] == "3"
        assert a["hamiltonian.framework"] == "agnostic"

    def test_weighted_edges_and_content_override(self):
        ff = MockFlowFile(content=b'[[0, 1, 2.0], [1, 2]]')
        res = MaxCutProblem().transform(MockContext(**{"Edges": "[[9,9]]"}), ff)
        wire = wire_of(res)
        terms = {label: re for label, re, _im in wire["terms"]}
        assert terms == {"III": -1.5, "IZZ": 1.0, "ZZI": 0.5}

    def test_maxcut_feeds_qiskit_qaoa(self):
        ham = MaxCutProblem().transform(MockContext(), MockFlowFile())
        res = QiskitQAOA().transform(MockContext(), result_to_flowfile(ham))
        assert res.relationship == "success"
        a = res.attributes
        # triangle max cut = 2, so the exact minimum of H is -2
        assert abs(float(a["qaoa.exact_minimum"]) - (-2.0)) < 1e-9
        assert float(a["qaoa.approximation_ratio"]) >= 0.99
        assert a["qaoa.best_measurement"] not in ("000", "111")

    def test_self_loop_routes_to_failure(self):
        res = MaxCutProblem().transform(
            MockContext(**{"Edges": "[[1, 1]]"}), MockFlowFile())
        assert res.relationship == "failure"
        assert "hamiltonian.error" in res.attributes

    def test_bad_json_routes_to_failure(self):
        res = MaxCutProblem().transform(
            MockContext(**{"Edges": "not json"}), MockFlowFile())
        assert res.relationship == "failure"
        assert "hamiltonian.error" in res.attributes


class TestQuboToHamiltonian:

    def test_default_qubo_wire_terms(self):
        # Q = [[-1, 2], [0, -1]]: f(00)=0, f(01)=f(10)=-1, f(11)=0
        res = QuboToHamiltonian().transform(MockContext(), MockFlowFile())
        wire = wire_of(res)
        assert wire["num_qubits"] == 2
        terms = {label: re for label, re, _im in wire["terms"]}
        assert terms == {"II": -0.5, "ZZ": 0.5}
        assert res.attributes["problem.type"] == "qubo"

    def test_qubo_feeds_qiskit_qaoa(self):
        ham = QuboToHamiltonian().transform(MockContext(), MockFlowFile())
        res = QiskitQAOA().transform(MockContext(), result_to_flowfile(ham))
        assert res.relationship == "success"
        a = res.attributes
        assert abs(float(a["qaoa.exact_minimum"]) - (-1.0)) < 1e-9
        assert a["qaoa.best_measurement"] in ("01", "10")

    def test_linear_terms(self):
        # Q = [[1, 0], [0, -2]]: c=-0.5, h0=-0.5, h1=+1.0
        res = QuboToHamiltonian().transform(
            MockContext(**{"QUBO Matrix": "[[1, 0], [0, -2]]"}), MockFlowFile())
        terms = {label: re for label, re, _im in wire_of(res)["terms"]}
        assert terms == {"II": -0.5, "IZ": -0.5, "ZI": 1.0}

    def test_non_square_routes_to_failure(self):
        res = QuboToHamiltonian().transform(
            MockContext(**{"QUBO Matrix": "[[1, 2, 3], [0, 1, 2]]"}), MockFlowFile())
        assert res.relationship == "failure"
        assert "hamiltonian.error" in res.attributes
