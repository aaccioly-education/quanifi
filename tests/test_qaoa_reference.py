"""M0: the independent reference (tests/qaoa_reference.py) agrees with the
*existing* PyquilQAOACircuit, and is discriminative against a set of
plausible bugs (mirrored Hamiltonian, wrong sign, swapped angles).

No NiFi/JVM: conftest.py stubs nifiapi.*.
"""

import numpy as np
import pytest

from pauli_dsl import diagonal_values
from qaoa_reference import (
    ANGLE_SETS,
    H_3BODY,
    H_ASYM,
    energies,
    fidelity,
    hamiltonian_ff,
    qasm2_state,
    reference_state,
    run,
    terms_of,
)


def _bit_reverse_state(psi, n):
    """Permute a state vector's basis ordering by reversing each index's bits."""
    out = np.zeros_like(psi)
    for i in range(2**n):
        j = int(format(i, "0{}b".format(n))[::-1], 2)
        out[j] = psi[i]
    return out


@pytest.mark.parametrize("spec", [H_ASYM, H_3BODY])
@pytest.mark.parametrize("layers,betas,gammas", ANGLE_SETS)
def test_pyquil_builder_matches_reference_state(spec, layers, betas, gammas):
    terms, n = terms_of(spec)
    ff = hamiltonian_ff(spec)
    res = run(
        "PyquilQAOACircuit",
        {"Layers": str(layers), "Betas": str(betas), "Gammas": str(gammas)},
        ff,
    )
    assert res.relationship == "success", res.attributes
    state = qasm2_state(res.attributes["circuit.qasm2"])
    ref = reference_state(terms, n, betas, gammas)
    assert fidelity(state, ref) >= 1 - 1e-9


def test_energies_matches_diagonal_values_after_bit_reversal():
    terms, n = terms_of(H_ASYM)
    direct = energies(terms, n)
    values = diagonal_values(terms, n)
    # diagonal_values indexes LSB=q0; energies indexes q0-left (bit-reversed).
    reversed_values = np.array(
        [values[int(format(i, "0{}b".format(n))[::-1], 2)] for i in range(2**n)]
    )
    np.testing.assert_allclose(direct, reversed_values)


def test_reference_is_discriminative_on_h_asym_p2():
    terms, n = terms_of(H_ASYM)
    betas, gammas = [0.37, -0.52], [0.81, 1.23]
    ref = reference_state(terms, n, betas, gammas)

    negated = reference_state(terms, n, betas, [-g for g in gammas])
    assert fidelity(ref, negated) < 0.6

    swapped = reference_state(terms, n, gammas, betas)
    assert fidelity(ref, swapped) < 0.6

    bit_reversed = _bit_reverse_state(ref, n)
    assert fidelity(ref, bit_reversed) < 0.6
