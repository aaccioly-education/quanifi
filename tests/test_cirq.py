"""
Unit tests for Cirq-based NiFi processors:
  CirqHadamardTransform, CirqPhaseOracle, CirqGroverOperator,
  CirqGroverCircuit, CirqSimulator.
"""

import json

import pytest

from CirqHadamardTransform import CirqHadamardTransform
from CirqPhaseOracle import CirqPhaseOracle
from CirqGroverOperator import CirqGroverOperator
from CirqGroverCircuit import CirqGroverCircuit
from CirqSimulator import CirqSimulator

from conftest import MockContext, MockFlowFile, result_to_flowfile


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _cirq_json_h(n=2):
    """Return a cirq_json-encoded H⊗n circuit."""
    import cirq
    qubits = cirq.LineQubit.range(n)
    circuit = cirq.Circuit(cirq.H.on_each(*qubits))
    return cirq.to_json(circuit)


def _build_oracle_flowfile(target="101"):
    """Run CirqPhaseOracle standalone to get an oracle FlowFile."""
    ctx = MockContext(**{
        "Marked State": target,
        "Insert Barriers": "false",
        "Output Format": "cirq_json",
    })
    r = CirqPhaseOracle().transform(ctx, MockFlowFile())
    return result_to_flowfile(r)


# ---------------------------------------------------------------------------
# CirqHadamardTransform
# ---------------------------------------------------------------------------

class TestCirqHadamardTransform:

    def test_standalone_cirq_json(self):
        proc = CirqHadamardTransform()
        ctx  = MockContext(**{"Qubit Count": "2", "Output Format": "cirq_json"})
        r = proc.transform(ctx, MockFlowFile())
        assert r.relationship == "success"
        assert r.attributes["circuit.num_qubits"] == "2"
        assert r.attributes["circuit.format"] == "cirq_json"
        # Content must be valid Cirq JSON
        import cirq
        circuit = cirq.read_json(json_text=r.contents.decode())
        assert len(list(circuit.all_qubits())) == 2

    def test_standalone_qasm2(self):
        proc = CirqHadamardTransform()
        ctx  = MockContext(**{"Qubit Count": "3", "Output Format": "qasm2"})
        r = proc.transform(ctx, MockFlowFile())
        assert r.attributes["circuit.format"] == "qasm2"
        assert r.attributes["circuit.num_qubits"] == "3"
        assert "OPENQASM 2" in r.contents.decode()

    def test_compose_onto_oracle(self):
        """H layer is appended to a CirqPhaseOracle output circuit."""
        oracle_ff = _build_oracle_flowfile("101")
        proc = CirqHadamardTransform()
        ctx  = MockContext(**{"Qubit Count": "3", "Output Format": "cirq_json"})
        r = proc.transform(ctx, oracle_ff)
        assert r.relationship == "success"
        assert r.attributes["circuit.num_qubits"] == "3"

    def test_metrics_emitted(self):
        proc = CirqHadamardTransform()
        ctx  = MockContext(**{"Qubit Count": "2", "Output Format": "cirq_json"})
        r = proc.transform(ctx, MockFlowFile())
        for key in ("circuit.depth", "circuit.gate_count", "circuit.nonlocal_gates", "circuit.t_count"):
            assert key in r.attributes

    def test_property_descriptors(self):
        names = {d.name for d in CirqHadamardTransform().getPropertyDescriptors()}
        assert names == {"Qubit Count", "Output Format"}


# ---------------------------------------------------------------------------
# CirqPhaseOracle
# ---------------------------------------------------------------------------

