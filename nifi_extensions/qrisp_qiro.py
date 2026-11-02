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

"""Qrisp QIRO (Quantum-Informed Recursive Optimization) driver for Maximum
Independent Set -- the logic behind ``QrispQIRO`` (not a processor).

QIRO (Finzgar et al., PRX Quantum 5, 020327): train a QAOA, read the strongest
one- or two-qubit correlation from the result, use it to fix or remove
vertices (shrinking the graph), then repeat on the smaller problem.

``run`` re-implements the ~15-line loop of ``QIROProblem.run_qiro`` using
Qrisp's own components *unmodified* (``create_max_indep_replacement_routine``,
``create_max_indep_cost_operator_reduced``, ``qiro_rx_mixer``,
``qiro_init_function``, ``find_max``). It differs from ``run_qiro`` in two
ways only: every stage gets an explicit, locally seeded initial point (Qrisp
would otherwise draw from the global NumPy RNG) and exact probabilities
(``shots=None``), and each reduction step is recorded in a human-readable
trace. Adding another problem type later means adding another entry to
``PROBLEMS``.
"""

import contextlib
import io
import json
import math
import time

import qrisp_qaoa

MIS = "max_independent_set"
REPLACEMENT_ROUTINE = (
    "qrisp.algorithms.qiro.create_max_indep_replacement_routine "
    "(unmodified, qrisp 0.9.5)"
)


# ---------------------------------------------------------------------------
# Graph helpers
# ---------------------------------------------------------------------------


def edges_from_terms(terms):
    """Every two-qubit Z-Z term is an edge; anything of higher order is not
    a graph problem and is rejected."""
    edges = set()
    for pauli, indices, _coeff in terms:
        if len(indices) > 2:
            raise ValueError(
                "QrispQIRO supports Maximum Independent Set only: found a "
                "{}-body Z term".format(len(indices))
            )
        if len(indices) == 2:
            a, b = sorted(int(i) for i in indices)
            edges.add((a, b))
    return sorted(edges)


def parse_edges(spec, n):
    """JSON edge list (or ``{"edges": [...]}``) -> sorted unique edges."""
    try:
        data = json.loads(spec)
    except json.JSONDecodeError as exc:
        raise ValueError("Edges must be valid JSON: {}".format(exc)) from exc
    if isinstance(data, dict) and "edges" in data:
        data = data["edges"]
    if not isinstance(data, list):
        raise ValueError("Edges must be a JSON list like [[0, 1], [1, 2]]")
    edges = set()
    for k, e in enumerate(data):
        if not isinstance(e, (list, tuple)) or len(e) < 2:
            raise ValueError("edge {} must be [u, v], got {!r}".format(k, e))
        try:
            u, v = int(e[0]), int(e[1])
        except (TypeError, ValueError) as exc:
            raise ValueError("edge {} must hold integers, got {!r}".format(k, e)) from exc
        if u == v:
            raise ValueError("edge {} is a self-loop ({}, {})".format(k, u, v))
        if not (0 <= u < n and 0 <= v < n):
            raise ValueError(
                "edge {} {!r} is outside the {} qubits of the Hamiltonian".format(
                    k, [u, v], n
                )
            )
        edges.add((min(u, v), max(u, v)))
    return sorted(edges)


def make_graph(n, edges):
    import networkx as nx

    graph = nx.Graph()
    graph.add_nodes_from(range(n))  # keep isolated nodes
    graph.add_edges_from(edges)
    return graph


def _sorted_edges(graph):
    return sorted((min(a, b), max(a, b)) for a, b in graph.edges())


def _fmt_list(items):
    items = sorted(items)
    return ", ".join(str(i) for i in items)


# ---------------------------------------------------------------------------
# Correlation + plain-English action
# ---------------------------------------------------------------------------


def correlation(res, item):
    """<Z_i> (vertex) or <Z_i Z_j> (edge) from a Qrisp result dict, with
    exactly the formula of ``qiro_utils.find_max``."""
    if isinstance(item, int):
        return float(sum(p * pow(-1, int(k[item])) for k, p in res.items()))
    a, b = item
    return float(
        sum(p * pow(-1, int(k[int(a)])) * pow(-1, int(k[int(b)])) for k, p in res.items())
    )


