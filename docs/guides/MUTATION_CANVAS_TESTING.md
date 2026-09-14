# Canvas testing guide — mutation pipeline (Layers B and A)

> Step-by-step, component-by-component build + test of the mutation flow
> (`QuantumTestCaseSource` mutation pass → 3 framework branches →
> `QuantumConsensusOracle` → `MutationScoreReport`). Build the **success flow**
> first (Stages 1–4), confirm it's green, then **add config fault injection**
> (Stages 5–6, Layer B), **exercise the edge cases** (Stage 7), and finally run
> **gate-level mutation testing proper** (Stage 8, Layer A —
> `QuantumMutator`). Every property value below is literal — paste it as-is.

> **Terminology.** Stages 5–7 inject faults into the *test-case parameters*
> (Layer B) — strictly that is **configuration fault injection** measuring
> *oracle sensitivity*, not mutation testing in the classical sense. Stage 8
> mutates the *circuit itself* on one branch (Layer A) — that is **mutation
> testing proper**, and it evaluates the differential harness via the
> reference-free DISAGREE path. See MUTATION_TESTING.md §0/§2.

All property values that start with `${…}` are NiFi Expression Language; they
read a FlowFile attribute that `EvaluateJsonPath` hoisted from the test-case row.
`:replaceEmpty('x')` supplies a fallback so a processor still works if the
attribute is absent.

## The target topology

```
GenerateFlowFile (Run Once)
  → QuantumTestCaseSource        (matrix → JSON array of rows)
  → SplitJson  ($)               (array → one FlowFile per row)
  → EvaluateJsonPath             (row fields → attributes)   ── matched ─┐
                                                                          │ (clone ×3)
        ┌─────────────────────────────────────────────────────────────┘
        ├─ QiskitGroverCircuit → QiskitAerSimulator ─┐
        ├─ CirqGroverCircuit   → CirqSimulator ──────┤→ QuantumConsensusOracle
        └─ QrispGroverSearch ────────────────────────┘     pass│fail → MutationScoreReport
                                                           waiting → (funnel / auto-terminate)
```

The single `EvaluateJsonPath` `matched` relationship is connected to **all three**
branch-entry processors — NiFi clones the row FlowFile to each, so the three
frameworks run the *same* case and meet again at the one oracle instance.

## Where to look for results

| Artifact | Path |
|---|---|
| Consensus verdicts (per case) | `reports/datadriven-grover-consensus.html` |
| Survival-rate report | `reports/datadriven-grover-mutation.html` |
| Oracle slot buffer (transient) | `reports/tmp/quanifi_consensus_state/` |
| Aggregator state | `reports/datadriven-grover-mutation-state.json` |

Also add a **LogAttribute** processor (or use NiFi Data Provenance → View
Details → Attributes) wherever you want to read `assert.verdict`,
`consensus.dissenters`, `mutation.survival_rate` directly off a FlowFile.

---

## Stage 0 — Prerequisites

1. NiFi 2.9.0 running; the new processors are discoverable in *Add
   Processor* (search "Quantum" / "Mutation"): `QuantumTestCaseSource`,
   `QuantumConsensusOracle`, `MutationScoreReport`, `QuantumMutator` (Stage 8)
   (plus the existing Grover builders/simulators). If a processor is missing, NiFi hasn't loaded the file —
   restart NiFi so it picks up the new `.py` files.
   **Cached-venv trap:** if a processor is *on the canvas* but shows stale or
   missing properties, NiFi is reusing the venv/property cache from when it was
   first placed — a plain restart won't refresh it. Wipe the cache, then restart:
   ```
   rm -rf "$NIFI_HOME/work/python/extensions/<ProcessorName>/"
   ```
2. `reports/` exists (it does). The processors auto-create their `tmp/…` state
   dirs.
3. **Clear stale oracle state before each experiment** (a half-filled slot from a
   previous, interrupted run will corrupt the next vote):
   ```
   rm -rf reports/tmp/quanifi_consensus_state/*
   rm -f  reports/datadriven-grover-mutation-state.json
   ```
4. **Empty all connection queues before each experiment.** Step 3 clears the
   *disk* state, but FlowFiles stranded in queues from an interrupted run
   (unconsumed splits, `waiting` outputs) will replay into the oracle and
   corrupt the next vote just as badly. Right-click the canvas / process group →
   **Empty all queues**.
