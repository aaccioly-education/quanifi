"""Tests for the Docker quickstart build-time helper
(``docker/nifi/quanifi_build.py``) and the readiness monitor
(``docker/nifi/quanifi_monitor.py``) and the host-side smoke tool
(``tools/quickstart_smoke.py``). All three are loaded through ``importlib``
(never ``import docker...``) so this file collects without the ``docker/``
package existing on ``sys.path``.

Portable: no JVM, no Docker. Runs on the host and in the Docker test profile.
"""

import gzip
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _load(name, rel_path):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel_path)
    module = importlib.util.module_from_spec(spec)
    # dataclasses (used by quanifi_monitor.py) looks the module up in
    # sys.modules by __module__ name while processing type hints; register it
    # before exec_module, the same way a normal import would.
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


qb = _load("quanifi_build", "docker/nifi/quanifi_build.py")

sys.path.insert(0, str(ROOT / "tools"))
import nifi_ready  # noqa: E402

qm = _load("quanifi_monitor", "docker/nifi/quanifi_monitor.py")
qs = _load("quickstart_smoke", "tools/quickstart_smoke.py")

import argparse  # noqa: E402


# ---------------------------------------------------------------------------
# select / helper_closure — synthetic tree
# ---------------------------------------------------------------------------


@pytest.fixture
def synthetic_src(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "ProcA.py").write_text(
        "class ProcA:\n"
        "    class Java:\n"
        "        implements = ['org.apache.nifi.python.processor.FlowFileTransform']\n"
        "    class ProcessorDetails:\n"
        "        version = '0.1.0'\n"
        "        dependencies = ['x>=1']\n"
        "    def transform(self, context, flowFile):\n"
        "        import helper_a\n"
        "        return helper_a.f()\n"
    )
    (src / "helper_a.py").write_text(
        "import helper_b\n\n\ndef f():\n    return helper_b.g()\n"
    )
    (src / "helper_b.py").write_text("def g():\n    return 1\n")
    (src / "ProcB.py").write_text(
        "class ProcB:\n"
        "    class Java:\n"
        "        implements = ['org.apache.nifi.python.processor.FlowFileTransform']\n"
        "    class ProcessorDetails:\n"
        "        version = '0.1.0'\n"
        "        dependencies = []\n"
        "    def transform(self, context, flowFile):\n"
        "        import ProcA\n"
        "        return ProcA\n"
    )
    (src / "NotAProc.py").write_text("def helper():\n    return 42\n")
    return src


class TestSelectSynthetic:
    def test_list_gives_processor_and_helper_closure(self, synthetic_src, tmp_path):
        spec = tmp_path / "list.txt"
        spec.write_text("# a comment\n\nProcA\n")

        manifest = qb.select(
            synthetic_src, str(spec), tmp_path / "out", tmp_path / "manifest.json"
        )

        assert manifest["helpers"] == ["helper_a", "helper_b"]
        assert [p["type"] for p in manifest["processors"]] == ["ProcA"]
        assert (tmp_path / "out" / "ProcA.py").exists()
        assert (tmp_path / "out" / "helper_a.py").exists()
        assert (tmp_path / "out" / "helper_b.py").exists()
        assert json.loads((tmp_path / "manifest.json").read_text()) == manifest

    def test_processor_importing_processor_raises(self, synthetic_src, tmp_path):
        spec = tmp_path / "list.txt"
        spec.write_text("ProcB\n")
        with pytest.raises(ValueError, match="processors must not import processors"):
            qb.select(
                synthetic_src, str(spec), tmp_path / "out", tmp_path / "manifest.json"
            )

    def test_unknown_name_raises(self, synthetic_src, tmp_path):
        spec = tmp_path / "list.txt"
        spec.write_text("NoSuchProc\n")
        with pytest.raises(ValueError):
            qb.read_processor_list(str(spec), synthetic_src)

    def test_duplicate_name_raises(self, synthetic_src, tmp_path):
        spec = tmp_path / "list.txt"
        spec.write_text("ProcA\nProcA\n")
        with pytest.raises(ValueError, match="duplicate"):
            qb.read_processor_list(str(spec), synthetic_src)

    def test_comments_and_blank_lines_ignored(self, synthetic_src, tmp_path):
        spec = tmp_path / "list.txt"
        spec.write_text("# header\n\n  \nProcA  # trailing comment\n\n")
        assert qb.read_processor_list(str(spec), synthetic_src) == ["ProcA"]

    def test_all_includes_proca_and_raises_on_procb(self, synthetic_src, tmp_path):
        names = qb.read_processor_list("all", synthetic_src)
        assert names == ["ProcA", "ProcB"]
        with pytest.raises(ValueError, match="processors must not import processors"):
            qb.select(
                synthetic_src, "all", tmp_path / "out", tmp_path / "manifest.json"
            )


