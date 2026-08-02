# AutoCorp CLI

A **local, terminal-first AI coding assistant** powered by [Ollama](https://ollama.com)
(`llama3.2`). It can **plan → build → test → explain** code, learns from past
builds, and is architected so [Agent Watchdog](#agent-watchdog-integration-future)
can later approve or block every action.

Everything runs **locally**. No cloud, no API keys, no fine-tuning.

> **This section describes the original build→test→explain loop.** The CLI has
> grown well beyond it since: pluggable code-generation engines (local/Claude/
> DeepSeek), a repair/self-healing pipeline, and a read-only-or-disposable
> repository-intelligence toolchain that can safely analyze and validate an
> external target repository end-to-end. See [Current commands](#current-commands)
> below for the full CLI surface, and `AI_ENGINEERING/ARCHITECTURE.md` /
> `AI_ENGINEERING/CURRENT_PHASE.md` for the complete, evidence-based picture of
> what's built, what's committed, and what's still in progress.

---

## The four brains

| Brain | Module | Job |
|-------|--------|-----|
| **Planner** | `brains/planner.py` | Breaks a request into steps + files + a test command, *before* any code is written |
| **Builder** | `brains/builder.py` | Writes each file (implementation first, then tests, each seeing the real contents of earlier files) |
| **Tester** | `brains/tester.py` | Runs the tests, reads failures, and proposes fixes |
| **Memory** | `memory/store.py` | Stores successful builds + mistakes/fixes in SQLite and recalls them for future requests |

The orchestrator (`core/orchestrator.py`) runs the loop:

```
recall lessons → plan → confirm → build → test → fix-loop → learn
```

---

## Architecture

```
                 ┌──────────────────────────────────────────┐
   request ───▶  │ Planner → Builder → Tester  (the brains)  │
                 └───────────────┬──────────────────────────┘
                                 │ every file write / command
                                 ▼
                 ┌──────────────────────────────────────────┐
                 │ Executor  →  CommandGate   (safety seam)  │   ◀── Agent Watchdog plugs in here
                 └───────────────┬──────────────────────────┘
                                 ▼
                 ┌──────────────────────────────────────────┐
                 │ Memory (SQLite)   +   Ollama (llama3.2)   │
                 └──────────────────────────────────────────┘
```

**Key rule:** the brains never touch the filesystem or shell directly. Every
write and command goes through `Executor`, which asks a `CommandGate` for
permission. That single choke point is what makes safety — and the future
Watchdog integration — clean.

---

## Setup

```bash
cd ~/autocorp_cli
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
# pytest is used to run the tests of generated Python projects:
pip install pytest
```

Requires Ollama running with `llama3.2` pulled:

```bash
ollama pull llama3.2
ollama serve            # if not already running
```

> Note: the code uses the installed tag `llama3.2:latest` (which **is** the 3.2B
> model). Override with `AUTOCORP_MODEL=...` if you pull a different tag.

---

## Usage

```bash
python autocorp.py                      # interactive REPL
python autocorp.py build "<request>"    # plan → build → test (confirms each action)
python autocorp.py plan  "<request>"    # show a plan only — writes nothing
python autocorp.py test  [workspace]    # run tests on an existing build
python autocorp.py explain <file>       # explain a source file
python autocorp.py memory               # show what it has learned

python autocorp.py --auto build "..."   # skip confirmations (allow-all gate)
```

### Current commands

The build→plan→test→explain→memory loop above is the original core. The CLI
has since grown to sixteen subcommands in total (run `python autocorp.py
--help` for the authoritative, current list — this table can drift, that
command cannot). Not all of them are committed to `main` yet — see
`AI_ENGINEERING/CURRENT_PHASE.md` for exactly which:

| Command | Purpose |
|---|---|
| `build` | plan → build → test a request (confirms each action) |
| `plan` | show a build plan only — writes nothing |
| `test` | run tests on an existing workspace |
| `explain` | explain a source file |
| `memory` | show stored builds and lessons |
| `scan` | read-only repository scan (git, files, TODO/FIXME markers) |
| `analyze` | read-only project architecture/health analysis |
| `plan-project` | read-only, evidence-cited project action planner |
| `repair` | narrow, deterministic repair executor (dry-run by default) |
| `propose-repair` | AI-generated repair proposal (review-only, never applies) |
| `live-readiness` | checks whether a target app is ready to be live-tested |
| `live-test` | controlled, non-mutating live test of a target FastAPI app |
| `workflow-test` | disposable end-to-end episode workflow test against a target repo (requires `--disposable`) |
| `publish-test` | disposable publishing-pipeline validation, up to but never past a real external upload (requires `--disposable`) |
| `quick-podcast` | generate a real, disposable, locally-listenable podcast episode against a target repo |
| `chat` | repository-aware conversational interface over existing AutoCorp capabilities |
| `episode-build` | guided, resumable CloneCast episode operator (terminal) — the repaired foundation `app` is built on |
| `app` | start the AutoCorp Chat App: local FastAPI server + browser chat UI over the guided episode operator — see below |

`scan`/`analyze`/`plan-project`/`repair`/`propose-repair`/`live-readiness`/
`live-test`/`workflow-test`/`publish-test`/`chat` all accept `--repo <path>`
to safely target an external repository (never AutoCorp's own, and never
without confirming the path is a real Git working tree first) — see
`AI_ENGINEERING/ARCHITECTURE.md` for how that safety boundary works. See
`AI_ENGINEERING/PHASES.md` for the full history of when and why each of
these was added.

By default the assistant **confirms before every file write and command**
(answer `y`, `n`, or `a` for "yes to all"). Generated projects land in
`workspace/<project_name>/`.

### Example

```bash
python autocorp.py --auto build \
  "a Python module strutils.py with reverse_string(s) and is_palindrome(s), plus pytest tests"
```

→ plans the project, writes `workspace/strutils/strutils.py` + `test_strutils.py`,
runs `pytest`, auto-fixes failures, and records the outcome to memory.

---

## AutoCorp Chat App (Phase 1) — local desktop chat, no terminal required

A local-first browser chat app that replaces the terminal-only guided
CloneCast episode workflow with a real double-click desktop application. It
does not replace or duplicate the guided operator — it drives it.

**What it does:** double-click the AutoCorp desktop icon → a local FastAPI
server starts (or an already-running one is reused) → your default browser
opens to the chat UI → you create or resume a CloneCast episode by typing and
clicking buttons, never a terminal command. Internal project/studio/research/
script/session/voice IDs are resolved automatically from friendly names.
Publishing stays locked in Phase 1 — there is no publish route anywhere in
the app.

### Architecture

```
autocorp.py app          → app/server.py (FastAPI factory) → app/routes.py
app/chat_controller.py   → runs brains/guided_clonecast_episode.py's
                            run_guided_episode_build() on a background thread
                            per chat session, unmodified — it is the *only*
                            place that drives the guided workflow from the
                            web layer. input_func()/output() are implemented
                            as a queue-based bridge to the browser instead of
                            the terminal's input()/print().
app/clonecast_client.py  → read-only CloneCast adapter (studio/voice lists,
                            config-check) used only to build friendly labels;
                            an explicit allowlist, never an arbitrary command.
app/session_store.py     → AppSession JSON records (chat transcript, pending
                            question, upload provenance) in
                            data/autocorp_app_sessions/. Workflow state itself
                            (studio, research, script, voice, audio,
                            publishing lock) stays in the guided operator's
                            own EpisodeSession records under
                            data/guided_clonecast_episode_sessions/ — not
                            duplicated.
app/file_service.py      → managed upload storage, SHA-256, path-traversal
                            and extension safety.
app/system_status.py     → real, non-mutating status checks.
app/launcher.py          → single-instance, readiness-gated desktop startup.
```

### Starting it

```bash
# Manual fallback (always supported):
.venv/bin/python autocorp.py app

# Custom port/host (loopback only unless --allow-external is passed):
.venv/bin/python autocorp.py app --host 127.0.0.1 --port 8787
```

Local URL: **http://127.0.0.1:8787**. Health check: `curl http://127.0.0.1:8787/health`.

### Desktop launcher

```bash
# Install the desktop icon + applications-menu entry (no sudo, idempotent):
./scripts/install_autocorp_desktop.sh

# Remove it:
rm -f ~/.local/share/applications/autocorp.desktop ~/Desktop/autocorp.desktop
```

Double-clicking the installed **AutoCorp** icon runs
`scripts/start_autocorp_app.sh`, which resolves the repository root from its
own location (not the caller's `$PWD`), waits for `GET /health` to actually
succeed before opening the browser, and — if a healthy server is already
listening on the configured port — reuses it instead of starting a second
process. Startup is logged to `data/autocorp_app_logs/launcher.log`
(server stdout/stderr in the same directory); the running server's PID is
recorded in `data/autocorp_app.pid`.

```bash
# View startup logs:
tail -f data/autocorp_app_logs/launcher.log data/autocorp_app_logs/server.err.log

# Stop the local app safely:
kill "$(cat data/autocorp_app.pid)"
```

### GPU / Ollama coordination policy (permanent)

Ollama is optional and off by default for production: nothing on the
research or approved-script path (`app/file_service.py`,
`app/clonecast_client.py`, `app/session_store.py`, the guided operator
itself) ever calls a local model to write, rewrite, or summarize research or
scripts — enforced by a structural regression test, not just convention.

Before the real Chatterbox audio stage, `app/gpu_guard.py` verifies actual
free VRAM on the configured GPU (`config.CHATTERBOX_GPU_NAME_SUBSTRING`,
default "RTX 4060 Ti"). If there isn't enough, and Ollama is holding VRAM it
doesn't currently need, the guard asks Ollama's own local API/CLI
(`ollama stop <model>`) to unload that model — this only unloads the model's
weights through Ollama's already-running daemon; it never stops the Ollama
service and never requires sudo. It then re-measures real free VRAM before
declaring success; if headroom still isn't there, generation does not start
— the session is preserved with a clear, resumable error and exactly which
process is holding VRAM (from `nvidia-smi`), never a silent fallback to
another device or a faked pass. Every reservation/release is logged to
`data/autocorp_app_logs/gpu_reservations.jsonl` and shown in the status
panel (`GET /api/status` → `gpu_resource_manager`). Disable with
`AUTOCORP_GPU_GUARD=0` if needed.

Video/lip-sync/upscaling coordination is intentionally not implemented:
those generation modes don't exist in any AutoCorp phase yet, so there is
nothing real to reserve a GPU for. `app/gpu_guard.py`'s `reserve_for_stage`/
`release_stage` API is generic enough to extend to a future generation
stage without rework when one actually exists.

### Sessions, Resume, and research acceptance

Every guided episode is an `AppSession` (chat transcript + upload
provenance) linked to a `guided_clonecast_episode.EpisodeSession` (the
actual studio/research/script/voice/audio/publishing state). Both are plain
JSON files, so a browser refresh or a server restart never loses a session.
A session that fails mid-workflow (research acceptance, GPU error, etc.)
shows up in the sidebar with **Resume**, which re-enters
`run_guided_episode_build(..., resume=...)` and continues from the next
incomplete step — it never re-imports research, re-creates the episode, or
re-imports the script that already succeeded (this is the guided operator's
own idempotency, reused as-is, not reimplemented).

Research is only ever considered usable after the app has verified, through
CloneCast's own `research-show`, that its lifecycle state is `accepted` —
including resolving `research-recover` and duplicate-research cases. The app
never marks research accepted itself and never calls `episode-create` before
that verification succeeds.

### Friendly studio and voice labels

The chat UI never asks you to type a studio ID or a voice profile ID. It
loads the real list from CloneCast (`radio-studio-list`, `voice-list`) and
shows friendly labels ("Shadow Frequency", "Larry — Approved"); your click
sends the real ID to the guided operator. Draft voices are hidden from the
normal picker and require an explicit two-step confirmation to use. A host's
display name (e.g. "Elias Voss") and a voice's `voice_profile_id` are always
stored and validated separately — the host's name can never be sent as a
voice ID.

### Approved scripts

An uploaded or pasted approved script is copied byte-for-byte into a managed
directory; its SHA-256 is checked before and after CloneCast's import, and
generation is blocked if they ever differ. Nothing rewrites, summarizes, or
"improves" it — not the app, not a local model.

### Audio review

Once mastering finishes, the chat shows an HTML `<audio>` player served from
`GET /api/sessions/{id}/audio` — a route with no path parameter, so it can
only ever serve the exact `final_audio` path CloneCast recorded for that
session, never an arbitrary local file. **Approve** records the owner's
listening decision only; it does not publish anything, and no API route in
this app can invoke a publishing command (`workflow_summary().publishing_lock_status`
is always `"locked"` until Approve, and the Phase 1 route table has zero
publish-shaped endpoints).

### Why publishing stays locked

Phase 1 has no publish route, no publish button, and the guided operator it
reuses already blocks every `publication-*`/`radio-publication-create`
command unless explicitly allowed (`CloneCastCLI.run(..., allow_publish=True)`,
never set anywhere in this app). Approving audio only unlocks
`owner_approval_status = publishing_eligible` in the record — it never
triggers an upload anywhere.

### Troubleshooting startup

- **"virtual environment python not found"** — run `python3 -m venv .venv &&
  .venv/bin/pip install -r requirements.txt -r requirements-dev.txt` from the
  repo root.
- **Browser doesn't open** — check `data/autocorp_app_logs/launcher.log` for
  the readiness result, and `server.err.log` for a server crash.
- **"CloneCast is not available at the configured path"** — set
  `AUTOCORP_CLONECAST_REPO=/path/to/clonecast` or fix `config.CLONECAST_REPO_PATH`.
- **GPU/VRAM errors during generation** — the app reports these honestly
  (e.g. insufficient free VRAM) rather than faking success; free VRAM (check
  `nvidia-smi`) and press Resume.

---

## Memory (learning without fine-tuning)

Stored in `data/autocorp.db` (SQLite):

- **builds** — every request, plan, workspace, and outcome.
- **lessons** — reusable `success` / `fix` knowledge.

Before planning, the assistant recalls lessons whose keywords overlap the new
request and feeds them to the model. After a build it records the result, and
every applied fix becomes a lesson. Recall is a local keyword match — no
embeddings, no extra dependencies.

---

## Agent Watchdog Integration (optional)

AutoCorp can hand command approval to [Agent Watchdog](../agent_watchdog_brain)
**without changing any brain**. Every write/command already flows through a
`CommandGate` (`safety/gate.py`):

```python
class CommandGate(ABC):
    def review_write(self, path, content) -> Decision: ...
    def review_command(self, command, cwd) -> Decision: ...
```

Three gates ship:

| Gate | Flag | Behavior |
|------|------|----------|
| `AllowAllGate` | `--auto` | permit everything, no prompts |
| `ConfirmGate` | *(default)* | ask the human before each action |
| `WatchdogGate` | `--watchdog` | Agent Watchdog reviews each command |

```bash
python autocorp.py --watchdog build "a Python CLI todo app with pytest tests"
```

**`WatchdogGate` (`safety/watchdog_gate.py`)** keeps the two apps **separate** —
it loads Agent Watchdog *at runtime as a library* from `AUTOCORP_WATCHDOG_PATH`
(default `~/agent_watchdog_brain`); no code is copied or merged. For each command:

1. **Deterministic rules** (`command_rules.detect_dangerous_patterns`) — an
   instant BLOCK on a known-dangerous command, offline, can't be overridden.
2. **AI risk score** (`watchdog_brain.review_action`, llama3.2) — blocks when
   `risk_score ≥ AUTOCORP_WATCHDOG_BLOCK` (default 8) or the recommendation is
   BLOCK; otherwise approves.

Each decision carries `action` (approve/block), `risk_score` (0–10), and a
`reason`. File writes are auto-approved (risk 0) since AutoCorp only writes
sanitized paths into its own `workspace/`.

**Safe fallback:** if Agent Watchdog can't be loaded (not installed, bad path,
import error), `WatchdogGate` falls back to the interactive `ConfirmGate` — it
never fails open silently.

Env vars: `AUTOCORP_WATCHDOG_PATH`, `AUTOCORP_WATCHDOG_BLOCK`,
`AUTOCORP_WATCHDOG_AI=0` (rules-only, fully offline — skips the llama3.2 call).

---

## Project structure

The tree below shows only the original build→test→explain core. `brains/` has
grown to 37 tracked modules (engine abstraction, repair/self-healing, and the
repository-intelligence/CloneCast-validation toolchain) — see
`AI_ENGINEERING/ARCHITECTURE.md` for the complete, current directory structure.

```
autocorp_cli/
├── autocorp.py            # CLI entry + REPL
├── config.py             # model, endpoint, paths, limits
├── core/
│   ├── llm.py            # Ollama client (JSON mode, extraction, health)
│   ├── console.py        # rich terminal helpers
│   └── orchestrator.py   # the plan→build→test→learn loop
├── brains/
│   ├── planner.py · builder.py · tester.py
├── memory/store.py       # SQLite builds + lessons
├── safety/
│   ├── gate.py           # CommandGate + AllowAllGate + ConfirmGate (the seam)
│   ├── watchdog_gate.py  # WatchdogGate — optional Agent Watchdog review
│   └── executor.py       # the only file/shell access
├── app/                   # AutoCorp Chat App (Phase 1) — see the section above
│   ├── server.py · routes.py · chat_controller.py · clonecast_client.py
│   ├── session_store.py · file_service.py · system_status.py · launcher.py
│   └── templates/ · static/
├── scripts/
│   ├── start_autocorp_app.sh          # desktop-launcher entry point
│   └── install_autocorp_desktop.sh    # installs the desktop/menu entry
├── desktop/
│   ├── autocorp.desktop               # reference .desktop file
│   └── autocorp.png                   # app icon
├── data/                 # SQLite db + app/episode sessions (auto-created, gitignored)
└── workspace/            # generated projects (auto-created, gitignored)
```
