# Mutation testing for Quanifi

> Design doc + build log. Captures the two-layer mutation model, how it maps to
> the QMutBench/Muskit literature, the **decisions taken** (§A), what is **already
> implemented** (§B), and the remaining build order (§7, the resume point).

## 0. TL;DR

- Quanifi can mutate at **two layers**: gate-level circuit content (the
  QMutBench port, Layer A — *built*, `QuantumMutator`) and **high-level
  config/EL knobs** (Layer B, quanifi-native — *built*). We did Layer B first.
- **Terminology (be precise in the write-up).** Only **Layer A is mutation
  testing in the classical DeMillo/Offutt sense**: it injects syntactic faults
  into the *program artifact* (the circuit), and the kill rate measures the
  test harness's fault-detection adequacy. **Layer B mutates the test-case
  parameters, not the program** — present it as *configuration fault
  injection* that measures **oracle sensitivity** (how reliably the consensus
  + ground-truth oracle notices a corrupted run), not as mutation testing per
  se. The structural tell: Layer-B mutants keep the base row's correct
  `test.expected` while the input changes — the inversion of the classical
  kill criterion, where the input stays fixed and the *program's* output is
  compared against the original's. The two layers share the `mut.*`
  bookkeeping, the oracle, and `MutationScoreReport`; only the injection
  point — and therefore the claim each survival rate supports — differs.
- Mutants are judged by a **K-way majority oracle** across the framework
  branches. Two checks, in severity order: **DISAGREE** (a branch dissents from
  the majority — the cross-framework differential signal) and **FAIL** (branches
  agree but the majority answer is wrong vs ground truth — catches whole-case
  mutants that every branch gets equally wrong). `PASS` = agree (and correct).
- Both loops are **end-to-end in code and tested** (481 tests green). Layer B:
  `QuantumTestCaseSource` (mutation pass) → branches → `QuantumConsensusOracle`
  (K-way verdict) → `MutationScoreReport` (survival rate). Layer A:
  `QuantumMutator` between one branch's builder and simulator, same oracle and
  report. What remains is the canvas wiring + first live runs (§7 step 3,
  MUTATION_CANVAS_TESTING.md Stage 8).
- **Controls (`mut.applied=false`) are excluded from the survival rate**; a
  dissenting control is a real cross-framework discrepancy, surfaced separately,
  never a kill (see §3, DISSERTATION.md).
- Adopt QMutBench's **survival-rate** metric and **position-interval bins**
  (0–10%, …, 90–100%) so results are comparable to the published dataset.

## A. Decisions taken

1. **Layer B first.** Build the config/EL mutator before the gate-level Layer-A
   `QuantumMutator`, because the EL knobs are quanifi's native abstraction
   surface and reuse the existing EL attribute contract end-to-end.
2. **Mutate in the test-case source, not a separate wire processor.** A NiFi
   processor can't touch another's *properties*, only the FlowFile. The knobs are
   already EL-driven from attributes, so the mutation pass lives in
   `QuantumTestCaseSource` and rewrites the shared attribute namespace.
3. **K-way majority oracle, not 2-way.** New `QuantumConsensusOracle` collects K
   branches and votes, generalising `QuantumDistributionComparison` +
   `QuantumAssertion`. The old 2-way processors stay for side-by-side reports and
   other flows; they are not removed.
4. **Fold ground truth into the oracle.** Pure consensus can't see a whole-case
   mutant (all branches share the fault and agree). The oracle therefore also
   FAILs an agreed-but-wrong answer vs `test.expected`. DISAGREE = single-branch
   dissent; FAIL = whole-case shared fault; both → killed. (See §3.)
5. **Endian-agnostic voting** via `_canonical(s)=min(s, s[::-1])` so framework
   bit-order conventions are not false disagreements.
6. **Controls excluded from the score; control dissents surfaced separately**
   (dedicated `control_dissent` relationship). A real framework discrepancy must
   never masquerade as a mutation kill (DISSERTATION.md).
7. **Reproducibility** via `Mutation Seed` → per-mutant derived `mut.seed`.
8. **Backward compatibility:** with no `Mutation Operators` configured,
   `QuantumTestCaseSource` output is byte-for-byte the legacy behaviour.
