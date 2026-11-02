"""QrispQIRO: Quantum-Informed Recursive Optimization for Maximum Independent
Set, as a train-only solver (MaxIndependentSetProblem -> QrispQIRO -> counts
simulator -> QuantumQAOAEvaluator -> QuanifiReport)."""

import json
import math

import networkx as nx
import numpy as np
import pytest

import qaoa_contract as qc
import qrisp_qiro
from MaxIndependentSetProblem import MaxIndependentSetProblem
from QuanifiReport import QuanifiReport
from QuantumQAOAEvaluator import QuantumQAOAEvaluator
from QrispQIRO import QrispQIRO
from QrispSimulator import QrispSimulator
from conftest import MockContext, MockFlowFile, result_to_flowfile_merged

# The Classiq MIS notebook graph: nx.fast_gnp_random_graph(8, 0.4, seed=12345).
EDGES = [
    [0, 2], [0, 4], [0, 6], [0, 7], [1, 2], [1, 4], [1, 5],
    [2, 4], [2, 5], [2, 6], [3, 4], [3, 5], [3, 6], [5, 6],
]  # fmt: skip
N = 8
OPTIMAL = {(0, 1, 3), (1, 3, 7), (1, 6, 7), (2, 3, 7), (4, 5, 7), (4, 6, 7)}
DEFAULTS = {"Layers": "3", "Recursions": "3", "Max Iterations": "50", "Random Seed": "11"}


def mis_flowfile(edges=EDGES):
    ff = MockFlowFile()
    r = MaxIndependentSetProblem().transform(
        MockContext(**{"Edges": json.dumps(edges), "Penalty Factor": "2.0"}), ff
    )
    assert r.relationship == "success"
    return result_to_flowfile_merged(r, ff)


def run_qiro(ff=None, **props):
    ff = ff or mis_flowfile()
    return QrispQIRO().transform(MockContext(**{**DEFAULTS, **props}), ff)


@pytest.fixture(scope="module")
def solved():
    r = run_qiro()
    assert r.relationship == "success", r.attributes
    return r


def total_variation(p, q):
    return 0.5 * sum(abs(p.get(k, 0.0) - q.get(k, 0.0)) for k in set(p) | set(q))


def hellinger(p, q):
    keys = set(p) | set(q)
    return math.sqrt(
        0.5 * sum((math.sqrt(p.get(k, 0.0)) - math.sqrt(q.get(k, 0.0))) ** 2 for k in keys)
    )


def test_classiq_graph_is_what_we_think():
    g = nx.fast_gnp_random_graph(8, 0.4, seed=12345)
    assert sorted(map(list, g.edges())) == EDGES


class TestGraph:
    def test_graph_derived_from_hamiltonian_equals_edge_list(self, solved):
        assert json.loads(solved.attributes["qiro.graph_edges"]) == EDGES
        terms, n, _ = qc.read_cost_hamiltonian(mis_flowfile())
        assert n == N
        assert [list(e) for e in qrisp_qiro.edges_from_terms(terms)] == EDGES

    def test_isolated_nodes_are_kept(self):
        # Node 3 has no edge: the Hamiltonian still has 4 qubits.
        r = run_qiro(mis_flowfile([[0, 1], [1, 2]]), Layers="1", Recursions="0")
        assert r.relationship == "success"
        assert r.attributes["qaoa.num_qubits"] == "3"
        g = qrisp_qiro.make_graph(5, [(0, 1)])
        assert sorted(g.nodes()) == [0, 1, 2, 3, 4]

    def test_edges_override(self):
        path = [[0, 1], [1, 2], [2, 3]]
        r = run_qiro(Edges=json.dumps(path), Layers="1", Recursions="1")
        assert r.relationship == "success", r.attributes
        assert json.loads(r.attributes["qiro.graph_edges"]) == path
        # hamiltonian.json stays the ORIGINAL problem
        assert json.loads(r.attributes["hamiltonian.json"])["num_qubits"] == N

    def test_edges_override_out_of_range_fails(self):
        r = run_qiro(Edges="[[0, 99]]")
        assert r.relationship == "failure"
        assert "outside" in r.attributes["qaoa.error"]

    def test_edges_override_bad_json_fails(self):
        r = run_qiro(Edges="not json")
        assert r.relationship == "failure"
        assert "Edges" in r.attributes["qaoa.error"]


