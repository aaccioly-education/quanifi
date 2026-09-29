"""
Tests for the Layer-A gate-level circuit mutator (QuantumMutator) — the
Gate-level mutation testing. Covers the operator cores,
the pass-through gating that keeps controls and Layer-B mutants unmutated,
the mut.* / circuit.* attribute contract, seed reproducibility, and the
failure paths (missing/unsupported format, unknown operator, no applicable
operator).
"""

import random

import pytest

from QuantumMutator import (
    QuantumMutator,
    _GATE_MUTATION_OPERATORS,
    _gate_positions,
    _position_interval,
    _derive_seed,
)

from conftest import MockContext, MockFlowFile


def _grover_qasm2():
    """A small concrete circuit (2q Grover, 1 iteration) as qasm2 text."""
    from qiskit import QuantumCircuit, qasm2
    qc = QuantumCircuit(2, 2)
    qc.h([0, 1])
    qc.cz(0, 1)
    qc.h([0, 1])
    qc.x([0, 1])
    qc.cz(0, 1)
    qc.x([0, 1])
    qc.h([0, 1])
    qc.measure([0, 1], [0, 1])
    return qasm2.dumps(qc)


def _parse_qasm2(text):
    from qiskit import qasm2
    return qasm2.loads(text, custom_instructions=qasm2.LEGACY_CUSTOM_INSTRUCTIONS)


def _gate_count(circuit):
    return sum(
        v for k, v in circuit.count_ops().items()
        if k not in ("barrier", "measure")
    )


def _run(content, attributes=None, **props):
    ctx = MockContext(**props)
    if attributes is None:
        attributes = {"circuit.format": "qasm2"}
    ff = MockFlowFile(content=content, attributes=attributes)
    return QuantumMutator().transform(ctx, ff)


# ---------------------------------------------------------------------------
# Operator cores (pure functions on a parsed circuit)
# ---------------------------------------------------------------------------

class TestOperatorCores:

    def test_add_gate_grows_circuit(self):
        qc = _parse_qasm2(_grover_qasm2())
        before = _gate_count(qc)
        out = _GATE_MUTATION_OPERATORS["gate.add"](qc, random.Random(1), 0.1)
        assert out is not None
        assert _gate_count(qc) == before + 1
        assert out["gate"] in ("x", "y", "z", "h", "s", "t", "cx", "cz")
        assert 0 <= out["position"] <= before

    def test_remove_gate_shrinks_circuit_keeps_measure(self):
        qc = _parse_qasm2(_grover_qasm2())
        before = _gate_count(qc)
        measures = qc.count_ops().get("measure", 0)
        out = _GATE_MUTATION_OPERATORS["gate.remove"](qc, random.Random(2), 0.1)
        assert out is not None
        assert _gate_count(qc) == before - 1
        assert qc.count_ops().get("measure", 0) == measures
        assert out["original"] == out["gate"]

    def test_remove_gate_inapplicable_on_empty(self):
        from qiskit import QuantumCircuit
        qc = QuantumCircuit(1)
        assert _GATE_MUTATION_OPERATORS["gate.remove"](qc, random.Random(0), 0.1) is None

    def test_replace_gate_same_arity_same_count(self):
        qc = _parse_qasm2(_grover_qasm2())
        before = _gate_count(qc)
        out = _GATE_MUTATION_OPERATORS["gate.replace"](qc, random.Random(3), 0.1)
        assert out is not None
        assert _gate_count(qc) == before
        assert out["gate"] != out["original"]
        # arity preserved: a 1q original gets a 1q pool gate, 2q gets cx/cz
        one_q = ("x", "y", "z", "h", "s", "t")
        if out["original"] in one_q:
            assert out["gate"] in one_q
        else:
            assert out["gate"] in ("cx", "cz")

    def test_perturb_rotation_shifts_angle(self):
        from qiskit import QuantumCircuit
        qc = QuantumCircuit(1)
        qc.rz(0.5, 0)
        out = _GATE_MUTATION_OPERATORS["rotation.perturb"](qc, random.Random(4), 0.25)
        assert out is not None and out["gate"] == "rz"
        assert float(qc.data[0].operation.params[0]) == pytest.approx(0.75)
        assert out["original"] == repr(0.5)

    @pytest.mark.parametrize("name,angle", [
        ("t", 0.7853981633974483),
        ("tdg", -0.7853981633974483),
        ("s", 1.5707963267948966),
        ("sdg", -1.5707963267948966),
        ("z", 3.141592653589793),
    ])
    def test_perturb_fixed_phase_gate_uses_canonical_angle(self, name, angle):
        from qiskit import QuantumCircuit
        qc = QuantumCircuit(1)
        getattr(qc, name)(0)
        out = _GATE_MUTATION_OPERATORS["rotation.perturb"](
            qc, random.Random(0), -0.125)
        assert out is not None and out["gate"] == "p"
        assert qc.data[0].operation.params[0] == pytest.approx(angle - 0.125)
        assert out["original"].startswith(name + "=")

    def test_perturb_rotation_inapplicable_without_params(self):
        qc = _parse_qasm2(_grover_qasm2())
        assert _GATE_MUTATION_OPERATORS["rotation.perturb"](qc, random.Random(0), 0.1) is None

    def test_gate_positions_exclude_measure_and_barrier(self):
        from qiskit import QuantumCircuit
        qc = QuantumCircuit(2, 2)
        qc.h(0)
        qc.barrier()
        qc.cx(0, 1)
        qc.measure([0, 1], [0, 1])
        assert len(_gate_positions(qc)) == 2

    def test_position_interval_bins(self):
        assert _position_interval(0, 10) == "0-10%"
        assert _position_interval(5, 10) == "50-60%"
        assert _position_interval(9, 10) == "90-100%"
        assert _position_interval(10, 10) == "90-100%"  # clamped (gate.add end slot)
        assert _position_interval(0, 0) == "0-10%"      # empty-circuit guard


