#!/usr/bin/env python3
"""Build-time helpers for the Quanifi quickstart NiFi image (docker/nifi/Dockerfile).

Four steps, importable for tests and runnable as CLI subcommands:

- ``select``: copy the listed processors plus their helper-module closure out
  of ``nifi_extensions/`` (AST only -- nothing here is ever imported) and
  write ``manifest.json``.
- ``flow``: convert a committed flow-definition JSON (``tools/build_grover_examples.py``'s
  output) into a gzip ``flow.json.gz`` NiFi loads directly into ``conf/``.
- ``prebake``: recreate NiFi's own on-disk per-processor venv layout under
  ``work/python/extensions/<Type>/<Version>/`` and pre-install every
  manifest processor's pinned dependencies, without booting NiFi.
- ``testenv``: one venv holding the union of every manifest processor's
  dependencies, for the Docker ``test`` image.

Python 3.11 (the base image's ``/usr/bin/python3``); uses ``tomllib``. Every
function below raises ``ValueError`` (or lets a called ``subprocess`` raise
``CalledProcessError``) on a problem; ``main`` is the only place that turns an
exception into ``SystemExit("quanifi_build: ...")``.
"""
from __future__ import annotations

import argparse
import ast
import copy
import gzip
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import tomllib
import uuid
from pathlib import Path

NAME_RE = re.compile(r"^[A-Z][A-Za-z0-9_]*$")

ROOT_ID = str(uuid.uuid5(uuid.NAMESPACE_URL, "https://quanifi.local/docker/root"))
ROOT_INSTANCE_ID = str(
    uuid.uuid5(uuid.NAMESPACE_URL, "https://quanifi.local/docker/root/instance")
)


# ---------------------------------------------------------------------------
# select
# ---------------------------------------------------------------------------


def processor_details(path):
    """AST-only inspection of a processor module; the file is never imported.

    Returns ``{"type", "module", "version", "dependencies"}`` for the first
    module-level class that defines nested ``Java`` and ``ProcessorDetails``
    classes, or ``None`` if the file defines no such class.
    """
    path = Path(path)
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in tree.body:
        if not isinstance(node, ast.ClassDef):
            continue
        nested = {n.name: n for n in node.body if isinstance(n, ast.ClassDef)}
        if "Java" not in nested or "ProcessorDetails" not in nested:
            continue

        version = None
        dependencies = []
        for stmt in nested["ProcessorDetails"].body:
            if not (
                isinstance(stmt, ast.Assign)
                and len(stmt.targets) == 1
                and isinstance(stmt.targets[0], ast.Name)
            ):
                continue
            target = stmt.targets[0].id
            if target == "version":
                version = ast.literal_eval(stmt.value)
            elif target == "dependencies":
                dependencies = ast.literal_eval(stmt.value)

        if node.name != path.stem:
            raise ValueError(
                "{}: class {} does not match file stem {}".format(
                    path, node.name, path.stem
                )
            )
        if not isinstance(version, str):
            raise ValueError(
                "{}: ProcessorDetails.version must be a str, got {!r}".format(
                    path, version
                )
            )
        if not isinstance(dependencies, list) or not all(
            isinstance(d, str) for d in dependencies
        ):
            raise ValueError(
                "{}: ProcessorDetails.dependencies must be a list of str, got {!r}".format(
                    path, dependencies
                )
            )
        return {
            "type": node.name,
            "module": path.stem,
            "version": version,
            "dependencies": dependencies,
        }
    return None


def module_imports(path):
    """Top-level names of every Import/ImportFrom (level 0) anywhere in the
    module, including imports nested inside functions."""
    path = Path(path)
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0 and node.module:
                names.add(node.module.split(".")[0])
    return names


