"""
Unit tests for Qiskit-based NiFi processors.

Each test exercises one processor in isolation — no running NiFi, no JVM.
The nifiapi stubs in conftest.py replace the real NiFi Python API.
"""

import json

import pytest

from QiskitHadamardTransform import QiskitHadamardTransform
from QiskitStatePreparation import QiskitStatePreparation
from QiskitGroverCircuit import QiskitGroverCircuit
from QiskitPhaseOracle import QiskitPhaseOracle
from QiskitGroverOperator import QiskitGroverOperator
from QiskitQFTCircuit import QiskitQFTCircuit
from QiskitPhaseEstimation import QiskitPhaseEstimation
from QiskitAerSimulator import QiskitAerSimulator
from QiskitGroverSearch import QiskitGroverSearch

from conftest import MockContext, MockFlowFile, result_to_flowfile


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _qasm3_h(n=2):
    from qiskit import QuantumCircuit, qasm3, transpile
    qc = QuantumCircuit(n)
    qc.h(range(n))
    tc = transpile(qc, basis_gates=["h", "cx", "rz", "x"], optimization_level=0)
    return qasm3.dumps(tc)


def _qasm2_h(n=2):
    from qiskit import QuantumCircuit, qasm2, transpile
    qc = QuantumCircuit(n)
    qc.h(range(n))
    tc = transpile(qc, basis_gates=["h", "cx", "rz", "x"], optimization_level=0)
    return qasm2.dumps(tc)


# ---------------------------------------------------------------------------
# QiskitHadamardTransform
# ---------------------------------------------------------------------------

class TestQiskitHadamardTransform:

    def test_standalone_qasm3(self):
        proc = QiskitHadamardTransform()
        ctx  = MockContext(**{"Qubit Count": "2", "Output Format": "qasm3"})
        r = proc.transform(ctx, MockFlowFile())
        assert r.relationship == "success"
        assert r.attributes["circuit.num_qubits"] == "2"
        assert r.attributes["circuit.format"] == "qasm3"
        assert r.contents.decode().startswith("OPENQASM")
        assert "circuit.qasm3" in r.attributes

    def test_standalone_qasm2(self):
        proc = QiskitHadamardTransform()
        ctx  = MockContext(**{"Qubit Count": "3", "Output Format": "qasm2"})
        r = proc.transform(ctx, MockFlowFile())
        assert r.relationship == "success"
        assert r.attributes["circuit.num_qubits"] == "3"
        assert r.attributes["circuit.format"] == "qasm2"
        assert "OPENQASM 2" in r.contents.decode()
        assert "circuit.qasm2" in r.attributes

    def test_standalone_qpy(self):
        proc = QiskitHadamardTransform()
        ctx  = MockContext(**{"Qubit Count": "2", "Output Format": "qpy"})
        r = proc.transform(ctx, MockFlowFile())
        assert r.relationship == "success"
        assert r.attributes["circuit.format"] == "qpy"
        assert len(r.contents) > 0

    def test_metrics_emitted(self):
        proc = QiskitHadamardTransform()
        ctx  = MockContext(**{"Qubit Count": "2", "Output Format": "qasm3"})
        r = proc.transform(ctx, MockFlowFile())
        for key in ("circuit.depth", "circuit.gate_count", "circuit.nonlocal_gates", "circuit.t_count"):
            assert key in r.attributes

    def test_compose_appends_h(self):
        """H layer is appended to an existing circuit supplied via FlowFile."""
        proc = QiskitHadamardTransform()
        ctx  = MockContext(**{"Qubit Count": "2", "Output Format": "qasm3"})
        ff   = MockFlowFile(
            content=_qasm3_h(2).encode(),
            attributes={"circuit.format": "qasm3"},
        )
        r = proc.transform(ctx, ff)
        assert r.relationship == "success"
        assert r.attributes["circuit.num_qubits"] == "2"
        # Composed circuit has more gates than the original single H layer
        assert int(r.attributes["circuit.gate_count"]) >= 2

    def test_property_descriptors(self):
        names = {d.name for d in QiskitHadamardTransform().getPropertyDescriptors()}
        assert names == {"Qubit Count", "Output Format"}