5. **Start every processor in the flow before Run Once.** A stopped branch is
   the most common cause of the `waiting`-forever trap (see the K-mismatch
   warning in Stage 4 and Troubleshooting).

---

## Stage 1 — The Qiskit branch alone (no oracle yet)

Build and verify one branch end-to-end first.

**GenerateFlowFile**
- Leave defaults. You'll trigger it with right-click → **Run Once** each test.

**QuantumTestCaseSource**
| Property | Value |
|---|---|
| Test Matrix | `[{"grover.marked_state":"11","grover.num_iterations":"1","test.expected":"11","test.partition":"2q"},{"grover.marked_state":"101","grover.num_iterations":"2","test.expected":"101","test.partition":"3q"}]` |
| Case ID Prefix | `case` |
| Mutation Operators | *(leave blank)* |

**SplitJson**
| Property | Value |
|---|---|
| JsonPath Expression | `$` |

Auto-terminate/funnel its `failure`/`original`; wire `split` onward.

**EvaluateJsonPath** — set these once; they cover the whole guide (mutation
included) so you never reconfigure it.
| Property | Value |
|---|---|
| Destination | `flowfile-attribute` |
| Return Type | `scalar` |
| Path Not Found Behavior | `skip` |

Then add one **dynamic property per field** (click +). Property *name* = the
attribute, *value* = the JsonPath:
```
grover.marked_state    = $['grover.marked_state']
grover.num_iterations  = $['grover.num_iterations']
grover.shots           = $['grover.shots']
grover.insert_barriers = $['grover.insert_barriers']
sim.noise_model        = $['sim.noise_model']
test.expected          = $['test.expected']
test.partition         = $['test.partition']
test.case_id           = $['test.case_id']
test.run_id            = $['test.run_id']
mut.applied            = $['mut.applied']
mut.operator           = $['mut.operator']
mut.target_attr        = $['mut.target_attr']
mut.original_value     = $['mut.original_value']
mut.seed               = $['mut.seed']
mut.base_case_id       = $['mut.base_case_id']
```
`Path Not Found Behavior = skip` is essential: control rows have no `mut.operator`
and non-mutated rows have no `mut.*` at all — `skip` lets those pass instead of
routing to `unmatched`/`failure`.

