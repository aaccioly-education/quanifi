# Data-driven differential testing

This is the runnable foundation for **data-driven / equivalence-partition
testing** of the quantum processors: define a table of input combinations once,
fan it out to every framework branch with identical parameters, and (later) vote
on the results. This document covers the two pieces that exist today —
**parameter externalization** and **`QuantumTestCaseSource`** — and the canvas
wiring that connects them. The K-way consensus oracle is the next layer.

## 1. Parameter externalization (the attribute contract)

A separate processor on the wire can only touch the **FlowFile** (content +
attributes), never another processor's configured **properties**. The bridge is
NiFi Expression Language: a property declared with
`ExpressionLanguageScope.FLOWFILE_ATTRIBUTES` can be set on the canvas to
`${attr}` and is then resolved from a FlowFile attribute at run time.

To let one test row drive a processor, set its properties to EL referencing a
shared attribute namespace. The Grover family now has **every** knob
EL-enabled (`Marked State`, `Num Iterations`, `Shots`, `Insert Barriers`,
`Output Format`):

| Attribute                | Drives                          |
| ------------------------ | ------------------------------- |
| `grover.marked_state`    | `Marked State` property         |
| `grover.num_iterations`  | `Num Iterations` property       |
| `grover.shots`           | `Shots` property                |
| `grover.insert_barriers` | `Insert Barriers` property      |
| `circuit.output_format`  | `Output Format` property        |

On the canvas, set e.g. `Num Iterations = ${grover.num_iterations}`. Because the
literal default is preserved, a processor used **without** these attributes still
behaves exactly as before. Use the `replaceEmpty` form to keep a fallback:
`${grover.num_iterations:replaceEmpty('1')}`.

Since the contract is *just attribute names*, the **same** test table drives the
Qiskit, Cirq, and Qrisp branches identically — that is what makes the frameworks
comparable in a single run.

## 2. `QuantumTestCaseSource`

Expands a compact **Test Matrix** into a JSON array of test-case rows (one
trigger FlowFile in → one table out). Two spec shapes, detected by JSON type:

- **Parameter axes** (JSON object) — Cartesian product:
  ```json
  {"grover.marked_state": ["00", "11", "000"], "grover.num_iterations": ["1", "2"]}
  ```
  → 6 cases.
- **Explicit table** (JSON array) — each element is one case used verbatim, so
  you can attach a per-row `test.expected` (ground-truth oracle):
  ```json
  [{"grover.marked_state": "0000", "test.partition": "all-zeros", "test.expected": "0000"},
   {"grover.marked_state": "1",    "test.partition": "n=1",       "test.expected": "1"}]
  ```

Every row is stamped with bookkeeping fields (never clobbering a value the row
sets itself):

- `test.case_id` — `{prefix}-000`, `{prefix}-001`, … (stable, ordered)
- `test.run_id`  — shared by all rows of one generation; the correlation key the
  future consensus oracle uses to group the K framework results of one run
- `test.partition` — equivalence-class label (per-row, or the static property)

## 2a. Arithmetic case manifests (`Case Input Mode`)

`Arithmetic Suite` states a **rule** for picking operand pairs, so the canvas
shows `boundary:2` and not the seven pairs it selects. For a campaign whose
inputs a reader has to be able to check, `Case Input Mode` adds two modes that
state the pairs themselves. The default is `legacy-auto`, which reproduces the
historical precedence exactly — every existing canvas keeps working untouched.

| Mode | Reads | Use it for |
| --- | --- | --- |
| `legacy-auto` (default) | non-blank `Arithmetic Suite`, else `Test Matrix` | everything that already exists |
| `generated-suite` | `Arithmetic Suite`, required | making the generated path explicit |
| `inline-arithmetic-json` | the `Arithmetic Cases` property | a readable demonstration — the cases are on the processor |
| `flowfile-content` | the incoming FlowFile | a reviewed, version-controlled file, fetched with a stock `FetchFile` |

The manifest is deliberately compact
(`experiments/test_cases/arithmetic_campaign1_boundary2.v1.json`):

```json
{
  "schema": "quanifi.arithmetic-cases/v1",
  "suite_id": "arithmetic-campaign1-boundary2-v1",
  "operation": "add",
  "bit_width": 2,
  "cases": [{"a": 0, "b": 0}, {"a": 0, "b": 3}, {"a": 3, "b": 0},
            {"a": 3, "b": 3}, {"a": 1, "b": 3}, {"a": 1, "b": 1},
            {"a": 3, "b": 1}]
}
```

**A case declares operands and nothing else.** Expected answers, carry
classification and `test.partition` are derived by `arithmetic_spec.make_case()`
— the same function a generated suite goes through — so a typo in a hand-edited
file cannot become the oracle. Version 1 is a closed schema: any unknown
top-level or per-case key is a rejection, which is what stops
`"expected_result": 5` from quietly becoming a second source of truth. Array
order is significant and is preserved.

