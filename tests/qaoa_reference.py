"""Independent QAOA reference implementation and test harness (not collected
by pytest: no ``test_*`` names at module scope).

Deliberately does not reuse any QAOA production code. The only "production"
imports are ``pauli_dsl`` (the shared wire-format helpers, not QAOA-specific),
``qiskit.qasm2``/``Statevector`` used purely as a foreign qasm2 parser (not the
Qiskit QAOA builder), ``conftest`` (the NiFi stub harness) and
``QuantumDistributionComparison`` (the Hellinger/normalize helpers). Every
other QAOA processor and helper module is treated as the system under test.

Convention (matches ``qaoa_contract`` and every builder/solver): H on every
qubit, then per layer ``diag(exp(-i*gamma*E(z)))`` followed by
``RX(2*beta)`` on every qubit, minimising the cost. State-vector indices and
measurement keys are q0-left (qubit 0 = the leftmost/most-significant
character).
"""

from __future__ import annotations

import importlib
import json

import numpy as np

from conftest import MockContext, MockFlowFile, result_to_flowfile_merged

# ---------------------------------------------------------------------------
# Test instances
# ---------------------------------------------------------------------------

H_ASYM = "0.5 Z0 Z1 + 0.5 Z1 Z2 - 0.3 Z0 + 0.2"
H_3BODY = "0.5 Z0 Z1 + 0.5 Z1 Z2 - 0.3 Z0 + 0.7 Z0 Z2 + 0.25 Z0 Z1 Z2 + 0.2"
H_NXM = "-0.8 Z0 Z1 + 0.5 Z1 Z2 - 0.3 Z0 + 0.2"

NXM_LAYERS = 2
NXM_BETAS = [-0.67, -0.42]
NXM_GAMMAS = [1.14, 1.27]
NXM_GROUND = "001"
NXM_EMIN = -1.4

ANGLE_SETS = [
    (1, [0.37], [0.81]),
    (2, [0.37, -0.52], [0.81, 1.23]),
    (3, [0.1, 0.2, 0.3], [0.9, -0.4, 0.6]),
]

HELLINGER_MAX = {4096: 0.04, 256: 0.20}
EXPECTATION_TOL = {4096: 0.06, 256: 0.25}


# ---------------------------------------------------------------------------
# Wire-format helpers
# ---------------------------------------------------------------------------


def wire(spec, n=0):
    """Compact-indexed Pauli text -> the neutral wire-format JSON text."""
    from pauli_dsl import parse_pauli_sum, terms_to_wire

    terms, num_qubits = parse_pauli_sum(spec, n)
    return json.dumps(terms_to_wire(terms, num_qubits))


def terms_of(spec, n=0):
    """Compact-indexed Pauli text -> ``(terms, num_qubits)``, round-tripped
    through the wire format (not the DSL parser's own term list)."""
    from pauli_dsl import wire_to_terms

    return wire_to_terms(wire(spec, n))


# ---------------------------------------------------------------------------
# Independent exact reference: energies, state, probabilities
# ---------------------------------------------------------------------------


def energies(terms, n):
    """Exact energy of every basis state, indexed q0-left: E[idx] where idx's
    bit (n-1-i) is qubit i. Computed directly (NOT via
    ``pauli_dsl.diagonal_values``), as an independent cross-check."""
    idx = np.arange(2**n)
    values = np.zeros(2**n, dtype=np.float64)
    for pauli, indices, coeff in terms:
        contrib = np.full(2**n, float(coeff))
        for i in indices:
            contrib *= 1.0 - 2.0 * ((idx >> (n - 1 - i)) & 1)
        values += contrib
    return values


def _rx(beta):
    """RX(2*beta) = exp(-i*beta*X)."""
    c, s = np.cos(beta), np.sin(beta)
    return np.array([[c, -1j * s], [-1j * s, c]], dtype=complex)


def reference_state(terms, n, betas, gammas):
    """The exact QAOA state vector, kron order q0 first (index = q0-left)."""
    e = energies(terms, n)
    psi = np.full(2**n, 2.0 ** (-n / 2), dtype=complex)
    for beta, gamma in zip(betas, gammas):
        psi = psi * np.exp(-1j * gamma * e)
        mixer = _rx(beta)
        for _ in range(n - 1):
            mixer = np.kron(mixer, _rx(beta))
        psi = mixer @ psi
    return psi


def reference_probabilities(terms, n, betas, gammas):
    psi = reference_state(terms, n, betas, gammas)
    probs = np.abs(psi) ** 2
    return {format(i, "0{}b".format(n)): float(probs[i]) for i in range(2**n)}


def qasm2_state(source):
    """Parse portable qasm2 with Qiskit (as a foreign parser only) and return
    the state vector re-indexed to q0-left."""
    from qiskit import qasm2
    from qiskit.quantum_info import Statevector

    circuit = qasm2.loads(source, custom_instructions=qasm2.LEGACY_CUSTOM_INSTRUCTIONS)
    sv = Statevector.from_instruction(circuit)
    n = circuit.num_qubits
    data = np.asarray(sv.data)
    out = np.zeros(2**n, dtype=complex)
    for k in range(2**n):
        j = int(format(k, "0{}b".format(n))[::-1], 2)
        out[j] = data[k]
    return out


def fidelity(a, b):
    return float(abs(np.vdot(a, b)) ** 2)


def hellinger(counts, probs):
    from QuantumDistributionComparison import _hellinger, _normalize

    return _hellinger(_normalize(counts), probs)


# ---------------------------------------------------------------------------
# Harness helpers (run a real processor through the NiFi stubs)
# ---------------------------------------------------------------------------


def run(name, props=None, ff=None):
    cls = getattr(importlib.import_module(name), name)
    return cls().transform(MockContext(**(props or {})), ff or MockFlowFile())


def chain(name, props=None, ff=None):
    ff = ff or MockFlowFile()
    result = run(name, props, ff)
    assert result.relationship == "success", result.attributes
    return result_to_flowfile_merged(result, ff)


def hamiltonian_ff(spec, n="0", producer="QiskitHamiltonian"):
    return chain(producer, {"Hamiltonian": spec, "Num Qubits": n})


def evaluate(ff, engine="QiskitAerSimulator", props=None):
    """Run a counts engine then QuantumQAOAEvaluator on its merged output.

    Returns (engine_result, merged_flowfile, evaluator_result).
    """
    if props is None:
        props = {"Shots": "1024", "Random Seed": "11"}
    engine_result = run(engine, props, ff)
    assert engine_result.relationship == "success", engine_result.attributes
    merged = result_to_flowfile_merged(engine_result, ff)
    evaluator_result = run("QuantumQAOAEvaluator", {}, merged)
    return engine_result, merged, evaluator_result