> **Implicit relationships on every Quanifi processor.** NiFi's Python
> framework adds **`original`** and **`failure`** to every Python processor,
> on top of whatever the processor declares (you'll recognise them by their
> generic descriptions). NiFi refuses to start a processor until *all*
> relationships are connected or auto-terminated, so at each Quanifi processor
> in this guide: auto-terminate **`original`** (it's just a copy of the input),
> and auto-terminate or LogAttribute **`failure`** (LogAttribute recommended on
> the builders/simulators — see the `lenshift` warning in Stage 7b).

**Wiring `EvaluateJsonPath` → `QiskitGroverCircuit`.** Drag from the
`EvaluateJsonPath` box to `QiskitGroverCircuit`; in the *Create Connection*
dialog tick the **`matched`** relationship (only). Auto-terminate or funnel
`EvaluateJsonPath`'s `unmatched` and `failure`. With `Path Not Found Behavior =
skip` every row exits via `matched`, so that single connection carries all rows.
(In Stages 3–4 you drag *additional* connections from the same `matched`
relationship to the Cirq and Qrisp entry processors — NiFi clones the FlowFile
to each.)

**QiskitGroverCircuit**
| Property | Value |
|---|---|
| Marked State | `${grover.marked_state}` |
| Num Iterations | `${grover.num_iterations:replaceEmpty('1')}` |
| Insert Barriers | `false` ← literal; this is a dropdown (allowable_values), so it **cannot** take EL or a parameter ref. Leave it `false`. |
| Output Format | `qasm3` |

> **Why `Insert Barriers` won't accept `${…}`:** the descriptor declares
> `allowable_values=["true","false"]`, so NiFi renders a constrained dropdown and
> the "reference parameter"/EL editor is disabled for it. Consequence: the
> `barriers.toggle` mutation operator can't propagate to this property from the
> canvas — it's covered by the unit tests only. To make it canvas-mutable you'd
> drop `allowable_values` from the descriptor (then it becomes a free-text EL
> field). Not needed for this guide.
> **The same constraint applies to `Output Format`** (also a dropdown:
> `qasm3`/`qpy`/`qasm2`), so the `format.swap` mutation operator is likewise
> unit-test-only. With the matrix used in this guide it is harmless-but-useless:
> the rows carry no `circuit.output_format` field, so the operator logs
> "inapplicable … skipping" and emits no mutants. If you *did* add that field to
> a row, the mutants would be emitted but the mutated value could never reach
> the dropdown — every branch would compute the correct answer and the mutants
> would all count as **survived**, silently inflating the survival rate. Leave
> it out of canvas runs.

**QiskitAerSimulator**
| Property | Value |
|---|---|
| Shots | `${grover.shots:replaceEmpty('1024')}` |
| Noise Model | `none` ← dropdown; leave it (it's the human-facing menu) |
| Noise Model (EL) | `${sim.noise_model:replaceEmpty('none')}` ← the EL hatch that drives `noise.inject` |

> The dropdown `Noise Model` can't take EL (allowable_values). The companion
> **`Noise Model (EL)`** free-text property overrides it when non-empty, so the
> `noise.inject` mutation operator reaches the simulator from the canvas while the
> dropdown still shows the pickable options.

Wire `QiskitAerSimulator.success` → a **QuanifiReport** (or LogAttribute) for now.

**Verify Stage 1:** Run Once on GenerateFlowFile. You should get two FlowFiles
out of the simulator with content like `{"11": 1024}` and `{"101": 1024}`
(roughly — shot noise). Confirm `sim.top_result` = `11` / `101` and
`sim.framework = qiskit`. If this works, the EL parameterization is correct.

---

## Stage 2 — Add the oracle with K = 1

Now insert `QuantumConsensusOracle` between the Qiskit simulator and the report,
and run it as a **single-branch** oracle to validate its wiring and the
ground-truth check before fanning out.

**QuantumConsensusOracle**
| Property | Value |
|---|---|
| Reports Directory | `reports` |
| Flow Name | `datadriven-grover` |
| State Directory | `reports/tmp/quanifi_consensus_state` |
| Consensus Label | `${test.run_id}-${test.case_id}` |
| Expected Branches | `1` |
| Branch Label | `${sim.framework:replaceEmpty(${grover.framework})}` |
| Check Ground Truth | `true` |

Wire `QiskitAerSimulator.success` → oracle. Oracle `pass` and `fail` → the
**QuanifiReport you placed in Stage 1** (or a LogAttribute — either is just a
landing spot to inspect the verdict FlowFiles; the oracle writes its own
consensus HTML regardless); `waiting` → auto-terminate; the framework-supplied
`original` and `failure` → auto-terminate (see the implicit-relationships note
in Stage 1). `MutationScoreReport` does **not** enter the flow until Stage 5 —
at that point you'll re-point `pass`/`fail` from here to it.

**Verify Stage 2 (PASS):** Run Once. `reports/datadriven-grover-consensus.html`
shows each case with verdict **PASS** (branch `qiskit`, majority = the marked
state, no dissenters). `assert.verdict = PASS` on the output FlowFile.

**Verify Stage 2 (FAIL path):** temporarily change the first matrix row's
`test.expected` to `00`. Re-run. That case now reports **FAIL** ("all branches
agree on |11⟩ but expected |00⟩"). This proves the ground-truth check — the
mechanism that will kill whole-case mutants. Put `test.expected` back to `11`.

---

## Stage 3 — Add the Cirq branch, K = 2

**CirqGroverCircuit**
| Property | Value |
|---|---|
| Marked State | `${grover.marked_state}` |
| Num Iterations | `${grover.num_iterations:replaceEmpty('1')}` |
| Insert Barriers | `false` ← literal dropdown, same as Qiskit (no EL) |
| Output Format | *(default)* |

Wiring: drag a second connection from `EvaluateJsonPath`'s **`matched`**
relationship to `CirqGroverCircuit` (the row is now cloned to both branches).

**CirqSimulator**
| Property | Value |
|---|---|
| Shots | `${grover.shots:replaceEmpty('1024')}` |
| Noise Model | `none` ← dropdown; leave it |
| Noise Model (EL) | `${sim.noise_model:replaceEmpty('none')}` ← EL hatch (same pattern as Qiskit) |

Wiring: connect **EvaluateJsonPath `matched`** to `CirqGroverCircuit` as well
(now two destinations → each row cloned to both branches). `CirqSimulator.success`
→ the **same** `QuantumConsensusOracle`. Set oracle **Expected Branches = 2**.

**Verify Stage 3:** Run Once. The consensus card now lists two branches
(`qiskit`, `cirq`), both agreeing → **PASS**. This is the cross-framework
agreement working. If you see DISAGREE here on a clean run, that's a real
bit-order/normalisation discrepancy worth noting (the oracle already
canonicalises endianness, so investigate before proceeding).

---

## Stage 4 — Add the Qrisp branch, K = 3 (full success flow)

**QrispGroverSearch** (all-in-one — no separate simulator)
| Property | Value |
|---|---|
| Marked State | `${grover.marked_state}` |
| Num Iterations | `${grover.num_iterations:replaceEmpty('1')}` |
| Shots | `${grover.shots:replaceEmpty('1024')}` |

Wiring: connect **EvaluateJsonPath `matched`** to `QrispGroverSearch` (third
destination). `QrispGroverSearch.success` → the **same** oracle. Set oracle
**Expected Branches = 3**.

**Verify Stage 4 (the success flow):** Run Once. Both cases report **PASS** over
three branches (`qiskit`, `cirq`, `qrisp`), zero dissenters, majority = the
marked state, matching `test.expected`. The success flow is now complete and
trustworthy.

> ⚠ **K must equal the number of running branches.** With Expected Branches = 3
> but a branch stopped, slots never fill and every case sits in `waiting`
> forever (and leaves state files behind). If you see only `waiting`, check all
> three branches are started and clear the state dir (Stage 0.3).

---

## Stage 5 — Add the survival-rate aggregator (baseline, still no mutation)

**MutationScoreReport**
| Property | Value |
|---|---|
| Verdict Attribute | `assert.verdict` |
| Reports Directory | `reports` |
| Flow Name | `datadriven-grover` |

Wiring: oracle `pass` **and** `fail` → `MutationScoreReport`. Its `success` →
auto-terminate; **`control_dissent` → a LogAttribute** (you want to see these).

**LogAttribute** (stock NiFi processor — a debugging tap that prints the
FlowFile's attributes into `logs/nifi-app.log`):
| Property | Value |
|---|---|
| Log Level | `warn` |
| Log Prefix | `CONTROL-DISSENT` |
| Log Payload | `false` |
| Attributes to Log | *(empty = log all)* |

Auto-terminate its `success`. Watch with
`tail -f "$NIFI_HOME/logs/nifi-app.log"`.

**Verify Stage 5:** Run Once. `reports/datadriven-grover-mutation.html` renders.
With no mutation, every row is treated as a control: `mutation.controls = 2`,
`mutation.mutants = 0`, `mutation.controls_dissenting = 0`. A clean baseline.

> **What `control_dissent` is (and why nothing reaches it yet).** From Stage 6
> on, every base case is emitted twice: unmodified (the *control*) and with
> corrupted parameters (the *mutants*). A control must always PASS — if it
> doesn't, the harness itself is broken for that case (a real cross-framework
> discrepancy), so `MutationScoreReport` excludes it from the score and routes
> it out the dedicated **`control_dissent`** relationship for a human to
> investigate. This mirrors the classical mutation-testing precondition that
> the *unmutated* program must pass before kills count.
> In **this** stage mutation is still off, so rows aren't labelled as controls
> yet and nothing will ever arrive at your `control_dissent` LogAttribute —
> that wiring only becomes active in Stage 6+, and Stage 7a demonstrates it
> firing. An empty LogAttribute here is expected, not a wiring bug.

---

## Stage 6 — Introduce mutants

Change **only** `QuantumTestCaseSource` — nothing downstream moves.

| Property | Value |
|---|---|
| Mutation Operators | `iterations.offbyone, marked_state.bitflip` |
| Mutants Per Row | *(blank = one mutant per operator)* |
| Mutation Seed | `777` |

Now each base row expands to **1 control + 2 mutants**. The mutants keep the
base row's `test.expected` (the *correct* answer), but corrupt the parameters —
so all three frameworks compute the *wrong* result, agree on it, and the oracle
returns **FAIL** vs `test.expected` → **killed**.

**Verify Stage 6:** clear state (Stage 0.3), Run Once. In
`datadriven-grover-mutation.html`:
- `marked_state.bitflip` mutants → **killed** (wrong target state ≠ expected).
- `iterations.offbyone` mutants → usually **killed** (over-rotation drops the
  marked-state amplitude; for 2q, 2 iterations sends prob to ~0.25 so the argmax
  flips). If a particular case still lands on the right argmax it shows as
  **survived** — a genuine equivalent/weak mutant, exactly the kind survival rate
  is meant to expose.
- The two control rows → **survived/PASS**, excluded from the score,
  `controls_dissenting = 0`.
- `mutation.survival_rate` / `mutation.mutation_score` summarise it; the per-
  operator table breaks it down.

Reproducibility: re-running with `Mutation Seed = 777` produces identical
mutants (same `mut.seed` per mutant).

---

## Stage 7 — Exercise the verdict paths and the control-row safeguard

### 7a. DISAGREE (single-branch dissent) — and the masquerading-fault demo

Whole-case mutation produces FAIL, not DISAGREE. To see a **single-branch
dissent** (and demonstrate the dissertation's control-row safeguard), deliberately
break one branch so it dissents on *every* row:

- On **CirqGroverCircuit**, set `Marked State` to the literal `00` (ignore the
  EL). Now Cirq always measures `00`, regardless of the row.

Clear state, Run Once. Observe:
- On **control rows**: Qiskit + Qrisp agree on the correct state, Cirq dissents →
  oracle **DISAGREE** → because `mut.applied = false`, `MutationScoreReport`
  routes it to **`control_dissent`** (your LogAttribute), and it is **excluded
  from the survival rate**. This is the safeguard in action: a real framework
  discrepancy is caught and *not* miscounted as a mutation kill.
- The consensus card names `cirq` in the **dissenters** column.

Restore `CirqGroverCircuit` `Marked State` to `${grover.marked_state}` afterward.

### 7b. Per-operator sweep

Set `Mutation Operators` to one operator at a time and confirm the expected
behaviour:
| Operator | Setup | Expected |
|---|---|---|
| `marked_state.bitflip` | as Stage 6 | killed (wrong state) |
| `iterations.zero` | add to list | killed (no amplification → uniform, argmax ≠ expected) |
| `shots.shrink` | add `grover.shots` to a matrix row, e.g. `"grover.shots":"1024"` | mostly survives (8 shots still argmax-correct) — a survival-rate probe, not a guaranteed kill |
| `marked_state.lenshift` | as Stage 6 | odd-shapes the branch (qubit-count mismatch) → DISAGREE or FAIL; **can strand the oracle slot — see warning below** |

> ⚠ **`lenshift` can strand oracle slots.** If the mutated marked state makes a
> *builder* error, that FlowFile exits via the builder's **`failure`**
> relationship and never reaches the oracle — the slot fills to 2/3 and the
> case sits in `waiting` forever (no DISAGREE, no FAIL). Before running this
> operator: wire each builder's and simulator's `failure` relationship to a
> LogAttribute (or funnel) so an erroring branch is *visible*, and expect to
> clear the state dir (Stage 0.3) and empty queues (Stage 0.4) afterwards.

### 7c. Confirm controls are never scored

In any mutated run, `mutation.controls` counts the control rows and
`mutation.survival_rate` is computed over **mutants only**. Verify the survival
rate doesn't change if you add more control rows.

---

## Stage 8 — Layer A: gate-level mutation testing proper (`QuantumMutator`)

Now mutate the **circuit**, not the test data. `QuantumMutator` is inserted on
the **Qiskit branch only**; the unmutated Cirq + Qrisp branches form the jury,
so a detected mutant surfaces as a single-branch **DISAGREE** (qiskit named as
dissenter) — no `test.expected` needed. Nothing else in the flow moves.

**Turn Layer B off first:** set `QuantumTestCaseSource` `Mutation Operators`
back to *(blank)* so the two layers don't mix in one score. Then drive the
control/mutant split from the matrix itself with paired rows (`EvaluateJsonPath`
already hoists `mut.applied` — Stage 1):

| Property (`QuantumTestCaseSource`) | Value |
|---|---|
| Test Matrix | `[{"grover.marked_state":"11","grover.num_iterations":"1","test.expected":"11","mut.applied":"false"},{"grover.marked_state":"11","grover.num_iterations":"1","test.expected":"11","mut.applied":"true"},{"grover.marked_state":"101","grover.num_iterations":"2","test.expected":"101","mut.applied":"false"},{"grover.marked_state":"101","grover.num_iterations":"2","test.expected":"101","mut.applied":"true"}]` |

`mut.applied=false` rows pass through the mutator byte-for-byte (the controls);
`mut.applied=true` rows (no `mut.operator` — that marks a Layer-B mutant, which
would also pass through) get one gate mutation each.

**QuantumMutator** — wire `QiskitGroverCircuit.success` → `QuantumMutator` →
`QiskitAerSimulator` (re-point the existing connection). Auto-terminate
`original`; **`failure` → a LogAttribute** — a mutant that fails to
parse/serialise exits here and strands the oracle slot exactly like `lenshift`
(Stage 7b warning).

| Property | Value |
|---|---|
| Mutation Operators | `gate.add, gate.remove, gate.replace` |
| Mutation Seed | `777` |
| Rotation Epsilon | `0.1` (unused unless `rotation.perturb` is listed) |

The mutator reads `circuit.format` and supports `qasm2`/`qasm3` only —
`QiskitGroverCircuit`'s `Output Format = qasm3` from Stage 1 is already right.

**Verify Stage 8:** clear state (Stage 0.3) + queues (0.4), Run Once. Expect:
- The two `mut.applied=true` cases → oracle **DISAGREE**, `consensus.dissenters
  = qiskit` → **killed**. The verdict FlowFile carries the Layer-A vector:
  `mut.operator` (`gate.add`/`gate.remove`/`gate.replace`), `mut.gate`,
  `mut.position`, `mut.position_interval`.
- The two control cases → **PASS** over 3 branches, excluded from the score.
- `datadriven-grover-mutation.html` breaks survival down by the `gate.*`
  operators.
- Re-running with `Mutation Seed = 777` reproduces identical mutants.

> **Survived gate mutants are expected, and they're the finding.** A `gate.add`
> that inserts a phase-only gate (`z`/`s`/`t`) on a computational-basis state
> just before measurement doesn't change the measured distribution — an
> **equivalent mutant**; argmax voting can't kill it. That's the survival-rate
> signal QMutBench is built around, and the motivation for the statistical
> (distribution-level) kill criterion in MUTATION_TESTING.md §7 step 5.

---

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| All cases stuck in `waiting` | Expected Branches ≠ running branch count, or a branch is stopped. Match K; clear state dir. |
| One case stuck in `waiting` after a mutated run | A branch errored on that mutant (e.g. `lenshift`) and exited via its `failure` relationship, so the slot never filled. Check the builders' `failure` routes; clear state dir + queues. |
| `unmatched`/`failure` out of EvaluateJsonPath | `Path Not Found Behavior` not `skip`, or a JsonPath typo. |
| Oracle DISAGREE on a clean (unmutated) run | Real cross-framework discrepancy (bit order / normalisation). This is a finding — record it; the canonicaliser handles simple endianness but not every convention. |
| Mutant shows `survived` unexpectedly | Could be a genuine equivalent mutant (weak over-rotation), or `test.expected` missing on that row (no ground-truth check → can't FAIL). Use the explicit-table matrix so every row carries `test.expected`. |
| Stale numbers after re-run | Clear `reports/tmp/quanifi_consensus_state/*` and `datadriven-grover-mutation-state.json` between experiments — **and empty all connection queues** (Stage 0.4): queued FlowFiles from the previous run replay into the oracle. |
| `QuantumMutator` routes everything to `failure` | Read `mut.error` on the FlowFile: unsupported `circuit.format` (must be qasm2/qasm3 — check the builder's Output Format), unknown operator name, or `no_applicable_operator` (e.g. only `rotation.perturb` listed against a circuit with no rotation gates). |
| Stage-8 case stuck in `waiting` | The mutant errored downstream (unparseable/unsimulatable mutant) and left via a `failure` relationship, so the oracle slot never filled — same shape as the `lenshift` trap. Check the mutator's and simulator's `failure` LogAttribute; clear state + queues. |
| Processor on canvas shows stale/missing properties | NiFi is reusing the processor's cached venv. `rm -rf nifi-2.9.0/work/python/extensions/<Name>/`, then restart (Stage 0.1). |
| Processor not in Add-Processor list | NiFi hasn't loaded the new file — restart NiFi. |

## Cross-references

- Pipeline design, decisions, and verdict semantics: `MUTATION_TESTING.md`.
- The control-row safeguard rationale: `DISSERTATION.md`.
- The EL attribute contract these branches rely on: `DATA_DRIVEN_TESTING.md`.
