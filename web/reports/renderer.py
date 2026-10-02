"""
Dynamic HTML card renderer for Quanifi quantum report runs.

Provides pure-Python rendering of ReportRun instances to match the visual
appearance of locally generated Quanifi HTML reports (via QuanifiReport
and reporting.py) without requiring Qiskit or Matplotlib dependencies.
"""

import html as html_lib
import json
import math
from datetime import datetime

_SECTION_REGISTRY = {
    "simulation": [
        ("body", "circuit_diagram"),
        ("body", "qasm_source"),
        ("footer", "derived_result"),
        ("footer", "counts_chart"),
        ("footer", "vqe_attrs"),
        ("footer", "qaoa_attrs"),
        ("footer", "ae_attrs"),
        ("footer", "noise_model"),
        ("footer", "circuit_attrs"),
        ("footer", "sim_attrs"),
        ("footer", "hardware_attrs"),
    ],
    "statevector": [
        ("body", "circuit_diagram"),
        ("body", "statevector_table"),
        ("footer", "qasm_source"),
        ("footer", "noise_model"),
        ("footer", "circuit_attrs"),
        ("footer", "sim_attrs"),
    ],
    "comparison": [
        ("body", "comparison_metrics"),
        ("footer", "dual_distribution_chart"),
    ],
    "consensus": [
        ("body", "consensus_verdict"),
        ("body", "consensus_branches"),
        ("body", "counts_chart"),
        ("footer", "circuit_diagram"),
        ("footer", "qasm_source"),
        ("footer", "mutation_attrs"),
        ("footer", "circuit_attrs"),
        ("footer", "sim_attrs"),
    ],
    "training": [
        ("body", "loss_curve"),
        ("footer", "train_attrs"),
        ("footer", "all_attrs"),
    ],
    "_default": [
        ("body", "raw_content"),
        ("footer", "all_attrs"),
    ],
}

_WIDE_SECTIONS = {"qaoa_attrs", "vqe_attrs", "ae_attrs", "train_attrs"}

_NOISE_PARAM_LABELS = {
    "error_1q": "1-qubit error rate",
    "error_2q": "2-qubit error rate",
    "t1_us": "T1 (µs)",
    "t2_us": "T2 (µs)",
    "gate_time_ns": "Gate time (ns)",
    "readout_error": "Readout error rate",
    "backend": "Backend",
    "probability": "Error probability",
    "gamma": "Damping γ",
}

_VERDICT_BADGES = {
    "PASS": '<span class="badge badge-agree">&#x2713; PASS</span>',
    "FAIL": '<span class="badge badge-differ">&#x2717; FAIL</span>',
    "DISAGREE": '<span class="badge badge-noise">&#x26A0; DISAGREE</span>',
}


def _fmt_amplitude(real, imag, tol=1e-6):
    r = abs(real) > tol
    i = abs(imag) > tol
    if not r and not i:
        return "0"
    if not i:
        return f"{real:.4f}"
    if not r:
        return f"{imag:+.4f}i"
    sign = "+" if imag >= 0 else "−"
    return f"{real:.4f} {sign} {abs(imag):.4f}i"


def _phase_color(deg):
    return f"hsl({deg % 360:.0f}, 65%, 62%)"


def _bitstring_with_ruler(bits):
    if not bits or any(c not in "01" for c in bits):
        return html_lib.escape(str(bits))
    return (
        f"<span style='font-family:monospace;letter-spacing:0.15em'>{bits}</span>"
        f"&nbsp;<span style='color:#999;font-size:0.85em;white-space:nowrap'>"
        f"(q0&thinsp;&rarr;&thinsp;q{len(bits) - 1})</span>"
    )


def bar_rows(dist: dict, fill_class: str = "bar-fill", limit: int = 12) -> str:
    total = sum(dist.values()) or 1
    rows = ""
    for state, val in list(dist.items())[:limit]:
        pct = (val / total * 100) if total else 0.0
        label = f"{val}&nbsp;({pct:.1f}%)" if isinstance(val, int) else f"{pct:.1f}%"
        rows += (
            f'<div class="bar-row">'
            f'<span class="bar-label">|{html_lib.escape(str(state))}&#x27E9;</span>'
            f'<div class="bar-track">'
            f'<div class="{fill_class}" style="width:{pct:.1f}%"></div>'
            f"</div>"
            f'<span class="bar-count">{label}</span>'
            f"</div>\n"
        )
    return rows


