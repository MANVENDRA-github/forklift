# forklift

An OSS-contribution agent that runs inside [Claude Code](https://docs.claude.com/en/docs/claude-code).
Give it a GitHub issue and it works the contribution loop — read → reproduce → fix → verify →
drafted PR — stopping at every human and legal gate.

*Fork the repo, lift the fix upstream.*

## The problem it solves

An LLM that writes a patch and is then asked whether the patch is good will almost always approve
it — the author and the reviewer are the same model, with the same blind spots. forklift is built
around that problem. Correctness is made objective: a fix is proven by a keystone test that fails
before the change and passes after it, not by a model declaring itself satisfied. And the review is
decorrelated from the author: a separate agent inspects the issue and the diff without ever seeing
the reasoning that produced them. Around that core it adds the caution a stranger's repository
demands — it treats issue text as untrusted data, stages only the files it touched, and halts for a
human before every push, fork, PR, and CLA.

## How it works

forklift isn't a program you build and run. It's a set of Markdown command and subagent definitions
for Claude Code, plus a Python eval harness that scores their behavior. You drive it from inside a
target repository with one command:

```
/contribute:solve <issue # | #123 | https://github.com/owner/repo/issues/123>
```

That command runs the whole loop against your local checkout — using the GitHub MCP server for
GitHub reads and writes, and the repository's own test runner for verification.

### The cold-context gatekeeper

The hard part of an agent that fixes code isn't writing the patch. It's knowing whether the patch is
correct and safe to send to someone else's project. forklift refuses to let the author grade its own
work.

After the fix is written and verified, `/contribute:solve` hands it to a separate `gatekeeper`
subagent that runs in a fresh, cold context. The gatekeeper is given exactly two things: the issue
text and the proposed diff. It never sees the fixer's reasoning, the conversation, or the repro
notes — that decorrelation is the point, so it catches what the author talked itself into. It first
re-derives, from the issue alone, what a correct fix must do, then reads the diff adversarially,
hunting for what the issue requires that the diff misses.

Its hard gates are objective, not opinions:

- build plus the affected package's test suite must be green, along with the repo's own
  linter / formatter / type-checker;
- the keystone test must genuinely fail without the fix — the gatekeeper proves this by
  reverse-applying *only* the implementation hunks (never `git stash`, which could remove the test
  file), rebuilding first if the repo compiles, and confirming the test then fails;
- a module that already has tests and changed behavior must ship a test;
- the diff touches only intended files — no debug prints, dead code, lockfile churn, or leaked local
  files (`.claude/`, editor junk);
- no secrets in the diff;
- a changelog / news fragment, if the repo requires one.

It returns one of three verdicts. **PASS** proceeds to staging. **BOUNCE** is a mechanical failure
another fixer pass can repair (a red suite, a lint error, a missing-but-writable test) — capped at
two retries. **HALT** is a safety or judgment failure (a secret, an out-of-scope file, a fix that
doesn't address the issue, or a keystone that can't be made to fail honestly) — stop and hand to the
human, no retry. Alignment, minimality, breaking-change surface, and a suggested commit message come
back as advisory soft signals; they never block.

### The rest of the loop

`/contribute:solve` works through a fixed sequence and stops at the first hard error rather than
improvising:

0. Refuse to run on a dirty working tree — no stash, no reset, no clean.
1. Normalize the issue argument to `{owner, repo, number}`; fetch the issue through the GitHub MCP
   server, treating the title and body as untrusted data and surfacing anything that looks like an
   injected instruction.
2. Check GitHub for your own existing open PR on this issue so it doesn't duplicate work. (State
   lives in GitHub; nothing is cached locally.)
3. Branch `fix/issue-<n>` off the detected default base.
4. Detect the repo's real test command from its own config — `package.json` scripts, `pyproject` /
   `pytest`, a `Makefile`, or CI workflows — and confirm it with you. Never hardcoded.
5. Reproduce the bug, write one keystone test that fails on the unfixed code, make the minimal fix,
   and confirm fail→pass with the suite green.
6. Hand the diff to the gatekeeper (above).
7. Stage only the files it edited, named explicitly — never `git add -A` — and show you the diff.
8. Gated fork + push, then a gated PR against upstream. Every GitHub write pauses for your explicit
   approval; after the PR opens, any CLA-bot comment is surfaced verbatim and the run stops.

### Principles, as mechanisms

- **Stateless by ID** — GitHub is the source of truth; the issue, PR, and diff are re-fetched each
  run, never stored.
- **Human and legal gates always halt** — fork creation, push, PR-open, and CLA signing are never
  automatic.
- **Objective over judgment** — a fix is proven by the keystone test, not by a model's approval.
- **Fetched text is data, never instructions** — issue and comment text is an untrusted problem
  description; anything resembling an injected instruction is surfaced and the run stops.