# ---------------------------------------------------------------------------
# select — real tree
# ---------------------------------------------------------------------------


class TestSelectRealTree:
    def test_matches_processors_txt(self, tmp_path):
        manifest = qb.select(
            ROOT / "nifi_extensions",
            str(ROOT / "docker/processors.txt"),
            tmp_path / "out",
            tmp_path / "manifest.json",
        )
        expected_names = qb.read_processor_list(
            str(ROOT / "docker/processors.txt"), ROOT / "nifi_extensions"
        )
        assert [p["type"] for p in manifest["processors"]] == expected_names
        assert manifest["helpers"] == ["reporting"]

        from conftest import MockContext  # noqa: F401 - installs nifiapi stubs

        for entry in manifest["processors"]:
            module = importlib.import_module(entry["module"])
            cls = getattr(module, entry["type"])
            assert entry["dependencies"] == cls.ProcessorDetails.dependencies
            assert entry["version"] == cls.ProcessorDetails.version


# ---------------------------------------------------------------------------
# flow
# ---------------------------------------------------------------------------


class TestFlow:
    def _manifest_for_canvas(self, tmp_path):
        canvas = json.loads((ROOT / "demo/grover/grover-3x3.json").read_text())
        types = qb.python_types(canvas["flowContents"])
        manifest = {
            "source": "docker/processors.txt",
            "processors": [
                {"type": t, "version": v, "dependencies": []} for t, v in types.items()
            ],
            "helpers": ["reporting"],
        }
        manifest_path = tmp_path / "manifest.json"
        manifest_path.write_text(json.dumps(manifest))
        return manifest_path

    def test_flow_on_committed_canvas(self, tmp_path):
        manifest_path = self._manifest_for_canvas(tmp_path)
        out = tmp_path / "flow" / "flow.json.gz"

        count = qb.build_flow(
            str(ROOT / "demo/grover/grover-3x3.json"), manifest_path, out
        )

        assert count == 8
        with gzip.open(out, "rt", encoding="utf-8") as handle:
            flow = json.load(handle)
        root = flow["rootGroup"]
        group = root["processGroups"][0]
        assert group["name"] == "Quanifi quickstart — Grover 3×3"
        assert group["groupIdentifier"] == root["identifier"]
        assert nifi_ready.python_processor_count(out) == 8

    def test_missing_processor_type_raises(self, tmp_path):
        canvas = json.loads((ROOT / "demo/grover/grover-3x3.json").read_text())
        types = qb.python_types(canvas["flowContents"])
        types.pop("QrispGroverCircuit")
        manifest = {
            "processors": [
                {"type": t, "version": v, "dependencies": []} for t, v in types.items()
            ],
            "helpers": [],
        }
        manifest_path = tmp_path / "manifest.json"
        manifest_path.write_text(json.dumps(manifest))

        with pytest.raises(ValueError, match="not in this image"):
            qb.build_flow(
                str(ROOT / "demo/grover/grover-3x3.json"),
                manifest_path,
                tmp_path / "flow.json.gz",
            )

    def test_running_component_raises(self, tmp_path):
        canvas = json.loads((ROOT / "demo/grover/grover-3x3.json").read_text())
        canvas["flowContents"]["processors"][0]["scheduledState"] = "RUNNING"
        canvas_path = tmp_path / "canvas.json"
        canvas_path.write_text(json.dumps(canvas))
        manifest_path = self._manifest_for_canvas(tmp_path)

        with pytest.raises(ValueError, match="RUNNING"):
            qb.build_flow(str(canvas_path), manifest_path, tmp_path / "flow.json.gz")

    def test_empty_canvas_writes_nothing(self, tmp_path):
        manifest_path = self._manifest_for_canvas(tmp_path)
        out = tmp_path / "flow" / "flow.json.gz"
        count = qb.build_flow("", manifest_path, out)
        assert count == 0
        assert not out.exists()
        assert out.parent.exists()


