"""Unit tests for QuantumQAOAEvaluator: framework-neutral QAOA scoring of any
counts engine's output against a cost Hamiltonian. No NiFi/JVM -- conftest.py
stubs nifiapi.*.
"""

import json

import pytest

import qaoa_contract as qc
from conftest import MockContext, MockFlowFile, result_to_flowfile_merged
from qaoa_reference import wire as ref_wire
from QuantumQAOAEvaluator import QuantumQAOAEvaluator

Z0_MINUS_Z1 = ref_wire("Z0 - Z1", 2)
TRIANGLE = "0.5 Z0 Z1 + 0.5 Z1 Z2 + 0.5 Z0 Z2"


def run(content, attrs, props=None):
    ff = MockFlowFile(content=content, attributes=attrs)
    return QuantumQAOAEvaluator().transform(MockContext(**(props or {})), ff)


def base_attrs(**extra):
    attrs = {
        "sim.bit_order": "q0_left",
        "builder.component": "X",
        "sim.component": "Y",
        "hamiltonian.json": Z0_MINUS_Z1,
    }
    attrs.update(extra)
    return attrs


def _assert_all_evaluator_keys_blank(attrs):
    for key in qc.EVALUATOR_KEYS:
        assert attrs[key] == "", "{} not blanked: {!r}".format(key, attrs[key])


class TestWorkedExample:
    def test_metrics_match_the_hand_computation(self):
        content = json.dumps({"10": 3, "01": 5, "00": 2}).encode()
        res = run(content, base_attrs())
        assert res.relationship == "success", res.attributes
        a = res.attributes
        assert a["qaoa.best_measurement"] == "10"
        assert float(a["qaoa.best_value"]) == -2.0
        assert float(a["qaoa.sampled_expectation"]) == pytest.approx(0.4)
        assert float(a["qaoa.exact_minimum"]) == -2.0
        assert float(a["qaoa.exact_maximum"]) == 2.0
        assert float(a["qaoa.approximation_ratio"]) == pytest.approx(1.0)
        assert float(a["qaoa.expectation_ratio"]) == pytest.approx(0.4)
        assert float(a["qaoa.optimal_probability"]) == pytest.approx(0.3)
        assert json.loads(a["qaoa.optimal_states"]) == ["10"]
        assert a["qaoa.num_optimal_states"] == "1"
        assert a["qaoa.shots"] == "10"
        assert a["qaoa.builder"] == "X"
        assert a["qaoa.engine"] == "Y"
        assert a["qaoa.evaluator"] == "QuantumQAOAEvaluator"
        assert res.contents == content
        assert a["report.type"] == "simulation"


class TestTieBreak:
    def test_lexicographic_tiebreak_and_optimal_state_count(self):
        content = json.dumps({"110": 9, "011": 1, "000": 5}).encode()
        res = run(
            content, base_attrs(**{"hamiltonian.json": ""}), {"Hamiltonian": TRIANGLE}
        )
        assert res.relationship == "success", res.attributes
        assert res.attributes["qaoa.best_measurement"] == "011"
        assert len(json.loads(res.attributes["qaoa.optimal_states"])) == 6
        assert res.attributes["qaoa.num_optimal_states"] == "6"


class TestNearTies:
    def test_ties_within_1e9_count_as_equal(self):
        wire = json.dumps({"num_qubits": 1, "terms": [["Z", 4e-10, 0.0]]})
        content = json.dumps({"0": 1, "1": 1}).encode()
        res = run(content, base_attrs(**{"hamiltonian.json": wire}))
        assert res.relationship == "success", res.attributes
        assert json.loads(res.attributes["qaoa.optimal_states"]) == ["0", "1"]
        assert res.attributes["qaoa.num_optimal_states"] == "2"
        assert float(res.attributes["qaoa.optimal_probability"]) == pytest.approx(1.0)


class TestProbabilityInput:
    def test_noninteger_values_pull_shots_from_sim_shots(self):
        content = json.dumps({"10": 0.25, "01": 0.75}).encode()
        res = run(content, base_attrs(**{"sim.shots": "400"}))
        assert res.relationship == "success", res.attributes
        assert res.attributes["qaoa.shots"] == "400"