# ---------------------------------------------------------------------------
# QiskitStatePreparation
# ---------------------------------------------------------------------------

class TestQiskitStatePreparation:

    def _run(self, state_type, n=2, amplitudes="", basis_state="0", fmt="qasm3"):
        ctx = MockContext(**{
            "State Type": state_type,
            "Qubit Count": str(n),
            "Amplitudes": amplitudes,
            "Target Basis State": basis_state,
            "Output Format": fmt,
        })
        return QiskitStatePreparation().transform(ctx, MockFlowFile())

    def test_uniform(self):
        r = self._run("uniform", n=2)
        assert r.relationship == "success"
        assert r.attributes["circuit.num_qubits"] == "2"
        assert r.attributes["circuit.state_type"] == "uniform"

    def test_ghz(self):
        r = self._run("ghz", n=3)
        assert r.relationship == "success"
        assert r.attributes["circuit.num_qubits"] == "3"
        assert r.attributes["circuit.state_type"] == "ghz"

    def test_basis_state(self):
        r = self._run("basis", n=2, basis_state="3")
        assert r.relationship == "success"
        assert r.attributes["state_prep.basis_state"] == "3"

    def test_custom_marks_target(self):
        r = self._run("custom", amplitudes="[0,0,0,1]", fmt="qasm3")
        assert r.relationship == "success"
        assert r.attributes["circuit.num_qubits"] == "2"

    def test_custom_unnormalised_accepted(self):
        r = self._run("custom", amplitudes="[1,1,1,1]")
        assert r.relationship == "success"

    def test_qasm2_output(self):
        r = self._run("uniform", n=2, fmt="qasm2")
        assert r.attributes["circuit.format"] == "qasm2"
        assert "OPENQASM 2" in r.contents.decode()

    # -- invalid input routes to `failure` with state_prep.error --------------

    def test_custom_missing_amplitudes_fails(self):
        r = self._run("custom", amplitudes="")
        assert r.relationship == "failure"
        assert "Amplitudes must be provided" in r.attributes["state_prep.error"]

    def test_custom_bad_json_fails(self):
        r = self._run("custom", amplitudes="not json")
        assert r.relationship == "failure"
        assert "state_prep.error" in r.attributes

    def test_custom_zero_norm_fails(self):
        r = self._run("custom", amplitudes="[0,0,0,0]")
        assert r.relationship == "failure"
        assert "zero norm" in r.attributes["state_prep.error"]

    def test_custom_non_power_of_two_fails(self):
        r = self._run("custom", amplitudes="[1,1,1]")
        assert r.relationship == "failure"
        assert "power of 2" in r.attributes["state_prep.error"]

    def test_basis_out_of_range_fails(self):
        r = self._run("basis", n=2, basis_state="5")
        assert r.relationship == "failure"
        assert "out of range" in r.attributes["state_prep.error"]


# ---------------------------------------------------------------------------
# QiskitGroverCircuit
# ---------------------------------------------------------------------------