class TestReproducibility:
    def test_same_seed_identical_output_with_perturbed_global_rng(self):
        np.random.seed(1)
        a = run_qiro()
        np.random.seed(999)
        np.random.rand(1234)  # consume global RNG state between the runs
        b = run_qiro()
        assert a.relationship == b.relationship == "success"
        assert a.contents == b.contents
        volatile = {"qaoa.elapsed_seconds", "perf.elapsed_seconds"}
        for k in a.attributes:
            if k not in volatile:
                assert a.attributes[k] == b.attributes[k], k

    def test_different_seed_changes_the_angles(self, solved):
        other = run_qiro(**{"Random Seed": "12"})
        assert other.relationship == "success"
        assert other.attributes["qaoa.betas"] != solved.attributes["qaoa.betas"]


class TestCircuit:
    def test_width_and_portable_gates(self, solved):
        a = solved.attributes
        assert a["circuit.num_qubits"] == str(N)
        profile = qc.qasm2_profile(solved.contents.decode())
        assert profile["num_qubits"] == N
        assert set(json.loads(a["circuit.gate_counts"])) <= qc.PORTABLE_GATES
        assert a["circuit.bit_order"] == "q0_left"
        assert a["builder.component"] == "QrispQIRO"
        assert a["qaoa.mixer_type"] == "QIRO_RX"

    def test_exported_qasm_matches_qrisp_final_stage_distribution(self):
        from qiskit import qasm2
        from qiskit.quantum_info import Statevector

        edges = [tuple(e) for e in EDGES]
        result = qrisp_qiro.run_quietly(N, edges, 3, 3, 50, "COBYLA", 11)
        source, _ = qrisp_qiro.export(result, N, 3)
        probs = Statevector(qasm2.loads(source)).probabilities_dict()
        exported = {k[::-1]: p for k, p in probs.items()}  # Qiskit is q0-right
        # Qrisp's measurement dict is rounded to 5 decimals and drops states
        # below ~1e-5, so exact agreement is impossible: total variation
        # distance is the right yardstick (Hellinger would be dominated by
        # the dropped 1e-6 tails).
        assert total_variation(exported, result["res"]) < 1e-4
        assert hellinger(exported, result["res"]) < 5e-3
        # ...and Qrisp's own most likely bitstring is a most likely state of
        # the exported circuit (the final stage has two tied optimal sets,
        # {0,1,3} and {1,3,7}, so compare probabilities, not keys).
        top_bits, top_p = result["top"]
        assert exported[top_bits] == pytest.approx(max(exported.values()), abs=1e-4)
        assert top_p == pytest.approx(exported[top_bits], abs=1e-4)

    def test_hamiltonian_json_is_original_and_evaluator_keys_blank(self, solved):
        a = solved.attributes
        assert json.loads(a["hamiltonian.json"])["num_qubits"] == N
        assert a["qaoa.best_measurement"] == ""
        assert a["qaoa.energy_method"] == "exact_probabilities"
        assert int(a["qaoa.cost_function_evals"]) == 4 * 50
        assert a["qiro.replacement_routine"].startswith(
            "qrisp.algorithms.qiro.create_max_indep_replacement_routine"
        )