def describe(item, sign, before, after_nodes, before_solutions, after_solutions):
    """Plain-English action, derived by diffing the graph and solutions
    before/after Qrisp's replacement routine."""
    removed = sorted(set(before.nodes()) - set(after_nodes))
    if isinstance(item, int):
        if item in after_solutions and item not in before_solutions:
            if removed:
                return "vertex {} joins the set; its neighbours {} are removed".format(
                    item, _fmt_list(removed)
                )
            return "vertex {} joins the set; it has no neighbours left to remove".format(
                item
            )
        return "vertex {} is dropped".format(item)
    a, b = item
    if sign > 0:
        return "edge ({},{}) correlated: both dropped".format(a, b)
    if removed:
        return "edge ({},{}) anti-correlated: Qrisp removes the neighbours of {} ({})".format(
            a, b, a, _fmt_list(removed)
        )
    return "edge ({},{}) anti-correlated: Qrisp removes the neighbours of {} (none)".format(
        a, b, a
    )


# ---------------------------------------------------------------------------
# The QIRO loop
# ---------------------------------------------------------------------------


# Qrisp's exact simulator runs in complex64, and two independent compiles of
# the same circuit differ by ~1e-6 relative in the probabilities (compile order
# is not deterministic). COBYLA turns that jitter into diverging parameter
# paths, so the optimizer's objective and the reported correlations are
# rounded to 4 decimals, far above the jitter and far below any decision that
# matters here.
COST_DECIMALS = 4
REPORT_DECIMALS = 4


def _stable(cost_factory):
    """Wrap Qrisp's classical cost so the optimizer sees the same float
    regardless of dict iteration order and float32 simulator jitter (sorted
    keys, rounding). Qrisp's function is not altered, only called on a sorted
    copy of the result."""

    def factory(problem):
        inner = cost_factory(problem)

        def cl_cost_function(res):
            return round(float(inner(dict(sorted(res.items())))), COST_DECIMALS)

        return cl_cost_function

    return factory


def run(n, edges, layers, recursions, maxiter, optimizer, seed):
    """Run QIRO for MIS. Returns a dict with the final-stage operators
    (``problem``), the trained angles, the trace and all bookkeeping."""
    import numpy as np
    from qrisp import QuantumVariable
    from qrisp.algorithms.qiro.qiroproblems.qiro_utils import find_max
    from qrisp.qaoa import create_max_indep_set_cl_cost_function
    from qrisp.qiro import (
        QIROProblem,
        create_max_indep_cost_operator_reduced,
        create_max_indep_replacement_routine,
        qiro_init_function,
        qiro_rx_mixer,
    )

    original = make_graph(n, edges)
    # Qrisp's replacement routine shallow-copies (and so mutates) the graph
    # it is given: hand it a private copy, never ``original``.
    problem = QIROProblem(
        problem=original.copy(),
        replacement_routine=create_max_indep_replacement_routine,
        cost_operator=create_max_indep_cost_operator_reduced,
        mixer=qiro_rx_mixer,
        cl_cost_function=_stable(create_max_indep_set_cl_cost_function),
        init_function=qiro_init_function,
    )
    # The classical cost is evaluated on the ORIGINAL graph at every stage
    # (QIROProblem built it from the graph passed above, which Qrisp never
    # mutates: only ``problem.problem`` is reduced).
    problem.callback = True

    # NiFi is one long-lived process: never let Qrisp draw its initial point
    # from the global NumPy RNG. One local generator feeds every stage.
    rng = np.random.default_rng(seed)
    options_optimizer = {"COBYLA": "COBYLA", "NELDER_MEAD": "Nelder-Mead", "POWELL": "Powell"}[
        optimizer
    ]

    def train():
        problem.optimization_costs = []
        init = rng.uniform(0.0, np.pi / 2, 2 * layers)
        theta, fun = problem.optimization_routine(
            lambda: QuantumVariable(n),
            layers,
            {"shots": None},
            "random",
            init,
            options_optimizer,
            {"maxiter": maxiter},
        )
        qv = QuantumVariable(n)
        problem.init_function(qv)
        for i in range(layers):
            problem.cost_operator(qv, theta[i])
            problem.mixer(qv, theta[i + layers])
        res = qv.get_measurement(shots=None)
        return list(theta), float(fun), res, len(problem.optimization_costs)

    started = time.perf_counter()
    stage_energies, stage_evals, steps = [], [], []
    solutions, exclusions = [], []
    stop_note = ""

    problem.set_init_function(problem.init_function)
    theta, fun, res, evals = train()
    stage_energies.append(fun)
    stage_evals.append(evals)

    for index in range(recursions):
        before = problem.problem.copy()  # real copy: Qrisp shares dicts
        before_solutions, before_exclusions = list(solutions), list(exclusions)
        nodes = list(before.nodes())
        edge_lists = [list(e) for e in before.edges()]
        item, _ = find_max(nodes, edge_lists, res, list(solutions))

        new_problem, new_solutions, sign, new_exclusions = (
            create_max_indep_replacement_routine(
                res, [problem.problem, list(solutions), list(exclusions)]
            )
        )
        if item is None or not sign:
            stop_note = (
                "stopped after {} reduction(s): no vertex or edge has a "
                "non-zero correlation".format(index)
            )
            break
        sign = int(sign)
        kind = "vertex" if isinstance(item, int) else "edge"
        item = int(item) if kind == "vertex" else [int(item[0]), int(item[1])]
        value = correlation(res, item if kind == "vertex" else tuple(item))
        solutions, exclusions = list(new_solutions), list(new_exclusions)

        step = {
            "step": index + 1,
            "kind": kind,
            "item": item,
            "correlation": round(value, REPORT_DECIMALS),
            "sign": sign,
            "action": describe(
                item if kind == "vertex" else tuple(item),
                sign,
                before,
                list(new_problem.nodes()),
                before_solutions,
                solutions,
            ),
            "solutions": sorted(solutions),
            "exclusions": sorted(exclusions),
            "remaining_nodes": sorted(
                set(new_problem.nodes()) - set(solutions) - set(exclusions)
            ),
            "remaining_edges": [list(e) for e in _sorted_edges(new_problem)],
        }
        if kind == "edge" and sign < 0:
            # Recorded, not corrected: Qrisp as shipped removes the
            # neighbours of the first endpoint only.
            documented = sorted(
                set(before.adj[item[0]]) & set(before.adj[item[1]])
            )
            step["branch"] = "negative_edge_correlation"
            step["qrisp_removed"] = sorted(set(exclusions) - set(before_exclusions))
            step["common_neighbours"] = documented
        steps.append(step)

        # Exactly as run_qiro does after each replacement.
        problem.problem = new_problem
        problem.cost_operator = problem.qiro_cost_operator(
            [new_problem, solutions, exclusions]
        )
        problem.mixer = problem.qiro_mixer([new_problem, solutions, exclusions])
        problem.init_function = problem.qiro_init_function(
            solutions=solutions, exclusions=exclusions
        )
        theta, fun, res, evals = train()
        stage_energies.append(fun)
        stage_evals.append(evals)

    elapsed = time.perf_counter() - started
    betas, gammas = qrisp_qaoa.from_qrisp_theta(theta, layers)
    top_bits = sorted(res.items(), key=lambda kv: (-round(kv[1], 6), kv[0]))[0]
    top_bits = (top_bits[0], round(float(top_bits[1]), REPORT_DECIMALS))
    fixed = sorted(solutions)
    excluded = sorted(exclusions)
    return {
        "problem": problem,
        "graph": original,
        "betas": betas,
        "gammas": gammas,
        "res": res,
        "steps": steps,
        "stop_note": stop_note,
        "fixed": fixed,
        "excluded": excluded,
        "remaining_nodes": sorted(set(range(n)) - set(fixed) - set(excluded)),
        "remaining_edges": [list(e) for e in _sorted_edges(problem.problem)],
        "stage_energies": stage_energies,
        "stage_evals": stage_evals,
        "optimal_value": stage_energies[-1],
        "top": top_bits,
        "elapsed": elapsed,
    }