class TestQiskitGroverCircuit:

    def _run(self, target="11", iterations=1, fmt="qasm3", barriers="false"):
        ctx = MockContext(**{
            "Marked State": target,
            "Num Iterations": str(iterations),
            "Insert Barriers": barriers,
            "Output Format": fmt,
        })
        return QiskitGroverCircuit().transform(ctx, MockFlowFile())

    def test_basic_qasm3(self):
        r = self._run("11")
        assert r.relationship == "success"
        assert r.attributes["circuit.marked_state"] == "11"
        assert r.attributes["circuit.num_qubits"] == "2"
        assert r.attributes["circuit.format"] == "qasm3"
        assert r.contents.decode().startswith("OPENQASM")

    def test_qasm2_output(self):
        r = self._run("101", fmt="qasm2")
        assert r.attributes["circuit.format"] == "qasm2"
        assert "OPENQASM 2" in r.contents.decode()
        assert r.attributes["circuit.num_qubits"] == "3"

    def test_qpy_output(self):
        r = self._run("11", fmt="qpy")
        assert r.attributes["circuit.format"] == "qpy"
        assert len(r.contents) > 0

    def test_circuit_metrics(self):
        r = self._run("11")
        for key in ("circuit.depth", "circuit.gate_count", "circuit.nonlocal_gates", "circuit.t_count"):
            assert key in r.attributes

    def test_iterations_attribute(self):
        r = self._run("11", iterations=2)
        assert r.attributes["circuit.num_iterations"] == "2"

    def test_three_qubit_target(self):
        r = self._run("101", iterations=2)
        assert r.attributes["circuit.num_qubits"] == "3"


# ---------------------------------------------------------------------------
# QiskitPhaseOracle
# ---------------------------------------------------------------------------

def _qiskit_oracle_flowfile(target="101", fmt="qasm3"):
    """Run QiskitPhaseOracle standalone to get an oracle FlowFile."""
    ctx = MockContext(**{
        "Marked State": target,
        "Insert Barriers": "false",
        "Output Format": fmt,
    })
    r = QiskitPhaseOracle().transform(ctx, MockFlowFile())
    return result_to_flowfile(r)


class TestQiskitPhaseOracle:

    def _run(self, target, fmt="qasm3", barriers="false"):
        ctx = MockContext(**{
            "Marked State": target,
            "Insert Barriers": barriers,
            "Output Format": fmt,
        })
        return QiskitPhaseOracle().transform(ctx, MockFlowFile())

    def test_standalone_2qubit(self):
        r = self._run("11")
        assert r.relationship == "success"
        assert r.attributes["circuit.num_qubits"] == "2"
        assert r.attributes["circuit.marked_state"] == "11"
        assert r.attributes["circuit.format"] == "qasm3"
        assert r.contents.decode().startswith("OPENQASM")

    def test_standalone_3qubit(self):
        r = self._run("101")
        assert r.attributes["circuit.num_qubits"] == "3"
        assert r.attributes["circuit.marked_state"] == "101"

    def test_qasm2_output(self):
        r = self._run("11", fmt="qasm2")
        assert r.attributes["circuit.format"] == "qasm2"
        assert "OPENQASM 2" in r.contents.decode()

    def test_oracle_marks_correct_state(self):
        """The oracle's unitary is diagonal with -1 only at |target⟩."""
        import io
        import numpy as np
        from qiskit import qpy
        from qiskit.quantum_info import Operator

        target = "10"  # qubit 0 = leftmost char → q0=1, q1=0 → index 1
        r = self._run(target, fmt="qpy")
        circuit = qpy.load(io.BytesIO(r.contents))[0]
        diag = np.diag(Operator(circuit).data)
        idx = sum(int(b) << i for i, b in enumerate(target))
        assert abs(diag[idx] + 1) < 1e-9
        assert all(abs(d - 1) < 1e-9 for j, d in enumerate(diag) if j != idx)

    def test_compose_onto_hadamard(self):
        """Oracle can be appended to an existing Qiskit circuit."""
        ctx = MockContext(**{
            "Marked State": "11",
            "Insert Barriers": "false",
            "Output Format": "qasm3",
        })
        ff = MockFlowFile(
            content=_qasm3_h(2).encode(),
            attributes={"circuit.format": "qasm3"},
        )
        r = QiskitPhaseOracle().transform(ctx, ff)
        assert r.relationship == "success"
        # Composed circuit has the H layer plus the oracle gates
        standalone = self._run("11")
        assert int(r.attributes["circuit.gate_count"]) > int(standalone.attributes["circuit.gate_count"])

    def test_compose_qubit_mismatch_fails(self):
        ctx = MockContext(**{
            "Marked State": "11",
            "Insert Barriers": "false",
            "Output Format": "qasm3",
        })
        ff = MockFlowFile(
            content=_qasm3_h(3).encode(),
            attributes={"circuit.format": "qasm3"},
        )
        r = QiskitPhaseOracle().transform(ctx, ff)
        assert r.relationship == "failure"
        assert "circuit.error" in r.attributes

    def test_invalid_marked_state_fails(self):
        r = self._run("1x0")
        assert r.relationship == "failure"
        assert "circuit.error" in r.attributes

    def test_metrics_emitted(self):
        r = self._run("101")
        for key in ("circuit.depth", "circuit.gate_count", "circuit.nonlocal_gates", "circuit.t_count"):
            assert key in r.attributes

    def test_property_descriptors(self):
        names = {d.name for d in QiskitPhaseOracle().getPropertyDescriptors()}
        assert names == {"Marked State", "Insert Barriers", "Output Format"}