class TestCirqPhaseOracle:

    def _run(self, target, fmt="cirq_json", barriers="false"):
        ctx = MockContext(**{
            "Marked State": target,
            "Insert Barriers": barriers,
            "Output Format": fmt,
        })
        return CirqPhaseOracle().transform(ctx, MockFlowFile())

    def test_standalone_2qubit(self):
        r = self._run("11")
        assert r.relationship == "success"
        assert r.attributes["circuit.num_qubits"] == "2"
        assert r.attributes["circuit.marked_state"] == "11"
        assert r.attributes["circuit.format"] == "cirq_json"

    def test_standalone_3qubit(self):
        r = self._run("101")
        assert r.attributes["circuit.num_qubits"] == "3"
        assert r.attributes["circuit.marked_state"] == "101"

    def test_qasm2_output(self):
        r = self._run("11", fmt="qasm2")
        assert r.attributes["circuit.format"] == "qasm2"
        assert "OPENQASM 2" in r.contents.decode()

    def test_valid_cirq_json(self):
        import cirq
        r = self._run("101")
        circuit = cirq.read_json(json_text=r.contents.decode())
        assert len(list(circuit.all_qubits())) == 3

    def test_compose_onto_hadamard(self):
        """Oracle can be appended to an existing Cirq circuit."""
        h_json = _cirq_json_h(2)
        ctx = MockContext(**{
            "Marked State": "11",
            "Insert Barriers": "false",
            "Output Format": "cirq_json",
        })
        ff = MockFlowFile(
            content=h_json.encode(),
            attributes={"circuit.format": "cirq_json"},
        )
        r = CirqPhaseOracle().transform(ctx, ff)
        assert r.relationship == "success"
        # Composed circuit should be longer than the oracle alone
        import cirq
        composed = cirq.read_json(json_text=r.contents.decode())
        standalone_ctx = MockContext(**{
            "Marked State": "11",
            "Insert Barriers": "false",
            "Output Format": "cirq_json",
        })
        standalone_r = CirqPhaseOracle().transform(standalone_ctx, MockFlowFile())
        standalone_c = cirq.read_json(json_text=standalone_r.contents.decode())
        assert len(composed) > len(standalone_c)

    def test_metrics_emitted(self):
        r = self._run("101")
        for key in ("circuit.depth", "circuit.gate_count", "circuit.nonlocal_gates"):
            assert key in r.attributes

    def test_property_descriptors(self):
        names = {d.name for d in CirqPhaseOracle().getPropertyDescriptors()}
        assert names == {"Marked State", "Insert Barriers", "Output Format"}


# ---------------------------------------------------------------------------
# CirqGroverOperator
# ---------------------------------------------------------------------------

class TestCirqGroverOperator:

    def _run(self, oracle_ff, iterations=1, fmt="cirq_json", barriers="false"):
        ctx = MockContext(**{
            "Num Iterations": str(iterations),
            "Insert Barriers": barriers,
            "Output Format": fmt,
        })
        return CirqGroverOperator().transform(ctx, oracle_ff)

    def test_reads_oracle_and_builds_circuit(self):
        oracle_ff = _build_oracle_flowfile("101")
        r = self._run(oracle_ff, iterations=2)
        assert r.relationship == "success"
        assert r.attributes["circuit.num_qubits"] == "3"
        assert r.attributes["circuit.num_iterations"] == "2"

    def test_marked_state_forwarded(self):
        oracle_ff = _build_oracle_flowfile("11")
        r = self._run(oracle_ff)
        # circuit.marked_state from the oracle FlowFile attribute is forwarded
        assert r.attributes.get("circuit.marked_state") == "11"

    def test_qasm2_output(self):
        oracle_ff = _build_oracle_flowfile("11")
        r = self._run(oracle_ff, fmt="qasm2")
        assert r.attributes["circuit.format"] == "qasm2"
        assert "OPENQASM 2" in r.contents.decode()

    def test_valid_cirq_json_output(self):
        import cirq
        oracle_ff = _build_oracle_flowfile("101")
        r = self._run(oracle_ff, iterations=2)
        circuit = cirq.read_json(json_text=r.contents.decode())
        assert len(list(circuit.all_qubits())) == 3

    def test_circuit_is_deeper_with_more_iterations(self):
        import cirq
        oracle_ff_1 = _build_oracle_flowfile("11")
        oracle_ff_2 = _build_oracle_flowfile("11")
        r1 = self._run(oracle_ff_1, iterations=1)
        r2 = self._run(oracle_ff_2, iterations=3)
        c1 = cirq.read_json(json_text=r1.contents.decode())
        c2 = cirq.read_json(json_text=r2.contents.decode())
        assert len(c2) > len(c1)

    def test_metrics_emitted(self):
        oracle_ff = _build_oracle_flowfile("11")
        r = self._run(oracle_ff)
        for key in ("circuit.depth", "circuit.gate_count", "circuit.nonlocal_gates", "circuit.t_count"):
            assert key in r.attributes

    def test_property_descriptors(self):
        names = {d.name for d in CirqGroverOperator().getPropertyDescriptors()}
        assert names == {"Num Iterations", "Insert Barriers", "Output Format"}