# ---------------------------------------------------------------------------
# The processor: happy path
# ---------------------------------------------------------------------------

class TestQuantumMutatorHappyPath:

    def test_mutates_and_sets_contract_attributes(self):
        res = _run(
            _grover_qasm2(),
            attributes={"circuit.format": "qasm2", "test.case_id": "case-001"},
            **{"Mutation Operators": "gate.remove", "Mutation Seed": "777"},
        )
        assert res.relationship == "success"
        a = res.attributes
        assert a["mut.applied"] == "true"
        assert a["mut.operator"] == "gate.remove"
        assert a["mut.gate"] and a["mut.position"].isdigit()
        assert a["mut.position_interval"].endswith("%")
        assert a["mut.target_attr"] == f"{a['mut.gate']}@{a['mut.position']}"
        assert a["mut.original_value"] == a["mut.gate"]
        assert a["mut.seed"].isdigit()
        assert a["mut.base_case_id"] == "case-001"
        assert a["mut.original_format"] == "qasm2"
        # circuit.* metrics recomputed for the mutant, format preserved
        assert a["circuit.format"] == "qasm2"
        mutant = _parse_qasm2(res.contents.decode("utf-8"))
        assert _gate_count(mutant) == int(a["circuit.gate_count"])
        assert _gate_count(mutant) == _gate_count(_parse_qasm2(_grover_qasm2())) - 1
        assert a["circuit.qasm2"] == res.contents.decode("utf-8")

    def test_qasm3_roundtrip(self):
        pytest.importorskip("qiskit_qasm3_import")
        from qiskit import qasm3
        text = qasm3.dumps(_parse_qasm2(_grover_qasm2()))
        res = _run(
            text,
            attributes={"circuit.format": "qasm3", "test.case_id": "c0"},
            **{"Mutation Operators": "gate.add", "Mutation Seed": "1"},
        )
        assert res.relationship == "success"
        assert res.attributes["circuit.format"] == "qasm3"
        assert "circuit.qasm3" in res.attributes
        # the mutant is valid qasm3
        qasm3.loads(res.contents.decode("utf-8"))

    def test_seed_reproducible_per_case(self):
        kw = {"Mutation Operators": "gate.add, gate.remove, gate.replace",
              "Mutation Seed": "42"}
        attrs = {"circuit.format": "qasm2", "test.case_id": "case-007"}
        r1 = _run(_grover_qasm2(), attributes=dict(attrs), **kw)
        r2 = _run(_grover_qasm2(), attributes=dict(attrs), **kw)
        assert r1.contents == r2.contents
        assert r1.attributes["mut.seed"] == r2.attributes["mut.seed"]
        # a different case id derives a different seed
        attrs2 = {"circuit.format": "qasm2", "test.case_id": "case-008"}
        r3 = _run(_grover_qasm2(), attributes=attrs2, **kw)
        assert r3.attributes["mut.seed"] != r1.attributes["mut.seed"]
        assert _derive_seed(42, 1) != _derive_seed(42, 2)

    def test_falls_through_inapplicable_operator(self):
        # rotation.perturb is inapplicable (no parameterised gates) -> falls
        # through to gate.remove instead of failing
        res = _run(
            _grover_qasm2(),
            attributes={"circuit.format": "qasm2"},
            **{"Mutation Operators": "rotation.perturb, gate.remove",
               "Mutation Seed": "5"},
        )
        assert res.relationship == "success"
        assert res.attributes["mut.operator"] == "gate.remove"


