"""
Tests for PennylanePhaseEstimation.

The builder must be interchangeable with QiskitPhaseEstimation /
CirqPhaseEstimation / QrispPhaseEstimation: same ideal phase readout, same
OpenQASM 2.0 interchange format, same circuit.* / result.* attribute contract.
"""

import math
from fractions import Fraction

import numpy as np
import pytest

pytest.importorskip("pennylane")
pytest.importorskip("qiskit")

from PennylanePhaseEstimation import PennylanePhaseEstimation, _analytic_distribution
from QiskitPhaseEstimation import QiskitPhaseEstimation

from conftest import MockContext, MockFlowFile, result_to_flowfile


def _run(m=3, builtin="T", phase=None):
    props = {"Phase Register Size": str(m), "Builtin Unitary": builtin}
    if phase is not None:
        props["Phase"] = phase
    return PennylanePhaseEstimation().transform(MockContext(**props), MockFlowFile())


def _phase_dist(qc, m):
    """Ideal distribution over the phase register; qubit 0 = MSB (q0-left keys)."""
    from qiskit.quantum_info import Statevector
    p = np.zeros(2 ** m)
    for key, value in Statevector(qc).probabilities_dict().items():
        p[int(key[::-1][:m], 2)] += float(value)
    return p


def _qiskit_reference(m, phi):
    from qiskit import QuantumCircuit
    from qiskit.circuit.library import PhaseEstimation, PhaseGate
    qpe = PhaseEstimation(m, PhaseGate(2 * math.pi * float(phi)))
    qc = QuantumCircuit(m + 1)
    qc.x(m)
    qc.compose(qpe, inplace=True)
    return _phase_dist(qc, m)


CASES = [
    (3, "1/4"),     # exact
    (3, "3/8"),     # exact
    (4, "5/16"),    # exact
    (3, "0.3"),     # inexact
    (4, "1/3"),     # inexact
    (5, "0.71"),    # inexact
]


class TestPennylanePhaseEstimationDistribution:

    @pytest.mark.parametrize("m,phase", CASES)
    def test_matches_qiskit_and_analytic(self, m, phase):
        from qiskit import qasm2
        r = _run(m, phase=phase)
        assert r.relationship == "success", r.attributes
        qc = qasm2.loads(r.contents.decode())
        got = _phase_dist(qc, m)
        phi = Fraction(phase)
        assert np.allclose(got, _analytic_distribution(m, phi), atol=1e-9, rtol=0)
        assert np.allclose(got, _qiskit_reference(m, phi), atol=1e-9, rtol=0)

    @pytest.mark.parametrize("gate", ["T", "S", "Z"])
    def test_matches_qiskit_sibling_builtin(self, gate):
        from qiskit import qasm2
        m = 3
        r = _run(m, gate)
        sib = QiskitPhaseEstimation().transform(
            MockContext(**{"Phase Register Size": str(m), "Builtin Unitary": gate,
                           "Insert Barriers": "false", "Output Format": "qasm2"}),
            MockFlowFile())
        a = _phase_dist(qasm2.loads(r.contents.decode()), m)
        b = _phase_dist(qasm2.loads(sib.contents.decode()), m)
        # Both standalone builders prepare |1> on the target, so readouts agree.
        assert np.allclose(a, b, atol=1e-9, rtol=0)

    def test_exact_phase_is_deterministic_and_msb_first(self):
        from qiskit import qasm2
        m = 3
        got = _phase_dist(qasm2.loads(_run(m, phase="3/8").contents.decode()), m)
        # 3/8 -> readout y = 3 = '011' with qubit 0 as MSB
        assert got[3] == pytest.approx(1.0, abs=1e-9)

    def test_end_to_end_decode_with_simulator_convention(self):
        """builder -> exact simulation -> result.bit_positions decode gives the phase."""
        from qiskit import qasm2
        from qiskit.quantum_info import Statevector
        for gate, expected in [("T", Fraction(1, 8)), ("S", Fraction(1, 4)), ("Z", Fraction(1, 2))]:
            r = _run(3, gate)
            qc = qasm2.loads(r.contents.decode())
            top = max(Statevector(qc).probabilities_dict().items(), key=lambda kv: kv[1])[0]
            top = top[::-1]  # canonical q0-left key, as the simulators emit
            positions = [int(p) for p in r.attributes["result.bit_positions"].split(",")]
            bits = "".join(top[p] for p in positions)
            assert Fraction(int(bits, 2), 2 ** len(positions)) == expected, gate

    def test_phase_wraps_modulo_one(self):
        from qiskit import qasm2
        got = _phase_dist(qasm2.loads(_run(3, phase="9/8").contents.decode()), 3)
        assert got[1] == pytest.approx(1.0, abs=1e-9)