# ---------------------------------------------------------------------------
# CirqGroverCircuit (all-in-one)
# ---------------------------------------------------------------------------

class TestCirqGroverCircuit:

    def _run(self, target="11", iterations=1, fmt="cirq_json", barriers="false"):
        ctx = MockContext(**{
            "Marked State": target,
            "Num Iterations": str(iterations),
            "Insert Barriers": barriers,
            "Output Format": fmt,
        })
        return CirqGroverCircuit().transform(ctx, MockFlowFile())

    def test_basic_cirq_json(self):
        r = self._run("11")
        assert r.relationship == "success"
        assert r.attributes["circuit.marked_state"] == "11"
        assert r.attributes["circuit.num_qubits"] == "2"
        assert r.attributes["circuit.format"] == "cirq_json"

    def test_qasm2_output(self):
        r = self._run("101", fmt="qasm2")
        assert r.attributes["circuit.format"] == "qasm2"
        assert "OPENQASM 2" in r.contents.decode()
        assert r.attributes["circuit.num_qubits"] == "3"

    def test_valid_cirq_json(self):
        import cirq
        r = self._run("11")
        circuit = cirq.read_json(json_text=r.contents.decode())
        assert len(list(circuit.all_qubits())) == 2

    def test_circuit_metrics(self):
        r = self._run("11")
        for key in ("circuit.depth", "circuit.gate_count", "circuit.nonlocal_gates", "circuit.t_count"):
            assert key in r.attributes

    def test_iterations_attribute(self):
        r = self._run("11", iterations=2)
        assert r.attributes["circuit.num_iterations"] == "2"


# ---------------------------------------------------------------------------
# CirqSimulator
# ---------------------------------------------------------------------------

