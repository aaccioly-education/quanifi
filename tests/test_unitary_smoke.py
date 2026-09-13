"""Quick smoke test of QuanifiUnitary + QuantumUnitaryComparison.

Verifies:
  1. Cirq Grover circuit -> QuanifiUnitary produces a 2^n x 2^n unitary
  2. Qiskit Grover circuit -> QuanifiUnitary produces a 2^n x 2^n unitary
  3. Both unitaries paired via QuantumUnitaryComparison report equivalence
     (process fidelity ≈ 1)
  4. Non-circuit input is routed to failure with a clear error
"""
import json
import os
import shutil

import pytest

from CirqGroverCircuit import CirqGroverCircuit
from QiskitGroverCircuit import QiskitGroverCircuit
from QuanifiUnitary import QuanifiUnitary
from QuantumUnitaryComparison import QuantumUnitaryComparison

from conftest import MockContext, MockFlowFile, result_to_flowfile


def _grover_circuit_cirq(target="101", iters=2):
    ctx = MockContext(**{
        "Marked State": target,
        "Num Iterations": str(iters),
        "Insert Barriers": "false",
        "Output Format": "cirq_json",
    })
    return CirqGroverCircuit().transform(ctx, MockFlowFile())


def _grover_circuit_qiskit(target="101", iters=2):
    ctx = MockContext(**{
        "Marked State": target,
        "Num Iterations": str(iters),
        "Insert Barriers": "false",
        "Output Format": "qasm3",
    })
    return QiskitGroverCircuit().transform(ctx, MockFlowFile())


def test_unitary_cirq_grover_3q():
    """Cirq Grover on 3 qubits -> 8x8 unitary."""
    circ = _grover_circuit_cirq("101", 2)
    r = QuanifiUnitary().transform(
        MockContext(**{"Max Qubits": "12"}),
        result_to_flowfile(circ),
    )
    assert r.relationship == "success"
    assert r.attributes["unitary.num_qubits"] == "3"
    assert r.attributes["unitary.dimension"] == "8"
    assert r.attributes["unitary.format"] == "json_real_imag"
    payload = json.loads(r.contents)
    assert len(payload["real"]) == 8
    assert len(payload["real"][0]) == 8


def test_unitary_qiskit_grover_3q():
    """Qiskit Grover on 3 qubits -> 8x8 unitary."""
    circ = _grover_circuit_qiskit("101", 2)
    r = QuanifiUnitary().transform(
        MockContext(**{"Max Qubits": "12"}),
        result_to_flowfile(circ),
    )
    assert r.relationship == "success"
    assert r.attributes["unitary.num_qubits"] == "3"
    assert r.attributes["unitary.dimension"] == "8"


def test_unitary_error_on_non_circuit():
    """Feeding shot counts (no circuit.format) -> failure with clear error."""
    ff = MockFlowFile(
        content=b'{"00": 512, "11": 512}',
        attributes={"sim.framework": "qiskit"},
    )
    r = QuanifiUnitary().transform(MockContext(**{"Max Qubits": "12"}), ff)
    assert r.relationship == "failure"
    assert r.attributes["unitary.error_type"] == "missing_circuit_format_attribute"
    assert "circuit.format" in r.attributes["unitary.error"]
    assert "circuit-producing processor" in r.attributes["unitary.error"]


def test_unitary_error_on_oversized_circuit():
    """A circuit bigger than Max Qubits -> failure with memory estimate."""
    circ = _grover_circuit_cirq("101", 2)
    r = QuanifiUnitary().transform(
        MockContext(**{"Max Qubits": "2"}),
        result_to_flowfile(circ),
    )
    assert r.relationship == "failure"
    assert r.attributes["unitary.error_type"] == "circuit_too_large"


def test_equivalence_cirq_vs_qiskit_grover(tmp_path):
    """End-to-end: Cirq and Qiskit Grover circuits for the same (target, iters)
    must produce equivalent unitaries (process fidelity ≈ 1)."""
    state_dir = str(tmp_path / "unitary_state")

    # Build the two circuits
    cirq_circ   = _grover_circuit_cirq("101", 2)
    qiskit_circ = _grover_circuit_qiskit("101", 2)

    # Compute their unitaries
    cirq_u   = QuanifiUnitary().transform(MockContext(**{"Max Qubits": "12"}),
                                          result_to_flowfile(cirq_circ))
    qiskit_u = QuanifiUnitary().transform(MockContext(**{"Max Qubits": "12"}),
                                          result_to_flowfile(qiskit_circ))

    # Compare
    cctx = MockContext(**{
        "State Directory":   state_dir,
        "Comparison Label":  "grover-3q",
        "Framework Label":   "${unitary.source_format}",
        "Tolerance":         "1e-6",
    })
    comp = QuantumUnitaryComparison()

    r1 = comp.transform(cctx, result_to_flowfile(cirq_u))
    assert r1.relationship == "failure"           # "waiting" goes to failure
    assert r1.attributes["equiv.status"] == "waiting"

    r2 = comp.transform(cctx, result_to_flowfile(qiskit_u))
    assert r2.relationship == "success"
    assert r2.attributes["equiv.status"] == "complete"
    assert r2.attributes["equiv.equivalent"] == "true"

    fidelity = float(r2.attributes["equiv.process_fidelity"])
    assert fidelity > 1.0 - 1e-6, f"fidelity should be ~1, got {fidelity}"
    assert r2.attributes["equiv.num_qubits"] == "3"
    assert r2.attributes["equiv.dimension"] == "8"


def test_equivalence_not_equivalent(tmp_path):
    """Same Grover target with different iteration counts should NOT be equivalent."""
    state_dir = str(tmp_path / "unitary_state")

    # Same target, different iteration counts -> different unitaries
    circ_a = _grover_circuit_cirq("101", 1)   # 1 iteration
    circ_b = _grover_circuit_cirq("101", 2)   # 2 iterations

    ua = QuanifiUnitary().transform(MockContext(**{"Max Qubits": "12"}),
                                    result_to_flowfile(circ_a))
    ub = QuanifiUnitary().transform(MockContext(**{"Max Qubits": "12"}),
                                    result_to_flowfile(circ_b))

    cctx = MockContext(**{
        "State Directory":   state_dir,
        "Comparison Label":  "different-iters",
        "Framework Label":   "a-vs-b",
        "Tolerance":         "1e-9",
    })
    comp = QuantumUnitaryComparison()
    comp.transform(cctx, result_to_flowfile(ua))
    r = comp.transform(cctx, result_to_flowfile(ub))

    assert r.relationship == "success"
    assert r.attributes["equiv.equivalent"] == "false"
    fid = float(r.attributes["equiv.process_fidelity"])
    assert fid < 0.99
