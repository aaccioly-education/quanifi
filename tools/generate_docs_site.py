#!/usr/bin/env python3
"""
generate_docs_site.py

Static site generator for Quanifi documentation.
Builds responsive, accessible HTML pages in docs/ for GitHub Pages deployment.
"""

import os
import re
import shutil
import glob
from pathlib import Path
import markdown

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DOCS_DIR = PROJECT_ROOT / "docs"
ASSETS_DIR = DOCS_DIR / "assets"
TUTORIALS_DIR = DOCS_DIR / "tutorials"
GUIDES_SRC_DIR = PROJECT_ROOT / "guides"

PAGES_MAP = [
    ("guides/DOCKER_QUICKSTART.md", "guides/docker-quickstart.html", "Docker Quick Start — Quanifi", "guides", [("Home", "../index.html"), ("Docker Quick Start", "docker-quickstart.html")]),
    # (source_rel_path, target_rel_path, page_title, active_tab, breadcrumb)
    ("README.md", "index.html", "Quanifi Documentation — Visual Quantum Software Engineering", "home", [("Home", "index.html")]),
    ("COMPONENTS.md", "components.html", "Component Reference — Quanifi Processor Catalogue", "components", [("Home", "index.html"), ("Components", "components.html")]),
    ("screenshots/README.md", "screenshots/index.html", "Canvas Screenshots Gallery — Quanifi Process Groups", "gallery", [("Home", "../index.html"), ("Screenshots", "index.html")]),
    ("guides/DEVELOPER_GUIDE.md", "guides/developer-guide.html", "Developer Guide — Quanifi Framework", "guides", [("Home", "../index.html"), ("Guides", "developer-guide.html"), ("Developer Guide", "developer-guide.html")]),
    ("guides/CREATING_PROCESSORS.md", "guides/creating-processors.html", "Creating Processors — NiFi Python Extensions", "guides", [("Home", "../index.html"), ("Guides", "developer-guide.html"), ("Creating Processors", "creating-processors.html")]),
    ("guides/NIFI_FLOW_CONFIGURATION_GUIDE.md", "guides/nifi-flow-configuration-guide.html", "NiFi Flow Configuration Guide — Visual Quantum Pipelines", "guides", [("Home", "../index.html"), ("Guides", "developer-guide.html"), ("Flow Configuration", "nifi-flow-configuration-guide.html")]),
    ("guides/PYQUIL_COMPONENTS.md", "guides/pyquil-components.html", "pyQuil Components & Rigetti QVM Integration", "guides", [("Home", "../index.html"), ("Guides", "developer-guide.html"), ("pyQuil Components", "pyquil-components.html")]),
    ("guides/QAOA_COMPONENTS.md", "guides/qaoa-components.html", "QAOA Components & N×M Cross-Framework Flow", "guides", [("Home", "../index.html"), ("Guides", "developer-guide.html"), ("QAOA Components", "qaoa-components.html")]),
    ("guides/INTERCHANGEABLE_GROVER_FLOW.md", "guides/interchangeable-grover-flow.html", "Interchangeable Grover Canvas Guide", "guides", [("Home", "../index.html"), ("Guides", "developer-guide.html"), ("Interchangeable Grover", "interchangeable-grover-flow.html")]),
    ("guides/DATA_DRIVEN_TESTING.md", "guides/data-driven-testing.html", "Data-Driven Differential Testing", "testing", [("Home", "../index.html"), ("Testing", "data-driven-testing.html"), ("Data-Driven Testing", "data-driven-testing.html")]),
    ("guides/MUTATION_CANVAS_TESTING.md", "guides/mutation-canvas-testing.html", "Mutation Canvas Testing Guide (Layers A & B)", "testing", [("Home", "../index.html"), ("Testing", "data-driven-testing.html"), ("Canvas Testing", "mutation-canvas-testing.html")]),
]

LINK_REPLACEMENTS = [
    (r'guides/DOCKER_QUICKSTART\.md', 'guides/docker-quickstart.html'),
    (r'DOCKER_QUICKSTART\.md', 'docker-quickstart.html'),
    (r'COMPONENTS\.md', 'components.html'),
    (r'\.\./COMPONENTS\.md', '../components.html'),
    (r'guides/DEVELOPER_GUIDE\.md', 'guides/developer-guide.html'),
    (r'DEVELOPER_GUIDE\.md', 'developer-guide.html'),
    (r'guides/CREATING_PROCESSORS\.md', 'guides/creating-processors.html'),
    (r'CREATING_PROCESSORS\.md', 'creating-processors.html'),
    (r'guides/NIFI_FLOW_CONFIGURATION_GUIDE\.md', 'guides/nifi-flow-configuration-guide.html'),
    (r'NIFI_FLOW_CONFIGURATION_GUIDE\.md', 'nifi-flow-configuration-guide.html'),
    (r'guides/PYQUIL_COMPONENTS\.md', 'guides/pyquil-components.html'),
    (r'PYQUIL_COMPONENTS\.md', 'pyquil-components.html'),
    (r'guides/QAOA_COMPONENTS\.md', 'guides/qaoa-components.html'),
    (r'QAOA_COMPONENTS\.md', 'qaoa-components.html'),
    (r'guides/INTERCHANGEABLE_GROVER_FLOW\.md', 'guides/interchangeable-grover-flow.html'),
    (r'INTERCHANGEABLE_GROVER_FLOW\.md', 'interchangeable-grover-flow.html'),
    (r'guides/DATA_DRIVEN_TESTING\.md', 'guides/data-driven-testing.html'),
    (r'DATA_DRIVEN_TESTING\.md', 'data-driven-testing.html'),
    (r'guides/MUTATION_CANVAS_TESTING\.md', 'guides/mutation-canvas-testing.html'),
    (r'MUTATION_CANVAS_TESTING\.md', 'mutation-canvas-testing.html'),
    (r'screenshots/README\.md', 'screenshots/index.html'),
    (r'\.\./README\.md', '../index.html'),
    (r'README\.md', 'index.html'),
    (r'\.\./guides/', 'tutorials/index.html'),
]