Both explicit modes reject the whole input rather than emitting a prefix of it,
and neither falls back to the other property on error — a malformed file cannot
silently run a stale definition. The offending bytes come back on `failure` with
`testsource.error` and a stable `testsource.error_code`.

### Three digests, three different questions

| Digest | Covers | Changes when |
| --- | --- | --- |
| **case set** (`testsource.case_set_sha256`) | operation, bit width, ordered pairs | a case is added, removed, changed or reordered |
| **manifest** (`testsource.manifest_sha256`) | the above plus `schema` and `suite_id` | also when the file is relabelled |
| **raw** (`testsource.raw_sha256`) | the exact supplied bytes | also when whitespace or key order changes |

The case set is the **scientific input identity**. A different case-set digest
is a different test-case condition, and its results must not be pooled with a
campaign pinned to the old one. The converse does not follow: the same digest is
necessary but not sufficient for pooling — the operator, implementation set,
loci, shots, layout policy, device and blocking rules must match too. Renaming
`suite_id` moves the manifest digest but not the case-set digest, and is not by
itself a new treatment.

A generated suite gets a case-set digest as well (after derivation), which is
how the generated and manifest paths can be shown to be the same treatment. It
has no manifest or raw digest and keeps its existing
`testsource.suite`/`.bit_width`/`.suite_seed` metadata instead.

### Fail-closed guards

`Expected Case Count`, `Expected Case Set SHA-256` and
`Expected Manifest SHA-256` are blank by default and refuse to emit **any** row
when they do not match. A hardware campaign should set all three: the batch size
downstream is a static number, so a quietly changed case list leaves the
submitter waiting forever for a circuit nobody will build.

`Expected Case Count` checks `testsource.base_case_count`, the count *before*
any Layer-B mutation expansion. `testsource.count` keeps its old meaning (rows
emitted, after expansion). They are equal on the arithmetic canvas because every
catalogued Layer-B operator targets a `grover.*`/`circuit.*`/`sim.*` attribute
that an arithmetic row does not carry — gate mutations there come from
`QuantumMutator`, not from this processor.

All the new properties are declared `ExpressionLanguageScope.NONE`. They are
campaign controls, and an incoming FlowFile attribute must not be able to
rewrite which cases an armed batch runs. NiFi Parameter Contexts still
substitute into them at deployment time.

### Provenance

Source-level: `testsource.input_mode`, `testsource.case_set_sha256`,
`testsource.base_case_count`, and — explicit modes only —
`testsource.manifest_sha256`, `.raw_sha256`, `.schema`, `.suite_id`.

The same identity is published a second time under the `arithmetic.` prefix
(`arithmetic.test_case_set_sha256`, `.test_manifest_sha256`, `.test_suite_id`,
`.test_suite_schema`) plus a per-row `arithmetic.test_case_ordinal`. That
duplication is load-bearing: the batch submitter captures everything under its
`Attribute Prefix` onto the manifest entry, so only the `arithmetic.*` copies
survive into a hardware result. The ordinal records the **declared** position —
parallel branch arrival and the submitter's kind-based interleave decide the
actual submission order, which `batch.entry_index` records.

## 3. Canvas wiring

```
GenerateFlowFile                 # any trigger; content ignored
   -> QuantumTestCaseSource      # Test Matrix -> JSON array of rows
   -> SplitJson                  # JsonPath Expression = $   (array -> one FF per row)
   -> EvaluateJsonPath           # Destination = flowfile-attribute, one entry per field:
   |                             #   grover.marked_state   = $['grover.marked_state']
   |                             #   grover.num_iterations = $['grover.num_iterations']
   |                             #   test.case_id          = $['test.case_id']
   |                             #   test.run_id           = $['test.run_id']
   |                             #   test.expected         = $['test.expected']   (if present)
   +-> QiskitGroverCircuit -> QiskitAerSimulator ----.
   +-> CirqGroverCircuit   -> CirqSimulator ---------+--> (consensus oracle, next layer)
   +-> QrispGroverSearch ---------------------------/
```

Each framework branch sets its properties to `${grover.*}` EL, so all three run
the *same* case. `QuantumMutator` (built — the Layer-A gate-level mutator, see
MUTATION_TESTING.md §2) placed between one branch's builder and simulator makes
that branch the dissenter the oracle should flag.

### Equivalence partitions worth seeding

The Grover code paths suggest natural partitions / boundaries: `n = 1`
(degenerate diffuser), all-zeros vs all-ones vs mixed `marked_state` (different
X-layer paths), and optimal vs over-rotated `num_iterations` (the over-rotated
row doubles as a mutant).

## Gotchas for the (future) vote

- **Bit-order normalization.** Qiskit counts are little-endian; Cirq/Qrisp use a
  different convention. The consensus oracle must canonicalize distribution keys
  before comparing, or it will report false disagreements.
- **Shot noise.** Compare on argmax or a distance threshold, never equality.