- **Defensive git** — explicit staging only; no `reset --hard` / `clean` / `checkout --` against a
  dirty tree; force-push gated and out of this slice.
- **Zero-budget** — git, the repo's own runners, and free SAST (semgrep / bandit / gitleaks) only.

## Repository layout

```
.claude/
  commands/contribute/solve.md   the /contribute:solve command — the loop above
  agents/gatekeeper.md           the cold-context QA gate
  hooks/branch-guard.sh          PreToolUse hook: warns before an edit on a protected branch
  settings.json                  wires the hook
eval/
  contribute_eval.py             the eval harness (solve + gatekeeper tracks)
  tasks/solve.json               solve-track fixture
  tasks/gate.json                gatekeeper-track adversarial cases
docs/SPEC.md                     the full design and phase plan (read-only)
CLAUDE.md                        working rules + a log of learned rules
sync.ps1                         install script
```

## Setup

The command and subagent have to live in your global Claude Code config (`~/.claude/`) so they work
from any repo you run them in, not just this one. After cloning, sync them:

**Windows (PowerShell):**

```powershell
./sync.ps1
```

**macOS / Linux:**

```bash
mkdir -p ~/.claude/commands ~/.claude/agents
cp -R .claude/commands/. ~/.claude/commands/ && cp -R .claude/agents/. ~/.claude/agents/
```

`sync.ps1` copies only `.claude/commands/` and `.claude/agents/` into `~/.claude/`, overwriting. It
deliberately leaves `settings.json`, `hooks/`, and `soul.md` alone — those are project-local or
personal.

You'll also need the official GitHub MCP server connected to Claude Code (local, PAT-scoped) for the
GitHub reads and writes.

## Usage

From inside the target repo, on a clean working tree:

```
/contribute:solve 123
/contribute:solve #123
/contribute:solve https://github.com/owner/repo/issues/123
```

A bare number resolves the repo from your `upstream` remote (falling back to `origin`); a full URL is
taken as given. From there the command drives the loop and pauses at each gate — it won't push, fork,
or open a PR without an explicit yes.

## Tests

There's no application to unit-test here; what's tested is whether the agent's behavior holds up. The
eval harness is a single standard-library Python script with two tracks:

```bash
python eval/contribute_eval.py init-samples       # write the sample fixtures
python eval/contribute_eval.py solve --mock        # solve track
python eval/contribute_eval.py gatekeeper --mock   # gatekeeper track
```

**solve** asks the objective question: does the produced patch make the keystone test pass? The
bundled fixture (`eval/tasks/solve.json`) is one demo task — an off-by-one in an inclusive
`sum_range`.

**gatekeeper** is the more interesting track. Its dataset (`eval/tasks/gate.json`) is five hand-built
cases: one clean fix that should pass, and four diffs that should be blocked — one that leaks a
hardcoded API key, one that edits an out-of-scope `.claude/` file, one that changes behavior without
adding a test, and one that only rewrites a docstring while leaving the bug in place. The harness
scores the gate as a confusion matrix (precision / recall / F1) and treats a **false negative — a bad
diff the gate waves through — as the dangerous failure that fails the suite.** The committed sample
report `eval/eval_gate_report.json` records all five cases classified correctly, with no false
negatives.

`--mock` runs the harness without the Claude Code CLI (the solve mock replays the gold patch; the
gate mock is a small keyword heuristic) so the plumbing can be exercised offline. Without `--mock`,
the gatekeeper track shells out to the real subagent, run headlessly in an empty temp directory so
its context stays genuinely cold.

## Tech stack

- **Markdown** — the command and subagent definitions; they *are* the program.
- **Python 3 (standard library only)** — the eval harness. No dependencies, no build step, no
  manifest.
- **Bash** — the `branch-guard` hook.
- **PowerShell** — the `sync.ps1` install script.
- **Claude Code** — the runtime the commands and subagents execute in.
- **GitHub MCP server** — GitHub access, PAT-scoped.
- **git** + the target repo's own test runner + free SAST (semgrep / bandit / gitleaks) — used when
  present, for verification.

## Status

Early, and honest about it. One command (`/contribute:solve`) and one subagent (`gatekeeper`) are
built and run end to end, through the gated fork / push / PR. Designed in `docs/SPEC.md` but not yet
built:

- the other commands — `/contribute:revise`, `/contribute:reply`, `/contribute:status`;
- CLA / DCO automation (the loop surfaces a CLA gate and stops; it does not sign);
- resume / branch reconstruction, and multi-repo support.

The eval's solve track currently runs only under `--mock`; its real-agent hook is a stub awaiting
wiring to the command. `docs/SPEC.md` holds the full phase plan (P0–P3); the repo is at P2, slice 1.