9. **Dropdown-vs-EL override pattern (NiFi UI constraint).** A property with
   `allowable_values` renders as a constrained dropdown and **cannot accept
   Expression Language** — so an attribute-driven mutation can't reach it. Where
   a knob must stay a discoverable menu *and* be data-driven, keep the dropdown
   and add an optional free-text companion `<Name> (EL)` that overrides it when
   non-empty (`override or dropdown`). **Applied to `Noise Model` on
   `QiskitAerSimulator` + `CirqSimulator`** (`Noise Model (EL)`), so `noise.inject`
   is canvas-drivable. The same one-property-companion fix applies to
   `Output Format` (`format.swap`) and `Insert Barriers` (`barriers.toggle`) when
   those operators need to run on the canvas — until then they are covered by the
   unit tests only. Equivalent mutants (barriers / serialization) are better
   produced at the content level by the Layer-A `QuantumMutator` anyway (§7).

## B. Implemented components (build log)

All in the working tree, **not committed**; full suite **402 passed**.

| Component | File | Role | Tests |
|---|---|---|---|
| Gate-level mutator (Layer A) | `nifi_extensions/QuantumMutator.py` | sits builder→simulator on one branch; parses qasm2/qasm3, applies one of `gate.add` / `gate.remove` / `gate.replace` / `rotation.perturb` from `_GATE_MUTATION_OPERATORS`, re-emits same format + full `mut.*` (incl. `mut.gate/position/position_interval/original_format`); controls (`mut.applied=false`) and Layer-B mutants pass through unmutated | `tests/test_quantum_mutator.py` (+21) |
| Mutation pass (Layer B) | `nifi_extensions/QuantumTestCaseSource.py` | `Mutation Operators` / `Mutants Per Row` / `Mutation Seed`; expands each base row → 1 control + cycled mutants with `mut.*`; 8 operators in `_MUTATION_OPERATORS`; `testsource.mutated/mutants/controls` summary | `tests/test_testcase_source.py` (+24) |
| K-way oracle | `nifi_extensions/QuantumConsensusOracle.py` | collects K branch distributions per slot, majority vote (endian-agnostic) + ground-truth check → `assert.verdict` PASS/FAIL/DISAGREE + `consensus.*`; passes `mut.*`/`test.*` through; relationships `pass`/`fail`/`waiting` | `tests/test_consensus_oracle.py` (+18) |
| Survival-rate aggregator | `nifi_extensions/MutationScoreReport.py` | accumulates verdicts per `test.run_id` (dedup by case id), survival rate per operator, controls excluded, `control_dissent` relationship, HTML report + `mutation.*` attrs | `tests/test_mutation_score.py` (+11) |
| Test harness | `tests/conftest.py` | added `nifiapi.relationship.Relationship` stub | — |

Pure cores are unit-tested independently of NiFi/filesystem: `_MUTATION_OPERATORS`
+ `_derive_seed`; `_consensus` / `_canonical` / `_gt_match`; `_score` /
`_classify`.

**Key emitted attributes (the inter-processor contract):**
- mutation pass → `mut.applied`, `mut.operator`, `mut.target_attr`,
  `mut.original_value`, `mut.seed`, `mut.base_case_id`
- oracle → `assert.verdict`, `assert.reason`, `consensus.majority_top`,
  `consensus.dissenters`, `consensus.dissenter_count`, `consensus.max_hellinger`
- aggregator → `mutation.survival_rate`, `mutation.mutation_score`,
  `mutation.mutants/survived/killed/controls/controls_dissenting`

## 1. What QMutBench gives us (and what it doesn't)

QMutBench (Mendiluze Usandizaga et al.) is a dataset of 723,079 Muskit-generated
quantum-circuit mutants over 375 circuits, published as QASM with an online
selection interface. Its mutation model is deliberately narrow:

- **3 operators:** Add gate, Remove gate, Replace gate.
- **Granularity:** individual QASM gate statements, across *all gates × all
  positions*.
- **Characteristic vector:** `(algorithm, #qubits, operator, affected gate,
  position interval)`.
- **Headline metric:** **survival rate** = % of mutants *not* detected (inverse
  of mutation score); emphasises hard-to-detect / equivalent mutants.
- **Artifact:** QASM — chosen precisely so any framework can consume it.

**Why this is only half the story for quanifi.** QMutBench mutates one level
*below* where quanifi operates. Quanifi's thesis is "compose quantum solutions
without gate-level code." A faithful QMutBench port is useful (and lets us
validate against their `.qasm` mutants for shared algorithms — Grover, QFT, QPE,
AE), but the mutations that matter most for a high-level dataflow platform are
on the **knobs**, not the gates. Hence two layers.