def read_processor_list(spec, src):
    """Parse a processor-list file (or ``"all"``) into a sorted/validated
    list of processor module names, relative to ``src``."""
    src = Path(src)
    if spec == "all":
        return sorted(
            path.stem
            for path in sorted(src.glob("*.py"))
            if processor_details(path) is not None
        )

    spec_path = Path(spec)
    names = []
    seen = set()
    for raw_line in spec_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.split("#", 1)[0].strip()
        if not line:
            continue
        if not NAME_RE.match(line):
            raise ValueError("{}: invalid processor name {!r}".format(spec, line))
        if line in seen:
            raise ValueError("{}: duplicate processor name {!r}".format(spec, line))
        seen.add(line)
        proc_path = src / (line + ".py")
        if not proc_path.exists():
            raise ValueError("{}: no such file {}".format(spec, proc_path))
        if processor_details(proc_path) is None:
            raise ValueError("{}: {} is not a processor module".format(spec, proc_path))
        names.append(line)
    return names


def helper_closure(names, src):
    """Breadth-first closure of every non-processor module transitively
    imported by ``names`` (processor module names, relative to ``src``).

    Raises if any module in the closure imports another *processor* module
    (other than itself): processors must not import processors, because NiFi
    loads each processor in its own module context and such an import makes
    the imported processor a silent "ghost component" never wired on any
    canvas.
    """
    src = Path(src)
    helpers = []
    seen_helpers = set()
    visited = set()
    queue = list(names)
    while queue:
        current = queue.pop(0)
        if current in visited:
            continue
        visited.add(current)
        module_path = src / (current + ".py")
        if not module_path.exists():
            continue
        for imported in sorted(module_imports(module_path)):
            candidate = src / (imported + ".py")
            if not candidate.exists():
                continue
            if processor_details(candidate) is not None:
                if imported == current:
                    continue
                raise ValueError(
                    "{} imports processor module {}; processors must not import "
                    "processors (ghost components)".format(current, imported)
                )
            if imported not in seen_helpers:
                seen_helpers.add(imported)
                helpers.append(imported)
                queue.append(imported)
    return sorted(seen_helpers)


def select(src, spec, out, manifest_path):
    """Copy the selected processors plus their helper closure into ``out``
    and write the build manifest to ``manifest_path``."""
    src = Path(src)
    names = read_processor_list(spec, src)
    details = [processor_details(src / (n + ".py")) for n in names]
    helpers = helper_closure(names, src)

    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    for name in names + helpers:
        shutil.copy2(src / (name + ".py"), out / (name + ".py"))

    manifest = {"source": str(spec), "processors": details, "helpers": helpers}
    manifest_path = Path(manifest_path)
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")

    print(
        "selected {} processors, {} helper modules: {}".format(
            len(names), len(helpers), ", ".join(names + helpers)
        )
    )
    return manifest


# ---------------------------------------------------------------------------
# flow
# ---------------------------------------------------------------------------


def python_types(group):
    """type -> bundle version for every python-extensions processor,
    recursively over nested process groups."""
    types = {}

    def walk(g):
        for proc in g.get("processors", []):
            bundle = proc.get("bundle", {})
            if bundle.get("artifact") == "python-extensions":
                types[proc["type"]] = bundle.get("version", "")
        for child in g.get("processGroups", []):
            walk(child)

    walk(group)
    return types


def _count_python_processor_instances(group):
    count = 0

    def walk(g):
        nonlocal count
        for proc in g.get("processors", []):
            if proc.get("bundle", {}).get("artifact") == "python-extensions":
                count += 1
        for child in g.get("processGroups", []):
            walk(child)

    walk(group)
    return count


def _raise_if_running(node):
    if node.get("scheduledState") == "RUNNING":
        raise ValueError(
            "canvas has a component in RUNNING state: {}".format(
                node.get("name", node.get("identifier"))
            )
        )
    for proc in node.get("processors", []):
        if proc.get("scheduledState") == "RUNNING":
            raise ValueError(
                "canvas has a component in RUNNING state: {}".format(
                    proc.get("name", proc.get("identifier"))
                )
            )
    for child in node.get("processGroups", []):
        _raise_if_running(child)


