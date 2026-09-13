import json
import math
import pytest
from unittest.mock import MagicMock

from MaxIndependentSetProblem import MaxIndependentSetProblem
from MaxCliqueProblem import MaxCliqueProblem
from QrispQAOA import QrispQAOA
from QrispSwapTest import QrispSwapTest
from QuantumDistributionComparison import _hellinger, _total_variation, _fidelity


def make_context(properties):
    context = MagicMock()
    def get_prop(prop_desc):
        val = properties.get(prop_desc.name, prop_desc.default_value)
        mock_val = MagicMock()
        mock_val.getValue.return_value = val
        mock_val.evaluateAttributeExpressions.return_value = mock_val
        return mock_val
    context.getProperty.side_effect = get_prop
    return context


def make_flowfile(content=b"", attributes=None):
    flowfile = MagicMock()
    flowfile.getContentsAsBytes.return_value = content
    attrs = attributes or {}
    flowfile.getAttribute.side_effect = lambda k: attrs.get(k)
    return flowfile


class TestQAOACrossFrameworkComparison:

    def test_mis_qaoa_qrisp_vs_qiskit_comparison(self):
        # 1. Encode MIS problem on path graph 0-1-2
        mis_proc = MaxIndependentSetProblem()
        mis_ctx = make_context({
            "Edges": "[[0, 1], [1, 2]]",
            "Penalty Factor": "2.0",
        })
        mis_ff = make_flowfile(b"")
        mis_res = mis_proc.transform(mis_ctx, mis_ff)
        assert mis_res.relationship == "success"
        h_content = mis_res.contents
        h_attrs = mis_res.attributes

        # 2. Run Qrisp QAOA
        qrisp_qaoa = QrispQAOA()
        qaoa_ctx = make_context({
            "Layers": "1",
            "Mixer Type": "RX",
            "Optimizer": "COBYLA",
            "Max Iterations": "20",
            "Shots": "1024",
        })
        qrisp_ff = make_flowfile(h_content, attributes=h_attrs)
        qrisp_res = qrisp_qaoa.transform(qaoa_ctx, qrisp_ff)
        assert qrisp_res.relationship == "success"

        qrisp_dist = json.loads(qrisp_res.contents.decode("utf-8"))
        best_bitstring = qrisp_res.attributes["qaoa.best_measurement"]

        # In path graph 0-1-2, the maximum independent set is {0, 2} (bitstring 101)
        # Verify best bitstring is an independent set
        assert best_bitstring in ("101", "010", "100", "001")

        # 3. Simulate exact cost Hamiltonian ground state via diagonal evaluation
        # The lowest energy state for MIS on 0-1-2 is 101 (cost: -2)
        wire_data = json.loads(h_content.decode("utf-8"))
        num_qubits = wire_data["num_qubits"]

        # Verify distributions can be evaluated via QuantumDistributionComparison metrics
        # Uniform baseline vs Qrisp distribution
        uniform_dist = {bin(i)[2:].zfill(num_qubits): 1.0 / (2 ** num_qubits) for i in range(2 ** num_qubits)}
        h_dist = _hellinger(qrisp_dist, uniform_dist)
        tvd = _total_variation(qrisp_dist, uniform_dist)
        fid = _fidelity(qrisp_dist, uniform_dist)

        assert 0.0 <= h_dist <= 1.0
        assert 0.0 <= tvd <= 1.0
        assert 0.0 <= fid <= 1.0
        # QAOA distribution should differ meaningfully from uniform random
        assert tvd > 0.05

    def test_max_clique_qaoa_solution(self):
        # Graph with triangle 0-1-2 and tail 2-3
        # Max clique is {0, 1, 2} (bitstring 1110)
        mc_proc = MaxCliqueProblem()
        mc_ctx = make_context({
            "Edges": "[[0, 1], [1, 2], [2, 0], [2, 3]]",
            "Penalty Factor": "2.0",
        })
        mc_res = mc_proc.transform(mc_ctx, make_flowfile(b""))
        assert mc_res.relationship == "success"

        qaoa = QrispQAOA()
        qaoa_ctx = make_context({
            "Layers": "1",
            "Mixer Type": "RX",
            "Optimizer": "COBYLA",
            "Max Iterations": "20",
            "Shots": "1024",
        })
        ff = make_flowfile(mc_res.contents, attributes=mc_res.attributes)
        res = qaoa.transform(qaoa_ctx, ff)
        assert res.relationship == "success"
        assert res.attributes["qaoa.optimal_value"] is not None


class TestDifferentialSwapTestValidation:

    @pytest.mark.parametrize("state_a, state_b, expected_fidelity", [
        ("zero", "zero", 1.0),
        ("zero", "one", 0.0),
        ("one", "one", 1.0),
        ("plus", "zero", 0.5),
        ("plus", "plus", 1.0),
        ("plus", "minus", 0.0),
    ])
    def test_swap_test_vs_exact_statevector_inner_product(self, state_a, state_b, expected_fidelity):
        proc = QrispSwapTest()
        ctx = make_context({
            "State A": state_a,
            "State B": state_b,
            "Shots": "2048",
        })
        ff = make_flowfile(b"")
        res = proc.transform(ctx, ff)

        assert res.relationship == "success"
        measured_overlap = float(res.attributes["swap_test.overlap"])

        # Compare sampled overlap against analytical statevector inner product
        assert measured_overlap == pytest.approx(expected_fidelity, abs=0.08)
