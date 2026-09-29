"""
Tests for reporting processors: QiskitCircuitReport, QuantumDistributionComparison,
QiskitStatevectorSimulator.

All tests that write files use pytest's tmp_path fixture to avoid polluting
the real reports directory.
"""

import json
import os

import pytest

from QiskitCircuitReport import QiskitCircuitReport
from QuantumDistributionComparison import QuantumDistributionComparison
from QiskitStatevectorSimulator import QiskitStatevectorSimulator
from QuanifiReport import QuanifiReport

from conftest import MockContext, MockFlowFile


# ---------------------------------------------------------------------------
# Helpers — realistic FlowFile payloads for reporting tests
# ---------------------------------------------------------------------------

def _grover_flowfile(target="11", framework="qiskit"):
    """Minimal FlowFile that looks like QiskitAerSimulator output after Grover."""
    from QiskitGroverCircuit import QiskitGroverCircuit
    from QiskitAerSimulator import QiskitAerSimulator
    from conftest import MockContext as MC, MockFlowFile as MFF, result_to_flowfile

    circuit_r = QiskitGroverCircuit().transform(
        MC(**{"Marked State": target, "Num Iterations": "1",
              "Insert Barriers": "false", "Output Format": "qasm3"}),
        MFF(),
    )
    sim_r = QiskitAerSimulator().transform(
        MC(**{"Shots": "256"}),
        result_to_flowfile(circuit_r),
    )
    # Merge circuit attributes into the simulator result so the report has everything
    attrs = {**circuit_r.attributes, **sim_r.attributes, "grover.framework": framework}
    return MockFlowFile(content=sim_r.contents, attributes=attrs)


def _sv_circuit_qasm3():
    """Produce a 2-qubit Bell-state-like circuit in QASM3 for statevector tests."""
    from qiskit import QuantumCircuit, qasm3, transpile
    qc = QuantumCircuit(2)
    qc.h(0)
    qc.cx(0, 1)
    tc = transpile(qc, basis_gates=["h", "cx", "rz", "x"], optimization_level=0)
    return qasm3.dumps(tc)


# ---------------------------------------------------------------------------
# QiskitCircuitReport
# ---------------------------------------------------------------------------

class TestQiskitCircuitReport:

    def test_creates_html_file(self, tmp_path):
        proc = QiskitCircuitReport()
        ctx  = MockContext(**{
            "Reports Directory": str(tmp_path),
            "Flow Name": "test-flow",
        })
        r = proc.transform(ctx, _grover_flowfile())
        assert r.relationship == "success"
        report_path = tmp_path / "test-flow.html"
        assert report_path.exists()
        content = report_path.read_text()
        assert "test-flow" in content

    def test_appends_on_second_run(self, tmp_path):
        proc = QiskitCircuitReport()
        ctx  = MockContext(**{
            "Reports Directory": str(tmp_path),
            "Flow Name": "append-test",
        })
        proc.transform(ctx, _grover_flowfile("11"))
        proc.transform(ctx, _grover_flowfile("11"))
        content = (tmp_path / "append-test.html").read_text()
        # Two run cards should be present; each card contains the run title
        assert content.count("run-card") >= 2

    def test_relationship_is_success(self, tmp_path):
        proc = QiskitCircuitReport()
        ctx  = MockContext(**{
            "Reports Directory": str(tmp_path),
            "Flow Name": "rel-test",
        })
        r = proc.transform(ctx, _grover_flowfile())
        assert r.relationship == "success"

    def test_property_descriptors(self):
        names = {d.name for d in QiskitCircuitReport().getPropertyDescriptors()}
        assert names == {"Reports Directory", "Flow Name"}


# ---------------------------------------------------------------------------
# QuantumDistributionComparison
# ---------------------------------------------------------------------------

class TestQuantumDistributionComparison:

    def _make_ctx(self, tmp_path, label="test-label"):
        return MockContext(**{
            "Reports Directory": str(tmp_path / "reports"),
            "Flow Name": "compare-test",
            "State Directory": str(tmp_path / "state"),
            "Comparison Label": label,
            "Framework Label": "${grover.framework}",
        })

    def _dist_ff(self, counts: dict, framework: str):
        return MockFlowFile(
            content=json.dumps(counts).encode(),
            attributes={"grover.framework": framework},
        )

    def test_first_arrival_is_pending(self, tmp_path):
        """The first FlowFile is stored and routes to 'failure' (first of pair)."""
        proc = QuantumDistributionComparison()
        ctx  = self._make_ctx(tmp_path)
        r = proc.transform(ctx, self._dist_ff({"00": 500, "11": 500}, "qiskit"))
        assert r.relationship == "failure"

    def test_second_arrival_produces_report(self, tmp_path):
        """The second FlowFile triggers the comparison and routes to 'success'."""
        proc = QuantumDistributionComparison()
        ctx  = self._make_ctx(tmp_path)
        ff_a = self._dist_ff({"00": 500, "11": 500}, "qiskit")
        ff_b = self._dist_ff({"00": 480, "11": 520}, "qrisp")
        proc.transform(ctx, ff_a)  # stores first
        r = proc.transform(ctx, ff_b)  # triggers comparison
        assert r.relationship == "success"

    def test_report_file_written(self, tmp_path):
        proc = QuantumDistributionComparison()
        ctx  = self._make_ctx(tmp_path)
        os.makedirs(tmp_path / "reports", exist_ok=True)
        proc.transform(ctx, self._dist_ff({"0": 600, "1": 400}, "qiskit"))
        proc.transform(ctx, self._dist_ff({"0": 580, "1": 420}, "cirq"))
        report_path = tmp_path / "reports" / "compare-test-comparison.html"
        assert report_path.exists()

    def test_metrics_in_result(self, tmp_path):
        proc = QuantumDistributionComparison()
        ctx  = self._make_ctx(tmp_path)
        proc.transform(ctx, self._dist_ff({"11": 1024}, "qiskit"))
        r = proc.transform(ctx, self._dist_ff({"11": 1024}, "qrisp"))
        # Distributions are identical → Hellinger distance ~ 0, fidelity ~ 1
        assert "compare.hellinger_distance" in r.attributes
        h = float(r.attributes["compare.hellinger_distance"])
        assert h < 0.05

    def test_state_cleared_after_pair(self, tmp_path):
        """After a successful pair, the state file is removed so a new pair can form."""
        proc = QuantumDistributionComparison()
        ctx  = self._make_ctx(tmp_path, label="pair-clear")
        proc.transform(ctx, self._dist_ff({"0": 512, "1": 512}, "a"))
        proc.transform(ctx, self._dist_ff({"0": 512, "1": 512}, "b"))
        # Third FlowFile should again be treated as first of a new pair
        r3 = proc.transform(ctx, self._dist_ff({"0": 512, "1": 512}, "c"))
        assert r3.relationship == "failure"