# ---------------------------------------------------------------------------
# QiskitGroverOperator
# ---------------------------------------------------------------------------

class TestQiskitGroverOperator:

    def _run(self, oracle_ff, iterations=1, fmt="qasm3", barriers="false"):
        ctx = MockContext(**{
            "Num Iterations": str(iterations),
            "Insert Barriers": barriers,
            "Output Format": fmt,
        })
        return QiskitGroverOperator().transform(ctx, oracle_ff)

    def test_reads_oracle_and_builds_circuit(self):
        oracle_ff = _qiskit_oracle_flowfile("101")
        r = self._run(oracle_ff, iterations=2)
        assert r.relationship == "success"
        assert r.attributes["circuit.num_qubits"] == "3"
        assert r.attributes["circuit.num_iterations"] == "2"

    def test_marked_state_forwarded(self):
        oracle_ff = _qiskit_oracle_flowfile("11")
        r = self._run(oracle_ff)
        assert r.attributes.get("circuit.marked_state") == "11"

    def test_qasm2_output(self):
        oracle_ff = _qiskit_oracle_flowfile("11")
        r = self._run(oracle_ff, fmt="qasm2")
        assert r.attributes["circuit.format"] == "qasm2"
        assert "OPENQASM 2" in r.contents.decode()

    def test_qpy_oracle_input(self):
        oracle_ff = _qiskit_oracle_flowfile("11", fmt="qpy")
        r = self._run(oracle_ff)
        assert r.relationship == "success"
        assert r.attributes["circuit.num_qubits"] == "2"

    def test_circuit_is_deeper_with_more_iterations(self):
        r1 = self._run(_qiskit_oracle_flowfile("11"), iterations=1)
        r2 = self._run(_qiskit_oracle_flowfile("11"), iterations=3)
        assert int(r2.attributes["circuit.depth"]) > int(r1.attributes["circuit.depth"])

    def test_missing_oracle_fails(self):
        r = self._run(MockFlowFile())
        assert r.relationship == "failure"
        assert "circuit.error" in r.attributes

    def test_unparseable_oracle_fails(self):
        ff = MockFlowFile(content=b"not a circuit", attributes={"circuit.format": "qasm3"})
        r = self._run(ff)
        assert r.relationship == "failure"
        assert "circuit.error" in r.attributes

    def test_metrics_emitted(self):
        oracle_ff = _qiskit_oracle_flowfile("11")
        r = self._run(oracle_ff)
        for key in ("circuit.depth", "circuit.gate_count", "circuit.nonlocal_gates", "circuit.t_count"):
            assert key in r.attributes

    def test_property_descriptors(self):
        names = {d.name for d in QiskitGroverOperator().getPropertyDescriptors()}
        assert names == {"Num Iterations", "Insert Barriers", "Output Format"}


