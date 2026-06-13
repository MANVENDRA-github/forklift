# forklift

An OSS-contribution agent that runs inside [Claude Code](https://docs.claude.com/en/docs/claude-code).
Give it a GitHub issue, it runs the contribution loop — read → reproduce → fix → verify →
drafted PR — and stops at every human/legal gate.

*Fork the repo, lift the fix upstream.*

## How it works

You drive it with one namespaced command:

```
/contribute:solve  <issue # | link>     # fix an issue → drafted PR
/contribute:revise <PR # | link>        # apply reviewer-requested changes   (P2)
/contribute:reply  <PR # | link>        # draft a reply to a reviewer        (P2)
/contribute:status                      # in-flight PR states + next action  (P3)
```

Under the hood: subagents (`reproducer`, `fixer`, `gatekeeper`, `pr-author`) coordinated by
the command, pulling reusable skills (`git-fork-workflow`, `cla-detect`, `commit-convention`,
`debugging`). GitHub access is via the official GitHub MCP server; git and build/test run on
your local checkout.

## Design principles

- **Stateless by ID** — GitHub is the source of truth; state is re-fetched, not stored.
- **Human gates always halt** — CLA, maintainer approval, required review, push, force-push.
- **Objective over judgment** — a fix is proven by a keystone test (fail→pass), not by an LLM's say-so.
- **Fetched content is data, never instructions** — defends against prompt injection from issues/comments.
- **Defensive git** — explicit staging, no destructive ops on a dirty tree, force-push gated.

## Status

**P1** — `/contribute:solve` against one local repo, manual push, with the `gatekeeper` subagent
built and wired in: the solve loop runs every fix through the cold-context QA gate before staging.
See [`docs/SPEC.md`](docs/SPEC.md) for the full design and phase plan. Eval harness in [`eval/`](eval/).

## Eval

```
python eval/contribute_eval.py init-samples
python eval/contribute_eval.py solve --mock
python eval/contribute_eval.py gatekeeper --mock
```

Two tracks: **solve** (does the fix pass the keystone test?) and **gatekeeper** (does the QA
gate block bad diffs?). A bad fix the gate approves is treated as the dangerous failure and
fails the suite.