class TestTrace:
    def test_trace_is_consistent(self, solved):
        a = solved.attributes
        steps = json.loads(a["qiro.steps"])
        fixed = json.loads(a["qiro.fixed_in_set"])
        excluded = json.loads(a["qiro.excluded"])
        remaining = json.loads(a["qiro.remaining_nodes"])
        active = json.loads(a["qiro.active_qubits"])
        assert len(steps) == int(a["qiro.recursions_done"]) <= int(a["qiro.recursions_requested"])
        assert len(json.loads(a["qiro.stage_energies"])) == len(steps) + 1
        # fixed vertices form an independent set; fixed/excluded are disjoint
        edge_set = {tuple(e) for e in EDGES}
        for u in fixed:
            for v in fixed:
                assert (u, v) not in edge_set
        assert not set(fixed) & set(excluded)
        assert set(remaining) == set(range(N)) - set(fixed) - set(excluded)
        assert remaining == active
        for s in steps:
            assert set(s) >= {"step", "kind", "item", "correlation", "sign", "action",
                              "solutions", "exclusions", "remaining_nodes", "remaining_edges"}  # fmt: skip
            assert s["kind"] in ("vertex", "edge")
            assert s["sign"] in (-1, 1)
            assert abs(s["correlation"]) <= 1.0 + 1e-9
            assert not set(s["solutions"]) & set(s["exclusions"])
            assert set(s["remaining_nodes"]) == (
                set(range(N)) - set(s["solutions"]) - set(s["exclusions"])
            )
        # steps are cumulative
        for prev, nxt in zip(steps, steps[1:]):
            assert set(prev["exclusions"]) <= set(nxt["exclusions"])
            assert set(prev["solutions"]) <= set(nxt["solutions"])
        assert json.loads(a["qiro.internal_top"])["bitstring"]

    def test_svg_is_self_contained(self, solved):
        svg = solved.attributes["qiro.trace_svg"]
        assert svg.startswith("<svg") and svg.endswith("</svg>")
        for forbidden in ("http://www.w3.org/1999/xlink", "<image", "<script", "@import", "url("):
            assert forbidden not in svg
        assert svg.count("<g transform") == int(solved.attributes["qiro.recursions_done"]) + 1

    def test_recursions_zero_is_a_single_qaoa_stage(self):
        r = run_qiro(Recursions="0")
        a = r.attributes
        assert r.relationship == "success", a
        assert a["qiro.recursions_done"] == "0"
        assert json.loads(a["qiro.steps"]) == []
        assert len(json.loads(a["qiro.stage_energies"])) == 1
        assert json.loads(a["qiro.fixed_in_set"]) == []
        assert json.loads(a["qiro.excluded"]) == []
        assert json.loads(a["qiro.active_qubits"]) == list(range(N))
        assert int(a["qaoa.cost_function_evals"]) == 50

    def test_recursions_out_of_range_fails(self):
        r = run_qiro(Recursions=str(N))
        assert r.relationship == "failure"
        assert "Recursions" in r.attributes["qaoa.error"]

    def test_action_texts_and_correlation(self):
        g = qrisp_qiro.make_graph(5, [(0, 1), (1, 2), (2, 3), (3, 4)])
        none = []
        after = g.copy()
        after.remove_nodes_from([0, 2])
        assert qrisp_qiro.describe(1, -1, g, after.nodes(), none, [1]) == (
            "vertex 1 joins the set; its neighbours 0, 2 are removed"
        )
        after = g.copy()
        after.remove_node(4)
        assert qrisp_qiro.describe(4, 1, g, after.nodes(), none, none) == "vertex 4 is dropped"
        after = g.copy()
        after.remove_nodes_from([1, 2])
        assert "both dropped" in qrisp_qiro.describe((1, 2), 1, g, after.nodes(), none, none)
        assert "anti-correlated" in qrisp_qiro.describe((1, 2), -1, g, after.nodes(), none, none)
        res = {"00": 0.25, "01": 0.25, "10": 0.5}
        assert qrisp_qiro.correlation(res, 0) == pytest.approx(0.25 + 0.25 - 0.5)
        assert qrisp_qiro.correlation(res, (0, 1)) == pytest.approx(0.25 - 0.25 - 0.5)