def export(result, n, layers):
    """Final stage's trained, bound circuit -> ``(qasm2, diagram)`` over
    exactly ``n`` qubits, q0-left, on the portable gate set."""
    from qiskit import QuantumCircuit, qasm2, transpile
    from qrisp import QuantumVariable

    problem = result["problem"]
    qv = QuantumVariable(n)
    problem.init_function(qv)
    for beta, gamma in zip(result["betas"], result["gammas"]):
        problem.cost_operator(qv, gamma)
        problem.mixer(qv, beta)
    qk = qv.qs.compile().to_qiskit()
    if qk.num_qubits != n:
        raise ValueError(
            "Qrisp compiled circuit has {} qubits, expected {}".format(qk.num_qubits, n)
        )
    single = QuantumCircuit(n)
    single.compose(qk, qubits=list(range(n)), inplace=True)
    single = transpile(
        single, basis_gates=["h", "x", "rx", "ry", "rz", "cx"], optimization_level=0
    )
    return qasm2.dumps(single), str(single.draw("text"))


def run_quietly(*args, **kwargs):
    """``run`` with stdout swallowed: Qrisp's tqdm progress bar prints to
    stdout, which would corrupt NiFi's py4j channel."""
    with contextlib.redirect_stdout(io.StringIO()):
        return run(*args, **kwargs)


# ---------------------------------------------------------------------------
# Trace SVG
# ---------------------------------------------------------------------------

_GREEN = "#1a7f37"
_GREY = "#8c959f"
_INK = "#1f2328"
_ACCENT = "#bf5b04"
_EDGE = "#57606a"
_FAINT = "#d0d7de"


