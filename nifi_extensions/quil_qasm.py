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

"""qasm2/qasm3 <-> pyquil Program translation, used by PyquilSimulator (input
translation) and PyquilGroverCircuit (output translation).

Modelled on braket_qasm.py, which solves the same "how does a non-Qiskit SDK
consume/produce OpenQASM" problem for Braket. The same lesson applies here:
never ship inline gate definitions in emitted QASM, and never assume an
importer's basis-gate set matches the source circuit's -- re-base through a
narrow, explicitly-supported gate set instead of trusting an opaque exporter.

Translating INTO pyquil (``to_quil_program``): re-use the Braket precedent --
parse the incoming qasm2/qasm3 with Qiskit, then `transpile` onto
``{h, cx, rz, x}``, a basis whose every member has a 1:1 Quil-native
counterpart (H, CNOT, RZ, X). Rather than calling Qiskit's own Quil/OpenQASM
exporter (none ships a direct-to-Quil backend anyway) or `pyquil.api`'s
`compiler.transpile_qasm_2` (which requires a running `quilc` server -- see
the module docstring rationale in PyquilSimulator.py for why that is a hard
no for this repo), the transpiled circuit's instructions are walked directly
and re-emitted as Quil text by hand, then re-parsed with `Program(...)` so
the result is a genuine pyquil object, not a string pretending to be one.

The returned Program carries an extra ``.num_qubits`` attribute set to the
circuit's full *declared* register width (`QuantumCircuit.num_qubits`, which
survives `transpile(..., optimization_level=0)` with no coupling map
unchanged), not just the highest qubit index a gate happens to reference.
Braket's engine silently drops untouched qubits from its counts (the
`ensure_full_register_measure` fix in braket_qasm.py); pyquil's own
`Program.get_qubit_indices()` has the identical failure mode for a program
built from gate objects alone (an idle qubit is never referenced by any
instruction, so it is invisible to the program). Carrying the declared width
explicitly, rather than re-deriving it from what the gates touch, is what
lets PyquilSimulator declare a full-width `ro` register and measure every
qubit regardless of whether the translated program ever gates it.

Translating OUT of pyquil (``to_qasm2``): emit the explicit standard-gate
subset used by the native builders. Reject gate modifiers, unresolved parameters
and non-gate instructions rather than silently changing their meaning.
"""

# Universal basis whose every member is a native Quil gate (cx as CNOT).
QUIL_SAFE_BASIS = ["h", "cx", "rz", "x"]

# The narrow, explicit gate set to_qasm2 is willing to emit. Anything else
# raises rather than risk emitting QASM that silently means something else.
_QASM2_SUPPORTED_GATES = {
    "H",
    "X",
    "Y",
    "Z",
    "RX",
    "RY",
    "RZ",
    "PHASE",
    "CPHASE",
    "CNOT",
    "CCNOT",
    "SWAP",
}


