import json
import pytest

from MaxCliqueProblem import MaxCliqueProblem
from MaxIndependentSetProblem import MaxIndependentSetProblem
from PortfolioRebalancingProblem import PortfolioRebalancingProblem
from QrispQAOA import QrispQAOA
from conftest import MockContext, MockFlowFile, result_to_flowfile


class TestMaxCliqueProblem:
    def test_triangle_clique(self):
        # 3-node complete graph (triangle)
        proc = MaxCliqueProblem()
        ctx = MockContext(**{
            "Edges": "[[0, 1], [1, 2], [0, 2]]",
            "Num Nodes": "3",
            "Penalty Factor": "2.0",
        })
        res = proc.transform(ctx, MockFlowFile())
        assert res.relationship == "success"
        attrs = res.attributes
        assert attrs["hamiltonian.format"] == "sparse_pauli_op_json"
        assert attrs["hamiltonian.problem_type"] == "max_clique"
        assert attrs["hamiltonian.num_qubits"] == "3"
        assert attrs["hamiltonian.non_edges_count"] == "0"

        # Feed to QrispQAOA
        qaoa = QrispQAOA()
        qaoa_ctx = MockContext(**{
            "Layers": "2",
            "Optimizer": "COBYLA",
            "Max Iterations": "50",
            "Shots": "512",
            "Initial Parameters": "zeros",
            "Mixer Type": "RX",
        })
        qaoa_res = qaoa.transform(qaoa_ctx, result_to_flowfile(res))
        assert qaoa_res.relationship == "success"
        assert qaoa_res.attributes["qaoa.framework"] == "qrisp"
        assert qaoa_res.attributes["qaoa.mixer_type"] == "RX"

    def test_clique_with_non_edges(self):
        # Square graph 0-1, 1-2, 2-3, 3-0. Non-edges are (0, 2) and (1, 3).
        proc = MaxCliqueProblem()
        ctx = MockContext(**{
            "Edges": "[[0, 1], [1, 2], [2, 3], [3, 0]]",
            "Num Nodes": "4",
            "Penalty Factor": "2.0",
        })
        res = proc.transform(ctx, MockFlowFile())
        assert res.relationship == "success"
        attrs = res.attributes
        assert attrs["hamiltonian.non_edges_count"] == "2"


class TestMaxIndependentSetProblem:
    def test_path_graph_mis(self):
        # Path graph 0 - 1 - 2. MIS is {0, 2} with size 2 (bitstring 101).
        proc = MaxIndependentSetProblem()
        ctx = MockContext(**{
            "Edges": "[[0, 1], [1, 2]]",
            "Num Nodes": "3",
            "Penalty Factor": "2.0",
        })
        res = proc.transform(ctx, MockFlowFile())
        assert res.relationship == "success"
        attrs = res.attributes
        assert attrs["hamiltonian.format"] == "sparse_pauli_op_json"
        assert attrs["hamiltonian.problem_type"] == "max_independent_set"
        assert attrs["hamiltonian.edges_count"] == "2"

        # Solve with QrispQAOA
        qaoa = QrispQAOA()
        qaoa_ctx = MockContext(**{
            "Layers": "2",
            "Optimizer": "COBYLA",
            "Max Iterations": "50",
            "Shots": "512",
            "Initial Parameters": "zeros",
            "Mixer Type": "RX",
        })
        qaoa_res = qaoa.transform(qaoa_ctx, result_to_flowfile(res))
        assert qaoa_res.relationship == "success"
        assert qaoa_res.attributes["qaoa.num_qubits"] == "3"


class TestPortfolioRebalancingProblem:
    def test_portfolio_formulation(self):
        proc = PortfolioRebalancingProblem()
        ctx = MockContext(**{
            "Expected Returns": "[0.10, 0.20, 0.15]",
            "Covariance Matrix": "[[0.05, 0.01, 0.02], [0.01, 0.08, 0.03], [0.02, 0.03, 0.06]]",
            "Risk Factor": "0.5",
            "Budget": "2",
            "Penalty Factor": "2.0",
        })
        res = proc.transform(ctx, MockFlowFile())
        assert res.relationship == "success"
        attrs = res.attributes
        assert attrs["hamiltonian.format"] == "sparse_pauli_op_json"
        assert attrs["hamiltonian.problem_type"] == "portfolio_rebalancing"
        assert attrs["hamiltonian.budget"] == "2"
        assert attrs["hamiltonian.num_qubits"] == "3"

        # Feed to QrispQAOA with XY mixer (Hamming weight preserving)
        qaoa = QrispQAOA()
        qaoa_ctx = MockContext(**{
            "Layers": "1",
            "Optimizer": "COBYLA",
            "Max Iterations": "30",
            "Shots": "256",
            "Initial Parameters": "zeros",
            "Mixer Type": "XY",
        })
        qaoa_res = qaoa.transform(qaoa_ctx, result_to_flowfile(res))
        assert qaoa_res.relationship == "success"
        assert qaoa_res.attributes["qaoa.mixer_type"] == "XY"