# ---------------------------------------------------------------------------
# QiskitQFTCircuit
# ---------------------------------------------------------------------------

class TestQiskitQFTCircuit:

    def _run(self, n=3, inverse="false", approx="0", swaps="true", fmt="qasm3"):
        ctx = MockContext(**{
            "Qubit Count": str(n),
            "Inverse": inverse,
            "Approximation Degree": approx,
            "Do Swaps": swaps,
            "Insert Barriers": "false",
            "Output Format": fmt,
        })
        return QiskitQFTCircuit().transform(ctx, MockFlowFile())

    def test_standalone(self):
        r = self._run(3)
        assert r.relationship == "success"
        assert r.attributes["circuit.num_qubits"] == "3"
        assert r.attributes["circuit.qft_inverse"] == "false"

    def test_inverse(self):
        r = self._run(3, inverse="true")
        assert r.attributes["circuit.qft_inverse"] == "true"

    def test_qasm2_output(self):
        r = self._run(2, fmt="qasm2")
        assert r.attributes["circuit.format"] == "qasm2"

    def test_metrics_emitted(self):
        r = self._run(3)
        for key in ("circuit.depth", "circuit.gate_count"):
            assert key in r.attributes


# ---------------------------------------------------------------------------
# QiskitPhaseEstimation
# ---------------------------------------------------------------------------

class TestQiskitPhaseEstimation:

    def _run(self, register=3, unitary="T", fmt="qasm3"):
        ctx = MockContext(**{
            "Phase Register Size": str(register),
            "Builtin Unitary": unitary,
            "Insert Barriers": "false",
            "Output Format": fmt,
        })
        return QiskitPhaseEstimation().transform(ctx, MockFlowFile())

    def test_t_gate(self):
        r = self._run(3, "T")
        assert r.relationship == "success"
        assert r.attributes["circuit.qpe_builtin"] == "T"
        assert r.attributes["circuit.phase_register_size"] == "3"

    def test_s_gate(self):
        r = self._run(3, "S")
        assert r.attributes["circuit.qpe_builtin"] == "S"

    def test_z_gate(self):
        r = self._run(3, "Z")
        assert r.attributes["circuit.qpe_builtin"] == "Z"

    def test_qasm2_output(self):
        r = self._run(3, "T", fmt="qasm2")
        assert r.attributes["circuit.format"] == "qasm2"

    def test_emits_phase_decode_hint(self):
        """Builder emits the declarative decode hint for QuanifiReport.

        Phase register is qubits 0..m-1 (q0 = MSB); the simulators emit
        canonical q0-left keys, so phase qubit j sits at string position j.
        For m=3 → positions "0,1,2" (same as CirqPhaseEstimation).
        """
        r = self._run(3, "T")
        assert r.attributes["result.decode"] == "phase"
        assert r.attributes["result.bit_positions"] == "0,1,2"
        assert "phase" in r.attributes["result.label"].lower()

    def test_no_self_measurement(self):
        """The builder must NOT measure_all() — that is the simulator's job.

        Self-measuring here creates a second classical register downstream and
        garbles the readout into doubled bitstrings like '1100 1100'.
        """
        r = self._run(3, "T", fmt="qasm3")
        assert "measure" not in r.attributes["circuit.qasm3"]

    def test_decodes_to_correct_phase_end_to_end(self):
        """builder → simulator → decoded phase matches the gate eigenphase."""
        from conftest import result_to_flowfile
        for gate, expected in [("T", "1/8"), ("S", "1/4"), ("Z", "1/2")]:
            cr = self._run(3, gate)
            sr = QiskitAerSimulator().transform(
                MockContext(**{"Shots": "2048"}), result_to_flowfile(cr),
            )
            top       = sr.attributes["sim.top_result"]
            positions = [int(p) for p in cr.attributes["result.bit_positions"].split(",")]
            from fractions import Fraction
            bits  = "".join(top[p] for p in positions)
            frac  = Fraction(int(bits, 2), 2 ** len(positions))
            assert f"{frac.numerator}/{frac.denominator}" == expected, gate