# ---------------------------------------------------------------------------
# QiskitStatevectorSimulator
# ---------------------------------------------------------------------------

class TestQiskitStatevectorSimulator:

    def test_creates_html_report(self, tmp_path):
        proc = QiskitStatevectorSimulator()
        ctx  = MockContext(**{
            "Reports Directory": str(tmp_path),
            "Flow Name": "sv-test",
            "Probability Threshold": "0.0",
            "Max States": "16",
        })
        qasm3_str = _sv_circuit_qasm3()
        ff = MockFlowFile(
            content=qasm3_str.encode(),
            attributes={"circuit.format": "qasm3", "circuit.qasm3": qasm3_str},
        )
        r = proc.transform(ctx, ff)
        assert r.relationship == "success"
        assert (tmp_path / "sv-test-statevector.html").exists()

    def test_attributes_pass_through(self, tmp_path):
        """FlowFile attributes are forwarded in the result."""
        proc = QiskitStatevectorSimulator()
        ctx  = MockContext(**{
            "Reports Directory": str(tmp_path),
            "Flow Name": "sv-attrs",
            "Probability Threshold": "0.0",
            "Max States": "16",
        })
        qasm3_str = _sv_circuit_qasm3()
        ff = MockFlowFile(
            content=qasm3_str.encode(),
            attributes={
                "circuit.format": "qasm3",
                "circuit.qasm3": qasm3_str,
                "circuit.marked_state": "11",
            },
        )
        r = proc.transform(ctx, ff)
        assert r.relationship == "success"
        assert r.attributes.get("circuit.marked_state") == "11"

    def test_property_descriptors(self):
        names = {d.name for d in QiskitStatevectorSimulator().getPropertyDescriptors()}
        assert names == {
            "Reports Directory", "Flow Name", "Report File Name",
            "Probability Threshold", "Max States",
        }

    def test_report_file_name_override(self, tmp_path):
        proc = QiskitStatevectorSimulator()
        ctx  = MockContext(**{
            "Reports Directory": str(tmp_path),
            "Flow Name": "sv-test",
            "Report File Name": "custom-report-name",
            "Probability Threshold": "0.0",
            "Max States": "16",
        })
        qasm3_str = _sv_circuit_qasm3()
        ff = MockFlowFile(
            content=qasm3_str.encode(),
            attributes={"circuit.format": "qasm3", "circuit.qasm3": qasm3_str},
        )
        r = proc.transform(ctx, ff)
        assert r.relationship == "success"
        assert (tmp_path / "custom-report-name.html").exists()
        assert not (tmp_path / "sv-test-statevector.html").exists()

    def test_simulation_card_attrs(self, tmp_path):
        """report.type=simulation + sim.framework=qiskit give QuanifiReport
        the proper simulation card instead of the bare default."""
        proc = QiskitStatevectorSimulator()
        ctx  = MockContext(**{
            "Reports Directory": str(tmp_path),
            "Flow Name": "sv-card",
            "Probability Threshold": "0.0",
            "Max States": "16",
        })
        qasm3_str = _sv_circuit_qasm3()
        ff = MockFlowFile(
            content=qasm3_str.encode(),
            attributes={"circuit.format": "qasm3", "circuit.qasm3": qasm3_str},
        )
        r = proc.transform(ctx, ff)
        assert r.relationship == "success"
        assert r.attributes["report.type"] == "simulation"
        assert r.attributes["sim.framework"] == "qiskit"

    def test_no_circuit_routes_to_failure(self, tmp_path):
        """JSON content (e.g. AerSimulator counts) with no circuit.qasm3 attr
        routes to failure with sim.error instead of raising RuntimeError."""
        proc = QiskitStatevectorSimulator()
        ctx  = MockContext(**{
            "Reports Directory": str(tmp_path),
            "Flow Name": "sv-fail",
        })
        ff = MockFlowFile(content=b'{"00": 512, "11": 512}')
        r = proc.transform(ctx, ff)
        assert r.relationship == "failure"
        assert "sim.error" in r.attributes

    def test_mid_circuit_measurement_routes_to_failure(self, tmp_path):
        """remove_final_measurements leaves mid-circuit measures, which
        Statevector.from_instruction rejects — route, don't raise."""
        import io
        from qiskit import QuantumCircuit, qpy
        qc = QuantumCircuit(2, 1)
        qc.h(0)
        qc.measure(0, 0)  # truly mid-circuit: an op follows on the same qubit
        qc.x(0)
        buf = io.BytesIO()
        qpy.dump(qc, buf)
        proc = QiskitStatevectorSimulator()
        ctx  = MockContext(**{
            "Reports Directory": str(tmp_path),
            "Flow Name": "sv-midmeas",
        })
        ff = MockFlowFile(content=buf.getvalue(),
                          attributes={"circuit.format": "qpy"})
        r = proc.transform(ctx, ff)
        assert r.relationship == "failure"
        assert "sim.error" in r.attributes