class TestContract:
    def test_wrong_problem_type_routes_to_failure(self):
        ff = mis_flowfile()
        attrs = ff.getAttributes()
        attrs["hamiltonian.problem_type"] = "max_clique"
        r = QrispQIRO().transform(
            MockContext(**DEFAULTS),
            MockFlowFile(content=bytes(ff.getContentsAsBytes()), attributes=attrs),
        )
        assert r.relationship == "failure"
        assert "max_independent_set" in r.attributes["qaoa.error"]

    def test_no_hamiltonian_routes_to_failure(self):
        r = QrispQIRO().transform(MockContext(**DEFAULTS), MockFlowFile())
        assert r.relationship == "failure"
        assert r.attributes["qaoa.error"]

    def test_missing_problem_type_is_accepted(self):
        ff = mis_flowfile([[0, 1], [1, 2]])
        attrs = ff.getAttributes()
        attrs.pop("hamiltonian.problem_type")
        r = QrispQIRO().transform(
            MockContext(**{**DEFAULTS, "Layers": "1", "Recursions": "1"}),
            MockFlowFile(content=bytes(ff.getContentsAsBytes()), attributes=attrs),
        )
        assert r.relationship == "success", r.attributes

    def test_processor_details(self):
        d = QrispQIRO.ProcessorDetails
        assert d.version == "0.1.0"
        assert "qrisp==0.9.5" in d.dependencies and "networkx" in d.dependencies
        names = [p.name for p in QrispQIRO().getPropertyDescriptors()]
        assert names == ["Layers", "Recursions", "Max Iterations", "Optimizer",
                         "Random Seed", "Edges"]  # fmt: skip


class TestEndToEnd:
    @pytest.fixture(scope="class")
    def scored(self, solved):
        ff = result_to_flowfile_merged(solved, mis_flowfile())
        sim = QrispSimulator().transform(
            MockContext(**{"Shots": "2048", "Random Seed": "11"}), ff
        )
        assert sim.relationship == "success", sim.attributes
        ff = result_to_flowfile_merged(sim, ff)
        ev = QuantumQAOAEvaluator().transform(
            MockContext(**{"Hamiltonian": "${hamiltonian.json}"}), ff
        )
        assert ev.relationship == "success", ev.attributes
        return result_to_flowfile_merged(ev, ff)

    def test_evaluator_scores_against_the_original_problem(self, scored):
        a = scored.getAttributes()
        best = a["qaoa.best_measurement"]
        verts = tuple(i for i, c in enumerate(best) if c == "1")
        assert len(best) == N
        assert verts in OPTIMAL
        assert float(a["qaoa.approximation_ratio"]) == pytest.approx(1.0)
        assert float(a["qaoa.optimal_probability"]) > 0.5
        assert a["qaoa.num_optimal_states"] == "6"
        assert a["qaoa.builder"] == "QrispQIRO"
        assert a["qaoa.evaluator"] == "QuantumQAOAEvaluator"
        assert a["report.type"] == "simulation"

    def test_qiro_attributes_survive_the_chain(self, scored, solved):
        a = scored.getAttributes()
        for key in ("qiro.steps", "qiro.trace_svg", "qiro.fixed_in_set", "qiro.excluded",
                    "qiro.remaining_nodes", "qiro.graph_edges"):  # fmt: skip
            assert a[key] == solved.attributes[key], key

    def test_report_renders_qiro_section_only_with_steps(self, scored, tmp_path):
        def report(ff, name):
            r = QuanifiReport().transform(
                MockContext(**{"Flow Name": name, "Reports Directory": str(tmp_path)}), ff
            )
            assert r.relationship == "success", r.attributes
            return (tmp_path / f"{name}.html").read_text()

        page = report(scored, "with-qiro")
        assert "QIRO Steps" in page
        assert "<svg" in page and "qiro.trace_svg" not in page
        steps = json.loads(scored.getAttribute("qiro.steps"))
        assert steps[0]["action"] in page
        assert "Fixed into the set" in page

        attrs = {k: v for k, v in scored.getAttributes().items() if not k.startswith("qiro.")}
        plain = report(
            MockFlowFile(content=bytes(scored.getContentsAsBytes()), attributes=attrs),
            "without-qiro",
        )
        assert "QIRO Steps" not in plain
        assert "QAOA Result" in plain