def _qasm_source(attrs):
    qasm3_code = attrs.get("circuit.qasm3", "")
    if qasm3_code:
        return qasm3_code, "qasm3"
    return attrs.get("circuit.qasm2", ""), "qasm2"


def _build_header(report_type, attrs):
    if report_type == "consensus":
        parts = []
        case_id = attrs.get("assert.case_id") or attrs.get("test.case_id", "")
        if case_id:
            parts.append(html_lib.escape(str(case_id)))
        target = attrs.get("circuit.marked_state", "")
        if target:
            parts.append(f"target |{html_lib.escape(str(target))}&#x27E9;")
        branches = attrs.get("consensus.branches", "")
        if branches:
            parts.append(f"{html_lib.escape(str(branches))} branch(es)")
        if attrs.get("mut.applied") == "true":
            op = attrs.get("mut.operator", "?")
            parts.append(
                f'<span class="badge badge-noise">mutant: {html_lib.escape(str(op))}</span>'
            )
        verdict = attrs.get("assert.verdict", "")
        badge = _VERDICT_BADGES.get(verdict)
        if badge:
            parts.append(badge)
        elif verdict:
            parts.append(html_lib.escape(str(verdict)))
        return " &middot; ".join(parts) if parts else "consensus"

    if report_type == "comparison":
        fa = attrs.get("compare.framework_a", "A")
        fb = attrs.get("compare.framework_b", "B")
        h = attrs.get("compare.hellinger_distance", "")
        agree = str(attrs.get("compare.agreement", "false")).lower() == "true"
        badge = (
            '<span class="badge badge-agree">&#x2713; agree</span>'
            if agree
            else '<span class="badge badge-differ">&#x2717; differ</span>'
        )
        h_part = f" &nbsp;&middot;&nbsp; H&nbsp;=&nbsp;{h}" if h else ""
        return (
            f'<span class="tag-a">{html_lib.escape(str(fa))}</span>'
            f" &nbsp;vs&nbsp; "
            f'<span class="tag-b">{html_lib.escape(str(fb))}</span>'
            f"{h_part} &nbsp;&middot;&nbsp; {badge}"
        )

    parts = []
    for key, fmt in [
        (
            "circuit.marked_state",
            lambda v: f"target |{html_lib.escape(str(v))}&#x27E9;",
        ),
        ("circuit.num_qubits", lambda v: f"{html_lib.escape(str(v))} qubits"),
        ("circuit.num_iterations", lambda v: f"{html_lib.escape(str(v))} iteration(s)"),
        ("circuit.depth", lambda v: f"depth {html_lib.escape(str(v))}"),
        ("sim.simulator", lambda v: html_lib.escape(str(v))),
    ]:
        val = attrs.get(key, "")
        if val:
            parts.append(fmt(val))

    noise = attrs.get("sim.noise_model", "")
    if noise and noise != "none":
        parts.append(
            f'<span class="badge badge-noise">noisy: {html_lib.escape(str(noise))}</span>'
        )

    return (
        " &middot; ".join(parts)
        if parts
        else html_lib.escape(str(report_type or "report"))
    )


