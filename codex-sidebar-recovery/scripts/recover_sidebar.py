"""Recover Codex sidebar metadata from existing project records; SQLite is read-only."""

import argparse
from contextlib import closing, contextmanager
from copy import deepcopy
import ctypes
from ctypes import wintypes
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import sqlite3
import sys
import time
import uuid


STATE = ".codex-global-state.json"
PROJECTS = "local-projects"
ORDER = "project-order"
MAPPING = "app-server-project-id-by-legacy-project-id-by-host"
ASSIGNMENTS = "thread-project-assignments"
MIGRATIONS = "app-server-projects-migration-by-host"
ALLOWED = {PROJECTS, ORDER, MAPPING, ASSIGNMENTS}
UNCHECKED = object()


class RecoveryError(RuntimeError):
    pass


class WorkerBusy(RecoveryError):
    pass


class PartialRecovery(RecoveryError):
    def __init__(self, message, backup):
        super().__init__(message)
        self.backup = str(backup)


def decode(data):
    value = json.loads(data.decode("utf-8-sig"))
    if not isinstance(value, dict):
        raise RecoveryError("Expected a JSON object")
    return value


def encode(value):
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def active_clients():
    if os.name != "nt":
        raise RecoveryError("Applying or waiting is supported on Windows only")

    class ProcessEntry(ctypes.Structure):
        _fields_ = [
            ("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD),
            ("th32ProcessID", wintypes.DWORD), ("th32DefaultHeapID", ctypes.c_size_t),
            ("th32ModuleID", wintypes.DWORD), ("cntThreads", wintypes.DWORD),
            ("th32ParentProcessID", wintypes.DWORD), ("pcPriClassBase", wintypes.LONG),
            ("dwFlags", wintypes.DWORD), ("szExeFile", wintypes.WCHAR * 260),
        ]

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    kernel.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    kernel.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(ProcessEntry)]
    kernel.Process32FirstW.restype = wintypes.BOOL
    kernel.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(ProcessEntry)]
    kernel.Process32NextW.restype = wintypes.BOOL
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    handle = kernel.CreateToolhelp32Snapshot(2, 0)
    if handle == ctypes.c_void_p(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    clients = []
    try:
        entry = ProcessEntry()
        entry.dwSize = ctypes.sizeof(entry)
        ok = kernel.Process32FirstW(handle, ctypes.byref(entry))
        while ok:
            if entry.szExeFile.lower() in {"chatgpt.exe", "codex.exe"}:
                clients.append({"pid": entry.th32ProcessID, "name": entry.szExeFile})
            ok = kernel.Process32NextW(handle, ctypes.byref(entry))
        error = ctypes.get_last_error()
        if error != 18:  # ERROR_NO_MORE_FILES is a successful enumeration end.
            raise ctypes.WinError(error)
    finally:
        kernel.CloseHandle(handle)
    return sorted(clients, key=lambda item: item["pid"])


def require_stopped():
    if active_clients():
        raise RecoveryError("Codex/ChatGPT is still running; exit naturally before applying")


def atomic_write(path, data, expected=UNCHECKED, guard=False):
    def check_expected():
        if expected is UNCHECKED:
            return
        actual = path.read_bytes() if path.exists() else None
        if actual != expected:
            raise RecoveryError("Concurrent change: " + path.name)

    check_expected()
    temp = path.with_name("." + path.name + ".recovery-" + uuid.uuid4().hex + ".tmp")
    try:
        with temp.open("xb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        if guard:
            require_stopped()
        check_expected()
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def write_status(output, phase, **details):
    value = {"status": phase, "timestamp": datetime.now(timezone.utc).isoformat(),
             "helper_pid": os.getpid(), **details}
    atomic_write(output / "status.json", encode(value))
    return value


def readonly(path):
    connection = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=5)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only=ON")
    return connection


def read_rows(connection):
    required = {
        "projects": {"id", "name", "position", "created_at_ms", "updated_at_ms"},
        "project_roots": {"project_id", "position", "path"},
        "project_idempotency_keys": {"key", "project_id"},
        "threads": {"id", "project_id", "archived"},
    }
    for table, columns in required.items():
        actual = {row[1] for row in connection.execute('PRAGMA table_info("' + table + '")')}
        if not columns <= actual:
            raise RecoveryError("Unsupported schema: " + table + " missing " + ", ".join(sorted(columns - actual)))
    connection.execute("BEGIN")
    try:
        return {
            "projects": [dict(r) for r in connection.execute("SELECT id,name,position,created_at_ms,updated_at_ms FROM projects ORDER BY position,id")],
            "roots": [dict(r) for r in connection.execute("SELECT project_id,position,path FROM project_roots ORDER BY project_id,position")],
            "aliases": [dict(r) for r in connection.execute("SELECT key,project_id FROM project_idempotency_keys ORDER BY key")],
            "threads": [dict(r) for r in connection.execute("SELECT id,project_id,archived FROM threads ORDER BY id")],
        }
    finally:
        connection.rollback()


def resolve_host_key(current, logical_home, supplied=None):
    if supplied:
        if not supplied.startswith("local:") or len(supplied) <= 6:
            raise RecoveryError("Expected an observed local:<logical Codex home> host key")
        return supplied
    keys = set(current.get(MAPPING, {})) | set(current.get(MIGRATIONS, {}))
    local = {key for key in keys if key.startswith("local:")}
    exact = "local:" + str(logical_home)
    if exact in local:
        return exact
    if len(local) == 1:
        return next(iter(local))
    raise RecoveryError("Cannot infer one host key; inspect the app state and pass --host-key")


def build_plan(current, rows, host_key):
    for key in (PROJECTS, MAPPING, ASSIGNMENTS):
        if not isinstance(current.get(key, {}), dict):
            raise RecoveryError("Unexpected state type: " + key)
    if current.get("app-server-pending-project-deletions-by-host", {}).get(host_key):
        raise RecoveryError("Pending project deletions exist; resolve them before recovery")
    if not rows["projects"]:
        raise RecoveryError("No saved projects to recover")
    restored = deepcopy(current)
    projects = deepcopy(current.get(PROJECTS, {}))
    mapping = deepcopy(current.get(MAPPING, {}))
    local_mapping = mapping.setdefault(host_key, {})
    aliases = {r["key"]: r["project_id"] for r in rows["aliases"]}
    native_ids = {r["id"] for r in rows["projects"]}
    roots = {}
    for row in rows["roots"]:
        roots.setdefault(row["project_id"], []).append(row["path"])
    legacy_for = {}
    for row in rows["projects"]:
        native_id = row["id"]
        candidates = {k for k, v in aliases.items() if v == native_id}
        known = {k for k, v in local_mapping.items() if v == native_id and k in projects}
        existing_ids = known | (candidates & projects.keys()) | ({native_id} & projects.keys())
        selected = existing_ids or candidates or {native_id}
        if len(selected) != 1:
            raise RecoveryError("Ambiguous project identity: " + native_id)
        legacy_id = next(iter(selected))
        legacy_for[native_id] = legacy_id
        paths = roots.get(native_id, [])
        if not paths or any(not isinstance(p, str) or not p for p in paths):
            raise RecoveryError("Project has no valid roots: " + native_id)
        existing = projects.get(legacy_id)
        if existing is not None:
            if existing.get("id") != legacy_id or existing.get("name") != row["name"] or existing.get("rootPaths") != paths:
                raise RecoveryError("Project cache/database conflict: " + legacy_id)
        else:
            projects[legacy_id] = {"id": legacy_id, "name": row["name"], "rootPaths": paths,
                                   "createdAt": row["created_at_ms"], "updatedAt": row["updated_at_ms"]}
        if legacy_id in local_mapping and local_mapping[legacy_id] != native_id:
            raise RecoveryError("Project mapping conflict: " + legacy_id)
        local_mapping[legacy_id] = native_id

    assignments = deepcopy(current.get(ASSIGNMENTS, {}))
    projectless = set(current.get("projectless-thread-ids", []))
    added = skipped = 0
    for row in rows["threads"]:
        native_id, tid = row["project_id"], row["id"]
        if native_id is None:
            continue
        if native_id not in native_ids:
            raise RecoveryError("Thread references a missing project: " + tid)
        if tid in projectless:
            skipped += 1
            continue
        wanted = {"projectId": legacy_for[native_id], "projectKind": "local"}
        if tid in assignments and assignments[tid] != wanted:
            raise RecoveryError("Conflicting existing thread assignment: " + tid)
        if tid not in assignments:
            assignments[tid] = wanted
            added += 1
    order = [legacy_for[r["id"]] for r in rows["projects"]]
    order.extend(pid for pid in current.get(ORDER, []) if pid in projects and pid not in order)
    order.extend(pid for pid in projects if pid not in order)
    restored.update({PROJECTS: projects, ORDER: order, MAPPING: mapping, ASSIGNMENTS: assignments})
    changed = {k for k in current.keys() | restored.keys() if current.get(k) != restored.get(k)}
    if not changed <= ALLOWED or set(order) != set(projects) or len(order) != len(set(order)):
        raise RecoveryError("Invalid recovery scope or project ordering")
    for assignment in assignments.values():
        if assignment.get("projectKind") == "local" and assignment.get("projectId") not in projects:
            raise RecoveryError("Assignment references a missing sidebar project")
    return restored, {
        "projects_before": len(current.get(PROJECTS, {})), "projects_after": len(projects),
        "database_threads": len(rows["threads"]),
        "database_assigned_threads": sum(r["project_id"] is not None for r in rows["threads"]),
        "assignments_added": added, "explicit_projectless_preserved": skipped,
        "changed_keys": sorted(changed), "database_writes": 0,
    }


def prepare(home, logical_home, output, host_key=None):
    destination = output / ("snapshot-" + datetime.now().strftime("%Y%m%d-%H%M%S-%f"))
    destination.mkdir(parents=True, exist_ok=False)
    original = (home / STATE).read_bytes()
    current = decode(original)
    key = resolve_host_key(current, logical_home, host_key)
    (destination / STATE).write_bytes(original)
    mirror = home / (STATE + ".bak")
    mirror_original = mirror.read_bytes() if mirror.exists() else None
    if mirror_original is not None:
        (destination / mirror.name).write_bytes(mirror_original)
    snapshot = destination / "state_5.sqlite"
    deadline = time.monotonic() + 30

    def progress(status_code, remaining, total):
        if time.monotonic() > deadline:
            raise RecoveryError("Snapshot timed out; retry after activity settles")

    with closing(readonly(home / "state_5.sqlite")) as source:
        with closing(sqlite3.connect(snapshot)) as target:
            source.backup(target, pages=128, progress=progress)
    with closing(readonly(snapshot)) as source:
        if source.execute("PRAGMA quick_check").fetchone()[0] != "ok":
            raise RecoveryError("Database snapshot integrity check failed")
        rows = read_rows(source)
    restored, summary = build_plan(current, rows, key)
    (destination / "candidate-global-state.json").write_bytes(encode(restored))
    (destination / "plan.json").write_bytes(encode(summary))
    return destination, original, mirror_original, restored, rows, summary


def apply_recovery(home, logical_home, output, host_key=None):
    require_stopped()
    destination, original, mirror_original, restored, rows, summary = prepare(home, logical_home, output, host_key)
    if not summary["changed_keys"]:
        return write_status(output, "unchanged", backup=str(destination), **summary)
    require_stopped()
    with closing(readonly(home / "state_5.sqlite")) as source:
        if read_rows(source) != rows:
            raise RecoveryError("Database changed during backup; no configuration was written")
    payload = encode(restored)
    atomic_write(home / STATE, payload, expected=original, guard=True)
    try:
        atomic_write(home / (STATE + ".bak"), payload, expected=mirror_original, guard=True)
        if decode((home / STATE).read_bytes()) != restored:
            raise RecoveryError("Primary state changed during verification")
        with closing(readonly(home / "state_5.sqlite")) as source:
            if read_rows(source) != rows:
                raise RecoveryError("Database changed during verification")
    except Exception as exc:
        raise PartialRecovery("Primary state was restored; further verification is needed: " + str(exc), destination) from exc
    return write_status(output, "repaired", backup=str(destination), **summary)


@contextmanager
def worker_lock(output):
    if os.name != "nt":
        raise RecoveryError("Detached recovery is supported on Windows only")
    import msvcrt
    with (output / "worker.lock").open("a+b") as handle:
        handle.seek(0)
        try:
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError as exc:
            raise WorkerBusy("Another recovery worker owns this output directory") from exc
        try:
            yield
        finally:
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)