class TestCirqSimulator:

    def test_cirq_json_input(self):
        proc = CirqSimulator()
        ctx  = MockContext(**{"Shots": "256"})
        ff   = MockFlowFile(
            content=_cirq_json_h(2).encode(),
            attributes={"circuit.format": "cirq_json"},
        )
        r = proc.transform(ctx, ff)
        assert r.relationship == "success"
        counts = json.loads(r.contents)
        assert sum(counts.values()) == 256
        assert r.attributes["sim.framework"] == "cirq"

    def test_qasm2_input(self):
        """CirqSimulator can accept qasm2 produced by a Qiskit processor."""
        from qiskit import QuantumCircuit, qasm2, transpile
        qc = QuantumCircuit(2)
        qc.h(range(2))
        tc = transpile(qc, basis_gates=["h", "cx", "rz", "x"], optimization_level=0)
        qasm2_str = qasm2.dumps(tc)

        proc = CirqSimulator()
        ctx  = MockContext(**{"Shots": "128"})
        ff   = MockFlowFile(
            content=qasm2_str.encode(),
            attributes={"circuit.format": "qasm2"},
        )
        r = proc.transform(ctx, ff)
        assert r.relationship == "success"
        assert sum(json.loads(r.contents).values()) == 128

    def test_unsupported_format_fails(self):
        proc = CirqSimulator()
        ctx  = MockContext(**{"Shots": "1024"})
        ff   = MockFlowFile(
            content=b"fake",
            attributes={"circuit.format": "qpy"},
        )
        r = proc.transform(ctx, ff)
        assert r.relationship == "failure"
        assert "sim.error" in r.attributes

    def test_top_result_attributes(self):
        proc = CirqSimulator()
        ctx  = MockContext(**{"Shots": "512"})
        ff   = MockFlowFile(
            content=_cirq_json_h(2).encode(),
            attributes={"circuit.format": "cirq_json"},
        )
        r = proc.transform(ctx, ff)
        assert "sim.top_result" in r.attributes
        assert "sim.top_probability" in r.attributes

    def test_grover_circuit_correct_target(self):
        """CirqGroverCircuit → CirqSimulator: top result must be the marked state."""
        grover_ctx = MockContext(**{
            "Marked State": "101",
            "Num Iterations": "2",
            "Insert Barriers": "false",
            "Output Format": "cirq_json",
        })
        circuit_r = CirqGroverCircuit().transform(grover_ctx, MockFlowFile())

        sim_ctx = MockContext(**{"Shots": "1024"})
        r = CirqSimulator().transform(sim_ctx, result_to_flowfile(circuit_r))
        assert r.relationship == "success"
        assert r.attributes["sim.top_result"] == "101"

    def test_decomposed_grover_correct_target(self):
        """CirqPhaseOracle → CirqGroverOperator → CirqSimulator pipeline."""
        oracle_r = CirqPhaseOracle().transform(
            MockContext(**{"Marked State": "101", "Insert Barriers": "false", "Output Format": "cirq_json"}),
            MockFlowFile(),
        )
        operator_r = CirqGroverOperator().transform(
            MockContext(**{"Num Iterations": "2", "Insert Barriers": "false", "Output Format": "cirq_json"}),
            result_to_flowfile(oracle_r),
        )
        sim_r = CirqSimulator().transform(
            MockContext(**{"Shots": "1024"}),
            result_to_flowfile(operator_r),
        )
        assert sim_r.relationship == "success"
        assert sim_r.attributes["sim.top_result"] == "101"

    # --- noise ------------------------------------------------------------

    def _ff(self):
        return MockFlowFile(content=_cirq_json_h(2).encode(),
                            attributes={"circuit.format": "cirq_json"})

    def test_default_is_ideal(self):
        r = CirqSimulator().transform(MockContext(**{"Shots": "256"}), self._ff())
        assert r.attributes["sim.noise_model"] == "none"
        assert "sim.noise_params" not in r.attributes

    def test_depolarizing_noise_runs(self):
        ctx = MockContext(**{"Shots": "512", "Noise Model": "depolarizing",
                             "Error Probability": "0.1"})
        r = CirqSimulator().transform(ctx, self._ff())
        assert r.relationship == "success"
        assert sum(json.loads(r.contents).values()) == 512
        assert r.attributes["sim.noise_model"] == "depolarizing"
        assert "probability" in r.attributes["sim.noise_params"]

    def test_bit_flip_noise_runs(self):
        ctx = MockContext(**{"Shots": "256", "Noise Model": "bit_flip",
                             "Error Probability": "0.2"})
        r = CirqSimulator().transform(ctx, self._ff())
        assert r.relationship == "success"
        assert r.attributes["sim.noise_model"] == "bit_flip"

    def test_amplitude_damp_noise_runs(self):
        ctx = MockContext(**{"Shots": "256", "Noise Model": "amplitude_damp",
                             "Damping Gamma": "0.1"})
        r = CirqSimulator().transform(ctx, self._ff())
        assert r.relationship == "success"
        assert r.attributes["sim.noise_model"] == "amplitude_damp"
        assert "gamma" in r.attributes["sim.noise_params"]

    def test_readout_error_only(self):
        ctx = MockContext(**{"Shots": "256", "Readout Error Rate": "0.2"})
        r = CirqSimulator().transform(ctx, self._ff())
        assert r.relationship == "success"
        assert r.attributes["sim.noise_model"] == "readout_only"

    # --- Noise Model (EL) override (Option B: dropdown for humans, EL for data) ---

    def test_el_override_wins_over_dropdown(self):
        ff = MockFlowFile(content=_cirq_json_h(2).encode(),
                          attributes={"circuit.format": "cirq_json",
                                      "sim.noise_model": "depolarizing"})
        ctx = MockContext(**{"Shots": "256", "Noise Model": "none",
                             "Noise Model (EL)": "${sim.noise_model}",
                             "Error Probability": "0.1"})
        r = CirqSimulator().transform(ctx, ff)
        assert r.attributes["sim.noise_model"] == "depolarizing"

    def test_blank_el_override_falls_back_to_dropdown(self):
        ctx = MockContext(**{"Shots": "256", "Noise Model": "bit_flip",
                             "Noise Model (EL)": "", "Error Probability": "0.2"})
        r = CirqSimulator().transform(ctx, self._ff())
        assert r.attributes["sim.noise_model"] == "bit_flip"

    def test_el_override_replace_empty_yields_none(self):
        # attribute absent -> replaceEmpty('none') -> override is 'none' -> ideal,
        # even though the dropdown says depolarizing.
        ctx = MockContext(**{"Shots": "256", "Noise Model": "depolarizing",
                             "Noise Model (EL)": "${sim.noise_model:replaceEmpty('none')}"})
        r = CirqSimulator().transform(ctx, self._ff())
        assert r.attributes["sim.noise_model"] == "none"
