import json
import os
import re
import time
from collections import Counter

from nifiapi.flowfiletransform import FlowFileTransform, FlowFileTransformResult
from nifiapi.properties import PropertyDescriptor, StandardValidators, ExpressionLanguageScope
from nifiapi.__jvm__ import JvmHolder


class QSharpSimulator(FlowFileTransform):
    """Run OpenQASM circuits with the Microsoft QDK local simulator.

    OpenQASM 2 and 3 are consumed **natively** by the QDK's own parser
    (``SymbolTable::new_qasm2`` / its built-in stdgates support) - there is
    no cross-framework translation step through Qiskit, and Qiskit is not a
    dependency of this processor. Circuit introspection (qubit count,
    measurement detection, and the Clifford safety guard) is done with the
    QDK's own `qdk.openqasm.circuit()` circuit-synthesis API.
    """

    class Java:
        implements = ['org.apache.nifi.python.processor.FlowFileTransform']

    class ProcessorDetails:
        version = "0.2.0"
        description = (
            "Reads an OpenQASM 2 or 3 circuit natively via the Microsoft QDK "
            "(no cross-framework translation through Qiskit), adds a "
            "full-register measurement when one isn't already present, and "
            "runs it with the QDK sparse-state or Clifford simulator. Emits "
            "canonical q0-left counts and optionally applies Pauli noise."
        )
        tags = ["quantum", "qsharp", "qdk", "simulation", "measurement", "noise"]
        dependencies = [
            "qdk>=1.30,<1.31",
        ]

    def __init__(self, **kwargs):
        JvmHolder.jvm = kwargs.get('jvm')
        super().__init__()

        self.shots = PropertyDescriptor(
            name="Shots",
            description="Number of times to sample the circuit.",
            required=True,
            default_value="1024",
            validators=[StandardValidators.POSITIVE_INTEGER_VALIDATOR],
            expression_language_scope=ExpressionLanguageScope.FLOWFILE_ATTRIBUTES,
        )
        self.simulator_type = PropertyDescriptor(
            name="Simulator Type",
            description=(
                "QDK simulation engine. 'sparse' supports general circuits; "
                "'clifford' is efficient but accepts only Clifford circuits."
            ),
            required=True,
            default_value="sparse",
            allowable_values=["sparse", "clifford"],
            expression_language_scope=ExpressionLanguageScope.FLOWFILE_ATTRIBUTES,
        )
        self.noise_model = PropertyDescriptor(
            name="Noise Model",
            description=(
                "Pauli noise applied after gates and before measurements by the "
                "QDK sparse-state simulator."
            ),
            required=True,
            default_value="none",
            allowable_values=["none", "pauli", "bit_flip", "phase_flip", "depolarizing"],
            expression_language_scope=ExpressionLanguageScope.FLOWFILE_ATTRIBUTES,
        )
        self.error_probability = PropertyDescriptor(
            name="Error Probability",
            description=(
                "Error probability for bit_flip, phase_flip, or depolarizing noise."
            ),
            required=False,
            default_value="0.01",
            expression_language_scope=ExpressionLanguageScope.FLOWFILE_ATTRIBUTES,
        )
        self.pauli_x = PropertyDescriptor(
            name="Pauli X Probability",
            description="Pauli-X probability when Noise Model is pauli.",
            required=False,
            default_value="0.001",
            expression_language_scope=ExpressionLanguageScope.FLOWFILE_ATTRIBUTES,
        )
        self.pauli_y = PropertyDescriptor(
            name="Pauli Y Probability",
            description="Pauli-Y probability when Noise Model is pauli.",
            required=False,
            default_value="0.001",
            expression_language_scope=ExpressionLanguageScope.FLOWFILE_ATTRIBUTES,
        )
        self.pauli_z = PropertyDescriptor(
            name="Pauli Z Probability",
            description="Pauli-Z probability when Noise Model is pauli.",
            required=False,
            default_value="0.001",
            expression_language_scope=ExpressionLanguageScope.FLOWFILE_ATTRIBUTES,
        )
        self.random_seed = PropertyDescriptor(
            name="Random Seed",
            description="Seed for reproducible QDK sampling. Empty = nondeterministic.",
            required=False,
            default_value="",
            expression_language_scope=ExpressionLanguageScope.FLOWFILE_ATTRIBUTES,
        )
        self.descriptors = [
            self.shots,
            self.simulator_type,
            self.random_seed,
            self.noise_model,
            self.error_probability,
            self.pauli_x,
            self.pauli_y,
            self.pauli_z,
        ]

    def getPropertyDescriptors(self):
        return self.descriptors

    def _prop(self, context, descriptor, flowFile):
        return (
            context.getProperty(descriptor)
            .evaluateAttributeExpressions(flowFile)
            .getValue()
        )

    def _fail(self, message):
        self.logger.error("QSharpSimulator: " + message)
        return FlowFileTransformResult(
            relationship="failure",
            contents=b"",
            attributes={"sim.error": message},
        )

    @staticmethod
    def _probability(raw, name):
        value = float(raw)
        if not 0.0 <= value <= 1.0:
            raise ValueError("{} must be between 0 and 1".format(name))
        return value

    def _build_noise(self, context, flowFile, kind):
        from qdk import BitFlipNoise, DepolarizingNoise, PauliNoise, PhaseFlipNoise

        if kind == "none":
            return None, {"sim.noise_model": "none"}

        if kind == "pauli":
            x = self._probability(self._prop(context, self.pauli_x, flowFile), "Pauli X Probability")
            y = self._probability(self._prop(context, self.pauli_y, flowFile), "Pauli Y Probability")
            z = self._probability(self._prop(context, self.pauli_z, flowFile), "Pauli Z Probability")
            if x + y + z > 1.0:
                raise ValueError("Pauli X, Y, and Z probabilities must sum to at most 1")
            params = {"x": x, "y": y, "z": z}
            noise = PauliNoise(x, y, z)
        else:
            p = self._probability(
                self._prop(context, self.error_probability, flowFile),
                "Error Probability",
            )
            params = {"probability": p}
            noise_types = {
                "bit_flip": BitFlipNoise,
                "phase_flip": PhaseFlipNoise,
                "depolarizing": DepolarizingNoise,
            }
            if kind not in noise_types:
                raise ValueError("unsupported noise model '{}'".format(kind))
            noise = noise_types[kind](p)

        return noise, {
            "sim.noise_model": kind,
            "sim.noise_params": json.dumps(params, sort_keys=True),
        }

    # Clifford-safe leaf gate names, keyed to how many controls each may carry
    # (as reported by the QDK circuit-synthesis JSON's "controls" list). H and
    # S are single-qubit-only in the Clifford group (a controlled-H/controlled-S
    # is not Clifford); X/Y/Z cover both bare Paulis (0 controls) and CX/CY/CZ
    # (1 control); SWAP is Clifford only when uncontrolled (a controlled-SWAP /
    # Fredkin gate is not).
    _CLIFFORD_SINGLE_ONLY = {"H", "S"}
    _CLIFFORD_PAULI = {"X", "Y", "Z"}
    _CLIFFORD_SWAP = {"SWAP"}

    # Narrow, single-purpose regexes used ONLY to recover the declared name of
    # a lone qubit register so a full-register measurement can be appended in
    # pure Python when a circuit has none (see `_analyze_qasm`). They are not
    # used to parse gates or validate the program - qubit count and gate
    # structure both come from the QDK's own `circuit()` API instead.
    _QASM2_QREG_RE = re.compile(r"(?m)^\s*qreg\s+(\w+)\s*\[\s*(\d+)\s*\]\s*;")
    _QASM3_QUBIT_ARRAY_RE = re.compile(r"(?m)^\s*qubit\s*\[\s*(\d+)\s*\]\s*(\w+)\s*;")
    _QASM3_QUBIT_SCALAR_RE = re.compile(r"(?m)^\s*qubit\s+(\w+)\s*;")

    # Used ONLY to strip a *partial*-register measurement so it can be replaced
    # by a whole-register one (see `_analyze_qasm`). Quanifi's simulators are
    # compared against each other by the distribution oracle, so every engine
    # must report counts over the same full register; a circuit that measures
    # only some of its qubits would otherwise make this engine emit narrower
    # bitstrings than Aer/Cirq/Braket for the identical circuit.
    _MEASURE_STMT_RE = re.compile(
        r"(?m)^\s*(?:measure\s+[^;]+;|[\w\[\]]+\s*=\s*measure\s+[^;]+;)\s*$\n?"
    )
    _CLASSICAL_DECL_RE = re.compile(
        r"(?m)^\s*(?:creg\s+\w+\s*\[\s*\d+\s*\]\s*;|bit\s*(?:\[\s*\d+\s*\])?\s*\w+\s*;)\s*$\n?"
    )

    @classmethod
    def _check_clifford_leaf(cls, gate_name, num_controls):
        if gate_name in cls._CLIFFORD_SINGLE_ONLY:
            return num_controls == 0
        if gate_name in cls._CLIFFORD_PAULI:
            return num_controls <= 1
        if gate_name in cls._CLIFFORD_SWAP:
            return num_controls == 0
        return False

    @classmethod
    def _scan_components(cls, components, check_clifford):
        """Recursively walk a QDK `circuit().json()` component tree.

        Returns ``(measured_qubits, violations)``. `measured_qubits` is the set
        of qubit indices carrying a measurement, which lets `_analyze_qasm`
        tell a whole-register measurement (pass through untouched) from a
        partial one (must be normalised). `violations` lists the
        disallowed gate names found (only populated when `check_clifford` is
        True). Container entries - the top-level "program" scope, and any
        user-defined `gate ... { ... }` call - carry a "children" list that
        the QDK has already expanded into primitive leaves, so they are
        recursed into rather than judged by their own (non-primitive) name;
        this lets a custom gate built entirely from Clifford primitives pass,
        instead of the coarser "reject every custom gate" fallback.
        """
        measured_qubits = set()
        violations = []
        for entry in components:
            for comp in entry.get("components", []):
                children = comp.get("children")
                if children:
                    child_measured, child_violations = cls._scan_components(
                        children, check_clifford
                    )
                    measured_qubits |= child_measured
                    violations.extend(child_violations)
                    continue

                kind = comp.get("kind")
                if kind == "measurement":
                    for target in comp.get("qubits", []):
                        index = target.get("qubit")
                        if index is not None:
                            measured_qubits.add(index)
                elif kind == "ket":
                    # State preparation to a computational basis state (e.g. a
                    # `reset`) - stabilizer-safe regardless of simulator type.
                    pass
                elif kind != "unitary":
                    # Any other/unrecognised component kind: fail closed
                    # rather than let something unvalidated reach the native
                    # Clifford simulator.
                    if check_clifford:
                        violations.append("unsupported circuit element '{}'".format(kind))
                elif check_clifford:
                    gate_name = comp.get("gate", "")
                    num_controls = len(comp.get("controls", []))
                    if not cls._check_clifford_leaf(gate_name, num_controls):
                        violations.append(gate_name)
        return measured_qubits, violations

    @classmethod
    def _find_single_qubit_register(cls, raw, fmt):
        """Return (name, width) for the lone declared qubit register, or None.

        Returns None when zero or multiple qubit registers are declared -
        the multi-register case is deliberately left unhandled here (see
        `_analyze_qasm`): auto-appending an unambiguous full-register
        measurement is only attempted for the common single-register shape
        Quanifi's circuit builders actually emit.
        """
        if fmt == "qasm2":
            matches = cls._QASM2_QREG_RE.findall(raw)
            if len(matches) != 1:
                return None
            name, width = matches[0]
            return name, int(width)

        array_matches = cls._QASM3_QUBIT_ARRAY_RE.findall(raw)
        scalar_matches = cls._QASM3_QUBIT_SCALAR_RE.findall(raw)
        total = len(array_matches) + len(scalar_matches)
        if total != 1:
            return None
        if array_matches:
            width, name = array_matches[0]
            return name, int(width)
        return scalar_matches[0], 1

    @classmethod
    def _analyze_qasm(cls, raw, fmt, require_clifford=False):
        """Inspect (and, if needed, minimally augment) OpenQASM for the QDK.

        Returns ``(source, num_qubits)``. `source` is `raw` unchanged when it
        already measures every qubit - the common builder shape, verified to
        reproduce the pre-change QDK counts bit-for-bit. When `raw` measures
        only *some* of its qubits, or none at all, a whole-register
        measurement is substituted in pure Python, reproducing the
        `remove_final_measurements()` + `measure_all()` normalisation the
        Qiskit hop used to perform. That normalisation is a cross-simulator
        contract, not an implementation detail: the distribution oracle
        compares this engine's counts against Aer/Cirq/Braket for the same
        circuit, so all of them must report over the same full register.

        Introspection uses `qdk.openqasm.circuit()` - the QDK's own
        circuit-synthesis API, NOT the executing sparse/Clifford *simulator*
        backend that has been observed to SIGSEGV on non-Clifford input under
        `run(type="clifford")`. Empirically, `circuit()` handles non-Clifford
        gates (T, arbitrary rotations, Toffoli) without incident, which is
        what makes it safe to use here as the pre-flight Clifford guard - see
        `_scan_components`. Qiskit is not used anywhere in this path.
        """
        if fmt not in ("qasm2", "qasm3"):
            raise ValueError(
                "unsupported circuit.format '{}' (expected qasm2 or qasm3)".format(fmt)
            )

        from qdk.openqasm import circuit as qdk_circuit

        parsed = qdk_circuit(raw)
        info = json.loads(parsed.json())
        num_qubits = len(info.get("qubits", []))

        measured_qubits, violations = cls._scan_components(
            info.get("componentGrid", []), require_clifford
        )
        if require_clifford and violations:
            raise ValueError(
                "Simulator Type = clifford requires a Clifford-only circuit "
                "(H, S, X/Y/Z, CX/CZ/SWAP and measurement); use sparse for "
                "Grover, arbitrary rotations, T gates, or multi-controlled "
                "operations"
            )

        # Already measures the whole register: hand the QDK the untouched
        # source, which is the path verified bit-for-bit against the old
        # Qiskit hop.
        if len(measured_qubits) == num_qubits:
            return raw, num_qubits

        register = cls._find_single_qubit_register(raw, fmt)
        if register is None:
            raise ValueError(
                "circuit does not measure every qubit and its qubit "
                "register(s) could not be unambiguously identified to add "
                "a full-register measurement automatically; add explicit "
                "measurements (e.g. 'measure q -> c;') before running"
            )
        # Drop the partial measurement and the classical registers it wrote
        # into, so the whole-register measurement appended below is the only
        # classical output and the bitstring width matches num_qubits.
        if measured_qubits:
            raw = cls._CLASSICAL_DECL_RE.sub("", cls._MEASURE_STMT_RE.sub("", raw))
        name, width = register
        if width != num_qubits:
            raise ValueError(
                "circuit has no measurement statements and its declared "
                "qubit register width ({}) does not match the parsed qubit "
                "count ({}); add explicit measurements before running".format(
                    width, num_qubits
                )
            )

        # Reserved, double-underscore-prefixed name to avoid colliding with
        # any register a builder already declared.
        meas_reg = "__qsharp_meas"
        if fmt == "qasm2":
            augmented = "{}\ncreg {}[{}];\nmeasure {} -> {};\n".format(
                raw, meas_reg, num_qubits, name, meas_reg
            )
        else:
            augmented = "{}\nbit[{}] {};\n{} = measure {};\n".format(
                raw, num_qubits, meas_reg, meas_reg, name
            )
        return augmented, num_qubits

    @staticmethod
    def _result_key(result):
        if isinstance(result, str):
            return result.replace(" ", "")
        if isinstance(result, (list, tuple)):
            return "".join(QSharpSimulator._result_key(item) for item in result)
        if result in (0, 1):
            return str(result)
        raise ValueError("QDK returned an unsupported measurement value: {!r}".format(result))

    def transform(self, context, flowFile):
        started = time.perf_counter()
        try:
            shots = int(self._prop(context, self.shots, flowFile))
            simulator_type = (self._prop(context, self.simulator_type, flowFile) or "sparse").strip()
            noise_kind = (self._prop(context, self.noise_model, flowFile) or "none").strip()
            seed_raw = (self._prop(context, self.random_seed, flowFile) or "").strip()
            seed = int(seed_raw) if seed_raw else None

            fmt = flowFile.getAttribute("circuit.format")
            if not fmt:
                raise ValueError("missing circuit.format (expected qasm2 or qasm3)")
            raw = bytes(flowFile.getContentsAsBytes()).decode("utf-8")

            # The core qdk import is intentionally lazy: NiFi installs each
            # processor's dependencies in an isolated environment on first use.
            # Quanifi simulations must not emit package telemetry by default.
            # Set before the first qdk import below (inside _analyze_qasm).
            os.environ.setdefault("QDK_PYTHON_TELEMETRY", "none")
            source, num_qubits = self._analyze_qasm(
                raw,
                fmt,
                require_clifford=simulator_type == "clifford",
            )

            if simulator_type == "clifford" and noise_kind != "none":
                raise ValueError(
                    "Pauli noise modes are supported only by the sparse simulator; "
                    "use Noise Model = none with clifford"
                )

            noise, noise_attrs = self._build_noise(context, flowFile, noise_kind)

            from qdk.openqasm import run

            run_kwargs = {
                "shots": shots,
                "as_bitstring": True,
                "type": simulator_type,
            }
            if simulator_type == "clifford":
                run_kwargs["num_qubits"] = num_qubits
            if seed is not None:
                run_kwargs["seed"] = seed
            if noise is not None:
                run_kwargs["noise"] = noise

            results = run(source, **run_kwargs)
            if isinstance(results, str):
                results = [results]
            counts = Counter(self._result_key(result) for result in results)
            if not counts:
                raise ValueError("QDK simulation returned no measurement results")

            sorted_counts = dict(sorted(counts.items(), key=lambda item: (-item[1], item[0])))
            top_state, top_count = next(iter(sorted_counts.items()))
            elapsed = time.perf_counter() - started

            return FlowFileTransformResult(
                relationship="success",
                contents=json.dumps(sorted_counts, indent=2).encode("utf-8"),
                attributes={
                    "sim.shots": str(shots),
                    "sim.top_result": top_state,
                    "sim.top_probability": "{:.4f}".format(top_count / shots),
                    "sim.framework": "qsharp",
                    "sim.component": "QSharpSimulator",
                    "sim.backend": simulator_type,
                    "sim.bit_order": "q0_left",
                    "report.type": "simulation",
                    "perf.elapsed_seconds": "{:.4f}".format(elapsed),
                    **({"run.seed": str(seed)} if seed is not None else {}),
                    **noise_attrs,
                },
            )
        except Exception as exc:
            return self._fail(str(exc))