def wait_for_exit(home, logical_home, output, host_key, timeout_hours, app_id):
    with worker_lock(output):
        prior = decode((output / "status.json").read_bytes()) if (output / "status.json").exists() else {}
        if prior.get("status") in {"repaired", "repaired_app_reopened", "unchanged"}:
            return prior
        quiet_since = None
        last_clients = None
        deadline = time.monotonic() + timeout_hours * 3600
        while time.monotonic() < deadline:
            if (output / "CANCEL").exists():
                return write_status(output, "cancelled")
            clients = active_clients()
            if clients != last_clients:
                write_status(output, "waiting_for_exit", active_clients=clients, timeout_hours=timeout_hours)
                last_clients = clients
            if clients:
                quiet_since = None
            else:
                quiet_since = quiet_since if quiet_since is not None else time.monotonic()
                if time.monotonic() - quiet_since >= 5:
                    result = apply_recovery(home, logical_home, output, host_key)
                    if app_id:
                        detail = {k: v for k, v in result.items() if k not in {"status", "timestamp", "helper_pid"}}
                        try:
                            os.startfile("shell:AppsFolder\\" + app_id)
                        except OSError as exc:
                            return write_status(output, result["status"], reopen_error=str(exc), **detail)
                        return write_status(output, "repaired_app_reopened", **detail)
                    return result
            time.sleep(1)
        return write_status(output, "expired_without_changes")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--codex-home", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--host-key")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--preview", action="store_true", help="Default: back up and plan without changing live files")
    mode.add_argument("--apply", action="store_true", help="Apply only after the app has stopped")
    mode.add_argument("--wait", action="store_true", help="Wait up to the timeout, then apply once")
    mode.add_argument("--cancel", action="store_true", help="Cancel a waiter using the same output directory")
    parser.add_argument("--timeout-hours", type=float, default=24)
    parser.add_argument("--restart-app-id", help="Observed Windows Store AppID; allowed with --wait only")
    args = parser.parse_args(argv)
    if args.restart_app_id and (not args.wait or not re.fullmatch(r"[A-Za-z0-9_.]+![A-Za-z0-9_.]+", args.restart_app_id)):
        parser.error("--restart-app-id requires --wait and a verified Store AppID")
    if not 0 < args.timeout_hours <= 24:
        parser.error("--timeout-hours must be greater than zero and at most 24")
    output = args.output_dir.absolute().resolve()
    try:
        logical_home = args.codex_home.absolute()
        home = logical_home.resolve(strict=True)
        if not home.is_dir() or output == home:
            raise RecoveryError("Use a dedicated output directory, not CODEX_HOME itself")
        output.mkdir(parents=True, exist_ok=True)
        if args.cancel:
            (output / "CANCEL").touch(exist_ok=True)
            result = {"status": "cancellation_requested"}
        elif args.wait:
            result = wait_for_exit(home, logical_home, output, args.host_key, args.timeout_hours, args.restart_app_id)
        elif args.apply:
            result = apply_recovery(home, logical_home, output, args.host_key)
        else:
            destination, _, _, _, _, summary = prepare(home, logical_home, output, args.host_key)
            result = {"status": "preview", "backup": str(destination), **summary}
        if sys.stdout is not None:
            print(json.dumps(result, ensure_ascii=True))
        return 0
    except Exception as exc:
        phase = "primary_restored_verification_pending" if isinstance(exc, PartialRecovery) else "failed"
        result = {"status": phase, "error": str(exc), "error_type": type(exc).__name__}
        if isinstance(exc, PartialRecovery):
            result["backup"] = exc.backup
        if not isinstance(exc, WorkerBusy) and output.is_dir() and output != args.codex_home.absolute().resolve():
            write_status(output, phase, **{k: v for k, v in result.items() if k != "status"})
        if sys.stderr is not None:
            print(json.dumps(result, ensure_ascii=True), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