## 2. Two mutation layers

### Layer A — Circuit-content mutation (the QMutBench/Muskit port) — **BUILT**

This is **mutation testing proper** (see §0 terminology): the mutated artifact
is the circuit — the program — not the test data.

`QuantumMutator` (`nifi_extensions/QuantumMutator.py`) sits **between a builder
and a simulator on one branch**, parses the FlowFile's circuit, applies one
syntactic mutation, and re-emits it in the same format. It **operates on the
QASM bridge formats** (`circuit.format` = `qasm2` or `qasm3`; cirq_json/qpy
route to `failure` — put the mutator on a QASM-emitting branch), so one mutator
serves Qiskit/Cirq/Qrisp identically and can ingest QMutBench `.qasm` directly.

Registered operators (`_GATE_MUTATION_OPERATORS`):

| Operator | Action | Notes |
|---|---|---|
| `gate.add` | Insert a gate at slot *p* | single-qubit (`x/y/z/h/s/t`) or two-qubit (`cx`/`cz`) on random wire(s) |
| `gate.remove` | Delete one gate statement | `measure`/`barrier` are never touched (barriers are semantically live — see QASM3-barrier bug in PROJECT_SUMMARY §3) |
| `gate.replace` | Same-arity swap (`h→x`, `cx→cz`, …) | Muskit constrains replacement to equal qubit count; ≥3-qubit gates (`mcx`) are not candidates |
| `rotation.perturb` *(extension)* | `rz(θ) → rz(θ+δ)`, δ = `Rotation Epsilon` | not classic Muskit; tunable difficulty — small δ ⇒ high survival rate |

Properties: `Mutation Operators` (comma list; one chosen per FlowFile,
seed-driven, falling through to the next listed operator when inapplicable —
EL like `${mut.operator}` lets the row pick), `Mutation Seed`, `Rotation
Epsilon`. Per-case seed = base seed mixed with `test.case_id` (recorded as
`mut.seed`), so one seed reproduces the whole run's mutants.

**Pass-through gating** (how controls coexist on the mutated branch): a
FlowFile with `mut.applied = "false"` (a control row) passes through
byte-for-byte; one already carrying a Layer-B mutation (`mut.applied = "true"`
*and* `mut.operator` set) also passes through, so layers never stack and kill
attribution stays per-operator. A row with `mut.applied = "true"` and **no**
`mut.operator` is the explicit "mutate me" marker — duplicate each matrix case
as a `false`/`true` pair to get paired control/mutant runs from one matrix.

