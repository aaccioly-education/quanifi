"""
Unit and integration tests for QrispQFTCircuit and QrispPhaseEstimation.

QrispQFTCircuit tests do NOT call the Qrisp simulator and run in the fast suite.
QrispPhaseEstimation tests call QPE + get_measurement() and are marked slow.
"""

import json

import pytest

from QrispQFTCircuit import QrispQFTCircuit
from QrispPhaseEstimation import QrispPhaseEstimation

from conftest import MockContext, MockFlowFile


# ---------------------------------------------------------------------------
# QrispQFTCircuit  (no simulator — fast suite)
# ---------------------------------------------------------------------------

class TestQrispQFTCircuit:

    def _run(self, n=3, inverse="false", do_swaps="true"):
        ctx = MockContext(**{
            "Qubit Count": str(n),
            "Inverse": inverse,
            "Do Swaps": do_swaps,
        })
        return QrispQFTCircuit().transform(ctx, MockFlowFile())

    # --- Structural ---

    def test_standalone_qasm2_format(self):
        r = self._run(3)
        assert r.relationship == "success"
        assert r.attributes["circuit.format"] == "qasm2"
        assert r.attributes["circuit.num_qubits"] == "3"
        assert r.attributes["circuit.framework"] == "qrisp"
        assert "OPENQASM 2" in r.contents.decode()

    def test_gate_definition_in_qasm2(self):
        """Full gate_QFT definition must appear in the output (not just H gates)."""
        r = self._run(3)
        body = r.contents.decode()
        assert "gate gate_QFT" in body

    def test_inverse_gate_definition(self):
        """Inverse QFT outputs gate_QFT_dg definition."""
        r = self._run(3, inverse="true")
        body = r.contents.decode()
        assert "gate gate_QFT_dg" in body

    def test_inverse_flag_attribute(self):
        r = self._run(3, inverse="true")
        assert r.attributes["circuit.qft_inverse"] == "true"

    def test_forward_flag_attribute(self):
        r = self._run(3, inverse="false")
        assert r.attributes["circuit.qft_inverse"] == "false"

    def test_no_swaps_attribute(self):
        r = self._run(3, do_swaps="false")
        assert r.attributes["circuit.qft_do_swaps"] == "false"

    def test_two_qubit(self):
        r = self._run(2)
        assert r.attributes["circuit.num_qubits"] == "2"
        assert "gate gate_QFT" in r.contents.decode()

    def test_metrics_emitted(self):
        r = self._run(3)
        for key in ("circuit.depth", "circuit.gate_count", "circuit.nonlocal_gates", "circuit.t_count"):
            assert key in r.attributes
        assert int(r.attributes["circuit.gate_count"]) > 1

    def test_x_gates_not_in_output(self):
        """The seed X gate used to trigger full gate generation must be stripped."""
        r = self._run(3)
        lines = r.contents.decode().split("\n")
        circuit_lines = [l for l in lines if l.startswith("x ")]
        assert len(circuit_lines) == 0

    def test_property_descriptors(self):
        names = {d.name for d in QrispQFTCircuit().getPropertyDescriptors()}
        assert names == {"Qubit Count", "Inverse", "Do Swaps"}

    # --- Pipeline: QrispQFTCircuit → QiskitAerSimulator ---

    def test_qft_pipeline_uniform_output(self):
        """QFT on |0>^n fed to QiskitAerSimulator gives 2^n equal-probability states."""
        from QiskitAerSimulator import QiskitAerSimulator
        from conftest import result_to_flowfile
        qft_r = self._run(3)
        sim_r = QiskitAerSimulator().transform(
            MockContext(**{"Shots": "1024"}),
            result_to_flowfile(qft_r),
        )
        assert sim_r.relationship == "success"
        counts = json.loads(sim_r.contents)
        assert len(counts) == 2 ** 3


# ---------------------------------------------------------------------------
# QrispPhaseEstimation  (uses QPE + get_measurement — marked slow)
# ---------------------------------------------------------------------------

@pytest.mark.slow
class TestQrispPhaseEstimation:

    def _run(self, m=3, unitary="T", shots=1024):
        ctx = MockContext(**{
            "Phase Register Size": str(m),
            "Builtin Unitary": unitary,
            "Shots": str(shots),
        })
        return QrispPhaseEstimation().transform(ctx, MockFlowFile())

    # --- Structural ---

    def test_t_gate_structure(self):
        r = self._run(3, "T")
        assert r.relationship == "success"
        assert r.attributes["qpe.builtin"] == "T"
        assert r.attributes["qpe.precision"] == "3"
        assert r.attributes["qpe.framework"] == "qrisp"

    def test_s_gate_structure(self):
        r = self._run(3, "S")
        assert r.attributes["qpe.builtin"] == "S"

    def test_z_gate_structure(self):
        r = self._run(3, "Z")
        assert r.attributes["qpe.builtin"] == "Z"

    def test_shots_attribute(self):
        r = self._run(3, "T", shots=512)
        assert r.attributes["qpe.shots"] == "512"

    def test_circuit_export_in_attributes(self):
        r = self._run(3, "T")
        assert "circuit.qasm2" in r.attributes
        assert "OPENQASM 2" in r.attributes["circuit.qasm2"]

    def test_property_descriptors(self):
        names = {d.name for d in QrispPhaseEstimation().getPropertyDescriptors()}
        assert names == {"Phase Register Size", "Builtin Unitary", "Shots", "Output Mode"}

    # --- Eigenphase correctness ---

    def test_t_gate_eigenphase(self):
        """T gate: eigenphase = 1/8 → top phase = 0.125."""
        r = self._run(3, "T")
        assert float(r.attributes["qpe.top_phase"]) == pytest.approx(0.125)

    def test_s_gate_eigenphase(self):
        """S gate: eigenphase = 1/4 → top phase = 0.25."""
        r = self._run(3, "S")
        assert float(r.attributes["qpe.top_phase"]) == pytest.approx(0.25)

    def test_z_gate_eigenphase(self):
        """Z gate: eigenphase = 1/2 → top phase = 0.5."""
        r = self._run(3, "Z")
        assert float(r.attributes["qpe.top_phase"]) == pytest.approx(0.5)

    def test_top_phase_dominates(self):
        """Top result probability must be high (Qrisp QPE is exact for these gates)."""
        r = self._run(3, "T")
        assert float(r.attributes["qpe.top_probability"]) > 0.95

    def test_output_is_probability_distribution(self):
        """JSON output values are probabilities (sum ≈ 1.0)."""
        r = self._run(3, "T")
        dist = json.loads(r.contents)
        assert abs(sum(dist.values()) - 1.0) < 0.01

    def test_larger_precision(self):
        """4-qubit precision still recovers T gate phase = 0.125."""
        r = self._run(4, "T")
        assert float(r.attributes["qpe.top_phase"]) == pytest.approx(0.125)
        assert r.attributes["qpe.precision"] == "4"