# ---------------------------------------------------------------------------
# dedup
# ---------------------------------------------------------------------------


class TestDedup:
    def test_identical_files_share_inode(self, tmp_path):
        a = tmp_path / "a" / "f.bin"
        b = tmp_path / "b" / "f.bin"
        a.parent.mkdir()
        b.parent.mkdir()
        a.write_bytes(b"x" * 1000)
        b.write_bytes(b"x" * 1000)

        linked, saved = qb.dedup(tmp_path)

        assert linked == 1
        assert saved == 1000
        assert a.stat().st_ino == b.stat().st_ino

    def test_same_content_different_mode_not_linked(self, tmp_path):
        a = tmp_path / "a" / "f.bin"
        b = tmp_path / "b" / "f.bin"
        a.parent.mkdir()
        b.parent.mkdir()
        a.write_bytes(b"y" * 500)
        b.write_bytes(b"y" * 500)
        os.chmod(a, 0o755)
        os.chmod(b, 0o644)

        linked, saved = qb.dedup(tmp_path)

        assert linked == 0
        assert saved == 0
        assert a.stat().st_ino != b.stat().st_ino

    def test_symlinks_untouched(self, tmp_path):
        a = tmp_path / "a" / "f.bin"
        a.parent.mkdir()
        a.write_bytes(b"z" * 100)
        link = tmp_path / "link.bin"
        link.symlink_to(a)

        linked, saved = qb.dedup(tmp_path)

        assert link.is_symlink()
        assert linked == 0


# ---------------------------------------------------------------------------
# prebake (fake subprocess.run)
# ---------------------------------------------------------------------------


class FakeRun:
    def __init__(self):
        self.calls = []

    def __call__(self, argv, check=True, cwd=None):
        self.calls.append(list(argv))
        if len(argv) >= 3 and argv[1:3] == ["-m", "venv"]:
            venv_path = Path(argv[3])
            (venv_path / "bin").mkdir(parents=True, exist_ok=True)
            (venv_path / "bin" / "python3").write_text("#!/bin/sh\n")
        return subprocess.CompletedProcess(argv, 0)


class TestPrebake:
    def test_venv_then_uv_and_markers(self, tmp_path, monkeypatch):
        manifest_path = tmp_path / "manifest.json"
        manifest_path.write_text(
            json.dumps(
                {
                    "processors": [
                        {"type": "TypeA", "version": "0.1.0", "dependencies": ["x>=1"]},
                        {"type": "TypeB", "version": "0.2.0", "dependencies": []},
                    ]
                }
            )
        )
        constraints = tmp_path / "constraints.txt"
        overrides = tmp_path / "overrides.txt"
        constraints.write_text("x==1.0\n")
        overrides.write_text("")
        monkeypatch.setattr(
            qb,
            "export_constraints",
            lambda project, uv, workdir: (constraints, overrides),
        )

        work_dir = tmp_path / "work"
        fake = FakeRun()
        qb.prebake(
            manifest_path, tmp_path / "project", work_dir, "python3", "uv", run=fake
        )

        type_a_calls = [c for c in fake.calls if "TypeA" in " ".join(c)]
        assert len(type_a_calls) == 2  # venv, then uv (has dependencies)
        assert type_a_calls[0][1:3] == ["-m", "venv"]
        assert type_a_calls[1][0] == "uv"
        env_a = work_dir / "extensions" / "TypeA" / "0.1.0"
        uv_call = type_a_calls[1]
        assert "--target" in uv_call and str(env_a) in uv_call
        assert "--python" in uv_call and str(env_a / "bin" / "python3") in uv_call
        assert "--constraints" in uv_call
        assert "--overrides" in uv_call
        assert "--link-mode" in uv_call and "hardlink" in uv_call

        type_b_calls = [c for c in fake.calls if "TypeB" in " ".join(c)]
        assert len(type_b_calls) == 1  # venv only, uv skipped (no dependencies)

        assert (env_a / "env-creation-complete.txt").exists()
        assert (env_a / "dependency-download.complete").read_text() == "True"
        env_b = work_dir / "extensions" / "TypeB" / "0.2.0"
        assert (env_b / "env-creation-complete.txt").exists()
        assert (env_b / "dependency-download.complete").read_text() == "True"


