"""Qiskit QAOA circuit construction plus native qasm2 export (not a
processor). Shared by ``QiskitQAOACircuit`` (fixed angles) and ``QiskitQAOA``
(trained angles), so "the builder reproduces the trained circuit exactly" is
a string-equality test: both call ``export`` with the same construction.

Uses ``qiskit.circuit.library.qaoa_ansatz`` (not the deprecated ``QAOAAnsatz``
class) — same unitary and parameter order, no deprecation warning.
"""

BASIS = ["h", "rx", "rz", "cx"]


def cost_operator(terms, n):
    from qiskit.quantum_info import SparsePauliOp

    return SparsePauliOp.from_sparse_list(
        [(pauli, idx, coeff) for pauli, idx, coeff in terms], num_qubits=n
    )


def bound_circuit(terms, n, betas, gammas):
    """The trained/fixed-angle circuit, transpiled onto the portable basis.

    Parameter order matches ``qaoa_ansatz``'s own sort: all betas, then all
    gammas -- the canonical order used everywhere else in this project.
    """
    from qiskit import transpile
    from qiskit.circuit.library import qaoa_ansatz

    layers = len(betas)
    ansatz = qaoa_ansatz(cost_operator(terms, n), reps=layers)
    assert (
        ansatz.num_parameters == 2 * layers
    ), "qaoa_ansatz produced {} parameters, expected {}".format(
        ansatz.num_parameters, 2 * layers
    )
    bound = ansatz.assign_parameters([*betas, *gammas])
    return transpile(bound, basis_gates=BASIS, optimization_level=0)


def export(terms, n, betas, gammas):
    """Returns ``(qasm2_source, text_diagram)``."""
    from qiskit import qasm2

    tc = bound_circuit(terms, n, betas, gammas)
    return qasm2.dumps(tc), str(tc.draw("text"))
