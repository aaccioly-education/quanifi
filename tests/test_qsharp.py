import json

from QSharpSimulator import QSharpSimulator
from conftest import MockContext, MockFlowFile, result_to_flowfile


def _qasm3_ff(build):
    from qiskit import qasm3

    circuit = build()
    return MockFlowFile(
        content=qasm3.dumps(circuit).encode("utf-8"),
        attributes={"circuit.format": "qasm3"},
    )


class TestQSharpSimulator:

    def test_counts_contract_and_bit_order(self):
        from qiskit import QuantumCircuit

        circuit = QuantumCircuit(3)
        circuit.x(0)
        result = QSharpSimulator().transform(
            MockContext(**{"Shots": "32", "Random Seed": "17"}),
            _qasm3_ff(lambda: circuit),
        )

        assert result.relationship == "success"
        assert json.loads(result.contents) == {"100": 32}
        assert result.attributes["sim.framework"] == "qsharp"
        assert result.attributes["sim.backend"] == "sparse"
        assert result.attributes["sim.bit_order"] == "q0_left"
        assert result.attributes["sim.top_result"] == "100"
        assert result.attributes["sim.top_probability"] == "1.0000"
        assert result.attributes["sim.shots"] == "32"
        assert result.attributes["sim.noise_model"] == "none"
        assert result.attributes["run.seed"] == "17"
        assert result.attributes["report.type"] == "simulation"
        assert float(result.attributes["perf.elapsed_seconds"]) >= 0.0

    def test_qasm2_builder_interop(self):
        from QiskitHadamardTransform import QiskitHadamardTransform

        built = QiskitHadamardTransform().transform(
            MockContext(**{"Qubit Count": "2", "Output Format": "qasm2"}),
            MockFlowFile(),
        )
        result = QSharpSimulator().transform(
            MockContext(**{"Shots": "64", "Random Seed": "7"}),
            result_to_flowfile(built),
        )

        assert result.relationship == "success"
        counts = json.loads(result.contents)
        assert sum(counts.values()) == 64
        assert set(counts) <= {"00", "01", "10", "11"}
        assert list(counts.values()) == sorted(counts.values(), reverse=True)

    def test_existing_measurements_are_replaced(self):
        from qiskit import QuantumCircuit

        circuit = QuantumCircuit(2, 1)
        circuit.x(1)
        circuit.measure(0, 0)
        result = QSharpSimulator().transform(
            MockContext(**{"Shots": "8"}),
            _qasm3_ff(lambda: circuit),
        )
        assert result.relationship == "success"
        assert json.loads(result.contents) == {"01": 8}

    def test_clifford_mode(self):
        from qiskit import QuantumCircuit

        circuit = QuantumCircuit(2)
        circuit.x(0)
        result = QSharpSimulator().transform(
            MockContext(**{"Shots": "8", "Simulator Type": "clifford"}),
            _qasm3_ff(lambda: circuit),
        )
        assert result.relationship == "success"
        assert result.attributes["sim.backend"] == "clifford"
        assert json.loads(result.contents) == {"10": 8}

    def test_non_clifford_circuit_fails_before_native_qdk_call(self):
        from qiskit import QuantumCircuit

        circuit = QuantumCircuit(1)
        circuit.t(0)
        result = QSharpSimulator().transform(
            MockContext(**{"Shots": "8", "Simulator Type": "clifford"}),
            _qasm3_ff(lambda: circuit),
        )
        assert result.relationship == "failure"
        assert "Clifford-only circuit" in result.attributes["sim.error"]
        assert "use sparse" in result.attributes["sim.error"]

    def test_depolarizing_noise_metadata(self):
        from qiskit import QuantumCircuit

        circuit = QuantumCircuit(1)
        result = QSharpSimulator().transform(
            MockContext(**{
                "Shots": "16",
                "Noise Model": "depolarizing",
                "Error Probability": "0.1",
                "Random Seed": "3",
            }),
            _qasm3_ff(lambda: circuit),
        )
        assert result.relationship == "success"
        assert result.attributes["sim.noise_model"] == "depolarizing"
        assert json.loads(result.attributes["sim.noise_params"]) == {"probability": 0.1}


class TestQSharpSimulatorErrors:

    def test_missing_format_fails(self):
        result = QSharpSimulator().transform(MockContext(), MockFlowFile(content=b"..."))
        assert result.relationship == "failure"
        assert "circuit.format" in result.attributes["sim.error"]

    def test_unsupported_format_fails(self):
        result = QSharpSimulator().transform(
            MockContext(),
            MockFlowFile(content=b"...", attributes={"circuit.format": "qpy"}),
        )
        assert result.relationship == "failure"
        assert "unsupported circuit.format" in result.attributes["sim.error"]

    def test_invalid_pauli_sum_fails(self):
        from qiskit import QuantumCircuit

        result = QSharpSimulator().transform(
            MockContext(**{
                "Noise Model": "pauli",
                "Pauli X Probability": "0.5",
                "Pauli Y Probability": "0.5",
                "Pauli Z Probability": "0.1",
            }),
            _qasm3_ff(lambda: QuantumCircuit(1)),
        )
        assert result.relationship == "failure"
        assert "sum to at most 1" in result.attributes["sim.error"]

    def test_noise_with_clifford_fails_clearly(self):
        from qiskit import QuantumCircuit

        result = QSharpSimulator().transform(
            MockContext(**{"Simulator Type": "clifford", "Noise Model": "bit_flip"}),
            _qasm3_ff(lambda: QuantumCircuit(1)),
        )
        assert result.relationship == "failure"
        assert "sparse simulator" in result.attributes["sim.error"]