SCREENSHOT_INFO = [
    ("all-components-overview.png", "All Components Overview", "All 83 Quanifi processors placed in labelled category blocks with no connections.", "Overview"),
    ("circuit-builders.png", "Circuit Builders", "26 modular processors across Qiskit, Cirq, Qrisp, pyQuil, and PennyLane.", "Builders"),
    ("algorithms-all-in-one-solvers.png", "Algorithms & Solvers", "18 all-in-one quantum algorithms: Grover, QAOA, VQE, QPE, Deutsch-Jozsa, Bernstein-Vazirani.", "Solvers"),
    ("differential-mutation-testing-infrastructure.png", "Differential & Mutation Testing", "11 testing processors: mutators, assertions, distribution oracles, and consensus verification.", "Testing"),
    ("simulators.png", "Quantum Simulators", "9 simulator backends including Aer, Statevector, Cirq, Qrisp, Braket, and PyQVM.", "Simulators"),
    ("hamiltonians-problem-encoders.png", "Hamiltonians & Problem Encoders", "6 combinatorial optimization encoders: MaxCut, MaxClique, MIS, Portfolio, Molecule.", "Encoders"),
    ("hardware-batch-lane.png", "Hardware Batch Lane", "5 batch execution poller and submitter processors for remote QPU platforms.", "Hardware"),
    ("hardware-execution.png", "Hardware Direct Execution", "2 direct cloud submission processors for IBM Quantum and IQM hardware.", "Hardware"),
    ("pennylane-qml-lane.png", "PennyLane QML Lane", "3 quantum machine learning components: Feature Embedding, Variational Classifier, Ansatz.", "QML"),
    ("reporting-utility.png", "Reporting & Unitary Utilities", "3 monitoring and verification utilities for NiFi FlowFiles.", "Utilities"),
    ("portfolio-optimization-qaoa-mean-variance-objective-with-xy-mixer-selects-optimal-2-assets-out-of-3-under-risk-return-trade-off-and-budget-k.png", "Portfolio QAOA Optimization", "Mean-variance portfolio selection flow with XY mixer selecting 2 optimal assets out of 3.", "Flows"),
    ("maximum-clique-qaoa-encodes-graph-complement-into-cost-hamiltonian-finds-maximum-clique-on-4-node-graph-via-qaoa-with-standard-rx-mixer.png", "Maximum Clique QAOA", "Graph complement encoding into cost Hamiltonian solved via QAOA with standard RX mixer.", "Flows"),
    ("maximum-independent-set-qaoa-graph-vertex-independence-optimization-finds-mis-on-3-node-path-graph-via-qaoa-with-edge-conflict-penalties.png", "Maximum Independent Set QAOA", "Graph vertex independence optimization on 3-node path graph via QAOA with penalty terms.", "Flows"),
    ("molecular-vqe-with-qccsd-unitary-coupled-cluster-with-hartree-fock-state-solves-ground-state-energy-of-4-qubit-hamiltonian-via-qccsd-ansatz-and-vqe.png", "Molecular VQE (UCCSD)", "Ground state energy calculation of 4-qubit molecular Hamiltonian using QCCSD ansatz and VQE.", "Flows"),
    ("swap-test-measures-quantum-state-overlap-via-ancilla-fredkin-gate-computes-fidelity-between-0-and-0-states-with-1024-measurement-shots.png", "Swap Test State Overlap", "Quantum state fidelity verification using Fredkin gate ancilla and 1024 measurement shots.", "Flows"),
    ("deutsch-jozsa-algorithm-distinguishes-constant-from-balanced-boolean-functions-single-query-evaluation-of-balanced-boolean-oracle-over-3-qubits.png", "Deutsch-Jozsa Algorithm Flow", "Single-query evaluation of 3-qubit balanced boolean oracle vs constant function.", "Flows"),
    ("bernstein-vazirani-algorithm-recovers-hidden-bitstring-s-in-o-1-queries-recovers-secret-s-1011-using-phase-kickback-and-hadamard-transform.png", "Bernstein-Vazirani Flow", "O(1) recovery of secret bitstring s='1011' using phase kickback and Hadamard transforms.", "Flows"),
]

def get_rel_root(target_path):
    parts = Path(target_path).parts
    depth = len(parts) - 1
    return "../" * depth if depth > 0 else "./"

def fix_links(html_content, current_rel_path):
    def replace_link(match):
        target, separator, fragment = match.group(2).partition("#")
        for pattern, replacement in LINK_REPLACEMENTS:
            if re.fullmatch(pattern, target):
                target = replacement
                break
        external = re.fullmatch(r"(?:\.\./)+(nifi_extensions|tests|tools|demo|experiments)/(.*)", target)
        if external:
            target = "https://github.com/saeg/quanifi/tree/main/" + external.group(1) + "/" + external.group(2)
        return 'href="' + target + (separator + fragment if separator else "") + '"'
    return re.sub(r"href=([\"'])(.*?)\1", replace_link, html_content)