# ---------------------------------------------------------------------------
# QuanifiReport — noise model surfacing
# ---------------------------------------------------------------------------

class TestQuanifiReportWebOutput:

    class _Response:
        status = 201

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return b'{"id":"a98e6218-e8bc-4ffc-a754-8d096ead48c5"}'

    def test_web_api_submits_structured_report_without_writing_file(self, tmp_path, monkeypatch):
        captured = {}

        def fake_urlopen(request, timeout):
            captured["request"] = request
            captured["timeout"] = timeout
            return self._Response()

        monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
        attrs = {"uuid": "flowfile-1", "report.type": "simulation",
                 "sim.framework": "qiskit"}
        ff = MockFlowFile(content=b'{"11":256}', attributes=attrs)
        ctx = MockContext(**{
            "Reports Directory": str(tmp_path),
            "Flow Name": "web-grover",
            "Output Mode": "Web API",
            "Reports API URL": "https://reports.example/api/v1/report-runs/",
            "Reports API Token": "secret-token",
            "Reports API Timeout Seconds": "7",
        })

        result = QuanifiReport().transform(ctx, ff)

        assert result.relationship == "success"
        assert result.attributes["report.web_run_id"].startswith("a98e")
        assert not (tmp_path / "web-grover.html").exists()
        assert captured["timeout"] == 7
        assert captured["request"].headers["Authorization"] == "Bearer secret-token"
        submitted = json.loads(captured["request"].data)
        assert submitted["payload"] == {"11": 256}
        assert submitted["attributes"]["sim.framework"] == "qiskit"

    def test_web_api_failure_routes_to_failure(self, tmp_path, monkeypatch):
        def fail(*args, **kwargs):
            raise OSError("service unavailable")

        monkeypatch.setattr("urllib.request.urlopen", fail)
        ctx = MockContext(**{
            "Reports Directory": str(tmp_path),
            "Flow Name": "web-failure",
            "Output Mode": "Web API",
            "Reports API URL": "https://reports.example/api/v1/report-runs/",
            "Reports API Token": "secret-token",
        })
        result = QuanifiReport().transform(ctx, MockFlowFile(content=b"{}"))
        assert result.relationship == "failure"
        assert "service unavailable" in result.attributes["report.error"]

    def test_invalid_output_mode_routes_to_failure(self, tmp_path):
        ctx = MockContext(**{
            "Reports Directory": str(tmp_path),
            "Flow Name": "invalid-mode",
            "Output Mode": "Kafka",
        })
        result = QuanifiReport().transform(ctx, MockFlowFile(content=b"{}"))
        assert result.relationship == "failure"
        assert "Unsupported Output Mode" in result.attributes["report.error"]


class TestQuanifiReportNoise:

    def _noisy_ff(self, model="depolarizing", params=None):
        params = params if params is not None else {"error_1q": 0.05, "error_2q": 0.1}
        attrs = {
            "report.type": "simulation",
            "sim.shots": "256",
            "sim.framework": "qiskit",
            "sim.top_result": "11",
            "sim.noise_model": model,
            "sim.noise_params": json.dumps(params),
        }
        return MockFlowFile(content=b'{"11": 200, "01": 56}', attributes=attrs)

    def test_noise_panel_and_badge_rendered(self, tmp_path):
        ctx = MockContext(**{"Reports Directory": str(tmp_path), "Flow Name": "noisy"})
        r = QuanifiReport().transform(ctx, self._noisy_ff())
        assert r.relationship == "success"
        html = (tmp_path / "noisy.html").read_text()
        assert "Noise Model" in html
        assert "depolarizing" in html
        assert 'class="badge badge-noise"' in html  # header badge, not just CSS
        assert "1-qubit error rate" in html  # friendly param label

    def test_from_backend_params_labelled(self, tmp_path):
        ff  = self._noisy_ff(model="from_backend", params={"backend": "fake_manila"})
        ctx = MockContext(**{"Reports Directory": str(tmp_path), "Flow Name": "hw"})
        QuanifiReport().transform(ctx, ff)
        html = (tmp_path / "hw.html").read_text()
        assert "from_backend" in html
        assert "fake_manila" in html
        assert "Backend" in html

    def test_cirq_params_friendly_labels(self, tmp_path):
        ff  = self._noisy_ff(model="amplitude_damp", params={"gamma": 0.1})
        ctx = MockContext(**{"Reports Directory": str(tmp_path), "Flow Name": "cirq"})
        QuanifiReport().transform(ctx, ff)
        html = (tmp_path / "cirq.html").read_text()
        assert "amplitude_damp" in html
        assert "Damping" in html  # friendly label for the 'gamma' key

    def test_ideal_run_has_no_noise_panel(self, tmp_path):
        attrs = {"report.type": "simulation", "sim.framework": "qiskit",
                 "sim.noise_model": "none"}
        ff  = MockFlowFile(content=b'{"11": 256}', attributes=attrs)
        ctx = MockContext(**{"Reports Directory": str(tmp_path), "Flow Name": "ideal"})
        QuanifiReport().transform(ctx, ff)
        html = (tmp_path / "ideal.html").read_text()
        assert "Noise Model" not in html
        assert 'class="badge badge-noise"' not in html