class TestDslProperty:
    def test_dsl_hamiltonian_matches_wire_json_result(self):
        content = json.dumps({"10": 3, "01": 5, "00": 2}).encode()
        res = run(
            content, base_attrs(**{"hamiltonian.json": ""}), {"Hamiltonian": "Z0 - Z1"}
        )
        assert res.relationship == "success", res.attributes
        assert res.attributes["qaoa.best_measurement"] == "10"
        assert float(res.attributes["qaoa.best_value"]) == -2.0


class TestIdentityOnly:
    def test_identity_only_hamiltonian_gives_ratio_one(self):
        content = json.dumps({"10": 3, "01": 5, "00": 2}).encode()
        res = run(
            content, base_attrs(**{"hamiltonian.json": ""}), {"Hamiltonian": "0.5"}
        )
        assert res.relationship == "success", res.attributes
        assert float(res.attributes["qaoa.approximation_ratio"]) == 1.0
        assert float(res.attributes["qaoa.expectation_ratio"]) == 1.0


class TestFailures:
    def _assert_failure(self, res):
        assert res.relationship == "failure"
        assert res.attributes["qaoa.error"]
        _assert_all_evaluator_keys_blank(res.attributes)
        return res

    def test_missing_bit_order(self):
        content = b'{"0": 1}'
        res = run(
            content, {k: v for k, v in base_attrs().items() if k != "sim.bit_order"}
        )
        self._assert_failure(res)
        assert res.contents == content

    def test_wrong_bit_order(self):
        res = self._assert_failure(
            run(b'{"0": 1}', base_attrs(**{"sim.bit_order": "q0_right"}))
        )
        assert "q0_left" in res.attributes["qaoa.error"]

    def test_non_json_content(self):
        self._assert_failure(run(b"not json", base_attrs()))

    def test_empty_object_content(self):
        self._assert_failure(run(b"{}", base_attrs()))

    def test_negative_value(self):
        self._assert_failure(run(json.dumps({"0": -1, "1": 1}).encode(), base_attrs()))

    def test_nan_value(self):
        self._assert_failure(run(b'{"0": NaN, "1": 1}', base_attrs()))

    def test_bool_value(self):
        self._assert_failure(
            run(json.dumps({"0": True, "1": 1}).encode(), base_attrs())
        )

    def test_non_binary_key(self):
        self._assert_failure(run(json.dumps({"1a": 1, "01": 2}).encode(), base_attrs()))

    def test_mixed_widths(self):
        self._assert_failure(run(json.dumps({"0": 1, "00": 1}).encode(), base_attrs()))

    def test_width_mismatch(self):
        wire3 = json.dumps({"num_qubits": 3, "terms": [["ZII", 1.0, 0.0]]})
        res = self._assert_failure(
            run(
                json.dumps({"00": 1, "01": 1}).encode(),
                base_attrs(**{"hamiltonian.json": wire3}),
            )
        )
        assert "width" in res.attributes["qaoa.error"]

    def test_circuit_num_qubits_mismatch(self):
        content = json.dumps({"10": 1, "01": 1}).encode()
        res = self._assert_failure(
            run(content, base_attrs(**{"circuit.num_qubits": "5"}))
        )
        assert "circuit.num_qubits" in res.attributes["qaoa.error"]

    def test_non_diagonal_hamiltonian(self):
        res = self._assert_failure(
            run(
                b'{"0": 1, "1": 1}',
                base_attrs(**{"hamiltonian.json": ""}),
                {"Hamiltonian": "X0"},
            )
        )
        assert "diagonal" in res.attributes["qaoa.error"]

    def test_blank_hamiltonian(self):
        res = self._assert_failure(
            run(b'{"0": 1}', base_attrs(**{"hamiltonian.json": ""}))
        )
        assert res.attributes["qaoa.error"]


class TestMergedChain:
    def test_stale_best_measurement_is_overwritten(self):
        upstream = MockFlowFile(
            content=b"placeholder",
            attributes=base_attrs(**{"qaoa.best_measurement": "111"}),
        )
        content = json.dumps({"10": 3, "01": 5, "00": 2}).encode()
        result = QuantumQAOAEvaluator().transform(
            MockContext(),
            MockFlowFile(content=content, attributes=upstream.getAttributes()),
        )
        merged = result_to_flowfile_merged(result, upstream)
        assert merged.getAttribute("qaoa.best_measurement") == "10"