def build_sidebar(active_target_path, rel_root):
    items = [
        # (label, rel_url, group, badge)
        ("Getting Started", f"{rel_root}tutorials/01_introduction_to_quanifi.html", "Core Reference", "Start"),
        ("Documentation Hub", f"{rel_root}index.html", "Core Reference", ""),
        ("Component Catalogue", f"{rel_root}components.html", "Core Reference", "all"),
        ("Screenshots Gallery", f"{rel_root}screenshots/index.html", "Core Reference", "18"),
        
        ("Developer Guide", f"{rel_root}guides/developer-guide.html", "Framework Guides", ""),
        ("Creating Processors", f"{rel_root}guides/creating-processors.html", "Framework Guides", ""),
        ("NiFi Flow Configuration", f"{rel_root}guides/nifi-flow-configuration-guide.html", "Framework Guides", ""),
        ("pyQuil Components", f"{rel_root}guides/pyquil-components.html", "Framework Guides", "Rigetti"),
        ("QAOA Components & Matrix", f"{rel_root}guides/qaoa-components.html", "Framework Guides", "N×M"),
        ("Interchangeable Grover", f"{rel_root}guides/interchangeable-grover-flow.html", "Framework Guides", ""),

        ("Data-Driven Testing", f"{rel_root}guides/data-driven-testing.html", "Testing & Mutation", ""),
        ("Mutation Canvas Testing", f"{rel_root}guides/mutation-canvas-testing.html", "Testing & Mutation", "Layer A/B"),

        ("Tutorials Curriculum", f"{rel_root}tutorials/index.html", "Interactive Tutorials", "Overview"),
        ("01. Intro to Quanifi", f"{rel_root}tutorials/01_introduction_to_quanifi.html", "Interactive Tutorials", "Module 1"),
        ("02. Deutsch-Jozsa", f"{rel_root}tutorials/02_deutsch_jozsa.html", "Interactive Tutorials", "Module 2"),
        ("03. Bernstein-Vazirani", f"{rel_root}tutorials/03_bernstein_vazirani.html", "Interactive Tutorials", "Module 3"),
        ("04. Grover Search", f"{rel_root}tutorials/04_grover_search.html", "Interactive Tutorials", "Module 4"),
        ("05. Portfolio QAOA", f"{rel_root}tutorials/05_portfolio_optimization_qaoa.html", "Interactive Tutorials", "Module 5"),
        ("06. Graph Decomposition", f"{rel_root}tutorials/06_disconnected_graph_decomposition.html", "Interactive Tutorials", "Module 6"),
    ]

    # Group items by group
    groups = {}
    for label, url, group, badge in items:
        if group not in groups:
            groups[group] = []
        groups[group].append((label, url, badge))

    sidebar_html = """
    <aside class="sidebar">
      <div class="widget">
        <div class="search-box">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <circle cx="11" cy="11" r="8"></circle>
            <line x1="21" y1="21" x2="16.65" y2="16.65"></line>
          </svg>
          <input type="text" id="docs-search-input" placeholder="Search documentation..." autocomplete="off">
        </div>
    """

    for group, group_items in groups.items():
        sidebar_html += f"""
        <h3 class="widget-title">{group}</h3>
        <ul class="widget-menu">
        """
        for label, url, badge in group_items:
            norm_target = active_target_path.replace("\\", "/")
            item_rel = url[len(rel_root):] if url.startswith(rel_root) else url
            is_active = (item_rel == norm_target)
            active_cls = ' class="active"' if is_active else ""
            badge_html = f'<span class="badge-count">{badge}</span>' if badge else ""
            sidebar_html += f'<li{active_cls}><a href="{url}"><span>{label}</span>{badge_html}</a></li>\n'
        sidebar_html += "</ul><div style='margin-bottom: 20px;'></div>\n"

    sidebar_html += """
      </div>
    </aside>
    """
    return sidebar_html