def to_quil_program(fmt, source):
    """Translate qasm2/qasm3 text into a pyquil Program.

    Parses with Qiskit, transpiles onto the Quil-safe basis {h, cx, rz, x},
    then re-emits the transpiled circuit as Quil text (by hand -- see module
    docstring) and parses that into a pyquil ``Program``. Measurements are
    stripped; callers add their own Declare/MEASURE for execution.

    Returns a pyquil Program with an extra ``.num_qubits`` attribute carrying
    the circuit's full declared register width. Raises ValueError on an
    unsupported format so callers can route to their failure relationship.
    """
    from qiskit import qasm2, qasm3, transpile

    if fmt == "qasm3":
        circuit = qasm3.loads(source)
    elif fmt == "qasm2":
        circuit = qasm2.loads(
            source, custom_instructions=qasm2.LEGACY_CUSTOM_INSTRUCTIONS
        )
    else:
        raise ValueError(
            "Unsupported circuit.format '{}'. Pyquil processors accept "
            "qasm2 or qasm3; set Output Format on the upstream circuit "
            "processor.".format(fmt)
        )

    circuit.remove_final_measurements()
    circuit = transpile(circuit, basis_gates=QUIL_SAFE_BASIS, optimization_level=0)

    lines = []
    for instruction in circuit.data:
        op = instruction.operation
        qubits = [circuit.find_bit(q).index for q in instruction.qubits]
        name = op.name
        if name == "h":
            lines.append("H {}".format(qubits[0]))
        elif name == "x":
            lines.append("X {}".format(qubits[0]))
        elif name == "cx":
            lines.append("CNOT {} {}".format(qubits[0], qubits[1]))
        elif name == "rz":
            theta = float(op.params[0])
            lines.append("RZ({!r}) {}".format(theta, qubits[0]))
        elif name in ("barrier", "delay", "id"):
            continue
        else:
            # transpile(..., basis_gates=QUIL_SAFE_BASIS) should make this
            # unreachable; a real hit means the transpile call itself needs
            # attention, not the caller's input.
            raise ValueError(
                "Unexpected gate '{}' survived transpiling to the "
                "Quil-safe basis {}".format(name, QUIL_SAFE_BASIS)
            )

    from pyquil import Program

    program = Program("\n".join(lines)) if lines else Program()
    program.num_qubits = circuit.num_qubits
    return program


def to_qasm2(program):
    """Translate a pyquil Program into OpenQASM 2.0 text.

    Covers the standard gates in _QASM2_SUPPORTED_GATES and rejects
    modifiers, symbolic angles, and non-gate instructions. Emits only standard qelib1.inc gate names; no gate
    definitions are shipped inline.
    """
    from pyquil.quilbase import Gate

    num_qubits = getattr(program, "num_qubits", None)
    if num_qubits is None:
        touched = program.get_qubit_indices()
        num_qubits = (max(touched) + 1) if touched else 0

    if not isinstance(num_qubits, int) or num_qubits < 1:
        raise ValueError("Program must declare at least one qubit")

    lines = [
        "OPENQASM 2.0;",
        'include "qelib1.inc";',
        "qreg q[{}];".format(num_qubits),
    ]
    for instr in program.instructions:
        if not isinstance(instr, Gate):
            # Declare/Measure/Pragma/... : callers pass an unmeasured,
            # gate-only Program (PyquilGroverCircuit builds no such
            # instruction), so seeing one here means something upstream
            # changed contract. Raise rather than silently drop it -- the
            # same "narrow and explicit" rule the gate-name check below
            # enforces.
            raise ValueError(
                "to_qasm2 only supports gate-only Programs; found a "
                "non-gate instruction: {}".format(instr)
            )
        name = instr.name
        if instr.modifiers:
            raise ValueError("to_qasm2 does not support gate modifiers")
        if name not in _QASM2_SUPPORTED_GATES:
            raise ValueError(f"to_qasm2 does not support Quil gate '{name}'")
        qs = [q.index for q in instr.qubits]
        if any(q < 0 or q >= num_qubits for q in qs):
            raise ValueError("Gate qubit outside declared register")
        args = ",".join(f"q[{q}]" for q in qs)
        mapped = {"CNOT": "cx", "CCNOT": "ccx", "PHASE": "u1", "CPHASE": "cu1"}.get(
            name, name.lower()
        )
        if name == "SWAP":
            a, b = qs
            lines.extend(
                [f"cx q[{a}],q[{b}];", f"cx q[{b}],q[{a}];", f"cx q[{a}],q[{b}];"]
            )
        elif name in {"RX", "RY", "RZ", "PHASE", "CPHASE"}:
            import math

            theta = complex(instr.params[0])
            if theta.imag or not math.isfinite(theta.real):
                raise ValueError("Gate angle must be finite and real")
            lines.append(f"{mapped}({theta.real!r}) {args};")
        else:
            lines.append(f"{mapped} {args};")
    return "\n".join(lines) + "\n"
