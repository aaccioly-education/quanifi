# Quanifi — quantum-computing components for Apache NiFi
# Copyright (C) 2026 Neilson Ramalho
#
# This program is free software: you can redistribute it and/or modify it under
# the terms of the GNU Affero General Public License, version 3, as published by
# the Free Software Foundation. This program is distributed WITHOUT ANY WARRANTY;
# without even the implied warranty of MERCHANTABILITY or FITNESS FOR A
# PARTICULAR PURPOSE. See the GNU Affero General Public License for more details.
#
# You should have received a copy of the license along with this program; if not,
# see <https://www.gnu.org/licenses/>. Commercial licensing is also available:
# see COMMERCIAL.md at the repository root.

"""Cirq QAOA circuit construction plus native qasm2 export (not a
processor). Shared by ``CirqQAOACircuit`` (fixed angles) and ``CirqQAOA``
(trained angles).

Cirq has no high-level QAOA class, so the ansatz is hand-rolled from
primitives: a sympy-parameterised ladder alternating cost layers
(CNOT ladder + Rz per Z-string term, exp(-i*gamma*H)) and Rx mixer layers.
"""


def symbolic_circuit(terms, n, layers):
    """The sympy-parameterised ansatz: H^n then ``layers`` alternating
    cost/mixer blocks. Returns ``(circuit, param_names)`` with names ordered
    betas then gammas, matching the canonical parameter order."""
    import cirq
    import sympy

    qubits = cirq.LineQubit.range(n)
    betas = [sympy.Symbol("beta_{}".format(layer)) for layer in range(layers)]
    gammas = [sympy.Symbol("gamma_{}".format(layer)) for layer in range(layers)]

    circuit = cirq.Circuit()
    circuit.append(cirq.H(q) for q in qubits)
    for layer in range(layers):
        for pauli, indices, coeff in terms:
            if not pauli:  # identity changes energy but only global circuit phase
                continue
            targets = [qubits[i] for i in indices]
            for a, b in zip(targets, targets[1:]):
                circuit.append(cirq.CNOT(a, b))
            circuit.append(cirq.rz(2.0 * coeff * gammas[layer]).on(targets[-1]))
            for a, b in reversed(list(zip(targets, targets[1:]))):
                circuit.append(cirq.CNOT(a, b))
        circuit.append(cirq.rx(2.0 * betas[layer]).on(q) for q in qubits)

    param_names = [str(s) for s in betas] + [str(s) for s in gammas]
    return circuit, param_names


def bound_circuit(terms, n, betas, gammas):
    """The trained/fixed-angle circuit (no measurements)."""
    import cirq

    layers = len(betas)
    circuit, names = symbolic_circuit(terms, n, layers)
    resolver = cirq.ParamResolver(dict(zip(names, [*betas, *gammas])))
    return cirq.resolve_parameters(circuit, resolver)


def export(terms, n, betas, gammas):
    """Returns ``(qasm2_source, text_diagram, svg_or_empty)``."""
    from cirq.contrib.svg import circuit_to_svg

    circuit = bound_circuit(terms, n, betas, gammas)
    try:
        svg = circuit_to_svg(circuit)
    except Exception:
        svg = ""
    return circuit.to_qasm(), str(circuit), svg