# ---------------------------------------------------------------------------
# QiskitAerSimulator
# ---------------------------------------------------------------------------

class TestQiskitAerSimulator:

    def test_qasm3_input_produces_counts(self):
        proc = QiskitAerSimulator()
        ctx  = MockContext(**{"Shots": "256"})
        ff   = MockFlowFile(
            content=_qasm3_h(2).encode(),
            attributes={"circuit.format": "qasm3"},
        )
        r = proc.transform(ctx, ff)
        assert r.relationship == "success"
        counts = json.loads(r.contents)
        assert sum(counts.values()) == 256
        assert r.attributes["sim.shots"] == "256"

    def test_qasm2_input(self):
        proc = QiskitAerSimulator()
        ctx  = MockContext(**{"Shots": "128"})
        ff   = MockFlowFile(
            content=_qasm2_h(2).encode(),
            attributes={"circuit.format": "qasm2"},
        )
        r = proc.transform(ctx, ff)
        assert r.relationship == "success"
        assert sum(json.loads(r.contents).values()) == 128

    def test_top_result_attributes_present(self):
        proc = QiskitAerSimulator()
        ctx  = MockContext(**{"Shots": "512"})
        ff   = MockFlowFile(
            content=_qasm3_h(2).encode(),
            attributes={"circuit.format": "qasm3"},
        )
        r = proc.transform(ctx, ff)
        assert "sim.top_result" in r.attributes
        assert "sim.top_probability" in r.attributes

    def test_passthrough_json_content(self):
        """JSON content with no circuit.format is passed through unchanged."""
        proc = QiskitAerSimulator()
        ctx  = MockContext(**{"Shots": "1024"})
        ff   = MockFlowFile(content=b'{"00": 512, "11": 512}')
        r = proc.transform(ctx, ff)
        assert r.relationship == "success"
        assert r.attributes.get("sim.passthrough") == "true"

    def test_grover_target_is_top(self):
        """Grover circuit for '11' → AerSimulator top result should be '11'."""
        grover_ctx = MockContext(**{
            "Marked State": "11",
            "Num Iterations": "1",
            "Insert Barriers": "false",
            "Output Format": "qasm3",
        })
        circuit_r = QiskitGroverCircuit().transform(grover_ctx, MockFlowFile())

        sim_ctx = MockContext(**{"Shots": "1024"})
        r = QiskitAerSimulator().transform(sim_ctx, result_to_flowfile(circuit_r))
        assert r.relationship == "success"
        assert r.attributes["sim.top_result"] == "11"

    def test_default_is_ideal(self):
        """No Noise Model property supplied → ideal simulation, sim.noise_model=none."""
        proc = QiskitAerSimulator()
        ctx  = MockContext(**{"Shots": "256"})
        ff   = MockFlowFile(content=_qasm3_h(2).encode(),
                            attributes={"circuit.format": "qasm3"})
        r = proc.transform(ctx, ff)
        assert r.attributes["sim.noise_model"] == "none"

    def test_depolarizing_noise_runs(self):
        proc = QiskitAerSimulator()
        ctx  = MockContext(**{
            "Shots": "512",
            "Noise Model": "depolarizing",
            "1-Qubit Error Rate": "0.05",
            "2-Qubit Error Rate": "0.1",
        })
        ff   = MockFlowFile(content=_qasm3_h(2).encode(),
                            attributes={"circuit.format": "qasm3"})
        r = proc.transform(ctx, ff)
        assert r.relationship == "success"
        assert sum(json.loads(r.contents).values()) == 512
        assert r.attributes["sim.noise_model"] == "depolarizing"
        assert "error_1q" in r.attributes["sim.noise_params"]

    # --- Noise Model (EL) override (Option B: dropdown for humans, EL for data) ---

    def test_el_override_wins_over_dropdown(self):
        proc = QiskitAerSimulator()
        ctx  = MockContext(**{"Shots": "256", "Noise Model": "none",
                              "Noise Model (EL)": "${sim.noise_model}",
                              "1-Qubit Error Rate": "0.05", "2-Qubit Error Rate": "0.1"})
        ff   = MockFlowFile(content=_qasm3_h(2).encode(),
                            attributes={"circuit.format": "qasm3",
                                        "sim.noise_model": "depolarizing"})
        r = proc.transform(ctx, ff)
        assert r.attributes["sim.noise_model"] == "depolarizing"

    def test_blank_el_override_falls_back_to_dropdown(self):
        proc = QiskitAerSimulator()
        ctx  = MockContext(**{"Shots": "256", "Noise Model": "none",
                              "Noise Model (EL)": ""})
        ff   = MockFlowFile(content=_qasm3_h(2).encode(),
                            attributes={"circuit.format": "qasm3"})
        r = proc.transform(ctx, ff)
        assert r.attributes["sim.noise_model"] == "none"

    def test_thermal_relaxation_noise_runs(self):
        proc = QiskitAerSimulator()
        ctx  = MockContext(**{
            "Shots": "256",
            "Noise Model": "thermal_relaxation",
            "T1 (us)": "50", "T2 (us)": "70", "Gate Time (ns)": "100",
        })
        ff   = MockFlowFile(content=_qasm3_h(2).encode(),
                            attributes={"circuit.format": "qasm3"})
        r = proc.transform(ctx, ff)
        assert r.relationship == "success"
        assert r.attributes["sim.noise_model"] == "thermal_relaxation"

    def test_readout_error_only(self):
        """Readout error with Noise Model=none still builds a model (readout_only)."""
        proc = QiskitAerSimulator()
        ctx  = MockContext(**{"Shots": "256", "Readout Error Rate": "0.1"})
        ff   = MockFlowFile(content=_qasm3_h(2).encode(),
                            attributes={"circuit.format": "qasm3"})
        r = proc.transform(ctx, ff)
        assert r.attributes["sim.noise_model"] == "readout_only"

    def test_from_backend_noise_runs(self):
        """Hardware-realistic noise from an offline fake backend."""
        proc = QiskitAerSimulator()
        ctx  = MockContext(**{
            "Shots": "256",
            "Noise Model": "from_backend",
            "Backend Name": "fake_manila",
        })
        ff   = MockFlowFile(content=_qasm3_h(2).encode(),
                            attributes={"circuit.format": "qasm3"})
        r = proc.transform(ctx, ff)
        assert r.relationship == "success"
        assert sum(json.loads(r.contents).values()) == 256
        assert r.attributes["sim.noise_model"] == "from_backend"
        assert "fake_manila" in r.attributes["sim.noise_params"]


# ---------------------------------------------------------------------------
# QiskitGroverSearch (all-in-one)
# ---------------------------------------------------------------------------

class TestQiskitGroverSearch:

    def _run(self, target, iterations=1, shots=1024):
        ctx = MockContext(**{
            "Marked State": target,
            "Num Iterations": str(iterations),
            "Shots": str(shots),
            "Insert Barriers": "false",
        })
        return QiskitGroverSearch().transform(ctx, MockFlowFile())

    def test_two_qubit_finds_target(self):
        r = self._run("11")
        assert r.relationship == "success"
        counts = json.loads(r.contents)
        assert max(counts, key=counts.get) == "11"

    def test_three_qubit_finds_target(self):
        r = self._run("101", iterations=2)
        counts = json.loads(r.contents)
        assert max(counts, key=counts.get) == "101"

    def test_grover_attributes(self):
        r = self._run("11")
        assert r.attributes["grover.marked_state"] == "11"
        assert r.attributes["grover.top_result"] == "11"
        assert "grover.top_probability" in r.attributes
        assert "grover.shots" in r.attributes
