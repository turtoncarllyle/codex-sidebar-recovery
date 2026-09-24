"""Synthetic profiles only. No test targets the caller's real Codex home."""

from contextlib import closing, nullcontext, redirect_stderr, redirect_stdout
from copy import deepcopy
import importlib.util
import io
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "codex-sidebar-recovery" / "scripts" / "recover_sidebar.py"
spec = importlib.util.spec_from_file_location("recovery", SCRIPT)
r = importlib.util.module_from_spec(spec)
spec.loader.exec_module(r)
HOST = r"local:C:\Users\example\.codex"


def fixture():
    current = {
        r.PROJECTS: {"legacy-test": {"id": "legacy-test", "name": "test",
                                   "rootPaths": [r"D:\work\shared"],
                                   "createdAt": 1, "updatedAt": 2, "color": "blue"}},
        r.ORDER: ["legacy-test"],
        r.MAPPING: {HOST: {"legacy-test": "native-test"}, "remote:example": {"old": "new"}},
        r.MIGRATIONS: {HOST: "complete"},
        r.ASSIGNMENTS: {},
        "queued-work": [{"id": "keep-this"}],
        "projectless-thread-ids": ["explicit-projectless"],
    }
    rows = {
        "projects": [
            {"id": "native-a", "name": "Demo \u9879\u76ee", "position": 0, "created_at_ms": 1, "updated_at_ms": 2},
            {"id": "native-test", "name": "test", "position": 1, "created_at_ms": 1, "updated_at_ms": 2},
        ],
        "roots": [
            {"project_id": "native-a", "position": 0, "path": "D:\\work\\\u793a\u4f8b"},
            {"project_id": "native-a", "position": 1, "path": r"D:\work\shared"},
            {"project_id": "native-test", "position": 0, "path": r"D:\work\shared"},
        ],
        "aliases": [
            {"key": "legacy-a", "project_id": "native-a"},
            {"key": "legacy-test", "project_id": "native-test"},
            {"key": "deleted-alias", "project_id": "deleted-native"},
        ],
        "threads": [
            {"id": "active", "project_id": "native-a", "archived": 0},
            {"id": "archived", "project_id": "native-a", "archived": 1},
            {"id": "explicit-projectless", "project_id": "native-a", "archived": 0},
            {"id": "no-project", "project_id": None, "archived": 0},
            {"id": "test-thread", "project_id": "native-test", "archived": 0},
        ],
    }
    return current, rows


def create_database(path, rows):
    with closing(sqlite3.connect(path)) as db:
        db.executescript("""
            CREATE TABLE projects (id TEXT PRIMARY KEY, name TEXT, position INTEGER,
                                   created_at_ms INTEGER, updated_at_ms INTEGER);
            CREATE TABLE project_roots (project_id TEXT, position INTEGER, path TEXT);
            CREATE TABLE project_idempotency_keys (key TEXT PRIMARY KEY, project_id TEXT);
            CREATE TABLE threads (id TEXT PRIMARY KEY, project_id TEXT, archived INTEGER);
        """)
        db.executemany("INSERT INTO projects VALUES (:id,:name,:position,:created_at_ms,:updated_at_ms)", rows["projects"])
        db.executemany("INSERT INTO project_roots VALUES (:project_id,:position,:path)", rows["roots"])
        db.executemany("INSERT INTO project_idempotency_keys VALUES (:key,:project_id)", rows["aliases"])
        db.executemany("INSERT INTO threads VALUES (:id,:project_id,:archived)", rows["threads"])
        db.commit()


