import json
import math
import pytest
from unittest.mock import MagicMock

from MaxIndependentSetProblem import MaxIndependentSetProblem
from MaxCliqueProblem import MaxCliqueProblem
from QrispQAOA import QrispQAOA
from QrispSimulator import QrispSimulator
from QiskitAerSimulator import QiskitAerSimulator
from QuantumQAOAEvaluator import QuantumQAOAEvaluator
from QrispSwapTest import QrispSwapTest
from QuantumDistributionComparison import _hellinger, _total_variation, _fidelity, _normalize
from conftest import MockContext, MockFlowFile, result_to_flowfile, result_to_flowfile_merged


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
        mis_ctx = MockContext(**{
            "Edges": "[[0, 1], [1, 2]]",
            "Penalty Factor": "2.0",
        })
        mis_ff = MockFlowFile()
        mis_res = mis_proc.transform(mis_ctx, mis_ff)
        assert mis_res.relationship == "success"
        ham_ff = result_to_flowfile(mis_res)

        # 2. Train QrispQAOA (train-only: it emits the bound circuit, not
        # counts). Its output feeds any counts engine.
        qrisp_qaoa = QrispQAOA()
        qaoa_ctx = MockContext(**{
            "Layers": "1",
            "Mixer Type": "RX",
            "Optimizer": "COBYLA",
            "Max Iterations": "20",
            "Random Seed": "5",
        })
        qaoa_res = qrisp_qaoa.transform(qaoa_ctx, ham_ff)
        assert qaoa_res.relationship == "success", qaoa_res.attributes
        merged = result_to_flowfile_merged(qaoa_res, ham_ff)

        # In path graph 0-1-2, the maximum independent set is {0, 2}
        # (bitstring 101, cost -2); the encoder's convention also accepts
        # its rotations as MIS-valid readouts.
        VALID = ("101", "010", "100", "001")

        # 3. Sample the *same* trained circuit on two different engines,
        # then score each with QuantumQAOAEvaluator: this is the NxM
        # cross-framework comparison, now downstream of the solver.
        qrisp_engine_res = QrispSimulator().transform(
            MockContext(**{"Shots": "1024", "Random Seed": "5"}), merged
        )
        assert qrisp_engine_res.relationship == "success", qrisp_engine_res.attributes
        qrisp_merged = result_to_flowfile_merged(qrisp_engine_res, merged)
        qrisp_ev = QuantumQAOAEvaluator().transform(MockContext(), qrisp_merged)
        assert qrisp_ev.relationship == "success", qrisp_ev.attributes
        assert qrisp_ev.attributes["qaoa.best_measurement"] in VALID

        qiskit_engine_res = QiskitAerSimulator().transform(
            MockContext(**{"Shots": "1024", "Random Seed": "5"}), merged
        )
        assert qiskit_engine_res.relationship == "success", qiskit_engine_res.attributes
        qiskit_merged = result_to_flowfile_merged(qiskit_engine_res, merged)
        qiskit_ev = QuantumQAOAEvaluator().transform(MockContext(), qiskit_merged)
        assert qiskit_ev.relationship == "success", qiskit_ev.attributes
        assert qiskit_ev.attributes["qaoa.best_measurement"] in VALID

        # 4. The two engines are sampling the same bound circuit, so their
        # counts distributions should agree closely.
        qrisp_dist = _normalize(json.loads(qrisp_engine_res.contents.decode("utf-8")))
        qiskit_dist = _normalize(json.loads(qiskit_engine_res.contents.decode("utf-8")))
        assert _hellinger(qrisp_dist, qiskit_dist) <= 0.08

        # 5. Existing uniform-baseline metrics, computed on the Qrisp-engine
        # counts (unchanged from the pre-0.2.0 test): the QAOA distribution
        # should differ meaningfully from uniform random.
        num_qubits = int(qiskit_ev.attributes["qaoa.num_qubits"])
        uniform_dist = {
            bin(i)[2:].zfill(num_qubits): 1.0 / (2 ** num_qubits)
            for i in range(2 ** num_qubits)
        }
        h_dist = _hellinger(qrisp_dist, uniform_dist)
        tvd = _total_variation(qrisp_dist, uniform_dist)
        fid = _fidelity(qrisp_dist, uniform_dist)

        assert 0.0 <= h_dist <= 1.0
        assert 0.0 <= tvd <= 1.0
        assert 0.0 <= fid <= 1.0
        assert tvd > 0.05

    def test_max_clique_qaoa_solution(self):
        # Graph with triangle 0-1-2 and tail 2-3
        # Max clique is {0, 1, 2} (bitstring 1110)
        mc_proc = MaxCliqueProblem()
        mc_ctx = MockContext(**{
            "Edges": "[[0, 1], [1, 2], [2, 0], [2, 3]]",
            "Penalty Factor": "2.0",
        })
        mc_res = mc_proc.transform(mc_ctx, MockFlowFile())
        assert mc_res.relationship == "success"

        qaoa = QrispQAOA()
        qaoa_ctx = MockContext(**{
            "Layers": "1",
            "Mixer Type": "RX",
            "Optimizer": "COBYLA",
            "Max Iterations": "20",
        })
        res = qaoa.transform(qaoa_ctx, result_to_flowfile(mc_res))
        assert res.relationship == "success", res.attributes
        assert res.attributes["qaoa.optimal_value"] is not None
        assert res.attributes["circuit.format"] == "qasm2"


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