def render_page(title, content_html, rel_root, active_tab, breadcrumbs, target_rel_path, is_home=False):
    # Build breadcrumb HTML
    crumbs_html = []
    for label, link in breadcrumbs:
        if link:
            crumbs_html.append(f'<a href="{link}">{label}</a>')
        else:
            crumbs_html.append(f'<span>{label}</span>')
    crumbs_str = ' <span class="sep">&rsaquo;</span> '.join(crumbs_html)

    sidebar_html = build_sidebar(target_rel_path, rel_root)

    hero_html = ""
    if is_home:
        hero_html = f"""
        <div style="background: linear-gradient(135deg, #01a1c0 0%, #00778f 100%); color: #fff; padding: 36px 38px; border-radius: var(--radius-sm); margin-bottom: 28px; border-bottom: 4px solid var(--accent); box-shadow: var(--shadow-md);">
          <div style="display: flex; gap: 8px; align-items: center; margin-bottom: 12px;">
            <span style="background: var(--accent); color: #fff; font-size: 11px; font-weight: 800; padding: 2px 8px; border-radius: 3px; text-transform: uppercase;">Apache NiFi &bull; Quantum Computing</span>
            <span style="font-size: 13px; color: #d0f0f7;">Open Source Framework v0.2.0</span>
          </div>
          <h1 style="font-size: 2.3rem; font-weight: 800; color: #fff; margin: 0 0 14px 0; line-height: 1.2;">Visual Quantum Software Engineering with Apache NiFi</h1>
          <p style="font-size: 1.15rem; color: #e6f7fa; line-height: 1.6; margin: 0 0 24px 0; max-width: 860px;">
            Quanifi combines enterprise dataflow orchestration with quantum programming frameworks (Qiskit, Cirq, Qrisp, PennyLane, and pyQuil), allowing quantum circuits, oracles, Hamiltonians, and measurements to flow through reusable visual pipelines.
          </p>
          <div style="display: flex; gap: 12px; flex-wrap: wrap; align-items: center;">
            <a href="{rel_root}tutorials/01_introduction_to_quanifi.html" class="btn-action btn-warning" style="color: #fff; font-weight: 800; font-size: 14.5px; padding: 10px 22px; box-shadow: 0 3px 8px rgba(0,0,0,0.25);">🚀 Getting Started: Beginner Guide &rarr;</a>
            <a href="#getting-started" class="btn-action btn-outline" style="background: rgba(255,255,255,0.18); color: #fff; border-color: rgba(255,255,255,0.4); font-weight: 600;">4-Step Quickstart &darr;</a>
            <a href="{rel_root}components.html" class="btn-action btn-outline" style="background: rgba(255,255,255,0.14); color: #fff; border-color: rgba(255,255,255,0.3);">Explore Processors</a>
            <a href="https://github.com/saeg/quanifi" target="_blank" rel="noopener" class="btn-action btn-outline" style="background: rgba(255,255,255,0.14); color: #fff; border-color: rgba(255,255,255,0.3);">GitHub</a>
          </div>
        </div>

        <section id="getting-started" class="getting-started-section">
          <div class="getting-started-header">
            <div class="getting-started-title-group">
              <span class="step-badge accent" style="margin-bottom: 8px;">New to Quanifi? Start Here</span>
              <h2>Getting Started: From Zero to Your First Quantum Flow</h2>
              <p>Follow this 4-step beginner roadmap to master visual quantum computing, from core mental models to running your first dataflow pipeline.</p>
            </div>
            <div>
              <a href="{rel_root}tutorials/01_introduction_to_quanifi.html" class="btn-action btn-primary" style="font-size: 13px; padding: 9px 18px; font-weight: 700;">Open Module 1 Tutorial &rarr;</a>
            </div>
          </div>

          <div class="steps-grid">
            <div class="step-card">
              <span class="step-badge">Step 1 &bull; Mental Model</span>
              <h3>1. The Visual Quantum Paradigm</h3>
              <p>Understand how Apache NiFi represents quantum algorithms as streaming FlowFiles. Learn how quantum circuits, Hamiltonians, and statevectors flow across processors without vendor lock-in.</p>
              <a href="{rel_root}tutorials/01_introduction_to_quanifi.html" class="card-link">Read Intro to Quanifi &rarr;</a>
            </div>

            <div class="step-card">
              <span class="step-badge">Step 2 &bull; Environment</span>
              <h3>2. Setup in 60 Seconds</h3>
              <p>Set up Python 3.12 with <code>uv</code> and run the test suite, or spin up the ready-to-run Apache NiFi Docker container with pre-configured quantum processors.</p>
              <div class="step-code">uv sync --frozen --extra dev --python 3.12<br>just test</div>
              <div style="display: flex; gap: 12px; margin-top: auto; flex-wrap: wrap;">
                <a href="{rel_root}guides/developer-guide.html" class="card-link">Developer Guide &rarr;</a>
                <a href="{rel_root}guides/docker-quickstart.html" class="card-link" style="color: var(--accent);">Docker Guide &rarr;</a>
              </div>
            </div>

            <div class="step-card">
              <span class="step-badge">Step 3 &bull; Execution</span>
              <h3>3. Run Your First Flow</h3>
              <p>Import an example process group onto the NiFi canvas. Connect builder and simulator processors, inspect FlowFile wire attributes, and trigger execution with interactive "Run Once".</p>
              <a href="{rel_root}guides/nifi-flow-configuration-guide.html" class="card-link">Flow Configuration Guide &rarr;</a>
            </div>

            <div class="step-card">
              <span class="step-badge">Step 4 &bull; Curriculum</span>
              <h3>4. Interactive Algorithms</h3>
              <p>Master 6 hands-on algorithm modules: Deutsch-Jozsa, Bernstein-Vazirani, Grover's search, QAOA portfolio optimization, and graph decomposition with full canvas templates.</p>
              <a href="{rel_root}tutorials/index.html" class="card-link">Start 6-Module Curriculum &rarr;</a>
            </div>
          </div>
        </section>

        <div style="margin: 36px 0 16px 0; border-top: 1px solid var(--border-light); padding-top: 24px;">
          <h2 style="font-size: 1.35rem; font-weight: 800; color: var(--primary-dark); margin: 0 0 6px 0;">Documentation &amp; Ecosystem Highlights</h2>
          <p style="color: var(--text-muted); font-size: 14px; margin: 0 0 18px 0;">Browse comprehensive processor specifications, mutation testing architecture, cross-framework optimization, and visual canvas galleries.</p>
        </div>

        <div class="cards-grid">
          <div class="card">
            <span class="card-badge">Catalogue</span>
            <h3 class="card-title">Quantum Processors</h3>
            <p class="card-desc">Circuit builders, unitary compilers, simulators, exact expectation evaluators, and cloud hardware pollers across supported quantum frameworks.</p>
            <a href="{rel_root}components.html" class="card-link">View Component Catalogue &rarr;</a>
          </div>
          <div class="card">
            <span class="card-badge">Architecture</span>
            <h3 class="card-title">Flow Configuration Guide</h3>
            <p class="card-desc">Exhaustive configuration handbook for every processor property, scheduling strategy, FlowFile wire contracts, and fault tolerance.</p>
            <a href="{rel_root}guides/nifi-flow-configuration-guide.html" class="card-link">Read Flow Guide &rarr;</a>
          </div>
          <div class="card">
            <span class="card-badge">Testing</span>
            <h3 class="card-title">Mutation & Differential Testing</h3>
            <p class="card-desc">Quantum-aware mutation operators, consensus oracles, and automated differential testing across heterogeneous quantum backends.</p>
            <a href="{rel_root}guides/mutation-canvas-testing.html" class="card-link">Explore Testing Pipeline &rarr;</a>
          </div>
          <div class="card">
            <span class="card-badge">Optimization</span>
            <h3 class="card-title">QAOA N×M Matrix Flow</h3>
            <p class="card-desc">Cross-framework combinatorial optimization: 5 circuit builders &times; 7 simulator backends with portable OpenQASM 2.0 contract.</p>
            <a href="{rel_root}guides/qaoa-components.html" class="card-link">Inspect QAOA Guide &rarr;</a>
          </div>
          <div class="card">
            <span class="card-badge">Beginners</span>
            <h3 class="card-title">Interactive Tutorials</h3>
            <p class="card-desc">Six step-by-step interactive modules covering Deutsch-Jozsa, Bernstein-Vazirani, Grover's search, QAOA portfolio, and graph cuts.</p>
            <a href="{rel_root}tutorials/index.html" class="card-link">Start Learning &rarr;</a>
          </div>
          <div class="card">
            <span class="card-badge">Visuals</span>
            <h3 class="card-title">Canvas Screenshots Gallery</h3>
            <p class="card-desc">18 high-resolution snapshots of the complete AllComponents process group and live algorithm flows in Apache NiFi.</p>
            <a href="{rel_root}screenshots/index.html" class="card-link">Browse Canvas Gallery &rarr;</a>
          </div>
        </div>
        """

    full_html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{title}</title>
  <link rel="icon" type="image/svg+xml" href="{rel_root}assets/img/quanifi-logo.svg">
  <link rel="stylesheet" href="{rel_root}assets/css/style.css">
  <script src="https://polyfill.io/v3/polyfill.min.js?features=es6"></script>
  <script id="MathJax-script" async src="https://cdn.jsdelivr.net/npm/mathjax@3/es5/tex-mml-chtml.js"></script>
