import gzip
import json
import time

from tools import nifi_ready


def test_python_processor_count_walks_nested_flow(tmp_path):
    flow = {
        "rootGroup": {
            "processors": [
                {"bundle": {"artifact": "nifi-standard-nar"}},
                {"bundle": {"artifact": "python-extensions"}},
            ],
            "processGroups": [{
                "processors": [
                    {"bundle": {"artifact": "python-extensions"}},
                ],
                "processGroups": [],
            }],
        }
    }
    path = tmp_path / "flow.json.gz"
    with gzip.open(path, "wt", encoding="utf-8") as handle:
        json.dump(flow, handle)

    assert nifi_ready.python_processor_count(path) == 2


def test_wait_requires_expected_current_start_loads(tmp_path):
    path = tmp_path / "nifi-app.log"
    old = "Successfully loaded Python Processor old\n"
    path.write_text(old, encoding="utf-8")
    inode, offset = path.stat().st_ino, path.stat().st_size
    path.write_text(old + "Successfully loaded Python Processor new\n",
                    encoding="utf-8")

    assert not nifi_ready.wait_for_quiet(
        path, quiet_seconds=0, deadline=time.time() + 0.02,
        expected_loaded=2, start_inode=inode, start_offset=offset,
        stall_seconds=0)


def test_wait_succeeds_only_after_all_current_start_loads(tmp_path):
    path = tmp_path / "nifi-app.log"
    path.write_text("old startup\n", encoding="utf-8")
    inode, offset = path.stat().st_ino, path.stat().st_size
    with path.open("a", encoding="utf-8") as handle:
        handle.write("Successfully loaded Python Processor one\n")
        handle.write("Successfully loaded Python Processor two\n")

    assert nifi_ready.wait_for_quiet(
        path, quiet_seconds=0, deadline=time.time() + 1,
        expected_loaded=2, start_inode=inode, start_offset=offset)


def test_read_new_log_resets_offset_after_rotation(tmp_path):
    path = tmp_path / "nifi-app.log"
    path.write_text("old\n", encoding="utf-8")
    old_inode, old_offset = path.stat().st_ino, path.stat().st_size
    path.rename(tmp_path / "nifi-app.1.log")
    path.write_text("new\n", encoding="utf-8")

    lines, inode, offset = nifi_ready.read_new_log(
        path, old_inode, old_offset)

    assert lines == ["new\n"]
    assert inode == path.stat().st_ino
    assert offset == path.stat().st_size


def test_categorize_processor_valid():
    proc = {"component": {"validationStatus": "VALID"}}
    cat, details = nifi_ready.categorize_processor(proc)
    assert cat == "valid"
    assert details is None


def test_categorize_processor_half_loaded_when_validating():
    proc = {"component": {"validationStatus": "VALIDATING"}}
    cat, details = nifi_ready.categorize_processor(proc)
    assert cat == "half_loaded"


def test_categorize_processor_half_loaded_on_runtime_environment():
    proc = {
        "component": {
            "validationStatus": "INVALID",
            "validationErrors": [
                "'Processor' is invalid because Initializing runtime environment for the Processor."
            ],
            "scheduledState": "ENABLED",
        }
    }
    cat, details = nifi_ready.categorize_processor(proc)
    assert cat == "half_loaded"


def test_categorize_processor_half_loaded_on_import_error():
    proc = {
        "component": {
            "validationStatus": "INVALID",
            "validationErrors": [
                "Failed to load: ModuleNotFoundError: No module named 'numpy._core'"
            ],
            "scheduledState": "ENABLED",
        }
    }
    cat, details = nifi_ready.categorize_processor(proc)
    assert cat == "half_loaded"


def test_categorize_processor_half_loaded_when_running_and_invalid():
    proc = {
        "component": {
            "validationStatus": "INVALID",
            "validationErrors": [
                "Relationship 'success' is invalid because it is not connected"
            ],
            "scheduledState": "RUNNING",
        }
    }
    cat, details = nifi_ready.categorize_processor(proc)
    assert cat == "half_loaded"


def test_categorize_processor_unconfigured_canvas_state():
    proc = {
        "component": {
            "validationStatus": "INVALID",
            "validationErrors": [
                "Relationship 'success' is invalid because it is not connected and not auto-terminated",
                "Processor requires an incoming connection but none is configured.",
            ],
            "scheduledState": "ENABLED",
        }
    }
    cat, details = nifi_ready.categorize_processor(proc)
    assert cat == "unconfigured"
    assert len(details) == 2


def test_audit_processors_separates_half_loaded_from_unconfigured(monkeypatch):
    fake_flow = {
        "processGroupFlow": {
            "flow": {
                "processors": [
                    {"component": {"id": "1", "name": "P1", "type": "T1", "validationStatus": "VALID"}},
                    {"component": {"id": "2", "name": "P2", "type": "T2", "validationStatus": "INVALID",
                                   "scheduledState": "ENABLED",
                                   "validationErrors": ["Relationship 'success' is not connected"]}},
                    {"component": {"id": "3", "name": "P3", "type": "T3", "validationStatus": "INVALID",
                                   "scheduledState": "ENABLED",
                                   "validationErrors": ["Initializing runtime environment for the Processor."]}},
                ],
                "processGroups": [],
            }
        }
    }
    monkeypatch.setattr(nifi_ready, "call", lambda path, token=None: fake_flow)
    half_loaded, unconfigured, valid_count, total = nifi_ready.audit_processors("token")
    assert total == 3
    assert valid_count == 1
    assert len(unconfigured) == 1
    assert unconfigured[0][0] == "P2"
    assert len(half_loaded) == 1
    assert half_loaded[0][0] == "P3"
