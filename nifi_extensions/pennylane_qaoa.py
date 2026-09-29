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

"""PennyLane QAOA circuit construction plus native qasm2 export (not a
processor). Shared by ``PennylaneQAOACircuit`` (fixed angles) and
``PennylaneQAOA`` (trained angles).

Built on ``qml.qaoa``'s ``cost_layer``/``mixer_layer``/``x_mixer``: PennyLane
supplies the QAOA layer structure, not a full solver. The RX mixer is
exported by PennyLane as ``h . rz . h``, never a bare ``rx`` gate.
"""


def cost_hamiltonian(terms):
    """Neutral (pauli, indices, coeff) terms -> a qml.Hamiltonian. Identity
    terms become ``qml.Identity(0)`` (they shift the energy but not the
    circuit's own action)."""
    import pennylane as qml

    observables, coeffs = [], []
    for pauli, indices, coeff in terms:
        if not pauli:
            observables.append(qml.Identity(0))
        else:
            obs = qml.Z(indices[0])
            for idx in indices[1:]:
                obs = obs @ qml.Z(idx)
            observables.append(obs)
        coeffs.append(float(coeff))
    return qml.Hamiltonian(coeffs, observables)


def apply(betas, gammas, cost_h, n):
    """H^n then, per layer, the qml.qaoa cost and (RX/transverse-field)
    mixer layers."""
    import pennylane as qml

    mixer_h = qml.qaoa.x_mixer(range(n))
    for w in range(n):
        qml.Hadamard(wires=w)
    for beta, gamma in zip(betas, gammas):
        qml.qaoa.cost_layer(gamma, cost_h)
        qml.qaoa.mixer_layer(beta, mixer_h)


def energy_function(terms, n, layers):
    """A default.qubit qnode: flat ``[beta..., gamma...]`` -> exact
    ``<cost_h>``."""
    import pennylane as qml

    cost_h = cost_hamiltonian(terms)
    dev = qml.device("default.qubit", wires=n)

    @qml.qnode(dev)
    def qnode(params):
        betas, gammas = params[:layers], params[layers:]
        apply(betas, gammas, cost_h, n)
        return qml.expval(cost_h)

    return qnode


def _strip_measurements(qasm):
    """Drop the trailing measure/creg lines PennyLane's to_openqasm always
    emits. Duplicated from ``PennylaneGroverCircuit`` rather than imported --
    NiFi loads each processor module independently, and a sibling import
    would make the processor a ghost component on the canvas."""
    kept = [
        ln for ln in qasm.splitlines() if not ln.strip().startswith(("measure", "creg"))
    ]
    return "\n".join(kept) + "\n"


def _strip_global_phase(qasm):
    """Remove OpenQASM 3 ``gphase`` statements, which OpenQASM 2.0 does not
    define; sound to drop for a directly-sampled circuit (see
    ``PennylaneGroverCircuit`` for the full rationale). Returns the cleaned
    source and the number of statements removed."""
    kept, dropped = [], 0
    for ln in qasm.splitlines():
        if ln.strip().startswith("gphase"):
            dropped += 1
            continue
        kept.append(ln)
    return "\n".join(kept) + "\n", dropped


def export(terms, n, betas, gammas):
    """Returns ``(qasm2_source, text_diagram, global_phase_dropped)``."""
    import pennylane as qml

    cost_h = cost_hamiltonian(terms)
    dev = qml.device("default.qubit", wires=n)

    @qml.qnode(dev)
    def qnode():
        apply(betas, gammas, cost_h, n)
        return qml.state()

    raw = qml.to_openqasm(qnode, measure_all=False)()
    qasm, dropped = _strip_global_phase(_strip_measurements(raw))
    diagram = qml.draw(qnode)()
    return qasm, diagram, dropped > 0