Mutant attributes (mirror QMutBench's characteristic vector):
`mut.operator`, `mut.gate`, `mut.position`, `mut.position_interval` (decile
bins), `mut.seed`, `mut.original_format` — plus the shared §4 contract
(`mut.target_attr` = gate locus `h@3`, `mut.base_case_id` = `test.case_id`,
so the mutant joins the *same oracle slot* as its unmutated siblings — that is
exactly the single-branch DISAGREE mechanism). The builder's stale `circuit.*`
metrics (depth, gate_count, …) are recomputed for the mutant.

Tests: `tests/test_quantum_mutator.py`. Canvas wiring: MUTATION_CANVAS_TESTING.md
Stage 8. Fills the `Mutator` placeholder referenced in `DATA_DRIVEN_TESTING.md` §3.

### Layer B — Config/EL parameter mutation (quanifi-native) — **BUILT**

Strictly speaking this layer is **configuration fault injection, not mutation
testing** (§0 terminology): it corrupts the test-case row, every branch
faithfully runs the corrupted spec, and the survival rate measures *oracle
sensitivity*. Keep the layer — it probes a failure surface gate-level mutation
cannot (the knobs a quanifi user actually turns) — but claim it as such.

Every processor knob is already EL-driven through the attribute contract
(`DATA_DRIVEN_TESTING.md` §1). So a config mutator never touches another
processor's properties — it **mutates the shared attribute namespace** that
`QuantumTestCaseSource` feeds the branches. A branch reading
`${grover.marked_state}` never knows it's running a mutant.

Operator catalog (grounded in real EL-enabled properties). The eight marked ✅
are **registered and tested** in `_MUTATION_OPERATORS`; the two ⬜ are designed
but not yet wired (they need the VQE/QPE attribute names and a tiny Pauli-string
edit — slot them into the same registry, no other change):

| Operator | Attribute mutated | Transform | Status |
|---|---|---|---|
| `marked_state.bitflip` | `grover.marked_state` | flip one char (`0000→0001`) | ✅ |
| `marked_state.lenshift` | `grover.marked_state` | ±1 length (qubit mismatch) | ✅ |
| `iterations.offbyone` | `grover.num_iterations` | +1 (over-rotate) | ✅ |
| `iterations.zero` | `grover.num_iterations` | → 0 (identity diffuser) | ✅ |
| `shots.shrink` | `grover.shots` | → 8 (shot-noise amplifier) | ✅ |
| `format.swap` | `circuit.output_format` | qasm2 ↔ qasm3 | ✅ |
| `barriers.toggle` | `grover.insert_barriers` | flip true/false | ✅ |
| `noise.inject` | `sim.noise_model` | none → depolarizing | ✅ |
| `hamiltonian.flip` | Pauli string | `Z↔X` one term / drop term / scale coeff | ⬜ |
| `qpe.regsize` | QPE phase-register size | off-by-one | ⬜ |

An operator whose target attribute is absent from a row is **silently skipped**
(logged), so listing all of them against a Grover row is safe. The `*.shots` /
`*.insert_barriers` targets are currently the `grover.*` names; generalise the
registry key if other algorithms expose differently-named shot/barrier knobs.

**Layer B subsumes a structured subset of Layer A:** mutating `num_iterations`
is equivalent to a coordinated batch of Add/Remove gate mutations — but
expressed at the level a quanifi user actually works. That's the novelty over
QMutBench.

## 3. The K-way consensus oracle (how a mutant is killed)

A mutant is judged by `QuantumConsensusOracle` — a **K-way majority vote** over
the framework branches. It generalises the old 2-way
`QuantumDistributionComparison → QuantumAssertion` chain into one processor and
emits the same `assert.verdict` contract.

```
QuantumTestCaseSource (+ mutation pass) → SplitJson → EvaluateJsonPath
   ├─ Qiskit branch  → QiskitGroverCircuit  → QiskitAerSimulator ─┐
   ├─ Cirq branch    → CirqGroverCircuit    → CirqSimulator ──────┤→ QuantumConsensusOracle
   └─ Qrisp branch   → QrispGroverSearch ────────────────────────┘   (one slot per case,
                                                                       collects K=3, votes)
                                                                          │ assert.verdict
                                                                          ▼
                                                                   MutationScoreReport
                                                              (PASS=survived, else=killed)
```

### Verdict semantics (severity order)

The oracle collects K distributions sharing a slot key (`${test.run_id}-${test.case_id}`),
then decides:

1. **DISAGREE** — at least one branch's top result dissents from the majority.
   This is the cross-framework *differential* signal: no golden reference, the
   jury of agreeing frameworks catches the outlier. Most severe.
2. **FAIL** — branches *agree* but the agreed answer is wrong vs `test.expected`
   (ground-truth check, endian-agnostic; skipped when no `test.expected`).
3. **PASS** — branches agree and (if checked) the answer is correct.

`MutationScoreReport` maps `PASS → survived`, anything else → **killed**.

### Why ground truth is folded into the oracle (key decision)

Pure cross-framework consensus alone is **not sufficient** for Layer-B mutation.
A config mutation applied to a whole case (our `QuantumTestCaseSource` mutation
pass rewrites the row that *all* K branches read) makes every branch compute the
**same** wrong answer — so they agree, and consensus alone would mark the mutant
*survived*. Two ways to catch it, both supported:

- **Whole-case mutation + ground truth (current default).** All K branches run
  the mutated params, agree on a wrong answer → the oracle's `test.expected`
  check returns **FAIL** → killed. Requires a per-case `test.expected` (the
  explicit-table form of the Test Matrix carries it).
- **Single-branch mutation + pure consensus (no ground truth needed).** Inject
  the mutation into *one* branch only (e.g. a Layer-A `QuantumMutator`, or route
  the mutant row to one framework while the control row feeds the others under
  the same slot key). The K−1 unmutated jury then makes that branch the lone
  **DISAGREE** → killed. This is the reference-free mode.

So: **DISAGREE = single-branch dissent; FAIL = whole-case shared fault.** Both
land as "killed" in the survival rate. The Layer-B mutation pass produces
whole-case mutants, so seed `test.expected` to score them; the Layer-A
`QuantumMutator` (§2, built) produces single-branch mutants scored by the
reference-free DISAGREE path. Note the two modes support different claims:
whole-case + ground truth assesses the *test matrix + oracle*; single-branch
injection assesses the *differential harness itself* (would cross-framework
consensus catch this bug?) — the standard way differential testers are
evaluated.

### Normalisation (built in — see DATA_DRIVEN_TESTING §"Gotchas for the vote")

- **Bit order:** the oracle compares top results **endian-agnostically** —
  `_canonical(s) = min(s, s[::-1])`, so Qiskit's little-endian readout and
  Cirq/Qrisp's MSB-first readout of one state vote together. (Limitation: also
  merges a genuine state with its bit-reverse; fine for the marked-state/phase
  decodes we score, revisit if a future algorithm needs strict keys.)