class PlanTests(unittest.TestCase):
    def setUp(self):
        self.current, self.rows = fixture()

    def plan(self):
        return r.build_plan(self.current, self.rows, HOST)

    def test_restores_ids_multiroots_and_database_order(self):
        actual, summary = self.plan()
        self.assertEqual(actual[r.ORDER], ["legacy-a", "legacy-test"])
        self.assertEqual(actual[r.PROJECTS]["legacy-a"]["rootPaths"],
                         [row["path"] for row in self.rows["roots"][:2]])
        self.assertEqual(actual[r.MAPPING][HOST]["legacy-a"], "native-a")
        self.assertEqual(actual[r.ASSIGNMENTS]["active"], {"projectId": "legacy-a", "projectKind": "local"})
        self.assertEqual(summary["assignments_added"], 3)
        self.assertEqual(summary["database_writes"], 0)

    def test_inputs_unrelated_fields_and_existing_project_unchanged(self):
        before, rows_before = deepcopy(self.current), deepcopy(self.rows)
        actual, _ = self.plan()
        self.assertEqual(self.current, before)
        self.assertEqual(self.rows, rows_before)
        self.assertEqual(actual[r.PROJECTS]["legacy-test"], before[r.PROJECTS]["legacy-test"])
        self.assertEqual(actual[r.MAPPING]["remote:example"], before[r.MAPPING]["remote:example"])
        for key in before.keys() - r.ALLOWED:
            self.assertEqual(actual[key], before[key])

    def test_no_resurrection_cwd_guess_or_unarchive(self):
        actual, summary = self.plan()
        self.assertNotIn("deleted-alias", actual[r.PROJECTS])
        self.assertNotIn("no-project", actual[r.ASSIGNMENTS])
        self.assertNotIn("explicit-projectless", actual[r.ASSIGNMENTS])
        self.assertEqual(actual[r.ASSIGNMENTS]["test-thread"]["projectId"], "legacy-test")
        self.assertEqual(self.rows["threads"][1]["archived"], 1)
        self.assertEqual(summary["explicit_projectless_preserved"], 1)

    def test_idempotent_and_preserves_unmigrated_projects(self):
        self.current[r.PROJECTS]["new"] = {"id": "new", "name": "New", "rootPaths": [r"D:\work\new"]}
        self.current[r.ASSIGNMENTS]["new-thread"] = {"projectId": "new", "projectKind": "local"}
        first, _ = self.plan()
        second, summary = r.build_plan(first, self.rows, HOST)
        self.assertEqual(second, first)
        self.assertEqual(summary["changed_keys"], [])
        self.assertEqual(first[r.ORDER][-1], "new")

    def test_native_identity_fallback(self):
        self.rows["aliases"] = [row for row in self.rows["aliases"] if row["project_id"] != "native-a"]
        actual, _ = self.plan()
        self.assertIn("native-a", actual[r.PROJECTS])
        self.assertEqual(actual[r.ASSIGNMENTS]["active"]["projectId"], "native-a")

    def test_ambiguous_identity_stops(self):
        self.rows["aliases"].append({"key": "legacy-a-2", "project_id": "native-a"})
        with self.assertRaisesRegex(r.RecoveryError, "Ambiguous"):
            self.plan()

    def test_existing_identity_disambiguates_historical_aliases(self):
        self.rows["aliases"].append({"key": "older-test", "project_id": "native-test"})
        actual, _ = self.plan()
        self.assertNotIn("older-test", actual[r.PROJECTS])
        self.assertEqual(actual[r.MAPPING][HOST]["legacy-test"], "native-test")

    def test_existing_assignment_conflict_stops(self):
        self.current[r.ASSIGNMENTS]["active"] = {"projectId": "legacy-test", "projectKind": "local"}
        with self.assertRaisesRegex(r.RecoveryError, "Conflicting existing thread"):
            self.plan()

    def test_existing_project_or_mapping_conflict_stops(self):
        self.current[r.PROJECTS]["legacy-test"]["name"] = "User renamed this"
        with self.assertRaisesRegex(r.RecoveryError, "cache/database conflict"):
            self.plan()
        self.current, self.rows = fixture()
        self.current[r.MAPPING][HOST]["legacy-test"] = "wrong-project"
        with self.assertRaisesRegex(r.RecoveryError, "mapping conflict"):
            self.plan()

    def test_pending_deletion_stops(self):
        self.current["app-server-pending-project-deletions-by-host"] = {HOST: ["native-a"]}
        with self.assertRaisesRegex(r.RecoveryError, "Pending project deletions"):
            self.plan()

    def test_missing_fk_root_or_projects_stops(self):
        for damage, message in [
            (lambda: self.rows["threads"][0].update(project_id="missing"), "missing project"),
            (lambda: self.rows.update(roots=[]), "no valid roots"),
            (lambda: self.rows.update(projects=[]), "No saved projects"),
        ]:
            with self.subTest(message=message):
                self.current, self.rows = fixture()
                damage()
                with self.assertRaisesRegex(r.RecoveryError, message):
                    self.plan()

    def test_invalid_state_shape_stops(self):
        for key in (r.PROJECTS, r.MAPPING, r.ASSIGNMENTS):
            with self.subTest(key=key):
                self.current, self.rows = fixture()
                self.current[key] = []
                with self.assertRaisesRegex(r.RecoveryError, "Unexpected state type"):
                    self.plan()

    def test_host_preserves_logical_path_instead_of_physical_junction_target(self):
        self.assertEqual(r.resolve_host_key(self.current, Path(r"E:\data\.codex")), HOST)
        self.current[r.MAPPING]["local:another"] = {}
        with self.assertRaisesRegex(r.RecoveryError, "Cannot infer"):
            r.resolve_host_key(self.current, Path(r"E:\data\.codex"))
        logical = Path(r"C:\Users\example\.codex")
        self.assertEqual(r.resolve_host_key(self.current, logical), HOST)
        self.assertEqual(r.resolve_host_key({}, logical, HOST), HOST)
        with self.assertRaisesRegex(r.RecoveryError, "observed local"):
            r.resolve_host_key({}, logical, "remote:wrong")


class ProfileTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="sidebar-recovery-test-")
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name) / "profile"
        self.output = Path(self.temp.name) / "output"
        self.home.mkdir()
        self.output.mkdir()
        self.current, self.rows = fixture()
        self.state = self.home / r.STATE
        self.mirror = self.home / (r.STATE + ".bak")
        self.db = self.home / "state_5.sqlite"
        self.original = r.encode(self.current)
        self.state.write_bytes(self.original)
        self.mirror.write_bytes(b'{"previous": true}\n')
        create_database(self.db, self.rows)

    def prepare(self):
        return r.prepare(self.home, self.home, self.output)

    def apply(self):
        with patch.object(r, "active_clients", return_value=[]):
            return r.apply_recovery(self.home, self.home, self.output)

    def test_preview_has_backup_and_candidate_without_source_changes(self):
        before = {p.name: p.read_bytes() for p in self.home.iterdir()}
        destination, original, _, planned, _, summary = self.prepare()
        self.assertEqual(original, self.original)
        self.assertEqual((destination / r.STATE).read_bytes(), self.original)
        self.assertEqual(r.decode((destination / "candidate-global-state.json").read_bytes()), planned)
        self.assertEqual(summary["projects_after"], 2)
        self.assertEqual({p.name: p.read_bytes() for p in self.home.iterdir()}, before)

    def test_snapshot_includes_committed_wal(self):
        with closing(sqlite3.connect(self.db)) as writer:
            writer.execute("PRAGMA journal_mode=WAL")
            writer.execute("PRAGMA wal_autocheckpoint=0")
            writer.execute("INSERT INTO threads VALUES ('wal-thread', 'native-a', 0)")
            writer.commit()
            self.assertGreater(Path(str(self.db) + "-wal").stat().st_size, 0)
            _, _, _, planned, _, summary = self.prepare()
            self.assertIn("wal-thread", planned[r.ASSIGNMENTS])
            self.assertEqual(summary["database_threads"], 6)

    def test_apply_updates_primary_and_mirror_not_database(self):
        db_before = self.db.read_bytes()
        mirror_before = self.mirror.read_bytes()
        result = self.apply()
        self.assertEqual(result["status"], "repaired")
        self.assertEqual(self.state.read_bytes(), self.mirror.read_bytes())
        self.assertEqual(self.db.read_bytes(), db_before)
        self.assertEqual((Path(result["backup"]) / r.STATE).read_bytes(), self.original)
        self.assertEqual((Path(result["backup"]) / self.mirror.name).read_bytes(), mirror_before)
        self.assertIn("Demo \u9879\u76ee", self.state.read_text(encoding="utf-8"))
        self.assertEqual(self.apply()["status"], "unchanged")

    def test_running_guard_rejects_before_backup_or_write(self):
        with patch.object(r, "active_clients", return_value=[{"pid": 123, "name": "Codex.exe"}]):
            with self.assertRaisesRegex(r.RecoveryError, "still running"):
                r.apply_recovery(self.home, self.home, self.output)
        self.assertEqual(list(self.output.iterdir()), [])
        self.assertEqual(self.state.read_bytes(), self.original)

    def test_reopened_app_before_primary_write_stops(self):
        with patch.object(r, "active_clients", side_effect=[[], [], [{"pid": 123, "name": "Codex.exe"}]]):
            with self.assertRaisesRegex(r.RecoveryError, "still running"):
                r.apply_recovery(self.home, self.home, self.output)
        self.assertEqual(self.state.read_bytes(), self.original)
        self.assertFalse(list(self.home.glob("*.tmp")))

    def test_reopened_app_before_mirror_reports_partial_recovery(self):
        mirror_before = self.mirror.read_bytes()
        with patch.object(r, "active_clients", side_effect=[[], [], [], [{"pid": 123, "name": "Codex.exe"}]]):
            with self.assertRaises(r.PartialRecovery) as raised:
                r.apply_recovery(self.home, self.home, self.output)
        self.assertNotEqual(self.state.read_bytes(), self.original)
        self.assertEqual(self.mirror.read_bytes(), mirror_before)
        self.assertTrue(Path(raised.exception.backup).is_dir())

    def test_atomic_write_rejects_concurrent_change(self):
        self.state.write_bytes(b'{"concurrent": true}')
        with self.assertRaisesRegex(r.RecoveryError, "Concurrent change"):
            r.atomic_write(self.state, b'{}', expected=self.original)
        self.assertEqual(self.state.read_bytes(), b'{"concurrent": true}')

    def test_database_change_after_snapshot_stops(self):
        original_prepare = r.prepare

        def changed(*args):
            result = original_prepare(*args)
            with closing(sqlite3.connect(self.db)) as writer:
                writer.execute("UPDATE threads SET project_id=NULL WHERE id='active'")
                writer.commit()
            return result

        with patch.object(r, "prepare", side_effect=changed):
            with self.assertRaisesRegex(r.RecoveryError, "Database changed"):
                self.apply()
        self.assertEqual(self.state.read_bytes(), self.original)

    def test_invalid_schema_or_json_does_not_write(self):
        with closing(sqlite3.connect(self.db)) as writer:
            writer.execute("DROP TABLE project_roots")
            writer.commit()
        with self.assertRaisesRegex(r.RecoveryError, "Unsupported schema"):
            self.apply()
        self.assertEqual(self.state.read_bytes(), self.original)
        self.state.write_bytes(b'\0' * 20)
        with self.assertRaises(ValueError):
            self.apply()
        self.assertEqual(self.state.read_bytes(), b'\0' * 20)

    def test_missing_mirror_is_created(self):
        self.mirror.unlink()
        result = self.apply()
        self.assertEqual(self.mirror.read_bytes(), self.state.read_bytes())
        self.assertFalse((Path(result["backup"]) / self.mirror.name).exists())

    def test_cli_default_is_preview_and_cancel_does_not_apply(self):
        args = ["--codex-home", str(self.home), "--output-dir", str(self.output)]
        with redirect_stdout(io.StringIO()) as stream:
            self.assertEqual(r.main(args), 0)
        self.assertEqual(json.loads(stream.getvalue())["status"], "preview")
        with redirect_stdout(io.StringIO()):
            self.assertEqual(r.main(args + ["--cancel"]), 0)
        self.assertTrue((self.output / "CANCEL").exists())
        self.assertEqual(self.state.read_bytes(), self.original)

    def test_duplicate_waiter_does_not_overwrite_first_worker_status(self):
        before = r.write_status(self.output, "waiting_for_exit")
        args = ["--codex-home", str(self.home), "--output-dir", str(self.output), "--wait"]
        with patch.object(r, "wait_for_exit", side_effect=r.WorkerBusy("busy")), redirect_stderr(io.StringIO()):
            self.assertEqual(r.main(args), 1)
        self.assertEqual(r.decode((self.output / "status.json").read_bytes()), before)

    def test_cancelled_waiter_never_applies(self):
        (self.output / "CANCEL").touch()
        with patch.object(r, "worker_lock", return_value=nullcontext()), patch.object(r, "apply_recovery") as apply:
            result = r.wait_for_exit(self.home, self.home, self.output, None, 1, None)
        apply.assert_not_called()
        self.assertEqual(result["status"], "cancelled")

    def test_timeout_never_applies(self):
        with patch.object(r, "worker_lock", return_value=nullcontext()), \
                patch.object(r.time, "monotonic", side_effect=[0, 10]), patch.object(r, "apply_recovery") as apply:
            result = r.wait_for_exit(self.home, self.home, self.output, None, 0.001, None)
        apply.assert_not_called()
        self.assertEqual(result["status"], "expired_without_changes")

    def test_waiter_reads_fresh_state_and_applies_once_after_quiet_period(self):
        self.current["queued-work"].append({"id": "saved-at-shutdown"})
        self.state.write_bytes(r.encode(self.current))
        with patch.object(r, "worker_lock", return_value=nullcontext()), \
                patch.object(r, "active_clients", return_value=[]), \
                patch.object(r.time, "sleep"), \
                patch.object(r.time, "monotonic", side_effect=[0, 0, 0, 0, 6, 6, 6, 6]):
            result = r.wait_for_exit(self.home, self.home, self.output, None, 1, None)
        self.assertEqual(result["status"], "repaired")
        self.assertEqual(r.decode(self.state.read_bytes())["queued-work"], self.current["queued-work"])
        self.assertEqual(len(list(self.output.glob("snapshot-*"))), 1)

    @unittest.skipUnless(os.name == "nt", "Windows process enumeration and locking")
    def test_native_enumeration_and_exclusive_worker_lock(self):
        self.assertIsInstance(r.active_clients(), list)
        with r.worker_lock(self.output):
            with self.assertRaises(r.WorkerBusy):
                with r.worker_lock(self.output):
                    self.fail("second worker acquired an exclusive lock")

    @unittest.skipUnless(os.name == "nt" and os.environ.get("RUN_WMI_TEST") == "1",
                         "Opt-in WMI handoff smoke test; requires a running Codex client")
    def test_detached_launcher_waits_and_cancels_on_synthetic_home(self):
        if not r.active_clients():
            self.skipTest("No protected client is running; cannot exercise waiting safely")
        pythonw = Path(sys.executable).with_name("pythonw.exe")
        self.assertTrue(pythonw.is_file())
        launcher = SCRIPT.with_name("Start-DetachedRecovery.ps1")
        status_path = self.output / "status.json"
        try:
            process = subprocess.run([
                "powershell.exe", "-NoProfile", "-File", str(launcher),
                "-CodexHome", str(self.home), "-OutputDirectory", str(self.output), "-Pythonw", str(pythonw),
            ], check=True, capture_output=True, encoding="utf-8", timeout=30)
            launched = json.loads(process.stdout)
            self.assertEqual(launched["ParentName"].lower(), "wmiprvse.exe")
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline:
                if status_path.exists() and r.decode(status_path.read_bytes()).get("status") == "waiting_for_exit":
                    break
                time.sleep(0.1)
            self.assertEqual(r.decode(status_path.read_bytes())["status"], "waiting_for_exit")
            self.assertEqual(self.state.read_bytes(), self.original)
        finally:
            (self.output / "CANCEL").touch()
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline:
                if status_path.exists() and r.decode(status_path.read_bytes()).get("status") == "cancelled":
                    break
                time.sleep(0.1)
            time.sleep(0.2)
        self.assertEqual(r.decode(status_path.read_bytes())["status"], "cancelled")
        self.assertEqual(self.state.read_bytes(), self.original)


if __name__ == "__main__":
    unittest.main()