# ---------------------------------------------------------------------------
# QuanifiReport — VQE result panel (shown only when vqe.* attrs are present)
# ---------------------------------------------------------------------------

class TestQuanifiReportVQE:

    def test_vqe_panel_rendered_for_vqe_run(self, tmp_path):
        attrs = {
            "report.type": "simulation",
            "sim.framework": "qiskit",
            "sim.top_result": "11",
            "vqe.framework": "qiskit",
            "vqe.optimal_value": "-1.00000000",
            "vqe.converged": "true",
            "vqe.num_iterations": "42",
        }
        ff  = MockFlowFile(content=b'{"11": 256}', attributes=attrs)
        ctx = MockContext(**{"Reports Directory": str(tmp_path), "Flow Name": "vqe-run"})
        r = QuanifiReport().transform(ctx, ff)
        assert r.relationship == "success"
        html = (tmp_path / "vqe-run.html").read_text()
        assert "VQE Result" in html
        assert "vqe.optimal_value" in html
        assert "-1.00000000" in html
        assert "vqe.converged" in html

    def test_no_vqe_panel_for_plain_simulation(self, tmp_path):
        attrs = {"report.type": "simulation", "sim.framework": "qiskit",
                 "sim.top_result": "11"}
        ff  = MockFlowFile(content=b'{"11": 256}', attributes=attrs)
        ctx = MockContext(**{"Reports Directory": str(tmp_path), "Flow Name": "plain-sim"})
        QuanifiReport().transform(ctx, ff)
        html = (tmp_path / "plain-sim.html").read_text()
        assert "VQE Result" not in html
        assert "vqe.optimal_value" not in html

    def test_no_vqe_panel_for_pennylane_expectation(self, tmp_path):
        """sim.expectations (Pennylane) is its own thing — not a vqe.* result."""
        attrs = {
            "report.type": "simulation",
            "sim.framework": "pennylane",
            "sim.observable": "Z",
            "sim.expectations": json.dumps({"q0": 0.5, "q1": -0.2}),
            "sim.top_result": "00",
        }
        ff  = MockFlowFile(content=b'{"00": 256}', attributes=attrs)
        ctx = MockContext(**{"Reports Directory": str(tmp_path), "Flow Name": "pennylane-qml"})
        QuanifiReport().transform(ctx, ff)
        html = (tmp_path / "pennylane-qml.html").read_text()
        assert "VQE Result" not in html
        assert "sim.expectations" in html  # still surfaced via Simulation Attributes


class TestQuanifiReportQAOA:

    def test_qaoa_panel_rendered_for_qaoa_run(self, tmp_path):
        attrs = {
            "report.type": "simulation",
            "sim.framework": "qiskit",
            "sim.top_result": "100",
            "qaoa.framework": "qiskit",
            "qaoa.optimal_value": "-0.50000000",
            "qaoa.best_measurement": "100",
            "qaoa.approximation_ratio": "1.000000",
            "qaoa.layers": "2",
        }
        ff  = MockFlowFile(content=b'{"100": 256}', attributes=attrs)
        ctx = MockContext(**{"Reports Directory": str(tmp_path), "Flow Name": "qaoa-run"})
        r = QuanifiReport().transform(ctx, ff)
        assert r.relationship == "success"
        html = (tmp_path / "qaoa-run.html").read_text()
        assert "QAOA Result" in html
        assert "qaoa.approximation_ratio" in html
        assert "-0.50000000" in html
        # best_measurement gets an inline orientation marker + bit-order caption
        assert "(q0&thinsp;&rarr;&thinsp;q2)" in html
        assert "q0_left" in html

    def test_qaoa_marker_skipped_for_nonbinary_values(self, tmp_path):
        """The orientation marker only renders for clean binary strings."""
        attrs = {
            "report.type": "simulation",
            "sim.framework": "qiskit",
            "sim.top_result": "100",
            "qaoa.framework": "qiskit",
            "qaoa.best_measurement": "n/a",   # not a bitstring — plain text
        }
        ff  = MockFlowFile(content=b'{}', attributes=attrs)
        ctx = MockContext(**{"Reports Directory": str(tmp_path), "Flow Name": "qaoa-nonbin"})
        QuanifiReport().transform(ctx, ff)
        html = (tmp_path / "qaoa-nonbin.html").read_text()
        assert "QAOA Result" in html
        assert "&rarr;&thinsp;q" not in html

    def test_no_qaoa_panel_for_plain_simulation(self, tmp_path):
        attrs = {"report.type": "simulation", "sim.framework": "qiskit",
                 "sim.top_result": "11"}
        ff  = MockFlowFile(content=b'{"11": 256}', attributes=attrs)
        ctx = MockContext(**{"Reports Directory": str(tmp_path), "Flow Name": "plain-sim2"})
        QuanifiReport().transform(ctx, ff)
        html = (tmp_path / "plain-sim2.html").read_text()
        assert "QAOA Result" not in html
        assert "qaoa.optimal_value" not in html

    def test_evaluator_output_renders_qaoa_panel(self, tmp_path):
        """Real chain: Hamiltonian -> fixed-angle builder -> engine ->
        QuantumQAOAEvaluator -> QuanifiReport. The evaluator (not the builder
        or the engine) is what supplies the qaoa.* keys the panel renders."""
        from conftest import result_to_flowfile_merged
        from QiskitHamiltonian import QiskitHamiltonian
        from PyquilQAOACircuit import PyquilQAOACircuit
        from QiskitAerSimulator import QiskitAerSimulator
        from QuantumQAOAEvaluator import QuantumQAOAEvaluator

        ham = QiskitHamiltonian().transform(
            MockContext(**{"Hamiltonian": "Z0 - Z1", "Num Qubits": "0"}), MockFlowFile())
        assert ham.relationship == "success"
        ham_ff = result_to_flowfile_merged(ham, MockFlowFile())

        built = PyquilQAOACircuit().transform(MockContext(), ham_ff)
        assert built.relationship == "success", built.attributes
        built_ff = result_to_flowfile_merged(built, ham_ff)

        sim = QiskitAerSimulator().transform(
            MockContext(**{"Shots": "1024", "Random Seed": "11"}), built_ff)
        assert sim.relationship == "success", sim.attributes
        sim_ff = result_to_flowfile_merged(sim, built_ff)

        ev = QuantumQAOAEvaluator().transform(MockContext(), sim_ff)
        assert ev.relationship == "success", ev.attributes
        ev_ff = result_to_flowfile_merged(ev, sim_ff)

        ctx = MockContext(**{"Reports Directory": str(tmp_path), "Flow Name": "qaoa-eval"})
        r = QuanifiReport().transform(ctx, ev_ff)
        assert r.relationship == "success"
        html = (tmp_path / "qaoa-eval.html").read_text()
        assert "QAOA Result" in html
        assert "qaoa.expectation_ratio" in html
        assert "q0_left" in html