def flow_definition_to_flow(defn):
    """Wrap a flow-definition snapshot (``{"flowContents": ..., ...}``, as
    written by ``tools/build_grover_examples.py``) into a full NiFi 2.9.0
    ``flow.json`` document (pre-gzip)."""
    group = copy.deepcopy(defn["flowContents"])
    group["groupIdentifier"] = ROOT_ID
    group.setdefault("maxConcurrentTasks", 1)
    group.setdefault("statelessFlowTimeout", "1 min")
    _raise_if_running(group)

    return {
        "encodingVersion": {"majorVersion": 2, "minorVersion": 0},
        "maxTimerDrivenThreadCount": 10,
        "registries": [],
        "parameterContexts": [],
        "parameterProviders": [],
        "controllerServices": [],
        "reportingTasks": [],
        "flowAnalysisRules": [],
        "connectors": [],
        "rootGroup": {
            "identifier": ROOT_ID,
            "instanceIdentifier": ROOT_INSTANCE_ID,
            "name": "NiFi Flow",
            "componentType": "PROCESS_GROUP",
            "comments": "",
            "position": {"x": 0.0, "y": 0.0},
            "processors": [],
            "connections": [],
            "labels": [],
            "funnels": [],
            "processGroups": [group],
            "remoteProcessGroups": [],
            "inputPorts": [],
            "outputPorts": [],
            "controllerServices": [],
            "scheduledState": "ENABLED",
            "defaultFlowFileExpiration": "0 sec",
            "defaultBackPressureObjectThreshold": 10000,
            "defaultBackPressureDataSizeThreshold": "1 GB",
            "flowFileConcurrency": "UNBOUNDED",
            "flowFileOutboundPolicy": "STREAM_WHEN_AVAILABLE",
            "executionEngine": "INHERITED",
            "maxConcurrentTasks": 1,
            "statelessFlowTimeout": "1 min",
        },
    }


def build_flow(canvas, manifest_path, out):
    """Convert ``canvas`` (a flow-definition JSON path, or ``""`` for none)
    into a gzip ``flow.json.gz`` at ``out``. Returns the Python processor
    instance count (0 if there is no canvas)."""
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    if canvas == "":
        print("no canvas")
        return 0

    canvas_path = Path(canvas)
    defn = json.loads(canvas_path.read_text(encoding="utf-8"))
    manifest = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
    manifest_types = {(p["type"], p["version"]) for p in manifest["processors"]}

    canvas_types = python_types(defn["flowContents"])
    missing = {t: v for t, v in canvas_types.items() if (t, v) not in manifest_types}
    if missing:
        formatted = ", ".join(
            "{} ({})".format(t, v) for t, v in sorted(missing.items())
        )
        raise ValueError(
            "canvas {} uses processors not in this image: {}; add them to the "
            "processor list".format(canvas_path, formatted)
        )

    flow = flow_definition_to_flow(defn)
    data = json.dumps(flow).encode("utf-8")
    with open(out, "wb") as raw:
        with gzip.GzipFile(fileobj=raw, mode="wb", mtime=0, filename="") as gz:
            gz.write(data)

    return _count_python_processor_instances(defn["flowContents"])


# ---------------------------------------------------------------------------
# prebake / testenv
# ---------------------------------------------------------------------------


def export_constraints(project, uv, workdir):
    """Export ``project``'s ``uv.lock`` as pinned constraints plus its
    ``[tool.uv].override-dependencies`` list, into ``workdir``."""
    project = Path(project)
    workdir = Path(workdir)
    proj_dir = workdir / "proj"
    proj_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(project / "pyproject.toml", proj_dir / "pyproject.toml")
    shutil.copy2(project / "uv.lock", proj_dir / "uv.lock")

    constraints = workdir / "constraints.txt"
    subprocess.run(
        [
            str(uv),
            "export",
            "--frozen",
            "--no-hashes",
            "--no-header",
            "--no-annotate",
            "--no-emit-project",
            "--all-extras",
            "--format",
            "requirements.txt",
            "--output-file",
            str(constraints),
        ],
        cwd=str(proj_dir),
        check=True,
    )

    with open(proj_dir / "pyproject.toml", "rb") as handle:
        pyproject = tomllib.load(handle)
    override_deps = (
        pyproject.get("tool", {}).get("uv", {}).get("override-dependencies", [])
    )
    overrides = workdir / "overrides.txt"
    overrides.write_text("\n".join(override_deps) + ("\n" if override_deps else ""))

    return constraints, overrides


