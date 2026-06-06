# AutoCorp CLI

A **local, terminal-first AI coding assistant** powered by [Ollama](https://ollama.com)
(`llama3.2`). It can **plan → build → test → explain** code, learns from past
builds, and is architected so [Agent Watchdog](#agent-watchdog-integration-future)
can later approve or block every action.

Everything runs **locally**. No cloud, no API keys, no fine-tuning.

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
├── data/                 # SQLite db (auto-created, gitignored)
└── workspace/            # generated projects (auto-created, gitignored)
```
