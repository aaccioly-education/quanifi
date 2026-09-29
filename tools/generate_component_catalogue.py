#!/usr/bin/env python3
"""Generate the processor catalogue from source metadata without importing SDKs."""

import ast
import re
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def generate():
    rows = defaultdict(list)
    for path in sorted((ROOT / "nifi_extensions").glob("*.py")):
        tree = ast.parse(path.read_text())
        details = next(
            (
                n
                for n in ast.walk(tree)
                if isinstance(n, ast.ClassDef) and n.name == "ProcessorDetails"
            ),
            None,
        )
        if details is None:
            continue
        description = "See the processor source for configuration details."
        for node in details.body:
            if isinstance(node, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == "description" for t in node.targets
            ):
                description = ast.literal_eval(node.value)
        description = " ".join(description.split()).replace("|", "\\|")
        name = path.stem
        if re.search(r"Batch|JobPoller", name):
            category = "Hardware batch lane"
        elif re.search(r"Device|RuntimeSampler", name):
            category = "Hardware execution"
        elif "Simulator" in name or "Expectation" in name:
            category = "Simulators"
        elif re.search(
            r"Oracle|Assertion|Mutator|MutationScore|Comparison|TestCase", name
        ):
            category = "Differential & mutation-testing infrastructure"
        elif re.search(r"Report|Unitary", name):
            category = "Reporting & utility"
        elif re.search(r"Hamiltonian|Problem|QuboTo", name):
            category = "Hamiltonians & problem encoders"
        elif re.search(r"Classifier|Dataset|FeatureEmbedding", name):
            category = "PennyLane QML lane"
        elif re.search(
            r"VQE|QAOA$|GroverSearch|Shor|AmplitudeEstimation|DeutschJozsa|BernsteinVazirani|SwapTest",
            name,
        ):
            category = "Algorithms (all-in-one solvers)"
        else:
            category = "Circuit builders"
        rows[category].append(
            f"| `{name}` | {description} | [Source](../nifi_extensions/{path.name}) |"
        )
    text = """# Component reference

> **Docker users: only a subset of processors is installed by default.**
> Add class names to `docker/processors.txt` and run `docker compose up -d --build nifi`.
> [Enable additional processors](guides/DOCKER_QUICKSTART.md#adding-processors-to-the-image).

This catalogue is generated from `ProcessorDetails.description` in the processor
source files. Shared helper modules are excluded. Regenerate it with `just docs`.
For FlowFile attributes and processor construction, see
[Creating processors](guides/CREATING_PROCESSORS.md).

"""
    count = sum(len(group) for group in rows.values())
    text += f"The current source tree contains **{count} processors**.\n\n"
    for category, group in sorted(rows.items()):
        text += f"## {category}\n\n| Processor | Description | Source |\n| --- | --- | --- |\n"
        text += "\n".join(group) + "\n\n"
    (ROOT / "docs/COMPONENTS.md").write_text(text.rstrip() + "\n")
    return count


if __name__ == "__main__":
    print(f"Documented {generate()} processors")