def _sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def dedup(root):
    """Hardlink identical files under ``root`` together (same content, same
    mode/uid/gid), following no symlinks. Returns (files relinked, bytes
    saved)."""
    root = Path(root)
    groups: dict[tuple, list[Path]] = {}
    for dirpath, _dirnames, filenames in os.walk(root, followlinks=False):
        for fname in filenames:
            path = Path(dirpath) / fname
            if path.is_symlink():
                continue
            try:
                st = path.stat()
            except OSError:
                continue
            if not stat.S_ISREG(st.st_mode) or st.st_size <= 0:
                continue
            key = (st.st_size, st.st_mode, st.st_uid, st.st_gid)
            groups.setdefault(key, []).append(path)

    linked = 0
    saved = 0
    for key, paths in groups.items():
        by_hash: dict[str, list[Path]] = {}
        for path in sorted(paths):
            by_hash.setdefault(_sha256(path), []).append(path)
        for _digest, plist in by_hash.items():
            plist = sorted(plist)
            first = plist[0]
            first_ino = first.stat().st_ino
            for other in plist[1:]:
                if other.stat().st_ino == first_ino:
                    continue
                tmp = other.parent / (other.name + ".quanifi-dedup.tmp")
                os.link(first, tmp)
                os.replace(tmp, other)
                linked += 1
                saved += key[0]
    return linked, saved


def _dir_stats(root):
    """(apparent_bytes, unique_bytes) under root: apparent counts every
    path's size, unique counts each inode's size once."""
    apparent = 0
    unique = 0
    seen_inodes = set()
    for dirpath, _dirnames, filenames in os.walk(root, followlinks=False):
        for fname in filenames:
            path = Path(dirpath) / fname
            if path.is_symlink():
                continue
            try:
                st = path.stat()
            except OSError:
                continue
            if not stat.S_ISREG(st.st_mode):
                continue
            apparent += st.st_size
            if st.st_ino not in seen_inodes:
                seen_inodes.add(st.st_ino)
                unique += st.st_size
    return apparent, unique


def prebake(manifest_path, project, work_dir, python, uv, run=subprocess.run):
    """Recreate NiFi's on-disk per-processor venv layout under
    ``work_dir/extensions/<Type>/<Version>/`` and pre-install every manifest
    processor's pinned dependencies, without booting NiFi."""
    manifest = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
    work_dir = Path(work_dir)
    per_type = {}

    with tempfile.TemporaryDirectory(dir="/tmp") as tmp:
        constraints, overrides = export_constraints(Path(project), uv, Path(tmp))
        cache = Path(tmp) / "uv-cache"

        for proc in manifest["processors"]:
            env = work_dir / "extensions" / proc["type"] / proc["version"]
            env.parent.mkdir(parents=True, exist_ok=True)
            run([str(python), "-m", "venv", str(env)], check=True, cwd=str(env.parent))
            (env / "env-creation-complete.txt").touch()
            deps = proc.get("dependencies") or []
            if deps:
                run(
                    [
                        str(uv),
                        "pip",
                        "install",
                        "--python",
                        str(env / "bin" / "python3"),
                        "--target",
                        str(env),
                        "--constraints",
                        str(constraints),
                        "--overrides",
                        str(overrides),
                        "--link-mode",
                        "hardlink",
                        "--cache-dir",
                        str(cache),
                        *deps,
                    ],
                    check=True,
                )
            (env / "dependency-download.complete").write_text("True")
            apparent, _unique = _dir_stats(env)
            per_type[proc["type"]] = {
                "dependencies": len(deps),
                "apparent_bytes": apparent,
            }

        linked, saved = dedup(work_dir / "extensions")

    apparent_total, unique_total = _dir_stats(work_dir / "extensions")

    for ptype, stats in sorted(per_type.items()):
        print(
            "prebake: {}: {} dependencies, {} bytes apparent".format(
                ptype, stats["dependencies"], stats["apparent_bytes"]
            )
        )
    print(
        "prebake: totals: {} bytes apparent, {} bytes unique, {} files relinked, "
        "{} bytes saved".format(apparent_total, unique_total, linked, saved)
    )

    return {
        "per_type": per_type,
        "apparent_bytes": apparent_total,
        "unique_bytes": unique_total,
        "linked": linked,
        "saved": saved,
    }