</head>
<body>

  <!-- Main Site Header -->
  <header class="site-header">
    <div class="header-container">
      <a href="{rel_root}index.html" class="branding-group">
        <img src="{rel_root}assets/img/quanifi-logo.svg" alt="Quanifi" height="52">
      </a>
      <div class="header-actions">
        <a href="{rel_root}tutorials/01_introduction_to_quanifi.html" class="btn-action btn-warning">Getting Started</a>
        <a href="{rel_root}components.html" class="btn-action btn-primary">Processors</a>
        <a href="{rel_root}guides/nifi-flow-configuration-guide.html" class="btn-action btn-outline">Flow Guide</a>
        <a href="https://github.com/saeg/quanifi" target="_blank" rel="noopener" class="btn-action btn-github" title="View Source on GitHub">
          <svg width="15" height="15" viewBox="0 0 24 24" fill="currentColor" style="vertical-align: text-bottom; margin-right: 4px;">
            <path d="M12 0C5.37 0 0 5.37 0 12c0 5.31 3.435 9.795 8.205 11.385.6.105.825-.255.825-.57 0-.285-.015-1.23-.015-2.235-3.015.555-3.795-.735-4.035-1.41-.135-.345-.72-1.41-1.23-1.695-.42-.225-1.02-.78-.015-.795.945-.015 1.62.87 1.845 1.23 1.08 1.815 2.805 1.305 3.495.99.105-.78.42-1.305.765-1.605-2.67-.3-5.46-1.335-5.46-5.925 0-1.305.465-2.385 1.23-3.225-.12-.3-.54-1.53.12-3.18 0 0 1.005-.315 3.3 1.23.96-.27 1.98-.405 3-.405s2.04.135 3 .405c2.295-1.56 3.3-1.23 3.3-1.23.66 1.65.24 2.88.12 3.18.765.84 1.23 1.905 1.23 3.225 0 4.605-2.805 5.625-5.475 5.925.435.375.81 1.095.81 2.22 0 1.605-.015 2.895-.015 3.3 0 .315.225.69.825.57A12.02 12.02 0 0024 12c0-6.63-5.37-12-12-12z"/>
          </svg>
          GitHub
        </a>
        <a href="https://github.com/saeg/quanifi/blob/main/LICENSE" target="_blank" rel="noopener" class="btn-action btn-license" title="GNU AGPL-3.0 License">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="vertical-align: text-bottom; margin-right: 4px;">
            <circle cx="12" cy="12" r="10"></circle>
            <path d="M14.31 8l5.74 9.94M9.69 8h11.48M7.38 12l5.74-9.94M9.69 16L3.95 6.06M14.31 16H2.83m13.79-4l-5.74 9.94"></path>
          </svg>
          AGPL-3.0
        </a>
      </div>
    </div>
  </header>

  <!-- Navigation Bar (Signature Blue & Gold Line) -->
  <nav id="site-navigation" role="navigation">
    <div class="nav-wrapper">
      <button class="mobile-menu-toggle" aria-label="Toggle navigation menu">&#9776;</button>
      <ul class="main-menu">
        <li class="{'active' if active_tab == 'home' else ''}"><a href="{rel_root}index.html">Overview</a></li>
        <li class="{'active' if active_tab == 'tutorials' and '01_intro' in target_rel_path else ''}"><a href="{rel_root}tutorials/01_introduction_to_quanifi.html" style="color: #ffd27d;"><span style="color: var(--accent); font-size: 10px;">&#9658;</span> Getting Started</a></li>
        <li class="{'active' if active_tab == 'components' else ''}"><a href="{rel_root}components.html">Components</a></li>
        <li class="{'active' if active_tab == 'guides' else ''}">
          <a href="{rel_root}guides/developer-guide.html">Guides &#9662;</a>
          <ul class="dropdown-menu">
            <li><a href="{rel_root}guides/developer-guide.html">Developer Guide</a></li>
            <li><a href="{rel_root}guides/creating-processors.html">Creating Processors</a></li>
            <li><a href="{rel_root}guides/nifi-flow-configuration-guide.html">NiFi Flow Configuration</a></li>
            <li><a href="{rel_root}guides/pyquil-components.html">pyQuil Components</a></li>
            <li><a href="{rel_root}guides/qaoa-components.html">QAOA Components &amp; Matrix</a></li>
            <li><a href="{rel_root}guides/interchangeable-grover-flow.html">Interchangeable Grover Flow</a></li>
          </ul>
        </li>
        <li class="{'active' if active_tab == 'testing' else ''}">
          <a href="{rel_root}guides/data-driven-testing.html">Testing &amp; Mutation &#9662;</a>
          <ul class="dropdown-menu">
            <li><a href="{rel_root}guides/data-driven-testing.html">Data-Driven Testing</a></li>
            <li><a href="{rel_root}guides/mutation-canvas-testing.html">Canvas Testing Guide (Layers A/B)</a></li>
          </ul>
        </li>
        <li class="{'active' if active_tab == 'tutorials' else ''}">
          <a href="{rel_root}tutorials/index.html">Tutorials &#9662;</a>
          <ul class="dropdown-menu">
            <li><a href="{rel_root}tutorials/index.html">Curriculum Overview</a></li>
            <li><a href="{rel_root}tutorials/01_introduction_to_quanifi.html">01. Intro to Quanifi</a></li>
            <li><a href="{rel_root}tutorials/02_deutsch_jozsa.html">02. Deutsch-Jozsa Algorithm</a></li>
            <li><a href="{rel_root}tutorials/03_bernstein_vazirani.html">03. Bernstein-Vazirani Algorithm</a></li>
            <li><a href="{rel_root}tutorials/04_grover_search.html">04. Grover Search Algorithm</a></li>
            <li><a href="{rel_root}tutorials/05_portfolio_optimization_qaoa.html">05. QAOA Portfolio Optimization</a></li>
            <li><a href="{rel_root}tutorials/06_disconnected_graph_decomposition.html">06. Graph Decomposition</a></li>
          </ul>
        </li>
        <li class="{'active' if active_tab == 'gallery' else ''}"><a href="{rel_root}screenshots/index.html">Canvas Gallery</a></li>
      </ul>
      <div class="nav-extra">
        <a href="https://github.com/saeg/quanifi" target="_blank" rel="noopener" class="nav-extra-link" title="GitHub Repository">
          <svg width="13" height="13" viewBox="0 0 24 24" fill="currentColor">
            <path d="M12 0C5.37 0 0 5.37 0 12c0 5.31 3.435 9.795 8.205 11.385.6.105.825-.255.825-.57 0-.285-.015-1.23-.015-2.235-3.015.555-3.795-.735-4.035-1.41-.135-.345-.72-1.41-1.23-1.695-.42-.225-1.02-.78-.015-.795.945-.015 1.62.87 1.845 1.23 1.08 1.815 2.805 1.305 3.495.99.105-.78.42-1.305.765-1.605-2.67-.3-5.46-1.335-5.46-5.925 0-1.305.465-2.385 1.23-3.225-.12-.3-.54-1.53.12-3.18 0 0 1.005-.315 3.3 1.23.96-.27 1.98-.405 3-.405s2.04.135 3 .405c2.295-1.56 3.3-1.23 3.3-1.23.66 1.65.24 2.88.12 3.18.765.84 1.23 1.905 1.23 3.225 0 4.605-2.805 5.625-5.475 5.925.435.375.81 1.095.81 2.22 0 1.605-.015 2.895-.015 3.3 0 .315.225.69.825.57A12.02 12.02 0 0024 12c0-6.63-5.37-12-12-12z"/>
          </svg>
          GitHub
        </a>
      </div>
    </div>
  </nav>

  <!-- Main Content Layout -->
  <div class="content-wrapper">
    {sidebar_html}

    <main class="main-content">
      <nav class="breadcrumbs">
        {crumbs_str}
      </nav>

      {hero_html}

      <article>
        {content_html}
      </article>
    </main>
  </div>

  <!-- Footer -->
  <footer id="colophon" class="site-footer" role="contentinfo">
    <div class="footer-wrapper">
      <div class="footer-columns">
        <div class="footer-col">
          <h3>Quanifi Project</h3>
          <p>Visual Quantum Software Engineering with Apache NiFi. Composing, testing, executing and verifying quantum algorithms as enterprise dataflows.</p>
          <p>Compatible with Qiskit, Cirq, Qrisp, pyQuil, and PennyLane across local simulators and quantum cloud hardware.</p>
        </div>
        <div class="footer-col">
          <h3>Documentation</h3>
          <ul>
            <li><a href="{rel_root}tutorials/01_introduction_to_quanifi.html">Getting Started Guide</a></li>
            <li><a href="{rel_root}components.html">Component Catalogue</a></li>
            <li><a href="{rel_root}guides/developer-guide.html">Developer Guide</a></li>
            <li><a href="{rel_root}guides/nifi-flow-configuration-guide.html">NiFi Flow Configuration</a></li>
            <li><a href="{rel_root}tutorials/index.html">Interactive Learning Curriculum</a></li>
            <li><a href="{rel_root}screenshots/index.html">Canvas Screenshots Gallery</a></li>
          </ul>
        </div>
        <div class="footer-col">
          <h3>Quantum Ecosystem</h3>
          <ul>
            <li><a href="{rel_root}guides/qaoa-components.html">QAOA Cross-Framework Matrix</a></li>
            <li><a href="{rel_root}guides/pyquil-components.html">Rigetti pyQuil Integration</a></li>
            <li><a href="{rel_root}guides/interchangeable-grover-flow.html">Interchangeable Grover Flow</a></li>
            <li><a href="{rel_root}guides/data-driven-testing.html">Data-Driven Differential Testing</a></li>
            <li><a href="{rel_root}guides/mutation-canvas-testing.html">Canvas Testing (Layers A/B)</a></li>
          </ul>
        </div>
        <div class="footer-col">
          <h3>Code &amp; Licenses</h3>
          <p>Free and open source quantum orchestration under the GNU AGPL-3.0, with commercial licensing available.</p>
          <ul>
            <li><a href="https://github.com/saeg/quanifi" target="_blank" rel="noopener">GitHub: saeg/quanifi</a></li>
            <li><a href="https://github.com/saeg/quanifi/issues" target="_blank" rel="noopener">Issue Tracker</a></li>
            <li><a href="https://github.com/saeg/quanifi/blob/main/LICENSE" target="_blank" rel="noopener">GNU AGPL-3.0 License</a></li>
            <li><a href="https://github.com/saeg/quanifi/blob/main/COMMERCIAL.md" target="_blank" rel="noopener">Commercial License Terms</a></li>
            <li><a href="https://github.com/saeg/quanifi/releases" target="_blank" rel="noopener">Release Notes</a></li>
          </ul>
        </div>
      </div>
    </div>
    <div class="footer-bottom">
      <div class="footer-wrapper">
        <p>Quanifi Documentation &bull; Dual-licensed under <a href="https://github.com/saeg/quanifi/blob/main/LICENSE" target="_blank" rel="noopener">GNU AGPL-3.0</a> and <a href="https://github.com/saeg/quanifi/blob/main/COMMERCIAL.md" target="_blank" rel="noopener">Commercial Terms</a> &bull; Source code hosted on <a href="https://github.com/saeg/quanifi" target="_blank" rel="noopener">GitHub</a></p>
      </div>
    </div>
  </footer>

  <script src="{rel_root}assets/js/main.js"></script>
