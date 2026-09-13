# Canvas screenshots

Generated images of the `AllComponents` process group: every Quanifi processor
placed once, in labelled blocks, with no connections. They exist to illustrate
the component catalogue in the documentation.

| Image | Contents |
| --- | --- |
| `all-components-overview.png` | All 83 processors, nine blocks, one frame |
| `circuit-builders.png` | 26 |
| `algorithms-all-in-one-solvers.png` | 18 |
| `differential-mutation-testing-infrastructure.png` | 11 |
| `simulators.png` | 9 |
| `hamiltonians-problem-encoders.png` | 6 |
| `hardware-batch-lane.png` | 5 |
| `pennylane-qml-lane.png` | 3 |
| `reporting-utility.png` | 3 |
| `hardware-execution.png` | 2 |

Blocks follow the categories in [`../COMPONENTS.md`](../COMPONENTS.md), so a
figure can be dropped beside the table it illustrates.

## Regenerating

Two steps, and NiFi must be stopped for the first one.

```bash
# 1. put the group on the canvas, then start NiFi
python tools/add_all_components_group.py --flow ~/projects/nifi-2.9.0/conf/flow.json.gz

# 2. photograph it (group UUID from the NiFi UI or the API)
export NIFI_USER=... NIFI_PASSWORD=...
python tools/nifi_screenshot.py --group <uuid> --out-dir docs/screenshots
```

`nifi_screenshot.py` drives headless Chrome over the DevTools protocol, reads
the rendered geometry of every processor and label in the same session as the
capture, and crops one image per block from those measurements — so the crops
cannot drift from the screenshot regardless of the zoom NiFi picked.

## Two things visible in the images

**Every processor carries a warning triangle.** The message is "requires an
upstream connection but currently has none", which is inherent to a group whose
components are deliberately unconnected. It is not a defect in the processors:
placing them in a real flow clears it.

**The version under each name is the processor's own.** They are not uniform —
76 are 0.1.0, six 0.2.0, one 0.1.1 — and the tool reads each from the class
rather than assuming, because a wrong bundle version renders a ghost component
that looks real in a screenshot.

## Before adding the group

Raise the Python process limits in `conf/nifi.properties`, or NiFi will hang on
startup with no error:

```
nifi.python.max.processes=400
nifi.python.max.processes.per.extension.type=20
```

If a start does hang, kill the orphaned extension processes before retrying —
they survive the JVM and block the next start:

```bash
pkill -f "nifi-2.9.0/work/python/extensions"
```