# ---------------------------------------------------------------------------
# quanifi_monitor: consume / decide
# ---------------------------------------------------------------------------


def _cfg(**kw):
    defaults = dict(stall_seconds=10, quiet_seconds=3, startup_timeout_seconds=100)
    defaults.update(kw)
    return qm.Config(**defaults)


class TestConsumeDecide:
    def test_ready_after_quiet(self):
        progress = qm.Progress(expected=8)
        cfg = _cfg()
        qm.consume(progress, ["Starting Flow Controller..."], 0.0)
        qm.consume(
            progress,
            ["Successfully loaded Python Processor {}".format(i) for i in range(8)],
            1.0,
        )
        qm.consume(progress, ["Started Application in 1.5 seconds"], 1.5)

        state, _ = qm.decide(progress, 1.5, started_at=0.0, cfg=cfg)
        assert state == "starting"
        state, msg = qm.decide(progress, 4.5, started_at=0.0, cfg=cfg)
        assert state == "ready"
        assert "8" in msg

    def test_stalled_after_silence(self):
        progress = qm.Progress(expected=8)
        cfg = _cfg()
        qm.consume(progress, ["Starting Flow Controller..."], 0.0)
        qm.consume(
            progress,
            ["Successfully loaded Python Processor {}".format(i) for i in range(3)],
            1.0,
        )
        state, msg = qm.decide(progress, 12.0, started_at=0.0, cfg=cfg)
        assert state == "stalled"
        assert "py4j startup wedge" in msg

    def test_progress_line_postpones_stall(self):
        progress = qm.Progress(expected=8)
        cfg = _cfg()
        qm.consume(progress, ["Starting Flow Controller..."], 0.0)
        qm.consume(progress, ["Successfully loaded Python Processor 0"], 1.0)
        qm.consume(progress, ["Installing dependencies for qiskit"], 8.0)
        state, _ = qm.decide(progress, 12.0, started_at=0.0, cfg=cfg)
        assert state == "starting"

    def test_installer_running_postpones_stall(self):
        progress = qm.Progress(expected=8)
        cfg = _cfg()
        qm.consume(progress, ["Starting Flow Controller..."], 0.0)
        qm.consume(progress, ["Successfully loaded Python Processor 0"], 1.0)
        state, _ = qm.decide(
            progress, 12.0, started_at=0.0, cfg=cfg, installer_running=True
        )
        assert state == "starting"

    def test_broken_on_import_error(self):
        progress = qm.Progress(expected=8)
        cfg = _cfg()
        qm.consume(progress, ["ImportError: No module named 'foo'"], 1.0)
        state, msg = qm.decide(progress, 2.0, started_at=0.0, cfg=cfg)
        assert state == "broken"
        assert "ImportError" in msg

    def test_broken_on_missing_types(self):
        progress = qm.Progress(expected=8)
        cfg = _cfg(missing_types=["FooProcessor"])
        state, msg = qm.decide(progress, 1.0, started_at=0.0, cfg=cfg)
        assert state == "broken"
        assert "FooProcessor" in msg

    def test_zero_expected_ready_after_quiet(self):
        progress = qm.Progress(expected=0)
        cfg = _cfg()
        qm.consume(progress, ["Started Application in 1 seconds"], 1.0)
        state, _ = qm.decide(progress, 1.5, started_at=0.0, cfg=cfg)
        assert state == "starting"
        state, _ = qm.decide(progress, 4.5, started_at=0.0, cfg=cfg)
        assert state == "ready"

    def test_timeout_without_flow_controller(self):
        progress = qm.Progress(expected=8)
        cfg = _cfg()
        state, msg = qm.decide(progress, 101.0, started_at=0.0, cfg=cfg)
        assert state == "timeout"
        assert "100" in msg


# ---------------------------------------------------------------------------
# quanifi_monitor: recovery gating
# ---------------------------------------------------------------------------