</body>
</html>
"""
    return full_html

def build_gallery_html(rel_root):
    gallery_html = '<div class="gallery-grid">\n'
    for filename, title, desc, tag in SCREENSHOT_INFO:
        img_src = f"{rel_root}screenshots/{filename}"
        gallery_html += f"""
        <div class="gallery-card">
          <img src="{img_src}" alt="{title}" loading="lazy">
          <div class="gallery-card-body">
            <span class="card-badge">{tag}</span>
            <h4 class="gallery-card-title">{title}</h4>
            <p class="gallery-card-desc">{desc}</p>
            <a href="{img_src}" target="_blank" class="card-link">View Full Resolution &rarr;</a>
          </div>
        </div>
        """
    gallery_html += "</div>\n"
    return gallery_html

def convert_tutorials():
    """Convert and style the 6 tutorials from guides/ into docs/tutorials/"""
    TUTORIALS_DIR.mkdir(parents=True, exist_ok=True)
    (TUTORIALS_DIR / "img").mkdir(parents=True, exist_ok=True)

    # Copy images
    if (GUIDES_SRC_DIR / "img").exists():
        for img in (GUIDES_SRC_DIR / "img").glob("*.*"):
            shutil.copy(img, TUTORIALS_DIR / "img" / img.name)

    tutorial_files = sorted(glob.glob(str(GUIDES_SRC_DIR / "0*.html")))
    
    # Also create tutorials/index.html curriculum
    curriculum_content = """
    <h1>Quanifi Interactive Tutorials</h1>
    <p class="lead">
      Learn how to compose, test, execute, and monitor quantum algorithms using Apache NiFi dataflows &mdash; written specifically for software engineers without a physics background.
    </p>

    <h2>Curriculum Overview</h2>
    <p>Each tutorial module breaks down quantum algorithms into intuitive computational concepts, guides you through running the flow on the live NiFi canvas, shows you how to interpret output reports, and finishes with a hands-on exercise.</p>

    <div class="cards-grid">
      <div class="card">
        <span class="card-badge">Module 01</span>
        <h3 class="card-title">Introduction to Quanifi</h3>
        <p class="card-desc">Understand the Quanifi architecture, FlowFile wire contracts, how OpenQASM flows between processors, and how to navigate the NiFi canvas.</p>
        <a href="01_introduction_to_quanifi.html" class="card-link">Start Module 1 &rarr;</a>
      </div>
      <div class="card">
        <span class="card-badge">Module 02</span>
        <h3 class="card-title">Deutsch-Jozsa Algorithm</h3>
        <p class="card-desc">Determine whether a black-box boolean function is constant or balanced in a single evaluation (O(1)) using quantum phase kickback.</p>
        <a href="02_deutsch_jozsa.html" class="card-link">Start Module 2 &rarr;</a>
      </div>
      <div class="card">
        <span class="card-badge">Module 03</span>
        <h3 class="card-title">Bernstein-Vazirani Algorithm</h3>
        <p class="card-desc">Recover an unknown n-bit secret bitmask in exactly 1 query (O(1)) compared to n queries in classical computing.</p>
        <a href="03_bernstein_vazirani.html" class="card-link">Start Module 3 &rarr;</a>
      </div>
      <div class="card">
        <span class="card-badge">Module 04</span>
        <h3 class="card-title">Grover's Search Algorithm</h3>
        <p class="card-desc">Search an unstructured database of N items in O(&radic;N) queries using oracle phase inversion and amplitude amplification.</p>
        <a href="04_grover_search.html" class="card-link">Start Module 4 &rarr;</a>
      </div>
      <div class="card">
        <span class="card-badge">Module 05</span>
        <h3 class="card-title">QAOA Portfolio Optimization</h3>
        <p class="card-desc">Solve budget-constrained Markowitz mean-variance portfolio selection using the Quantum Approximate Optimization Algorithm and XY mixers.</p>
        <a href="05_portfolio_optimization_qaoa.html" class="card-link">Start Module 5 &rarr;</a>
      </div>
      <div class="card">
        <span class="card-badge">Module 06</span>
        <h3 class="card-title">Graph Cut Decomposition</h3>
        <p class="card-desc">Decompose large, disconnected problem graphs across independent parallel NiFi processor groups to fit NISQ qubit constraints.</p>
        <a href="06_disconnected_graph_decomposition.html" class="card-link">Start Module 6 &rarr;</a>
      </div>
    </div>
    """

    index_html = render_page(
        "Tutorials Curriculum — Quanifi",
        curriculum_content,
        "../",
        "tutorials",
        [("Home", "../index.html"), ("Tutorials", "index.html")],
        "tutorials/index.html"
    )
    with open(TUTORIALS_DIR / "index.html", "w", encoding="utf-8") as f:
        f.write(index_html)
    print("Created docs/tutorials/index.html")

    # Convert each 0X_*.html
    for src_file in tutorial_files:
        src_path = Path(src_file)
        dest_path = TUTORIALS_DIR / src_path.name
        with open(src_path, "r", encoding="utf-8") as f:
            raw_html = f.read()

        # Extract title
        title_m = re.search(r'<title>(.*?)</title>', raw_html, re.IGNORECASE)
        page_title = title_m.group(1) if title_m else "Quanifi Tutorial"

        # Extract content inside <main class="container">...</main>
        content_m = re.search(r'<main[^>]*>(.*?)</main>', raw_html, re.DOTALL | re.IGNORECASE)
        if content_m:
            content = content_m.group(1)
        else:
            # Fallback to body
            body_m = re.search(r'<body[^>]*>(.*?)</body>', raw_html, re.DOTALL | re.IGNORECASE)
            content = body_m.group(1) if body_m else raw_html

        content = content.replace("../docs/guides/QAOA_COMPONENTS.md", "../guides/qaoa-components.html")
        content = content.replace("../docs/guides/DOCKER_QUICKSTART.md", "../guides/docker-quickstart.html")

        # Fix relative image paths: img/ -> tutorials/img/ or img/
        # Inside tutorials/, img/ works directly!
        
        # Wrap in our layout
        final_html = render_page(
            page_title,
            content,
            "../",
            "tutorials",
            [("Home", "../index.html"), ("Tutorials", "index.html"), (src_path.stem.replace("_", " ").title(), src_path.name)],
            f"tutorials/{src_path.name}"
        )

        with open(dest_path, "w", encoding="utf-8") as f:
            f.write(final_html)
        print(f"Created docs/tutorials/{src_path.name}")

def main():
    from generate_component_catalogue import generate
    generate()
    print("Starting Quanifi Documentation Site Generator...")
    md = markdown.Markdown(extensions=['tables', 'fenced_code', 'toc', 'codehilite'])

    for src_rel, target_rel, title, active_tab, breadcrumbs in PAGES_MAP:
        src_file = DOCS_DIR / src_rel
        target_file = DOCS_DIR / target_rel

        if not src_file.exists():
            print(f"Warning: Source file {src_file} does not exist, skipping.")
            continue

        target_file.parent.mkdir(parents=True, exist_ok=True)
        rel_root = get_rel_root(target_rel)

        with open(src_file, "r", encoding="utf-8") as f:
            md_text = f.read()

        md.reset()
        content_html = md.convert(md_text)
        content_html = fix_links(content_html, target_rel)

        # For screenshots/index.html, append the gallery grid
        if target_rel == "screenshots/index.html":
            content_html += "<h2>Process Group Visual Gallery</h2>\n"
            content_html += "<p>Click any canvas image below to open full resolution in the lightbox viewer:</p>\n"
            content_html += build_gallery_html(rel_root)

        is_home = (target_rel == "index.html")
        full_html = render_page(title, content_html, rel_root, active_tab, breadcrumbs, target_rel, is_home=is_home)

        with open(target_file, "w", encoding="utf-8") as f:
            f.write(full_html)
        print(f"Generated {target_file}")

    # Convert tutorials
    convert_tutorials()

    # Create .nojekyll in docs/
    nojekyll = DOCS_DIR / ".nojekyll"
    if not nojekyll.exists():
        nojekyll.touch()
        print("Created docs/.nojekyll")

    print("Documentation generation complete!")

if __name__ == "__main__":
    main()
