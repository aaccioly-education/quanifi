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

"""Native pyQuil circuit building and local variational execution.

No NiFi API and no provider calls. Hamiltonian labels follow the existing
MSB-first wire format; externally reported bitstrings always put qubit 0 left.
"""

import itertools
import json
import math
from collections import Counter

MAX_QUBITS = 16
MAX_GROVER_QUBITS = 8  # phase-polynomial synthesis is exponential


def integer(value, name, minimum=1, maximum=MAX_QUBITS):
    # Reject nonintegral JSON values instead of truncating them.
    if isinstance(value, bool) or str(value).strip() != str(int(value)):
        raise ValueError(f"{name} must be an integer")
    number = int(value)
    if not minimum <= number <= maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")
    return number


def angles(value, size, name="Parameters"):
    import numpy as np

    if isinstance(value, str):
        value = json.loads(value) if value.strip().startswith("[") else value.split(",")
    result = np.asarray(value, dtype=float)
    if result.shape != (size,) or not np.all(np.isfinite(result)):
        raise ValueError(f"{name} must contain exactly {size} finite numbers")
    return result


def marked_bits(value):
    if not isinstance(value, str) or not value or set(value) - {"0", "1"}:
        raise ValueError("Marked State must be a nonempty binary string")
    integer(len(value), "Grover qubits", maximum=MAX_GROVER_QUBITS)
    return value


def mcz_ops(qubits):
    """All-ones phase flip, up to global phase; no ancillas.

    For n>3, expand pi*AND(x) in parity characters. RZ(a) contributes
    -a/2 times the parity character to the phase. The implemented angles
    therefore produce -pi*AND(x) plus a constant, equivalent to a phase flip.
    Cost is O(n*2**n); small widths use exact Z/CZ/CCZ identities.
    """
    from pyquil.gates import CCNOT, CNOT, H, RZ, Z

    n = len(qubits)
    if n == 1:
        return [Z(qubits[0])]
    if n == 2:
        a, b = qubits
        return [H(b), CNOT(a, b), H(b)]
    if n == 3:
        a, b, c = qubits
        return [H(c), CCNOT(a, b, c), H(c)]
    ops = []
    for size in range(1, n + 1):
        angle = math.pi / 2 ** (n - 1) * (-1) ** size
        for subset in itertools.combinations(qubits, size):
            target = subset[-1]
            ops.extend(CNOT(q, target) for q in subset[:-1])
            ops.append(RZ(angle, target))
            ops.extend(CNOT(q, target) for q in reversed(subset[:-1]))
    return ops


def phase_oracle_ops(qubits, target):
    from pyquil.gates import X

    flips = [X(q) for q, bit in zip(qubits, target) if bit == "0"]
    return flips + mcz_ops(qubits) + flips


def diffusion_ops(qubits):
    """Reflection about the uniform state, up to global phase."""
    from pyquil.gates import H, X

    hs, xs = [H(q) for q in qubits], [X(q) for q in qubits]
    return hs + xs + mcz_ops(qubits) + xs + hs


def phase_oracle(target):
    from pyquil import Program

    marked_bits(target)
    program = Program(phase_oracle_ops(range(len(target)), target))
    program.num_qubits = len(target)
    return program


def grover(oracle, iterations):
    from pyquil import Program
    from pyquil.gates import H

    n = integer(oracle.num_qubits, "Grover qubits", maximum=MAX_GROVER_QUBITS)
    iterations = integer(iterations, "Num Iterations", minimum=0, maximum=100)
    program = Program([H(q) for q in range(n)])
    for _ in range(iterations):
        program += oracle
        program += diffusion_ops(range(n))
    program.num_qubits = n
    return program


def circuit_depth(instructions):
    last = {}
    for instr in instructions:
        qs = [q.index for q in instr.qubits]
        depth = max((last.get(q, 0) for q in qs), default=0) + 1
        for q in qs:
            last[q] = depth
    return max(last.values(), default=0)


def hamiltonian(data):
    """Validate the neutral wire format BEFORE its permissive shared decoder."""
    from pauli_dsl import wire_to_terms

    if isinstance(data, str):
        data = json.loads(data)
    if not isinstance(data, dict):
        raise ValueError("Hamiltonian must be a sparse_pauli_op_json object")
    n = integer(data["num_qubits"], "Hamiltonian qubits")
    entries = data.get("terms")
    if not isinstance(entries, list) or not 1 <= len(entries) <= 4096:
        raise ValueError("Hamiltonian requires 1..4096 terms")
    for term in entries:
        if not isinstance(term, list) or len(term) != 3:
            raise ValueError("Each Hamiltonian term must be [label, real, imaginary]")
        label, real, imag = term
        if not isinstance(label, str) or len(label) != n or set(label) - set("IXYZ"):
            raise ValueError("Pauli label must contain I/X/Y/Z and match num_qubits")
        if not math.isfinite(float(real)) or not math.isfinite(float(imag)):
            raise ValueError("Hamiltonian coefficients must be finite")
        if float(imag) != 0:
            raise ValueError("Hamiltonian coefficients must be real (Hermitian)")
    return wire_to_terms(data)