# ---------------------------------------------------------------------------
# QuanifiReport — generic derived-result decoder (Sampler algorithms: QPE, …)
# ---------------------------------------------------------------------------

class TestQuanifiReportDerivedResult:

    def _qpe_ff(self, top_result="1100", positions="3,2,1", label="Estimated phase (φ)"):
        """FlowFile mimicking QiskitPhaseEstimation → QiskitAerSimulator output.

        For T (φ=1/8, m=3) the simulator's MSB-left readout is '1100'; the
        builder's declarative hint says the phase bits are at positions 3,2,1
        → '001' → 1/8.
        """
        attrs = {
            "report.type":         "simulation",
            "sim.framework":       "qiskit",
            "sim.top_result":      top_result,
            "result.decode":       "phase",
            "result.bit_positions": positions,
            "result.label":        label,
        }
        return MockFlowFile(content=b'{"1100": 1024}', attributes=attrs)

    def test_phase_decoded_and_rendered(self, tmp_path):
        ctx = MockContext(**{"Reports Directory": str(tmp_path), "Flow Name": "qpe"})
        r = QuanifiReport().transform(ctx, self._qpe_ff())
        assert r.relationship == "success"
        html = (tmp_path / "qpe.html").read_text()
        assert "Decoded Result" in html
        assert "Estimated phase" in html
        assert "0.1250" in html       # 1/8 as a decimal
        assert "1/8" in html          # nice fraction
        assert "001" in html          # readout bits MSB→LSB

    def test_no_panel_without_decode_hint(self, tmp_path):
        """A plain simulation card (no result.decode) shows no Decoded Result."""
        attrs = {"report.type": "simulation", "sim.framework": "qiskit",
                 "sim.top_result": "11"}
        ff  = MockFlowFile(content=b'{"11": 256}', attributes=attrs)
        ctx = MockContext(**{"Reports Directory": str(tmp_path), "Flow Name": "plain"})
        QuanifiReport().transform(ctx, ff)
        html = (tmp_path / "plain.html").read_text()
        assert "Decoded Result" not in html

    def test_malformed_positions_skip_panel(self, tmp_path):
        """Out-of-range positions are caught and skip the panel (not a 500)."""
        ff  = self._qpe_ff(top_result="11", positions="3,2,1")  # too short
        ctx = MockContext(**{"Reports Directory": str(tmp_path), "Flow Name": "bad"})
        r = QuanifiReport().transform(ctx, ff)
        assert r.relationship == "success"
        html = (tmp_path / "bad.html").read_text()
        assert "Decoded Result" not in html

    def test_s_and_z_gate_phases(self, tmp_path):
        """The same generic renderer handles 1/4 (S) and 1/2 (Z) readouts."""
        for top, frac in [("1010", "1/4"), ("1001", "1/2")]:
            ctx = MockContext(**{"Reports Directory": str(tmp_path), "Flow Name": frac.replace("/", "-")})
            QuanifiReport().transform(ctx, self._qpe_ff(top_result=top))
            html = (tmp_path / f"{frac.replace('/', '-')}.html").read_text()
            assert frac in html

    def test_cirq_phase_layout_decoded(self, tmp_path):
        """Cirq's MSB-first layout (phase bits at positions 0..m-1) decodes too.

        CirqPhaseEstimation → CirqSimulator emits sim.framework=cirq with the
        phase register at string positions 0,1,2 (vs Qiskit's 3,2,1). The same
        generic renderer formats both — it only follows result.bit_positions.
        For T (φ=1/8, m=3) the Cirq readout is '0011' → phase bits '001' → 1/8.
        """
        attrs = {
            "report.type":          "simulation",
            "sim.framework":        "cirq",
            "sim.top_result":       "0011",
            "result.decode":        "phase",
            "result.bit_positions": "0,1,2",
            "result.label":         "Estimated phase (φ)",
        }
        ff  = MockFlowFile(content=b'{"0011": 1024}', attributes=attrs)
        ctx = MockContext(**{"Reports Directory": str(tmp_path), "Flow Name": "cirq-qpe"})
        QuanifiReport().transform(ctx, ff)
        html = (tmp_path / "cirq-qpe.html").read_text()
        assert "Decoded Result" in html
        assert "1/8" in html
        assert "001" in html