class TestRecoveryGating:
    def test_recovery_allowed_truth_table(self):
        assert qm.recovery_allowed(False, False, 0, 3) == (
            False,
            "QUANIFI_AUTO_RECOVER is off",
        )
        allowed, reason = qm.recovery_allowed(True, True, 0, 3)
        assert allowed is False
        assert "autoResumeState" in reason
        assert qm.recovery_allowed(True, False, 3, 3) == (
            False,
            "recovery limit reached (3/3)",
        )
        assert qm.recovery_allowed(True, False, 2, 3) == (True, "")

    def test_auto_resume_enabled(self, tmp_path):
        props = tmp_path / "nifi.properties"
        props.write_text("nifi.flowcontroller.autoResumeState=true\n")
        assert qm.auto_resume_enabled(props) is True
        props.write_text("nifi.flowcontroller.autoResumeState=false\n")
        assert qm.auto_resume_enabled(props) is False
        assert qm.auto_resume_enabled(tmp_path / "missing.properties") is True


# ---------------------------------------------------------------------------
# quanifi_monitor: check / reset
# ---------------------------------------------------------------------------


class TestCheck:
    def test_check_states(self, tmp_path):
        status = tmp_path / "status.json"
        qm.write_status(status, state="ready", message="all good")
        assert qm.check(argparse.Namespace(status=str(status))) == 0

        qm.write_status(status, state="starting", message="starting up")
        assert qm.check(argparse.Namespace(status=str(status))) == 1

        assert qm.check(argparse.Namespace(status=str(tmp_path / "missing.json"))) == 1

    def test_reset_writes_starting(self, tmp_path):
        status = tmp_path / "status.json"
        qm.reset(argparse.Namespace(status=str(status)))
        data = json.loads(status.read_text())
        assert data["state"] == "starting"


# ---------------------------------------------------------------------------
# quanifi_monitor: watch end-to-end (in-process, fake clock/sleep/kill)
# ---------------------------------------------------------------------------


def _write_flow(path, python_processor_count=1):
    flow = {
        "rootGroup": {
            "processors": [
                {"bundle": {"artifact": "python-extensions"}}
                for _ in range(python_processor_count)
            ],
            "processGroups": [],
        }
    }
    with gzip.open(path, "wt", encoding="utf-8") as handle:
        json.dump(flow, handle)


class TestWatchEndToEnd:
    def test_watch_reaches_ready(self, tmp_path, monkeypatch):
        log = tmp_path / "nifi-app.log"
        log.write_text("")
        flow_path = tmp_path / "flow.json.gz"
        _write_flow(flow_path, 1)
        status = tmp_path / "status.json"
        recoveries = tmp_path / "recoveries"

        monkeypatch.setenv("QUANIFI_POLL_SECONDS", "0.05")
        monkeypatch.setenv("QUANIFI_QUIET_SECONDS", "0.1")
        monkeypatch.setenv("QUANIFI_STALL_SECONDS", "5")
        monkeypatch.setenv("QUANIFI_STARTUP_TIMEOUT_SECONDS", "10")

        fake_time = [0.0]

        def fake_clock():
            return fake_time[0]

        def fake_sleep(seconds):
            fake_time[0] += seconds
            text = log.read_text()
            if fake_time[0] >= 0.1 and "Starting Flow Controller" not in text:
                with log.open("a") as handle:
                    handle.write("Starting Flow Controller...\n")
            elif fake_time[0] >= 0.2 and "Successfully loaded" not in text:
                with log.open("a") as handle:
                    handle.write("Successfully loaded Python Processor abc (Type)\n")
            elif fake_time[0] >= 0.3 and "Started Application" not in text:
                with log.open("a") as handle:
                    handle.write("Started Application in 1 seconds\n")

        args = argparse.Namespace(
            log=str(log),
            flow=str(flow_path),
            props="",
            manifest="",
            status=str(status),
            recoveries=str(recoveries),
            start_inode="0",
            start_offset="0",
        )
        rc = qm.watch(
            args,
            clock=fake_clock,
            sleep=fake_sleep,
            kill=lambda: None,
            installer=lambda: False,
        )

        assert rc == 0
        data = json.loads(status.read_text())
        assert data["state"] == "ready"

    def test_watch_stall_triggers_bounded_recovery(self, tmp_path, monkeypatch):
        log = tmp_path / "nifi-app.log"
        log.write_text("")
        flow_path = tmp_path / "flow.json.gz"
        _write_flow(flow_path, 1)
        props = tmp_path / "nifi.properties"
        props.write_text("nifi.flowcontroller.autoResumeState=false\n")
        status = tmp_path / "status.json"
        recoveries = tmp_path / "recoveries"

        monkeypatch.setenv("QUANIFI_POLL_SECONDS", "0.05")
        monkeypatch.setenv("QUANIFI_QUIET_SECONDS", "0.1")
        monkeypatch.setenv("QUANIFI_STALL_SECONDS", "0.2")
        monkeypatch.setenv("QUANIFI_STARTUP_TIMEOUT_SECONDS", "10")
        monkeypatch.setenv("QUANIFI_AUTO_RECOVER", "true")
        monkeypatch.setenv("QUANIFI_MAX_RECOVERIES", "3")

        fake_time = [0.0]
        appended = [False]

        def fake_clock():
            return fake_time[0]

        def fake_sleep(seconds):
            fake_time[0] += seconds
            if not appended[0] and fake_time[0] >= 0.05:
                with log.open("a") as handle:
                    handle.write("Starting Flow Controller...\n")
                appended[0] = True
            # No Python processor ever loads: this must stall.

        killed = []
        args = argparse.Namespace(
            log=str(log),
            flow=str(flow_path),
            props=str(props),
            manifest="",
            status=str(status),
            recoveries=str(recoveries),
            start_inode="0",
            start_offset="0",
        )
        rc = qm.watch(
            args,
            clock=fake_clock,
            sleep=fake_sleep,
            kill=lambda: killed.append(1),
            installer=lambda: False,
        )

        assert rc == 2
        assert len(killed) == 1
        assert recoveries.read_text().strip() == "1"


