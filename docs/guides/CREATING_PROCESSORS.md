# Creating a new NiFi Python processor

Every processor is a single `.py` file dropped into `nifi_extensions/`. NiFi picks it up on the next restart (or after a short poll).

---

## The skeleton

Copy this and fill in the blanks:

```python
from nifiapi.flowfiletransform import FlowFileTransform, FlowFileTransformResult
from nifiapi.properties import PropertyDescriptor, StandardValidators, ExpressionLanguageScope
from nifiapi.__jvm__ import JvmHolder


class MyProcessor(FlowFileTransform):

    # 1. Tells NiFi this is a FlowFile transformer. Do not change this.
    class Java:
        implements = ['org.apache.nifi.python.processor.FlowFileTransform']

    # 2. Metadata and pip packages NiFi will install automatically.
    class ProcessorDetails:
        version = "0.1.0"
        description = "One sentence describing what this does."
        tags = ["quantum", "qiskit"]
        dependencies = ["qiskit>=2.0.0", "qiskit-aer>=0.13.0"]

    # 3. Constructor — always this exact pattern.
    def __init__(self, **kwargs):
        JvmHolder.jvm = kwargs.get('jvm')   # must come first
        super().__init__()                   # no kwargs here

        # Define your properties here (not outside __init__)
        self.my_property = PropertyDescriptor(
            name="My Property",
            description="What the user should type here.",
            required=True,
            default_value="some default",
            validators=[StandardValidators.NON_EMPTY_VALIDATOR],
            expression_language_scope=ExpressionLanguageScope.FLOWFILE_ATTRIBUTES,
        )
        self.descriptors = [self.my_property]

    def getPropertyDescriptors(self):
        return self.descriptors

    # 4. The actual logic. Receives a FlowFile, returns a result.
    def transform(self, context, flowFile):
        # Read your property value
        value = context.getProperty(self.my_property) \
                       .evaluateAttributeExpressions(flowFile) \
                       .getValue()

        # Import heavy libraries here, not at the top of the file
        from qiskit import QuantumCircuit

        # ... do work ...
        output = "result"

        return FlowFileTransformResult(
            relationship="success",
            contents=output.encode("utf-8"),
            attributes={"my.output": output},
        )
```

---

## What each piece does

**`Java` inner class** — required marker so NiFi can find the processor when it scans the file. Never remove it.

**`ProcessorDetails`** — `dependencies` is the list of pip packages NiFi installs into its own isolated environment when the processor first loads. Add anything your code needs here.

**`__init__`** — NiFi passes the JVM gateway as `jvm=`. You must assign it to `JvmHolder.jvm` *before* calling `super().__init__()`, otherwise validators like `StandardValidators.POSITIVE_INTEGER_VALIDATOR` will not work. Properties must also be created here, not as class-level variables — they need the JVM to be ready.

**`transform`** — called once per FlowFile. `context.getProperty(descriptor).getValue()` reads the configured value. Return `FlowFileTransformResult` with `relationship="success"` (or `"failure"`) and optionally new `contents` (bytes) and `attributes` (dict).

**Lazy imports** — import Qiskit (and other heavy packages) inside `transform`, not at the top of the file. This avoids a race condition where the module loads before the dependency venv is fully ready.

---

## Available validators

| Validator | Use for |
|-----------|---------|
| `StandardValidators.NON_EMPTY_VALIDATOR` | any required string |
| `StandardValidators.POSITIVE_INTEGER_VALIDATOR` | counts, shot numbers |
| `StandardValidators.INTEGER_VALIDATOR` | any integer |
| `StandardValidators.NUMBER_VALIDATOR` | floats |
| `StandardValidators.BOOLEAN_VALIDATOR` | true/false flags |

---

## Workflow

1. Create `nifi_extensions/MyProcessor.py` using the skeleton above.
2. Restart NiFi (`bin/nifi.sh restart` from the NiFi home directory).
3. Search for `MyProcessor` in the NiFi UI processor list — it will appear.
4. The first time you place it on the canvas, NiFi installs the pip dependencies. This takes ~30 seconds and the processor shows "Initializing" during that time.
5. After that, subsequent restarts reuse the cached packages (fast).

---

## Qiskit package split to be aware of

Some Qiskit features require extra packages beyond the core:

| Feature | Extra package needed |
|---------|---------------------|
| `qasm3.loads()` (reading QASM3) | `qiskit-qasm3-import` |
| `qasm3.dumps()` (writing QASM3) | none — built into `qiskit` |
| QPY read/write | none — built into `qiskit` |
| Simulation | `qiskit-aer` |

Add the extra package to `ProcessorDetails.dependencies` and delete the processor's cached venv (`work/python/extensions/<Name>/`) before restarting, otherwise NiFi reuses the old install.

---

## Things that will break it silently

- Defining `PropertyDescriptor` outside `__init__` (class-level) — properties won't show up in the UI.
- Calling `super().__init__(**kwargs)` instead of `super().__init__()` — NiFi crashes with a `TypeError`.
- Missing the `Java` inner class — NiFi won't discover the processor at all.
- Importing Qiskit at the top of the file — may fail on first load before the venv is ready.
- Missing an optional Qiskit sub-package — error only appears at runtime when the feature is called, not at load time.