class TestQuanifiReportConsensus:
    """The consensus card (report.type=consensus, from QuantumConsensusOracle)."""

    _BASE_ATTRS = {
        "report.type":               "consensus",
        "assert.verdict":            "PASS",
        "assert.reason":             "all 3 branches agree on |101⟩ (matches expected)",
        "assert.case_id":            "case-001",
        "assert.run_id":             "run-42",
        "consensus.expected":        "101",
        "consensus.majority_top":    "101",
        "consensus.majority_count":  "3",
        "consensus.branches":        "3",
        "consensus.dissenters":      "",
        "consensus.max_hellinger":   "0.0123",
        "circuit.marked_state":      "101",
        "circuit.num_qubits":        "3",
        "sim.framework":             "qiskit",
        "sim.top_result":            "101",
    }

    def test_pass_card_renders_verdict_panel(self, tmp_path):
        ff  = MockFlowFile(content=b'{"101": 976, "000": 48}',
                           attributes=dict(self._BASE_ATTRS))
        ctx = MockContext(**{"Reports Directory": str(tmp_path),
                             "Flow Name": "consensus-pass"})
        r = QuanifiReport().transform(ctx, ff)
        assert r.relationship == "success"
        assert r.attributes["report.type"] == "consensus"
        html = (tmp_path / "consensus-pass.html").read_text()
        assert "Consensus Verdict" in html
        assert "PASS" in html
        assert "all 3 branches agree" in html
        assert "3/3 branches" in html
        assert "Dissenters" in html and "none" in html
        assert "case-001 / run-42" in html
        # counts chart + attr panels come along
        assert "Measurement Counts" in html
        assert "Simulation Attributes" in html
        # no mutation panel without mut.* attrs
        assert "<h3>Mutation</h3>" not in html

    def test_fail_and_disagree_badges(self, tmp_path):
        for verdict, marker in (("FAIL", "badge-differ"),
                                ("DISAGREE", "badge-noise")):
            attrs = dict(self._BASE_ATTRS)
            attrs["assert.verdict"] = verdict
            attrs["consensus.dissenters"] = "cirq" if verdict == "DISAGREE" else ""
            ff  = MockFlowFile(content=b'{"101": 976}', attributes=attrs)
            name = f"consensus-{verdict.lower()}"
            ctx = MockContext(**{"Reports Directory": str(tmp_path),
                                 "Flow Name": name})
            QuanifiReport().transform(ctx, ff)
            html = (tmp_path / f"{name}.html").read_text()
            assert verdict in html
            assert marker in html
        # dissenter named on the DISAGREE card
        html = (tmp_path / "consensus-disagree.html").read_text()
        assert "cirq" in html

    def test_mutant_card_shows_mutation_panel_and_header_badge(self, tmp_path):
        attrs = dict(self._BASE_ATTRS)
        attrs.update({
            "assert.verdict":     "FAIL",
            "mut.applied":        "true",
            "mut.operator":       "marked_state.bitflip",
            "mut.target_attr":    "grover.marked_state",
            "mut.original_value": "101",
            "mut.seed":           "777000",
            "mut.base_case_id":   "case-000",
        })
        ff  = MockFlowFile(content=b'{"001": 976}', attributes=attrs)
        ctx = MockContext(**{"Reports Directory": str(tmp_path),
                             "Flow Name": "consensus-mutant"})
        QuanifiReport().transform(ctx, ff)
        html = (tmp_path / "consensus-mutant.html").read_text()
        assert "<h3>Mutation</h3>" in html
        assert "mut.operator" in html
        assert "marked_state.bitflip" in html
        assert "mutant: marked_state.bitflip" in html  # header badge

    def test_consensus_without_verdict_degrades_gracefully(self, tmp_path):
        # Mislabeled FlowFile: consensus type but no assert.verdict — the
        # verdict panel is skipped, the rest of the card still renders.
        attrs = {"report.type": "consensus", "sim.framework": "qiskit"}
        ff  = MockFlowFile(content=b'{"11": 256}', attributes=attrs)
        ctx = MockContext(**{"Reports Directory": str(tmp_path),
                             "Flow Name": "consensus-bare"})
        r = QuanifiReport().transform(ctx, ff)
        assert r.relationship == "success"
        html = (tmp_path / "consensus-bare.html").read_text()
        assert "Consensus Verdict" not in html
        assert "Measurement Counts" in html

    def test_consensus_branches_table_renders(self, tmp_path):
        attrs = dict(self._BASE_ATTRS)
        branches = [
            {"label": "QiskitGrover and CirqSimulator", "top": "101", "dissent": False},
            {"label": "CirqGrover and QrispSimulator", "top": "000", "dissent": True},
        ]
        attrs["consensus.branches_json"] = json.dumps(branches)
        ff = MockFlowFile(content=b'{"101": 976}', attributes=attrs)
        ctx = MockContext(**{"Reports Directory": str(tmp_path),
                             "Flow Name": "consensus-branches"})
        QuanifiReport().transform(ctx, ff)
        html = (tmp_path / "consensus-branches.html").read_text()
        assert "Consensus Branches" in html
        assert "QiskitGrover and CirqSimulator" in html
        assert "CirqGrover and QrispSimulator" in html
        assert "majority" in html
        assert "dissents" in html