# ---------------------------------------------------------------------------
# quickstart_smoke.evaluate_result
# ---------------------------------------------------------------------------


def _valid_branches():
    return [
        {"label": "{} and {}".format(comp, engine), "top": "110", "dissent": False}
        for _, comp in qs.grover.BUILDERS
        for engine in qs.grover.ENGINES
    ]


def _valid_doc():
    return {
        "assert.verdict": "PASS",
        "consensus.branches": "9",
        "consensus.branches_json": _valid_branches(),
        "consensus.max_hellinger": "0.05",
    }


class TestEvaluateResult:
    def test_valid_document(self):
        assert qs.evaluate_result(_valid_doc()) == []

    def test_verdict_fail_is_one_error(self):
        doc = _valid_doc()
        doc["assert.verdict"] = "FAIL"
        errors = qs.evaluate_result(doc)
        assert len(errors) == 1
        assert "assert.verdict" in errors[0]

    def test_eight_branches_is_one_error(self):
        doc = _valid_doc()
        doc["consensus.branches"] = "8"
        errors = qs.evaluate_result(doc)
        assert len(errors) == 1
        assert "consensus.branches" in errors[0]

    def test_one_wrong_top_is_one_error(self):
        doc = _valid_doc()
        doc["consensus.branches_json"][0]["top"] = "011"
        errors = qs.evaluate_result(doc)
        assert len(errors) == 1
        assert "top" in errors[0]

    def test_a_dissent_is_one_error(self):
        doc = _valid_doc()
        doc["consensus.branches_json"][0]["dissent"] = True
        errors = qs.evaluate_result(doc)
        assert len(errors) == 1
        assert "dissented" in errors[0]

    def test_max_hellinger_too_high_is_one_error(self):
        doc = _valid_doc()
        doc["consensus.max_hellinger"] = "0.2"
        errors = qs.evaluate_result(doc)
        assert len(errors) == 1
        assert "max_hellinger" in errors[0]

    def test_wrong_label_is_one_error(self):
        doc = _valid_doc()
        doc["consensus.branches_json"][0]["label"] = "Bogus label"
        errors = qs.evaluate_result(doc)
        assert len(errors) == 1
        assert "branch labels" in errors[0]

    def test_branches_json_as_str_is_accepted(self):
        doc = _valid_doc()
        doc["consensus.branches_json"] = json.dumps(doc["consensus.branches_json"])
        assert qs.evaluate_result(doc) == []