def pauli_sum(terms):
    from pyquil.paulis import PauliSum, PauliTerm

    operators = []
    for pauli, indices, coeff in terms:
        term = PauliTerm("I", 0, coeff)
        for p, q in zip(pauli, indices):
            term *= PauliTerm(p, q)
        operators.append(term)
    return PauliSum(operators)


def simulate(program):
    from pyquil.simulation import NumpyWavefunctionSimulator

    n = integer(program.num_qubits, "Simulation qubits")
    return NumpyWavefunctionSimulator(n_qubits=n).do_program(program)


def expectation(program, terms):
    return float(simulate(program).expectation(pauli_sum(terms)).real)


def sample(program, shots, seed):
    """Sample the exact local pyQuil wavefunction; axes are q0,q1,... ."""
    import numpy as np

    shots = integer(shots, "Shots", maximum=1_000_000)
    state = simulate(program).wf.reshape(-1)
    probabilities = np.abs(state) ** 2
    probabilities /= probabilities.sum()
    values = np.random.default_rng(seed).choice(len(state), shots, p=probabilities)
    counts = Counter(format(int(i), f"0{program.num_qubits}b") for i in values)
    return dict(sorted(counts.items(), key=lambda item: (-item[1], item[0])))


def ansatz_recipe(n, reps, rotations, entanglement):
    n = integer(n, "Num Qubits")
    reps = integer(reps, "Reps", minimum=0, maximum=16)
    if rotations not in ("ry", "ry_rz"):
        raise ValueError("Rotations must be ry or ry_rz")
    if entanglement not in ("linear", "full", "none"):
        raise ValueError("Entanglement must be linear, full or none")
    return dict(
        version=1,
        num_qubits=n,
        reps=reps,
        rotations=rotations,
        entanglement=entanglement,
    )


def read_recipe(raw):
    r = json.loads(raw)
    if r.get("version") != 1:
        raise ValueError("Unsupported ansatz.pyquil_json version")
    return ansatz_recipe(r["num_qubits"], r["reps"], r["rotations"], r["entanglement"])


def parameter_count(recipe):
    return (
        recipe["num_qubits"]
        * (recipe["reps"] + 1)
        * (2 if recipe["rotations"] == "ry_rz" else 1)
    )


def ansatz(recipe, parameters):
    from pyquil import Program
    from pyquil.gates import CNOT, RY, RZ

    values = iter(angles(parameters, parameter_count(recipe)))
    n, reps = recipe["num_qubits"], recipe["reps"]
    pairs = (
        [(i, i + 1) for i in range(n - 1)]
        if recipe["entanglement"] == "linear"
        else (
            list(itertools.combinations(range(n), 2))
            if recipe["entanglement"] == "full"
            else []
        )
    )
    program = Program()
    for layer in range(reps + 1):
        for q in range(n):
            program += RY(float(next(values)), q)
            if recipe["rotations"] == "ry_rz":
                program += RZ(float(next(values)), q)
        if layer < reps:
            program += [CNOT(a, b) for a, b in pairs]
    program.num_qubits = n
    return program


def qaoa(terms, n, betas, gammas):
    from pyquil import Program
    from pyquil.gates import CNOT, H, RX, RZ
    from pauli_dsl import is_diagonal

    integer(n, "Num Qubits")
    if not is_diagonal(terms):
        raise ValueError("QAOA cost must be diagonal (I/Z terms only)")
    layers = integer(len(betas), "Layers", maximum=16)
    betas, gammas = angles(betas, layers, "Betas"), angles(gammas, layers, "Gammas")
    program = Program([H(q) for q in range(n)])
    for beta, gamma in zip(betas, gammas):
        for pauli, indices, coeff in terms:
            if not pauli:  # identity changes energy but only global circuit phase
                continue
            target = indices[-1]
            program += [CNOT(q, target) for q in indices[:-1]]
            program += RZ(float(2 * gamma * coeff), target)
            program += [CNOT(q, target) for q in reversed(indices[:-1])]
        program += [RX(float(2 * beta), q) for q in range(n)]
    program.num_qubits = n
    return program


def qft(n, inverse=False, swaps=True):
    """QFT on q0-left integers: exp(+2*pi*i*x*y/2**n)/sqrt(2**n)."""
    from pyquil import Program
    from pyquil.gates import CPHASE, H, SWAP

    n = integer(n, "Num Qubits")
    program = Program()
    for q in range(n):
        program += H(q)
        for control in range(q + 1, n):
            program += CPHASE(math.pi / 2 ** (control - q), control, q)
    if swaps:
        program += [SWAP(q, n - 1 - q) for q in range(n // 2)]
    if inverse:
        reversed_ops = []
        for gate in reversed(program.instructions):
            qs = [q.index for q in gate.qubits]
            reversed_ops.append(
                CPHASE(-float(gate.params[0].real), *qs)
                if gate.name == "CPHASE"
                else gate
            )
        program = Program(reversed_ops)
    program.num_qubits = n
    return program