class TestPennylanePhaseEstimationQasm:

    @pytest.mark.parametrize("m,phase", CASES)
    def test_valid_openqasm2_loads_in_qiskit(self, m, phase):
        from qiskit import qasm2
        r = _run(m, phase=phase)
        text = r.contents.decode()
        assert text.startswith("OPENQASM 2.0;")
        qc = qasm2.loads(text)
        assert qc.num_qubits == m + 1
        assert qc.num_clbits == 0

    def test_no_measurement_or_classical_register(self):
        text = _run(4, phase="1/3").contents.decode()
        assert "measure" not in text
        assert "creg" not in text
        assert "gphase" not in text

    def test_content_matches_attribute(self):
        r = _run(3, "S")
        assert r.attributes["circuit.qasm2"] == r.contents.decode()


class TestPennylanePhaseEstimationContract:

    def test_attribute_contract_matches_siblings(self):
        r = _run(3, "T")
        sib = QiskitPhaseEstimation().transform(
            MockContext(**{"Phase Register Size": "3", "Builtin Unitary": "T",
                           "Insert Barriers": "false", "Output Format": "qasm2"}),
            MockFlowFile())
        a = r.attributes
        for key in ("circuit.format", "circuit.num_qubits", "circuit.phase_register_size",
                    "circuit.unitary_num_qubits", "circuit.qpe_builtin", "circuit.diagram",
                    "circuit.depth", "circuit.gate_count", "circuit.nonlocal_gates",
                    "circuit.t_count", "result.decode", "result.bit_positions",
                    "result.label", "circuit.qasm2"):
            assert key in a, key
        assert a["circuit.format"] == "qasm2"
        assert a["circuit.num_qubits"] == sib.attributes["circuit.num_qubits"] == "4"
        assert a["circuit.phase_register_size"] == "3"
        assert a["circuit.qpe_phase_register_size"] == "3"
        assert a["circuit.qpe_builtin"] == "T"
        assert a["circuit.qpe_phase"] == "1/8"
        assert a["result.decode"] == "phase"
        assert a["result.bit_positions"] == sib.attributes["result.bit_positions"] == "0,1,2"
        assert a["builder.framework"] == "pennylane"
        assert int(a["circuit.depth"]) > 0
        assert int(a["circuit.gate_count"]) > 0

    def test_custom_phase_overrides_builtin(self):
        r = _run(3, "T", phase="3/8")
        assert r.attributes["circuit.qpe_builtin"] == "phase(3/8)"
        assert r.attributes["circuit.qpe_phase"] == "3/8"

    def test_runs_on_downstream_simulator(self):
        from QiskitAerSimulator import QiskitAerSimulator
        cr = _run(3, "S")
        sr = QiskitAerSimulator().transform(
            MockContext(**{"Shots": "512"}), result_to_flowfile(cr))
        assert sr.relationship == "success"
        top = sr.attributes["sim.top_result"]
        positions = [int(p) for p in cr.attributes["result.bit_positions"].split(",")]
        bits = "".join(top[p] for p in positions)
        assert Fraction(int(bits, 2), 8) == Fraction(1, 4)

    @pytest.mark.parametrize("props", [
        {"Phase Register Size": "abc"},
        {"Phase Register Size": "0"},
        {"Phase Register Size": "3", "Phase": "not-a-number"},
    ])
    def test_bad_input_routes_to_failure(self, props):
        r = PennylanePhaseEstimation().transform(MockContext(**props), MockFlowFile())
        assert r.relationship == "failure"
        assert r.attributes["qpe.error"]
