"""Minimal prototype: a Qiskit circuit -> SVG diagram, nothing else.

Run:  python demo/circuit_svg_prototype.py
Then open the printed .svg path in a browser.

This is the irreducible rendering core (same idiom as QiskitCircuitReport.py).
How the SVG gets *displayed* (web app, file link, NiFi content-viewer NAR) is a
separate decision -- this script just proves the diagram generation.
"""

import io
import os

import matplotlib
matplotlib.use("Agg")  # headless: no display needed
import matplotlib.pyplot as plt

from qiskit import QuantumCircuit


def build_circuit() -> QuantumCircuit:
    """A tiny 3-qubit example (Bell + a marked-state flavour)."""
    qc = QuantumCircuit(3, 3)
    qc.h(range(3))
    qc.cz(0, 2)
    qc.h(range(3))
    qc.measure(range(3), range(3))
    return qc


def render_svg(qc: QuantumCircuit) -> str:
    fig = qc.draw("mpl", fold=-1, style={"backgroundcolor": "#ffffff"})
    buf = io.BytesIO()
    fig.savefig(buf, format="svg", bbox_inches="tight", facecolor="white")
    plt.close(fig)
    raw = buf.getvalue().decode("utf-8")
    start = raw.find("<svg")          # strip XML/doctype preamble
    return raw[start:] if start >= 0 else raw


def main() -> None:
    out_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "circuit.svg")
    svg = render_svg(build_circuit())
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(svg)
    print(f"Wrote circuit diagram -> {out_path}")


if __name__ == "__main__":
    main()