- **Shot noise:** the vote is on the argmax (top result), never on count
  equality; `consensus.max_hellinger` is reported for distribution-shape insight.

### Failure mode designed around: control rows

A *real framework bug* (e.g. a missed bit-order normalisation) makes an honest
branch dissent and **looks like a killed mutant on an unmutated row**. Mitigation
(implemented): the mutation pass always emits **control rows** (`mut.applied=false`);
`MutationScoreReport` **excludes controls from the survival rate** and routes a
dissenting control to a dedicated `control_dissent` relationship for
investigation. See DISSERTATION.md for the full rationale.

## 4. Mutant bookkeeping attributes (both layers)

Every mutated row/FlowFile carries, alongside the existing `test.run_id` /
`test.case_id` correlation keys:

- `mut.applied` — `true` / `false` (control)
- `mut.operator` — e.g. `iterations.offbyone`
- `mut.target_attr` — the attribute (Layer B) or gate locus (Layer A)
- `mut.original_value` — pre-mutation value (for the report)
- `mut.seed` — RNG seed for reproducibility
- `mut.base_case_id` — `test.case_id` of the unmutated control this mutant
  derives from (lets the oracle pair a mutant against its own baseline)
- Layer A only: `mut.gate`, `mut.position`, `mut.position_interval`

## 5. Metric: survival rate (adopt QMutBench's framing verbatim)

- `survival_rate(group) = killed=false / total` for a group sharing a
  characteristic vector.
- **Position-interval bins:** 0–10%, …, 90–100% (paper §III).
- Reporting these in the same shape as QMutBench lets us **validate** our
  mutators against the published dataset for shared algorithms, and reuse their
  selection criteria vocabulary.

## 6. Reusable infra checklist

| Need | Status |
|---|---|
| Fan-out one case to K framework branches | ✅ `QuantumTestCaseSource` + EL contract |
| Config (Layer B) mutation | ✅ mutation pass in `QuantumTestCaseSource` |
| K-way verdict (consensus + ground truth) | ✅ `QuantumConsensusOracle` |
| 2-way distance/report (other flows) | ✅ `QuantumDistributionComparison` / `QuantumAssertion` (unchanged) |
| Survival-rate scoring | ✅ `MutationScoreReport` |
| Cross-framework comparability | ✅ EL attribute namespace (`grover.*`, `sim.*`, …) |
| Canvas wiring + first live run | ⬜ §7 step 3 |
| Gate-level mutator (Layer A) | ✅ `QuantumMutator` (§2; canvas wiring ⬜ — MUTATION_CANVAS_TESTING.md Stage 8) |

## 7. Build order — RESUME AT STEP 3

Agreed direction: **Layer B (config/EL) first, K-way consensus oracle.** Steps 1,
2, 2b are done (§B); the loop is complete in code. Step 3 (canvas wiring + first
live run) is the resume point.

1. ~~**Mutation pass in `QuantumTestCaseSource`.**~~ **DONE.** Properties
   `Mutation Operators` (comma list), `Mutants Per Row`, `Mutation Seed`. Each
   base row → one `mut.applied=false` control + cycled mutant rows carrying the
   full `mut.*` bookkeeping (incl. `mut.base_case_id`); `testsource.mutated`/
   `mutants`/`controls` summary attributes. Eight Layer-B operators registered
   (`marked_state.bitflip|lenshift`, `iterations.offbyone|zero`, `shots.shrink`,
   `format.swap`, `barriers.toggle`, `noise.inject`); Hamiltonian/QPE operators
   slot into `_MUTATION_OPERATORS` when needed. No-operators path is byte-for-
   byte legacy. 24 new tests in `tests/test_testcase_source.py`; full suite green
   (373 passed).
