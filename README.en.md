# codex-sidebar-recovery

[简体中文](README.md) | **[English](README.en.md)**

## Recover Missing Codex Sidebar Projects

**Purpose: recover missing desktop sidebar projects and ungrouped threads when their project records still exist in the local Codex database.**

This skill turns a verified desktop recovery into a reusable workflow: distinguish a display preference from damaged sidebar metadata, then rebuild only that metadata from existing database records. It is not a session-cleanup tool: it does not delete threads, write to the database, or create source-code directories.

> This is a community recovery tool, not an official OpenAI repair utility. Applying recovery supports Windows and macOS. Internal storage is not a stable API; preview first. The database must remain intact and the current global state must be valid JSON.

## Included

- A concise skill entrypoint, Chinese/English guides, storage notes, and an offline recovery handoff.
- A standard-library Python tool that defaults to backup and preview and stops on identity or assignment conflicts.
- Preservation of multiple project roots, legacy ID mappings, explicit projectless markers, and archive flags.
- Independent Windows/macOS waiters that can survive the current assistant exiting, without forcibly stopping the app.
- Synthetic-data tests and a Windows / macOS / Ubuntu test workflow. Ubuntu tests cover POSIX planning and simulated recovery, not Linux desktop repair support.

## Install

Ask Codex in an environment that supports skill installation:

```text
Install the codex-sidebar-recovery skill from
https://github.com/turtoncarllyle/codex-sidebar-recovery/tree/main/codex-sidebar-recovery
```

Install the nested `codex-sidebar-recovery` directory, not the repository root. Let the installer resolve the skill location for your client version; reload or restart as directed if discovery does not pick it up.

Local scripts require Python 3.10+. Prefer an available Codex Python runtime without changing system PATH. The Windows waiter requires `pythonw.exe`, PowerShell 5.1+, and working WMI/CIM; the macOS waiter requires `launchctl`. A cloud-only assistant without local file/process access can provide instructions, but cannot repair your local app directly.

## Use After Installation

```text
Use $codex-sidebar-recovery to investigate missing Codex sidebar projects
and threads that are no longer grouped under their projects.
The database still contains the projects. Inspect and preview first.
This assistant is running inside the affected app: do not close or kill it.
Prepare an independent waiter if recovery must happen after I exit.
```

For diagnosis only, say "preview only; do not apply." To proceed, explicitly authorize restoring the sidebar metadata covered by the preview.

## Workflow

1. Check project grouping preferences and identify the active `CODEX_HOME`, junctions, and data source.
2. Compare database records with the sidebar cache in global JSON; confirm that source records are not missing.
3. Preview to produce configuration backups, a SQLite snapshot including committed WAL data, a candidate state, and a change summary.
4. After authorization, apply while the app is closed, or start the platform-specific independent waiter and verify its parent and `waiting_for_exit` status before exiting naturally.
5. Inspect the result, then open the app and verify project visibility and thread grouping. Script success is not UI verification.

Preview from the repository root:

```powershell
$codexHome = if ($env:CODEX_HOME) { $env:CODEX_HOME } else { Join-Path $env:USERPROFILE '.codex' }
$output = Join-Path $codexHome ('backups\sidebar-recovery-' + (Get-Date -Format 'yyyyMMdd-HHmmss'))
python -B .\codex-sidebar-recovery\scripts\recover_sidebar.py --codex-home $codexHome --output-dir $output --preview
```

Use a verified absolute interpreter path if Python is not on PATH. If the host key is ambiguous, supply an observed value using `--host-key`; the tool does not guess the relationship between logical and physical home paths.

On macOS, use `python3` and `~/.codex`:

```zsh
codexHome="${CODEX_HOME:-$HOME/.codex}"
output="$codexHome/backups/sidebar-recovery-$(date +%Y%m%d-%H%M%S)"
python3 -B ./codex-sidebar-recovery/scripts/recover_sidebar.py --codex-home "$codexHome" --output-dir "$output" --preview
```

See the [desktop handoff guide](codex-sidebar-recovery/references/desktop-handoff.md) for applying, cancellation, and rollback. Detailed references are Chinese; commands and status identifiers remain in English.

## Lessons From the Incident

**A healthy project database does not guarantee a healthy sidebar cache.** In the observed incident, database projects and thread foreign keys remained intact while the desktop cache had only one project. The exact process that originally reset the configuration was not proven.

**Do not race the running app.** The affected build persisted in-memory global state back to JSON. A normal child process may also exit with Codex; hiding a console window does not establish independence. Verify WMI ownership on Windows or `launchd` ownership on macOS before asking the user to quit.

**Restore explicit ID relationships, not directory guesses.** Projects may share roots and a project may have multiple roots. Legacy IDs, database IDs, and the host key must be connected correctly.

## Tips and Safety Notes

- Use a dedicated output directory for each recovery. Applying always reads and backs up fresh state rather than blindly applying an old preview.
- Do not delete SQLite WAL/SHM files or treat a raw copy of a live database's main file as a complete backup.
- The waiter expires after at most 24 hours. Tray instances and other Codex CLI processes keep it waiting; it never kills them.
- Keep the app closed during recovery until `status.json` reports completion or an actionable error.
- Primary JSON and `.bak` are replaced atomically one at a time, not as a two-file transaction. A later failure is reported as partial recovery.
- Missing/corrupt databases, unknown storage formats, and ordinary ChatGPT cloud projects are outside scope. macOS write behavior still needs to be checked against the client version. If current JSON is corrupt, preserve evidence for manual recovery rather than substituting an empty object.
- Snapshots contain private project paths and thread metadata. Keep them in controlled local storage, never in public repositories, issues, or chat attachments.

## Files and Validation

- [Skill entrypoint](codex-sidebar-recovery/SKILL.md)
- [Storage and recovery notes](codex-sidebar-recovery/references/storage-and-recovery.md)
- [Recovery script](codex-sidebar-recovery/scripts/recover_sidebar.py)
- [Windows detached launcher](codex-sidebar-recovery/scripts/Start-DetachedRecovery.ps1)
- [macOS detached launcher](codex-sidebar-recovery/scripts/start-detached-recovery.sh)
- [Isolated tests](tests/test_recovery.py)
- [Automated test runs](https://github.com/turtoncarllyle/codex-sidebar-recovery/actions)

```powershell
python -B -m unittest discover -s tests -v
```

Tests never access real Codex state. Recovery tests use synthetic process output or isolated mocks; Windows/macOS tests separately exercise native process enumeration and lock exclusion. They do not prove compatibility with every desktop build. Verify the UI after any real recovery.

## References and License

Repository organization and bilingual documentation follow [codex-session-cleanup](https://github.com/turtoncarllyle/codex-session-cleanup), but recovery and cleanup are different operations and must not be interchanged. For the skill mechanism, see [official OpenAI skill documentation](https://learn.chatgpt.com/docs/build-skills).

[MIT License](LICENSE)