def trace_svg(n, edges, steps):
    """Self-contained inline SVG: the original graph on a circle, a start
    panel then one mini-panel per QIRO step. Filled green = fixed into the
    set, grey dashed = removed, outline = still undecided."""
    cols = 4
    pw, ph = 224, 272
    panels = [
        {
            "title": "Start",
            "sub": "all {} vertices open".format(n),
            "fixed": [],
            "excluded": [],
            "item": None,
        }
    ] + [
        {
            "title": "Step {}".format(s["step"]),
            "sub": s["action"],
            "fixed": s["solutions"],
            "excluded": s["exclusions"],
            "item": s["item"] if s["kind"] == "edge" else [s["item"]],
        }
        for s in steps
    ]
    rows = int(math.ceil(len(panels) / cols))
    width = cols * pw
    height = rows * ph
    radius = 72
    cx, cy = pw / 2, 118

    pos = {
        i: (
            cx + radius * math.cos(-math.pi / 2 + 2 * math.pi * i / n),
            cy + radius * math.sin(-math.pi / 2 + 2 * math.pi * i / n),
        )
        for i in range(n)
    }

    def wrap(text, limit=34):
        words, lines, cur = text.split(), [], ""
        for w in words:
            if cur and len(cur) + 1 + len(w) > limit:
                lines.append(cur)
                cur = w
            else:
                cur = (cur + " " + w).strip()
        if cur:
            lines.append(cur)
        return lines[:3]

    def esc(s):
        return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    out = [
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}" width="100%" '
        'role="img" aria-label="QIRO reduction steps" '
        'font-family="-apple-system, Segoe UI, Helvetica, Arial, sans-serif">'.format(
            w=width, h=height
        ),
        '<rect width="{}" height="{}" fill="#ffffff"/>'.format(width, height),
    ]
    for k, panel in enumerate(panels):
        ox, oy = (k % cols) * pw, (k // cols) * ph
        fixed, excluded = set(panel["fixed"]), set(panel["excluded"])
        chosen = panel["item"] or []
        chosen_edge = (
            tuple(sorted(chosen)) if len(chosen) == 2 else None
        )
        out.append('<g transform="translate({},{})">'.format(ox, oy))
        out.append(
            '<rect x="6" y="6" width="{}" height="{}" rx="8" fill="#ffffff" '
            'stroke="{}"/>'.format(pw - 12, ph - 12, _FAINT)
        )
        out.append(
            '<text x="{}" y="26" text-anchor="middle" font-size="13" '
            'font-weight="700" fill="{}">{}</text>'.format(cx, _INK, esc(panel["title"]))
        )
        for a, b in edges:
            dead = a in excluded or b in excluded
            is_chosen = chosen_edge == (a, b)
            (x1, y1), (x2, y2) = pos[a], pos[b]
            if is_chosen:
                style = 'stroke="{}" stroke-width="3.2"'.format(_ACCENT)
            elif dead:
                style = 'stroke="{}" stroke-width="1" stroke-dasharray="3 3"'.format(
                    _FAINT
                )
            else:
                style = 'stroke="{}" stroke-width="1.6"'.format(_EDGE)
            out.append(
                '<line x1="{:.1f}" y1="{:.1f}" x2="{:.1f}" y2="{:.1f}" {}/>'.format(
                    x1, y1, x2, y2, style
                )
            )
        for i in range(n):
            x, y = pos[i]
            if i in fixed:
                shape = 'fill="{}" stroke="{}" stroke-width="2"'.format(_GREEN, _GREEN)
                txt = "#ffffff"
            elif i in excluded:
                shape = (
                    'fill="#ffffff" stroke="{}" stroke-width="1.6" '
                    'stroke-dasharray="3 2"'.format(_GREY)
                )
                txt = _GREY
            else:
                shape = 'fill="#ffffff" stroke="{}" stroke-width="2"'.format(_INK)
                txt = _INK
            if len(chosen) == 1 and chosen[0] == i:
                out.append(
                    '<circle cx="{:.1f}" cy="{:.1f}" r="19" fill="none" '
                    'stroke="{}" stroke-width="2.5"/>'.format(x, y, _ACCENT)
                )
            out.append(
                '<circle cx="{:.1f}" cy="{:.1f}" r="13" {}/>'.format(x, y, shape)
            )
            out.append(
                '<text x="{:.1f}" y="{:.1f}" text-anchor="middle" font-size="12" '
                'font-weight="600" fill="{}">{}</text>'.format(x, y + 4, txt, i)
            )
        for j, line in enumerate(wrap(panel["sub"])):
            out.append(
                '<text x="{}" y="{}" text-anchor="middle" font-size="11" '
                'fill="{}">{}</text>'.format(cx, 236 + j * 14, _INK, esc(line))
            )
        out.append("</g>")
    out.append("</svg>")
    return "".join(out)
