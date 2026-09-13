import sys
import argparse
from qiskit import QuantumCircuit
from qiskit_aer import AerSimulator


def run_circuit(num_qubits: int) -> str:
    qc = QuantumCircuit(num_qubits)
    # GHZ state: equal superposition of |00...0> and |11...1>
    qc.h(0)
    for i in range(1, num_qubits):
        qc.cx(0, i)
    qc.measure_all()

    backend = AerSimulator()
    result = backend.run(qc, shots=1).result()
    return list(result.get_counts().keys())[0]


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run a Qiskit quantum circuit and output the measurement bitstring")
    parser.add_argument("--qubits", type=int, default=2)
    args = parser.parse_args()

    try:
        bitstring = run_circuit(args.qubits)
        sys.stdout.write(bitstring)
        sys.exit(0)
    except Exception as e:
        sys.stderr.write(str(e))
        sys.exit(1)
