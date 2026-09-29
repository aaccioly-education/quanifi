"""Qrisp QAOA circuit construction plus native qasm2 export (not a
processor). Shared by ``QrispQAOACircuit`` (fixed angles) and ``QrispQAOA``
(trained angles).

Qrisp compiles each ``QuantumVariable`` into its own one-qubit register
(``qreg qv_0[1]; qreg qv_1[1]; ...``); ``export`` normalises that into a
single ``qreg q[n]`` by composing into a fresh ``QuantumCircuit(n)``, which is
what makes the output a portable NxM interchange row. The XY mixer emits
``s``/``sdg``/``sx``/``sxdg``, none of which are in the portable subset, so
those outputs are rebased onto ``{h, x, rx, ry, rz, cx}``; the RX mixer's
output is already portable and is left untouched (rebasing changes its
emitted text).
"""

from qaoa_contract import PORTABLE_GATES


def cost_operator(terms):
    """Phase-separation function U_P(C, gamma): CNOT ladder + Rz per
    Z-string term. Identity terms only shift the energy and are skipped."""
    from qrisp import cx, rz

    def op(qv, gamma):
        for pauli, indices, coeff in terms:
            if not pauli:
                continue
            for a, b in zip(indices, indices[1:]):
                cx(qv[a], qv[b])
            rz(2.0 * coeff * gamma, qv[indices[-1]])
            for a, b in reversed(list(zip(indices, indices[1:]))):
                cx(qv[a], qv[b])

    return op


def mixer(name):
    """RX (transverse field) or XY (Hamming-weight-preserving ring) mixer."""
    if name == "XY":
        from qrisp.qaoa import XY_mixer

        return XY_mixer
    from qrisp.qaoa import RX_mixer

    return RX_mixer


def prepare(qv, terms, betas, gammas, mixer_name):
    """H^n then, per layer, the cost operator and the selected mixer."""
    from qrisp import h

    cost = cost_operator(terms)
    mix = mixer(mixer_name)
    h(qv)
    for beta, gamma in zip(betas, gammas):
        cost(qv, gamma)
        mix(qv, beta)


def export(terms, n, betas, gammas, mixer_name):
    """Returns ``(qasm2_source, text_diagram, rebased)``."""
    from qiskit import QuantumCircuit, qasm2, transpile
    from qrisp import QuantumVariable

    qv = QuantumVariable(n)
    prepare(qv, terms, betas, gammas, mixer_name)
    qk = qv.qs.compile().to_qiskit()
    if qk.num_qubits != n:
        raise ValueError(
            "Qrisp compiled circuit has {} qubits, expected {}".format(qk.num_qubits, n)
        )
    single = QuantumCircuit(n)
    single.compose(qk, qubits=list(range(n)), inplace=True)
    rebased = False
    if set(single.count_ops()) - PORTABLE_GATES:
        single = transpile(
            single, basis_gates=["h", "x", "rx", "ry", "rz", "cx"], optimization_level=0
        )
        rebased = True
    return qasm2.dumps(single), str(single.draw("text")), rebased


def to_qrisp_theta(betas, gammas):
    """Canonical [betas..., gammas...] -> Qrisp's internal [gammas...,
    betas...] parameter order."""
    return [*gammas, *betas]


def from_qrisp_theta(theta, layers):
    """Qrisp's internal parameter order -> canonical (betas, gammas)."""
    return list(theta[layers:]), list(theta[:layers])