class RunCardRenderer:
    def __init__(self, flow_name, report_type, attrs, content, raw, timestamp):
        self.flow_name = flow_name or ""
        self.report_type = report_type or "_default"
        self.attrs = attrs or {}
        self.content = content if content is not None else {}
        self.raw = raw or b""
        self.timestamp = timestamp

    def render(self) -> str:
        sections = _SECTION_REGISTRY.get(
            self.report_type, _SECTION_REGISTRY["_default"]
        )
        data = {"content": self.content, "attrs": self.attrs, "raw": self.raw}

        body_panels = []
        footer_panels = []

        for loc, name in sections:
            renderer = getattr(self, f"_render_{name}", None)
            if renderer is None:
                continue
            try:
                inner = renderer(data)
            except Exception:
                inner = None
            if inner is None:
                continue

            if loc == "body":
                body_panels.append(
                    f'<div class="panel" style="min-width:0;overflow-x:auto">{inner}</div>'
                )
            else:
                basis = "1 1 100%" if name in _WIDE_SECTIONS else "1 1 340px"
                footer_panels.append(
                    f'<div class="panel" style="flex:{basis};min-width:0;overflow-x:auto">{inner}</div>'
                )

        header_title = _build_header(self.report_type, self.attrs)
        ts_str = ""
        if isinstance(self.timestamp, datetime):
            ts_str = self.timestamp.strftime("%Y-%m-%d %H:%M:%S")
        elif self.timestamp:
            ts_str = str(self.timestamp)
        else:
            ts_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        body_section = (
            f'<div class="run-body" style="display:flex;flex-wrap:wrap">{"".join(body_panels)}</div>'
            if body_panels
            else ""
        )
        footer_section = (
            f'<div class="run-footer" style="display:flex;flex-wrap:wrap;gap:16px;border-top:1px solid #d0d7de;padding:16px">{"".join(footer_panels)}</div>'
            if footer_panels
            else ""
        )

        return (
            f'<section class="run-card">\n'
            f'  <header class="run-header">\n'
            f'    <span class="run-title">{header_title}</span>\n'
            f'    <span class="run-time">{html_lib.escape(ts_str)}</span>\n'
            f"  </header>\n"
            f"  {body_section}\n"
            f"  {footer_section}\n"
            f"</section>"
        )

    def _diagram_panel(self, svg_html, attrs, large):
        scroll = (
            "<div class='svg-scroll' style='overflow:auto;max-width:100%;max-height:520px'>"
            f"{svg_html}</div>"
        )
        if not large:
            return f"<h3>Circuit Diagram</h3>{scroll}"
        depth = attrs.get("circuit.depth", "?")
        gates = attrs.get("circuit.gate_count", "?")
        return (
            "<h3>Circuit Diagram</h3>"
            "<details><summary style='cursor:pointer;color:#0969da'>"
            f"Large circuit (depth {html_lib.escape(str(depth))}, "
            f"{html_lib.escape(str(gates))} gates) — click to expand"
            f"</summary>{scroll}</details>"
        )

    def _render_circuit_diagram(self, data):
        svg_attr = data["attrs"].get("circuit.svg", "")
        if svg_attr:
            start = svg_attr.find("<svg")
            svg_html = svg_attr[start:] if start >= 0 else svg_attr
            return self._diagram_panel(
                svg_html, data["attrs"], large=len(svg_html) > 120_000
            )

        ascii_diagram = data["attrs"].get("circuit.diagram", "")
        if ascii_diagram:
            return (
                "<h3>Circuit Diagram</h3>"
                "<pre class='code-block' style='max-height:520px;overflow:auto'>"
                f"{html_lib.escape(ascii_diagram)}</pre>"
            )

        return None

    def _render_qasm_source(self, data):
        qasm_code, dialect = _qasm_source(data["attrs"])
        if not qasm_code:
            return None
        heading = "OpenQASM 3" if dialect == "qasm3" else "OpenQASM 2"
        pre = (
            "<pre class='code-block' style='max-height:420px;overflow:auto'>"
            f"{html_lib.escape(qasm_code)}</pre>"
        )
        lines = qasm_code.count("\n") + 1
        if lines <= 30:
            return f"<h3>{heading}</h3>{pre}"
        return (
            f"<h3>{heading}</h3>"
            "<details><summary style='cursor:pointer;color:#0969da'>"
            f"{lines} lines — click to expand</summary>{pre}</details>"
        )

    def _render_counts_chart(self, data):
        counts = data["content"]
        if not isinstance(counts, dict) or not counts:
            return None
        bars = bar_rows(counts, "bar-fill")
        return f"<h3>Measurement Counts</h3><div class='bar-chart'>{bars}</div>"

    def _render_circuit_attrs(self, data):
        rows = [
            (k, v)
            for k, v in sorted(data["attrs"].items())
            if k.startswith("circuit.")
            and k
            not in ("circuit.qasm3", "circuit.qasm2", "circuit.diagram", "circuit.svg")
        ]
        if not rows:
            return None
        trs = "".join(
            f"<tr><td>{k}</td><td>{html_lib.escape(str(v))}</td></tr>" for k, v in rows
        )
        return (
            f"<h3>Circuit Attributes</h3>"
            f"<table><tr><th>Key</th><th>Value</th></tr>{trs}</table>"
        )

    def _render_noise_model(self, data):
        attrs = data["attrs"]
        model = attrs.get("sim.noise_model", "")
        if not model or model == "none":
            return None

        try:
            params = json.loads(attrs.get("sim.noise_params", "") or "{}")
        except Exception:
            params = {}

        trs = f"<tr><td>Model</td><td class='metric-val'>{html_lib.escape(str(model))}</td></tr>"
        for key, val in params.items():
            label = _NOISE_PARAM_LABELS.get(key, key)
            trs += (
                f"<tr><td>{html_lib.escape(label)}</td>"
                f"<td class='metric-val'>{html_lib.escape(str(val))}</td></tr>"
            )
        return (
            f"<h3>Noise Model</h3>"
            f"<table><tr><th>Parameter</th><th>Value</th></tr>{trs}</table>"
        )

    def _render_sim_attrs(self, data):
        rows = [
            (k, v)
            for k, v in sorted(data["attrs"].items())
            if k.startswith("sim.")
            and k not in ("sim.statevector_data", "sim.noise_model", "sim.noise_params")
        ]
        if not rows:
            return None
        trs = "".join(
            f"<tr><td>{k}</td><td>{html_lib.escape(str(v))}</td></tr>" for k, v in rows
        )
        return (
            f"<h3>Simulation Attributes</h3>"
            f"<table><tr><th>Key</th><th>Value</th></tr>{trs}</table>"
        )

    def _render_hardware_attrs(self, data):
        rows = [
            (k, v)
            for k, v in sorted(data["attrs"].items())
            if k.startswith("hw.") and k != "hw.error"
        ]
        if not rows:
            return None
        trs = "".join(
            f"<tr><td>{k}</td><td>{html_lib.escape(str(v))}</td></tr>" for k, v in rows
        )
        return (
            f"<h3>Hardware Execution</h3>"
            f"<table><tr><th>Key</th><th>Value</th></tr>{trs}</table>"
        )

    def _render_derived_result(self, data):
        attrs = data["attrs"]
        kind = attrs.get("result.decode")
        if not kind:
            return None
        top = attrs.get("sim.top_result")
        positions_raw = attrs.get("result.bit_positions")
        if not top or not positions_raw:
            return None

        try:
            positions = [int(p) for p in positions_raw.split(",")]
            bits = "".join(top[p] for p in positions)
            value = int(bits, 2)
        except (ValueError, IndexError):
            return None

        label = attrs.get("result.label", "Decoded value")
        if kind == "phase":
            from fractions import Fraction

            m = len(positions)
            frac = Fraction(value, 2**m)
            value_html = (
                f"{value / (2 ** m):.4f} (= {frac.numerator}/{frac.denominator})"
            )
        else:
            value_html = str(value)

        return (
            f"<h3>Decoded Result</h3>"
            f"<table><tr><th>Key</th><th>Value</th></tr>"
            f"<tr><td>{html_lib.escape(label)}</td><td>{value_html}</td></tr>"
            f"<tr><td>Readout bits (MSB→LSB)</td><td>{html_lib.escape(bits)}</td></tr>"
            f"</table>"
        )

    def _render_vqe_attrs(self, data):
        rows = [
            (k, v) for k, v in sorted(data["attrs"].items()) if k.startswith("vqe.")
        ]
        if not rows:
            return None
        trs = "".join(
            f"<tr><td>{k}</td><td>{html_lib.escape(str(v))}</td></tr>" for k, v in rows
        )
        return f"<h3>VQE Result</h3><table><tr><th>Key</th><th>Value</th></tr>{trs}</table>"

    def _render_qaoa_attrs(self, data):
        rows = [
            (k, v) for k, v in sorted(data["attrs"].items()) if k.startswith("qaoa.")
        ]
        if not rows:
            return None
        trs = "".join(
            "<tr><td>{}</td><td>{}</td></tr>".format(
                k,
                (
                    _bitstring_with_ruler(str(v))
                    if k == "qaoa.best_measurement"
                    else html_lib.escape(str(v))
                ),
            )
            for k, v in rows
        )
        caption = (
            "<p style='color:#999;font-size:0.85em;margin:4px 0 0'>"
            "Bitstrings read left&nbsp;&rarr;&nbsp;right: character <i>i</i> is "
            "qubit/node <i>i</i> (<code>sim.bit_order = q0_left</code>)."
            "</p>"
        )
        return (
            f"<h3>QAOA Result</h3>"
            f"<table><tr><th>Key</th><th>Value</th></tr>{trs}</table>"
            f"{caption}"
        )

    def _render_loss_curve(self, data):
        history = (data.get("content") or {}).get("loss_history")
        if not isinstance(history, list) or len(history) < 2:
            return None
        try:
            ys = [float(v) for v in history]
        except (TypeError, ValueError):
            return None

        w, h, pad = 560, 210, 34
        lo, hi = min(ys), max(ys)
        span = (hi - lo) or 1.0
        pts = []
        for i, v in enumerate(ys):
            px = pad + (w - 2 * pad) * i / (len(ys) - 1)
            py = h - pad - (h - 2 * pad) * (v - lo) / span
            pts.append(f"{px:.1f},{py:.1f}")
        svg = (
            f"<svg viewBox='0 0 {w} {h}' width='100%' role='img'>"
            f"<line x1='{pad}' y1='{h - pad}' x2='{w - pad}' y2='{h - pad}' stroke='#d8dee4'/>"
            f"<line x1='{pad}' y1='{pad}' x2='{pad}' y2='{h - pad}' stroke='#d8dee4'/>"
            f"<polyline points='{' '.join(pts)}' fill='none' stroke='#0969da' stroke-width='2'/>"
            f"<text x='{pad}' y='{pad - 8}' fill='#656d76' font-size='11'>max {hi:.4f}</text>"
            f"<text x='{pad}' y='{h - pad + 16}' fill='#656d76' font-size='11'>min {lo:.4f}</text>"
            f"<text x='{w - pad}' y='{h - pad + 16}' fill='#656d76' font-size='11' text-anchor='end'>epoch {len(ys)}</text>"
            f"</svg>"
        )
        return f"<h3>Training Loss</h3><div class='svg-scroll'>{svg}</div>"

    def _render_train_attrs(self, data):
        rows = [
            (k, v) for k, v in sorted(data["attrs"].items()) if k.startswith("train.")
        ]
        if not rows:
            return None
        trs = "".join(
            f"<tr><td>{k}</td><td>{html_lib.escape(str(v))}</td></tr>" for k, v in rows
        )
        return (
            f"<h3>Training Result</h3>"
            f"<table><tr><th>Key</th><th>Value</th></tr>{trs}</table>"
        )

    def _render_ae_attrs(self, data):
        rows = [(k, v) for k, v in sorted(data["attrs"].items()) if k.startswith("ae.")]
        if not rows:
            return None
        trs = "".join(
            f"<tr><td>{k}</td><td>{html_lib.escape(str(v))}</td></tr>" for k, v in rows
        )
        return (
            f"<h3>Amplitude Estimation Result</h3>"
            f"<table><tr><th>Key</th><th>Value</th></tr>{trs}</table>"
        )

    def _render_consensus_verdict(self, data):
        attrs = data["attrs"]
        verdict = attrs.get("assert.verdict", "")
        if not verdict:
            return None

        badge = _VERDICT_BADGES.get(verdict, html_lib.escape(str(verdict)))
        rows = [("Verdict", badge)]

        reason = attrs.get("assert.reason", "")
        if reason:
            rows.append(("Reason", html_lib.escape(str(reason))))
        expected = attrs.get("consensus.expected", "")
        if expected:
            rows.append(("Expected", f"|{html_lib.escape(str(expected))}&#x27E9;"))
        majority = attrs.get("consensus.majority_top", "")
        if majority:
            count = attrs.get("consensus.majority_count", "?")
            branches = attrs.get("consensus.branches", "?")
            rows.append(
                (
                    "Majority",
                    f"|{html_lib.escape(str(majority))}&#x27E9; "
                    f"({html_lib.escape(str(count))}/{html_lib.escape(str(branches))} branches)",
                )
            )
        dissenters = attrs.get("consensus.dissenters", "")
        rows.append(
            ("Dissenters", html_lib.escape(str(dissenters)) if dissenters else "none")
        )
        max_h = attrs.get("consensus.max_hellinger", "")
        if max_h:
            rows.append(("Max Hellinger between branches", html_lib.escape(str(max_h))))
        case_id = attrs.get("assert.case_id") or attrs.get("test.case_id", "")
        run_id = attrs.get("assert.run_id") or attrs.get("test.run_id", "")
        if case_id or run_id:
            rows.append(
                (
                    "Case / Run",
                    f"{html_lib.escape(str(case_id)) or '?'} / {html_lib.escape(str(run_id)) or '?'}",
                )
            )

        trs = "".join(
            f"<tr><td>{label}</td><td class='metric-val'>{value}</td></tr>"
            for label, value in rows
        )
        return (
            f"<h3>Consensus Verdict</h3>"
            f"<table><tr><th>Field</th><th>Value</th></tr>{trs}</table>"
        )

    def _render_consensus_branches(self, data):
        raw_json = data["attrs"].get("consensus.branches_json", "")
        if not raw_json:
            return None
        try:
            branches = json.loads(raw_json)
        except Exception:
            return None
        if not branches:
            return None

        trs = ""
        for b in branches:
            label = b.get("label", "")
            top = b.get("top", "")
            is_d = b.get("dissent", False)
            badge = (
                '<span class="badge badge-differ">&#x2717; dissents</span>'
                if is_d
                else '<span class="badge badge-agree">&#x2713; majority</span>'
            )
            val_style = "color:#cf222e" if is_d else "color:#1a7f37"
            top_str = f"|{html_lib.escape(str(top))}&#x27E9;" if top else "—"
            trs += (
                f"<tr><td><code>{html_lib.escape(str(label))}</code></td>"
                f"<td style='font-family:monospace;{val_style}'>{top_str}</td>"
                f"<td>{badge}</td></tr>\n"
            )

        return (
            f"<h3>Consensus Branches</h3>"
            f"<div style='max-height:360px;overflow-y:auto'>"
            f"<table><thead><tr><th>Branch</th><th>Top Outcome</th><th>Status</th></tr></thead>"
            f"<tbody>{trs}</tbody></table></div>"
        )

    def _render_mutation_attrs(self, data):
        rows = [
            (k, v) for k, v in sorted(data["attrs"].items()) if k.startswith("mut.")
        ]
        if not rows:
            return None
        trs = "".join(
            f"<tr><td>{k}</td><td>{html_lib.escape(str(v))}</td></tr>" for k, v in rows
        )
        return (
            f"<h3>Mutation</h3><table><tr><th>Key</th><th>Value</th></tr>{trs}</table>"
        )

    def _render_statevector_table(self, data):
        sv_json = data["attrs"].get("sim.statevector_data", "")
        if not sv_json:
            return None
        try:
            state_data = json.loads(sv_json)
        except Exception:
            return None
        if not state_data:
            return None

        sv_rows = ""
        for s in state_data:
            pct = s["prob"] * 100
            amp = _fmt_amplitude(s["amp_real"], s["amp_imag"])
            color = _phase_color(s["phase_deg"])
            sv_rows += (
                f"<tr>"
                f'<td class="sv-state">|{html_lib.escape(str(s["state"]))}&#x27E9;</td>'
                f'<td class="sv-prob">'
                f'<div class="sv-bar-row">'
                f'<div class="sv-bar-track">'
                f'<div class="sv-bar-fill" style="width:{pct:.1f}%"></div>'
                f"</div>"
                f'<span class="sv-pct">{pct:.1f}%</span>'
                f"</div></td>"
                f'<td class="sv-amp">{html_lib.escape(amp)}</td>'
                f'<td class="sv-phase" style="color:{color}">{s["phase_deg"]:.1f}&deg;</td>'
                f"</tr>\n"
            )

        return (
            f"<h3>Statevector &mdash; amplitude &amp; phase</h3>"
            f"<table class='sv-table'>"
            f"<tr><th>State</th><th>Probability</th><th>Amplitude</th><th>Phase</th></tr>"
            f"{sv_rows}"
            f"</table>"
        )

    def _render_comparison_metrics(self, data):
        attrs = data["attrs"]
        fa = attrs.get("compare.framework_a", "A")
        fb = attrs.get("compare.framework_b", "B")
        h = attrs.get("compare.hellinger_distance", "?")
        tv = attrs.get("compare.total_variation", "?")
        fid = attrs.get("compare.fidelity", "?")
        top_a = attrs.get("compare.top_result_a", "?")
        top_b = attrs.get("compare.top_result_b", "?")
        agree = str(attrs.get("compare.agreement", "false")).lower() == "true"
        badge = (
            '<span class="badge badge-agree">&#x2713; agree</span>'
            if agree
            else '<span class="badge badge-differ">&#x2717; differ</span>'
        )
        trs = (
            f"<tr><td>Hellinger Distance</td><td class='metric-val'>{h}</td><td>0 = identical &nbsp;/&nbsp; 1 = disjoint</td></tr>"
            f"<tr><td>Total Variation</td><td class='metric-val'>{tv}</td><td>half the L1 distance between distributions</td></tr>"
            f"<tr><td>Fidelity</td><td class='metric-val'>{fid}</td><td>1 = identical &nbsp;/&nbsp; 0 = disjoint</td></tr>"
            f"<tr><td>Top result</td><td class='metric-val'>"
            f"<span class='tag-a'>|{html_lib.escape(str(top_a))}&#x27E9;</span> / "
            f"<span class='tag-b'>|{html_lib.escape(str(top_b))}&#x27E9;</span>"
            f"</td><td>{badge}</td></tr>"
        )
        return (
            f"<h3>Distance Metrics</h3>"
            f"<table><tr><th>Metric</th><th>Value</th><th>Interpretation</th></tr>{trs}</table>"
        )

    def _render_dual_distribution_chart(self, data):
        content = data["content"]
        if not isinstance(content, dict):
            return None
        dists = content.get("distributions", {})
        if len(dists) < 2:
            return None

        attrs = data["attrs"]
        fa = attrs.get("compare.framework_a", "")
        fb = attrs.get("compare.framework_b", "")
        keys = list(dists.keys())
        dist_a = dists.get(fa) if fa else dists.get(keys[0], {})
        dist_b = dists.get(fb) if fb else dists.get(keys[1], {})
        label_a = fa or keys[0]
        label_b = fb or keys[1]

        bars_a = bar_rows(dist_a, "bar-fill-a")
        bars_b = bar_rows(dist_b, "bar-fill-b")

        return (
            f"<div style='display:flex;gap:0'>"
            f"<div style='flex:1;padding-right:20px;border-right:1px solid #d0d7de'>"
            f"<h3><span class='tag-a'>{html_lib.escape(str(label_a))}</span></h3>"
            f"<div class='bar-chart'>{bars_a}</div>"
            f"</div>"
            f"<div style='flex:1;padding-left:20px'>"
            f"<h3><span class='tag-b'>{html_lib.escape(str(label_b))}</span></h3>"
            f"<div class='bar-chart'>{bars_b}</div>"
            f"</div>"
            f"</div>"
        )

    def _render_raw_content(self, data):
        raw = data["raw"]
        if isinstance(raw, str):
            text = raw
        elif isinstance(raw, bytes):
            try:
                text = raw.decode("utf-8")
            except Exception:
                text = repr(raw)
        else:
            text = str(raw)
        return f"<h3>Content</h3><pre class='code-block'>{html_lib.escape(text)}</pre>"

    def _render_all_attrs(self, data):
        rows = sorted(data["attrs"].items())
        if not rows:
            return None
        trs = "".join(
            f"<tr><td>{k}</td><td>{html_lib.escape(str(v))}</td></tr>" for k, v in rows
        )
        return f"<h3>All Attributes</h3><table><tr><th>Key</th><th>Value</th></tr>{trs}</table>"


def render_run_card(
    run_or_flow, report_type=None, attrs=None, content=None, raw=None, timestamp=None
) -> str:
    """Render a run card HTML string.

    Can be called either as `render_run_card(run)` passing a ReportRun instance,
    or with individual arguments.
    """
    if hasattr(run_or_flow, "report"):
        run = run_or_flow
        raw_bytes = (
            run.raw_payload.encode("utf-8") if isinstance(run.raw_payload, str) else b""
        )
        renderer = RunCardRenderer(
            flow_name=run.report.flow_name,
            report_type=run.report.report_type,
            attrs=run.attributes or {},
            content=run.payload,
            raw=raw_bytes,
            timestamp=run.source_timestamp or run.created_at,
        )
    else:
        renderer = RunCardRenderer(
            flow_name=run_or_flow,
            report_type=report_type,
            attrs=attrs,
            content=content,
            raw=raw,
            timestamp=timestamp,
        )
    return renderer.render()