class TestQuanifiReportSvgOverflow:
    """Wide circuit SVGs must be wrapped in a scroll container so they don't
    bleed over the neighbouring panels (consensus/simulation cards)."""

    def test_cirq_svg_wrapped_in_scroll_container(self, tmp_path):
        attrs = {
            "report.type": "simulation",
            "circuit.svg": '<svg xmlns="http://www.w3.org/2000/svg" '
                           'width="5000" height="100"></svg>',
            "sim.framework": "cirq",
        }
        ff  = MockFlowFile(content=b'{"11": 256}', attributes=attrs)
        ctx = MockContext(**{"Reports Directory": str(tmp_path),
                             "Flow Name": "svg-scroll"})
        QuanifiReport().transform(ctx, ff)
        html = (tmp_path / "svg-scroll.html").read_text()
        # Inline style on the wrapper so the fix works even in report files
        # whose baked-in page CSS predates the .svg-scroll rule.
        assert "svg-scroll" in html
        assert "overflow:auto" in html
        assert ".svg-scroll { overflow-x: auto" in html  # CSS shipped with page
        # Footer wrapping is likewise inlined per card; every panel contains
        # its own overflow so wide content can't invade the neighbour panel.
        assert 'class="run-footer" style="flex-wrap:wrap"' in html
        assert 'style="flex:1 1 340px;min-width:0;overflow-x:auto"' in html


class TestQuanifiReportAE:

    def test_ae_panel_rendered_for_ae_run(self, tmp_path):
        attrs = {
            "report.type": "simulation",
            "sim.framework": "qiskit",
            "sim.top_result": "0.500000",
            "ae.framework": "qiskit",
            "ae.method": "canonical",
            "ae.estimate": "0.50000000",
            "ae.mle": "0.20022934",
            "ae.probability": "0.20000000",
        }
        ff  = MockFlowFile(content=b'{"0.500000": 0.68}', attributes=attrs)
        ctx = MockContext(**{"Reports Directory": str(tmp_path), "Flow Name": "ae-run"})
        r = QuanifiReport().transform(ctx, ff)
        assert r.relationship == "success"
        html = (tmp_path / "ae-run.html").read_text()
        assert "Amplitude Estimation Result" in html
        assert "ae.estimate" in html
        assert "0.50000000" in html
        assert "ae.mle" in html

    def test_no_ae_panel_for_plain_simulation(self, tmp_path):
        attrs = {"report.type": "simulation", "sim.framework": "qiskit",
                 "sim.top_result": "11"}
        ff  = MockFlowFile(content=b'{"11": 256}', attributes=attrs)
        ctx = MockContext(**{"Reports Directory": str(tmp_path), "Flow Name": "plain-sim2"})
        QuanifiReport().transform(ctx, ff)
        html = (tmp_path / "plain-sim2.html").read_text()
        assert "Amplitude Estimation Result" not in html
        assert "ae.estimate" not in html


class TestQuanifiReportTraining:

    def test_training_card_renders_loss_curve_and_attrs(self, tmp_path):
        attrs = {
            "report.type": "training",
            "train.framework": "pennylane",
            "train.final_loss": "0.1234",
            "train.train_accuracy": "0.9500",
        }
        content = json.dumps({"loss_history": [1.0, 0.6, 0.3, 0.15, 0.1234]})
        ff  = MockFlowFile(content=content.encode(), attributes=attrs)
        ctx = MockContext(**{"Reports Directory": str(tmp_path), "Flow Name": "train-run"})
        r = QuanifiReport().transform(ctx, ff)
        assert r.relationship == "success"
        html = (tmp_path / "train-run.html").read_text()
        assert "Training Loss" in html
        assert "polyline" in html
        assert "Training Result" in html
        assert "train.final_loss" in html

    def test_no_training_panels_for_simulation_card(self, tmp_path):
        attrs = {"report.type": "simulation", "sim.framework": "qiskit",
                 "sim.top_result": "11"}
        ff  = MockFlowFile(content=b'{"11": 256}', attributes=attrs)
        ctx = MockContext(**{"Reports Directory": str(tmp_path), "Flow Name": "plain-sim3"})
        QuanifiReport().transform(ctx, ff)
        html = (tmp_path / "plain-sim3.html").read_text()
        assert "Training Loss" not in html
        assert "Training Result" not in html