# ---------------------------------------------------------------------------
# Pass-through gating (controls / Layer-B mutants)
# ---------------------------------------------------------------------------

class TestPassThroughGating:

    def test_control_row_passes_through_unmutated(self):
        text = _grover_qasm2()
        res = _run(
            text,
            attributes={"circuit.format": "qasm2", "mut.applied": "false"},
        )
        assert res.relationship == "success"
        assert res.contents.decode("utf-8") == text
        assert "mut.operator" not in res.attributes

    def test_layer_b_mutant_passes_through_unmutated(self):
        text = _grover_qasm2()
        res = _run(
            text,
            attributes={"circuit.format": "qasm2", "mut.applied": "true",
                        "mut.operator": "iterations.offbyone"},
        )
        assert res.relationship == "success"
        assert res.contents.decode("utf-8") == text
        assert "mut.gate" not in res.attributes

    def test_marked_for_layer_a_is_mutated(self):
        # mut.applied=true WITHOUT a Layer-B operator marks the row for
        # Layer-A mutation (the paired-row recipe)
        res = _run(
            _grover_qasm2(),
            attributes={"circuit.format": "qasm2", "mut.applied": "true"},
            **{"Mutation Operators": "gate.remove", "Mutation Seed": "9"},
        )
        assert res.relationship == "success"
        assert res.attributes["mut.operator"] == "gate.remove"


# ---------------------------------------------------------------------------
# Failure paths
# ---------------------------------------------------------------------------

class TestQuantumMutatorFailures:

    def test_missing_circuit_format(self):
        res = _run(_grover_qasm2(), attributes={})
        assert res.relationship == "failure"
        assert res.attributes["mut.error_type"] == "missing_circuit_format_attribute"
        assert "circuit.format" in res.attributes["mut.error"]

    def test_unsupported_format(self):
        res = _run("{}", attributes={"circuit.format": "cirq_json"})
        assert res.relationship == "failure"
        assert res.attributes["mut.error_type"] == "unsupported_format"

    def test_unknown_operator(self):
        res = _run(
            _grover_qasm2(),
            attributes={"circuit.format": "qasm2"},
            **{"Mutation Operators": "gate.remove, bogus.op"},
        )
        assert res.relationship == "failure"
        assert "bogus.op" in res.attributes["mut.error"]

    def test_parse_failure(self):
        res = _run("not qasm at all", attributes={"circuit.format": "qasm2"})
        assert res.relationship == "failure"
        assert res.attributes["mut.error_type"] == "parse_failure"

    def test_no_applicable_operator(self):
        # only rotation.perturb listed, but the circuit has no rotation gates
        res = _run(
            _grover_qasm2(),
            attributes={"circuit.format": "qasm2"},
            **{"Mutation Operators": "rotation.perturb"},
        )
        assert res.relationship == "failure"
        assert res.attributes["mut.error_type"] == "no_applicable_operator"

    def test_bad_epsilon(self):
        res = _run(
            _grover_qasm2(),
            attributes={"circuit.format": "qasm2"},
            **{"Mutation Operators": "gate.remove", "Rotation Epsilon": "lots"},
        )
        assert res.relationship == "failure"
        assert "Rotation Epsilon" in res.attributes["mut.error"]