2. ~~**Survival-rate aggregator.**~~ **DONE.** `MutationScoreReport` (see §B).
   Consumes `assert.verdict` + `mut.*`, controls excluded, `control_dissent`
   relationship, HTML report + `mutation.*` attrs.
2b. ~~**K-way majority oracle.**~~ **DONE.** `QuantumConsensusOracle` (see §B and
   §3). Replaces the 2-way comparison+assertion for this flow; PASS/FAIL/DISAGREE
   with ground truth folded in; endian-agnostic vote.
3. **← RESUME HERE. Wire the flow on the canvas** (the §3 diagram) and run it
   live. Concretely:
   - Build a 3-branch Grover flow (Qiskit/Cirq/Qrisp) fed by `QuantumTestCaseSource`
     → `SplitJson` → `EvaluateJsonPath` (hoist `grover.*` + `test.*` + `mut.*`
     into attributes), each branch's props set to `${grover.*}` EL.
   - Set `QuantumTestCaseSource` `Mutation Operators = iterations.offbyone,
     marked_state.bitflip`, use the **explicit-table** Test Matrix so each row
     carries `test.expected` (needed for the FAIL path on whole-case mutants).
   - All three branches → one `QuantumConsensusOracle` (`Expected Branches = 3`,
     `Consensus Label = ${test.run_id}-${test.case_id}`) → `MutationScoreReport`.
   - **Acceptance:** an `iterations.offbyone` mutant row is killed (FAIL),
     `iterations.zero` is killed, the control row passes; survival report renders.
   - Needs `flow.json.gz` edits — **back it up first** —
     and a live NiFi. Good to do interactively, not blind.
4. ~~**`QuantumMutator`** for Layer A (gate-level QASM)~~ **DONE** (§2 Layer A).
   Unlocks the single-branch DISAGREE path of §3. Canvas wiring is
   MUTATION_CANVAS_TESTING.md Stage 8; QMutBench `.qasm` cross-validation for
   Grover/QFT/QPE/AE remains open.
5. *(Later)* **Statistical kill criterion.** The vote/kill is currently on the
   argmax (plus ground truth). For mutants that shift the *distribution* without
   flipping the argmax (small `rotation.perturb`, `noise.inject`), promote
   `consensus.max_hellinger` from a reported attribute to a thresholded — or
   better, chi-square/KS-tested — kill decision against the control run's
   distribution. This is the classical "differs from the original program"
   criterion adapted to sampled outputs (what Muskit/QMutPy do), and it also
   closes the distribution-comparison stats gap in the dissertation's
   differential-testing chapter.
6. *(Later)* **Register `hamiltonian.flip` + `qpe.regsize`** in
   `_MUTATION_OPERATORS` once the VQE/QPE attribute names are pinned.
7. *(Later, frontier)* **Flow-structure mutation** — drop/reorder/swap a
   processor in the dataflow itself. No QMutBench analogue; quanifi-native.

## References

- **Built processors:** `nifi_extensions/QuantumMutator.py` (Layer-A gate-level
  mutator), `nifi_extensions/QuantumTestCaseSource.py` (Layer-B mutation pass),
  `nifi_extensions/QuantumConsensusOracle.py` (K-way oracle),
  `nifi_extensions/MutationScoreReport.py` (survival rate). Tests:
  `tests/test_quantum_mutator.py`, `tests/test_testcase_source.py`,
  `tests/test_consensus_oracle.py`, `tests/test_mutation_score.py`.
- `DISSERTATION.md` — the control-row safeguard / masquerading-fault finding.
- `QMutBench.md` (paper, repo root) — dataset, operators, survival rate.
- `DATA_DRIVEN_TESTING.md` — the EL attribute contract, `QuantumTestCaseSource`,
  the `Mutator` placeholder, vote gotchas.
- `PROJECT_SUMMARY.md` — QASM2 bridge, framework conventions.
- Muskit: Mendiluze Usandizaga et al., ASE '21. QMutBench empirical basis:
  *Empirical Software Engineering* 30(4):100, 2025.
- Online: https://enautmendi.github.io/QMutBench/ ·
  https://github.com/EnautMendi/QMutBench