def testenv(manifest_path, project, venv, python, uv, extras=()):
    """One venv holding the union of every manifest processor's dependencies
    plus ``extras`` (e.g. ``pytest``), for the Docker ``test`` image."""
    manifest = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
    deps = set()
    for proc in manifest["processors"]:
        deps.update(proc.get("dependencies") or [])
    deps.update(extras or ())

    subprocess.run([str(python), "-m", "venv", str(venv)], check=True)

    with tempfile.TemporaryDirectory(dir="/tmp") as tmp:
        constraints, overrides = export_constraints(Path(project), uv, Path(tmp))
        cache = Path(tmp) / "uv-cache"
        subprocess.run(
            [
                str(uv),
                "pip",
                "install",
                "--python",
                str(Path(venv) / "bin" / "python"),
                "--constraints",
                str(constraints),
                "--overrides",
                str(overrides),
                "--cache-dir",
                str(cache),
                *sorted(deps),
            ],
            check=True,
        )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv=None):
    parser = argparse.ArgumentParser(prog="quanifi_build", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    p_select = sub.add_parser("select")
    p_select.add_argument("--src", required=True, type=Path)
    p_select.add_argument("--processors", required=True)
    p_select.add_argument("--out", required=True, type=Path)
    p_select.add_argument("--manifest", required=True, type=Path)

    p_flow = sub.add_parser("flow")
    p_flow.add_argument("--canvas", required=True)
    p_flow.add_argument("--manifest", required=True, type=Path)
    p_flow.add_argument("--out", required=True, type=Path)

    p_prebake = sub.add_parser("prebake")
    p_prebake.add_argument("--manifest", required=True, type=Path)
    p_prebake.add_argument("--project", required=True, type=Path)
    p_prebake.add_argument("--work-dir", required=True, type=Path)
    p_prebake.add_argument("--python", required=True)
    p_prebake.add_argument("--uv", required=True)
    p_prebake.add_argument("--enabled", required=True)

    p_testenv = sub.add_parser("testenv")
    p_testenv.add_argument("--manifest", required=True, type=Path)
    p_testenv.add_argument("--project", required=True, type=Path)
    p_testenv.add_argument("--venv", required=True, type=Path)
    p_testenv.add_argument("--python", required=True)
    p_testenv.add_argument("--uv", required=True)
    p_testenv.add_argument("--extra", action="append", default=[])

    args = parser.parse_args(argv)

    try:
        if args.command == "select":
            select(args.src, args.processors, args.out, args.manifest)
        elif args.command == "flow":
            count = build_flow(args.canvas, args.manifest, args.out)
            print("flow has {} Python processor instance(s)".format(count))
        elif args.command == "prebake":
            enabled = args.enabled.strip().lower() == "true"
            if not enabled:
                print("pre-bake disabled: NiFi installs dependencies on first use")
                return
            prebake(args.manifest, args.project, args.work_dir, args.python, args.uv)
        elif args.command == "testenv":
            testenv(
                args.manifest,
                args.project,
                args.venv,
                args.python,
                args.uv,
                args.extra,
            )
    except Exception as exc:  # noqa: BLE001 - deliberately broad: CLI boundary
        raise SystemExit("quanifi_build: {}".format(exc))


if __name__ == "__main__":
    main(sys.argv[1:])
