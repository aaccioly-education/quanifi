# Demo runbook — showing the arithmetic experiment safely

How to demonstrate the experiment to a group without touching the running
campaigns, without spending provider credit, and without contaminating the
results.

There are four things a careless demo can damage. The design below removes each
one structurally rather than by remembering to be careful.

| risk | how it is removed |
| --- | --- |
| spending QPU credit | the demo group has **no submitter and no poller**, and no processor even has an `API Token` property. It cannot reach a provider. |
| contaminating the campaign | the demo uses a **different case set**, so its digest differs. `arithmetic_campaign_report.py` pools by digest and refuses to render if a demo archive is all it finds. |
| disturbing a campaign group | the demo is a separate process group with its own name and its own reports directory. Campaign groups are never started. |
| breaking NiFi | the demo group is 12 processors, not 25, and is built once — well before the demo, not during it. |

---

## 1. Build it (do this a day early, not on the day)

NiFi must be stopped, and it needs a restart afterwards. Restarts on this
instance have wedged before, so leave time.

```bash
just nifi-stop

python tools/add_arithmetic_group.py \
    --flow ~/projects/nifi-2.9.0/conf/flow.json.gz \
    --case-mode inline \
    --cases-file experiments/test_cases/arithmetic_demo_2bit.v1.json \
    --group-name "DEMO — Arithmetic (simulator, 4 cases)" \
    --reports-dir "$HOME/quanifi-reports/demo"

just nifi-start
```

Then confirm, before the audience arrives, that the group is valid and that
every campaign group is still stopped and in `preflight`.

To remove it afterwards: same command with `--group-name "DEMO — …" --remove`.

### Why a different case set

`experiments/test_cases/arithmetic_demo_2bit.v1.json` declares four pairs —
`0+0`, `1+1`, `1+3`, `3+3` — two that carry out of two bits and two that do not.
That is enough to show the effect the study is about, and its case-set digest is
`088a325f…`, not the campaign's `b9ad5c54…`.

This matters more than it looks. A demo built from the *campaign's* manifest
would carry the campaign's digest, and the campaign report pools by digest
alone: a demo archive would silently become a tenth job. With a different digest
the report refuses outright and names the mismatch. `tests/test_demo_group.py`
pins that behaviour.

---

## 2. What to show, in order

**a. The canvas states the experiment.** Open
`DEMO — Arithmetic (simulator, 4 cases)` → `Arithmetic — Test Cases`. The four
operand pairs are readable as JSON on the processor. This is the point of the
whole `Case Input Mode` work: the inputs used to be a rule (`boundary:2`) that
lived in Python, and now they are data on the canvas.

Worth saying out loud: a case declares **only its operands**. The expected
answer, the carry flag and the partition label are all derived by
`arithmetic_spec.make_case()`. A typo in that box cannot become the oracle.

**b. The guards.** Show `Expected Case Count = 4` and
`Expected Case Set SHA-256 = 088a325f…`. Then edit one operand in the JSON and
start the processor: it routes to `failure` with
`testsource.error_code = guard.case_set_digest` and emits **no rows at all**.
Undo the edit. This is the most convincing thirty seconds in the demo — it shows
the experiment refusing to run rather than running something different.

**c. Run it.** Start the group, then start the trigger once and stop it. Three
frameworks build the same four cases, three simulators run them, and the oracle
scores each against the classical answer. The report lands in
`~/quanifi-reports/demo/`.

**d. The real results.** Open the two campaign reports — they are
self-contained HTML and open from `file://`:

- `reports/arithmetic-campaign.html` — IBM `ibm_kingston`
- `reports/arithmetic-campaign-iqm.html` — IQM `garnet`

These are the actual hardware findings, and the "Campaign incomplete" alert is
honest rather than embarrassing: it is the tool refusing to let a partial
campaign be read as confirmatory.

---

## 3. Rules for the day

- **Start nothing outside the DEMO group.** Every campaign group is stopped and
  in `preflight`; leave them that way.
- **Never set `Submit Mode` to `armed`** on any group during a demo. The demo
  group has no submitter at all, so this only applies if someone opens a
  campaign group to look at it.
- If you want to show the *hardware* path, show it in `preflight`: it costs the
  batch, reports the layout and shot requirement, and stops. Use a campaign
  group read-only, or build a preflight-only clone with
  `tools/add_arithmetic_hw_group.py --case-mode inline --group-name "DEMO HW …"`.
  A preflight makes read-only provider calls, so it needs a token and a network.
- Do not run `arithmetic_campaign_report.py` against a glob that includes the
  demo archives. It will refuse, which is correct, but it is a confusing thing
  to hit live.

---

## 4. If you want no NiFi at all

The whole experiment runs headless, which is the lowest-risk demo there is:

```bash
# The full simulator study, scored against the classical answer
just experiment-arithmetic

# Cost a hardware batch without submitting anything
.venv/bin/python experiments/arithmetic_preflight.py \
    --cases-file experiments/test_cases/arithmetic_demo_2bit.v1.json
```

Both use the same `arithmetic_spec` parser and the same derivation the canvas
uses, so what you show headless is what the canvas does.