class TestQuanifiReportHardware:
    """The hw.* panel is vendor-neutral: every hardware gateway emits that prefix."""

    def test_hardware_panel_rendered_for_iqm_run(self, tmp_path):
        attrs = {
            "report.type": "simulation",
            "sim.framework": "qrisp",
            "sim.top_result": "11",
            "hw.provider": "iqm-resonance",
            "hw.device": "garnet",
            "hw.job_id": "0e6a1f2c-1111-2222-3333-444455556666",
            "hw.status": "completed",
            "hw.poll_attempts": "12",
        }
        ff  = MockFlowFile(content=b'{"11": 256}', attributes=attrs)
        ctx = MockContext(**{"Reports Directory": str(tmp_path), "Flow Name": "iqm-run"})
        r = QuanifiReport().transform(ctx, ff)
        assert r.relationship == "success"
        html = (tmp_path / "iqm-run.html").read_text()
        assert "Hardware Execution" in html
        assert "iqm-resonance" in html
        assert "garnet" in html
        assert "0e6a1f2c-1111-2222-3333-444455556666" in html

    def test_hardware_panel_rendered_for_ibm_run(self, tmp_path):
        attrs = {
            "report.type": "simulation",
            "sim.framework": "qiskit",
            "sim.top_result": "11",
            "hw.provider": "ibm-runtime",
            "hw.backend": "ibm_brisbane",
            "hw.mode": "cloud",
        }
        ff  = MockFlowFile(content=b'{"11": 256}', attributes=attrs)
        ctx = MockContext(**{"Reports Directory": str(tmp_path), "Flow Name": "ibm-run"})
        QuanifiReport().transform(ctx, ff)
        html = (tmp_path / "ibm-run.html").read_text()
        assert "Hardware Execution" in html
        assert "ibm_brisbane" in html

    def test_no_hardware_panel_for_plain_simulation(self, tmp_path):
        attrs = {"report.type": "simulation", "sim.framework": "qiskit",
                 "sim.top_result": "11"}
        ff  = MockFlowFile(content=b'{"11": 256}', attributes=attrs)
        ctx = MockContext(**{"Reports Directory": str(tmp_path), "Flow Name": "plain-sim4"})
        QuanifiReport().transform(ctx, ff)
        html = (tmp_path / "plain-sim4.html").read_text()
        assert "Hardware Execution" not in html


# ---------------------------------------------------------------------------
# QuanifiReport — QASM dialect handling. Builders publish circuit.qasm2 OR
# circuit.qasm3 depending on their Output Format, so a report that only reads
# circuit.qasm3 silently drops the diagram and source panels on a qasm2 flow.
# ---------------------------------------------------------------------------

class TestQuanifiReportQasmDialects:

    @staticmethod
    def _grover_run(fmt):
        """A Grover build + simulate pair emitted in the given QASM dialect."""
        from QiskitGroverCircuit import QiskitGroverCircuit
        from QiskitAerSimulator import QiskitAerSimulator
        from conftest import result_to_flowfile

        circuit_r = QiskitGroverCircuit().transform(
            MockContext(**{"Marked State": "101", "Num Iterations": "2",
                           "Insert Barriers": "false", "Output Format": fmt}),
            MockFlowFile(),
        )
        sim_r = QiskitAerSimulator().transform(
            MockContext(**{"Shots": "256"}), result_to_flowfile(circuit_r))
        return MockFlowFile(content=sim_r.contents,
                            attributes={**circuit_r.attributes, **sim_r.attributes})

    @pytest.mark.parametrize("fmt, heading", [("qasm2", "OpenQASM 2"),
                                              ("qasm3", "OpenQASM 3")])
    def test_diagram_and_source_rendered_for_both_dialects(self, fmt, heading, tmp_path):
        ff  = self._grover_run(fmt)
        ctx = MockContext(**{"Reports Directory": str(tmp_path), "Flow Name": fmt})
        assert QuanifiReport().transform(ctx, ff).relationship == "success"
        html = (tmp_path / f"{fmt}.html").read_text()
        assert "Circuit Diagram" in html
        assert heading in html
        assert 'class="run-body"' in html  # both panels live in the card body

    def test_qasm2_circuit_parses_for_the_drawer(self):
        """The diagram path must parse qasm2 with the qasm2 loader, not qasm3."""
        from qiskit import qasm2
        attrs = self._grover_run("qasm2").getAttributes()
        assert "circuit.qasm3" not in attrs
        circuit = qasm2.loads(attrs["circuit.qasm2"])
        assert circuit.num_qubits == 3

    def test_ascii_diagram_is_the_last_resort(self, tmp_path):
        """With no QASM at all, the builder's text drawing still gets a panel."""
        attrs = {"report.type": "simulation", "sim.framework": "qiskit",
                 "circuit.diagram": "q_0: ┤ H ├", "circuit.num_qubits": "1"}
        ff  = MockFlowFile(content=b'{"1": 256}', attributes=attrs)
        ctx = MockContext(**{"Reports Directory": str(tmp_path), "Flow Name": "ascii"})
        QuanifiReport().transform(ctx, ff)
        html = (tmp_path / "ascii.html").read_text()
        assert "Circuit Diagram" in html
        assert "┤ H ├" in html

    def test_no_diagram_panel_without_any_circuit(self, tmp_path):
        attrs = {"report.type": "simulation", "sim.framework": "qiskit"}
        ff  = MockFlowFile(content=b'{"1": 256}', attributes=attrs)
        ctx = MockContext(**{"Reports Directory": str(tmp_path), "Flow Name": "bare"})
        QuanifiReport().transform(ctx, ff)
        html = (tmp_path / "bare.html").read_text()
        assert "Circuit Diagram" not in html
        assert "OpenQASM" not in html
