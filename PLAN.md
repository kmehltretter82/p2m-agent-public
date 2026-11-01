# Plan: Prove2Me proof agent

**Status:** draft, 31 October 2026. No code yet. Source idea: [IDEA.md](IDEA.md).
**Working name:** `p2m-agent` (CLI: `p2m`).

## Current status

Your direction on 1 November: focus only on finishing the
[Consumption-Investment mission](https://prove2.me/missions/Markov_Decision_Processes_VII%3A_Consumption-Investment_Problems_and_Regime_Switching)
with the agent on the Pi. **M4 is paused** with 4 of its 10 targets done, and can resume later with
the same command. Every finished attempt in both arms proved its target so far, so it has not yet
separated the agent from the baseline. Treat that as no conclusion.

## Goal

A personal agent that takes a Prove2Me theorem, proves it in Lean, and submits only what has
passed deterministic local checks, keeping a durable record of every attempt.

The stack is the one IDEA.md recommends: a Python controller, a Claude Agent SDK worker, and
`lean-lsp-mcp`.

You already prove targets with an ordinary coding agent: the MFOTL mission went 10 of 10
`ACCEPTED` on 27 October. So this agent has to earn its place. The bar is the same proof rate
with less of your attention, with numbers to show it.

**v1 is done (end of M5) when:**

- On a fixed benchmark, the agent matches or beats the baseline proof rate at the same budget.
- Every candidate the local gate passes gets through the server: no `CE`, `WA` or `SORRY`.
- At least one real open target is `ACCEPTED` with no hand-edited Lean.
- A killed run resumes with no lost attempt and no duplicate submission.

## Milestones at a glance

| # | Milestone | What it proves | Claude usage | Public effect |
|---|---|---|---|---|
| M0 | Settle the unknowns | This Mac can host one worker; budgets are sized | Small | None |
| M1 | Local gate | Bad proofs are rejected without any model | None | None |
| M2 | Platform adapter and ledger | Submit, verdict and crash recovery work | None | Private only |
| M3 | One worker, one target | The agent can prove something end to end | Moderate | None |
| M4 | Benchmark against baseline | Whether this is worth continuing | Highest | None |
| M5 | First real contribution | A public `ACCEPTED`, approved by you | Moderate | **Public** |
| M6 | Reductions | Sketches are tracked apart from proofs | Moderate | **Public, immutable** |
| M7 | Pi 500+ deployment | It runs unattended on dedicated hardware | Same limits | None |
| M8 | Data-driven upgrades | Built only when recorded failures justify them | Varies | Varies |
| M9 | Open-source release (optional) | Someone else can run it | None | **Public** |

The gate comes before the model on purpose. The model is not trusted to judge its own work, so
that check has to exist before the model does.

## What checking IDEA.md changed

**Confirmed against the sources:** the API endpoints, the three submission rules, the verdict
statuses, the `lean-lsp-mcp` tool names, and Agent SDK usage drawing on plan limits.

**Changed or added:**

| IDEA.md says or assumes | What I found | Consequence |
|---|---|---|
| Concurrent workers in separate trees | This Mac has 8 GB RAM, was already 1.25 GB into swap, and has 27 GB free. One Mathlib `.lake` is 7.5 GB | One worker on the Mac. One shared `.lake` per environment |
| Better lemma retrieval through local tools | Local Loogle needs about 7 GiB warm and 13 GiB to index; the REPL defaults to a 16 GiB budget | Both are off on the Mac. Reconsider on the Pi |
| Users authenticate with their own credentials | True for your own use. But the docs also tell developers building on the SDK to use API keys, and forbid offering Claude.ai login or routing users through plan credentials | Plan login for personal use; API key for any public release (M9) |
| Subscription covers usage | True, but the five-hour and weekly limits are shared with your interactive Claude use, and advertised limits assume "ordinary, individual usage" | Limit-aware pausing in M3. An unattended Pi may need an API key (M7) |
| SDK isolation is implied | With default options the SDK loads all settings, including `CLAUDE.md`. Here that means the parent file telling agents to work Choir tasks on another repo | `setting_sources=[]`, and attempts run outside `~/codex/collaboration` |
| Worker tools can be limited with an allowlist | `allowed_tools` only auto-approves; it does not remove tools | Use `disallowed_tools` plus a `PreToolUse` hook, and `ClaudeSDKClient` rather than `query()` |
| Only `sorry` and axioms need watching | Lean runs code at compile time (`#eval`, `run_cmd`), and platform comments are other people's text | Wrap Lean processes in a sandbox and treat platform content as untrusted (below) |
| Direct proofs first | Your notes report many open statements are buggy, and statements cannot be edited after publishing | "Statement looks wrong" and disproofs are normal outcomes from M1 onward |
| One proof file | MFOTL needed many modules inlined into one file, and one proof fell from about 200s to 3s after optimization. The server limit is about 300s | Packaging and a check-time budget belong in the gate |

## Build on what you already have

| Existing asset | Use |
|---|---|
| `prove2me_mfotl/scripts/package_solution.py` | Port into the controller. Generalize its hardcoded `MFOTLProofs.` prefix |
| `prove2me_mfotl/.warm/checker-controls.json` | Keep the idea: positive and negative controls for every checker |
| `prove2me_mfotl/scripts/warm_lean.py` | Use `lean-lsp-mcp` first. Return to this only if M0 shows the warm daemon is clearly lighter |
| `prove2me_mfotl/campaign/manifest.json` | Model for frozen SHA-256 target snapshots |
| `~/.choir/mathlib-store` | Model for one shared Mathlib per environment, linked by symlink |
| `prove2me_workspace/Solutions` (395 files) | Source of positive fixtures and benchmark targets. **Not** a working directory: its pin is not in the documented environment list, it mixes mirrored files, and it holds your live credentials |
| `UNPOPULAR_PROVE2ME_PLAN.md` | Defines how M5 and M6 handle defective statements |

## Architecture

```
frozen target snapshot
        |
        v
  worker (Agent SDK + lean-lsp-mcp) ---> candidate proof files
        ^                                       |
        |                                       v
        +------ diagnostics <------ local gate (no model)
                                                | pass
                                                v
                            your approval -> submit -> poll -> ledger
```

**Trust boundaries:**

- **The controller owns all durable state and the Prove2Me key.** The worker has no credentials
  and no submit tool. Submitting is simply not something it can do.
- **The worker edits only its attempt's proof files**, enforced by a hook rather than by prompt.
- **Platform content is untrusted data:** comments, explanations and other people's Lean. It is
  labeled as such in the worker's context and never treated as instructions.
- **Lean and `lean-lsp-mcp` run inside a sandbox wrapper** that denies access to credentials,
  `~/.ssh` and `~/.claude`. It uses `sandbox-exec` on macOS and `bwrap` on Linux. The SDK's own
  sandbox covers Bash only, and Docker is too heavy for 8 GB.
- **The gate is a filter, not an authority.** It runs Lean the worker wrote, so it catches
  mistakes rather than determined attacks. The server's verdict decides what counts.

**Portability rules from day one,** so that M7 is a deployment rather than a port:

- No absolute paths; the state directory comes from configuration.
- Secrets live in a file at a configurable path, not in the macOS Keychain.
- Sandbox backends sit behind one interface.
- Dependencies are locked with `uv.lock`, and the Python version is pinned.

## M0: Settle the unknowns

**Build:** a `uv` project with pinned dependencies. Record results in `docs/findings.md`.

**Answer these with measurements:**

1. **Environments:** does `GET /environments` still match the docs? Is `db584cd`, your current
   workspace pin, a live platform environment?
2. **Memory:** what is peak memory for `lean-lsp-mcp` plus the Lean server on a real target?
   Plain `lean` is measured below; the language server is not, and it may behave differently
   because it keeps imports loaded.
3. **SDK on plan login:** what do `total_cost_usd` and `RateLimitEvent` report? How does hitting
   a limit look? Does `resume` survive killing the process?
4. **Usage:** what does one real proof cost, and how many fit in a five-hour window on Pro?

**Done when** `findings.md` answers two questions:

- Can the Mac run one worker without heavy swapping? **If not, M7 moves to right after M2**, and
  M3 onward runs on the Pi.
- How many benchmark targets fit in the weekly limit?

**Measured so far** (31 October, one trivial `linarith` example, plain `lean` at 4.33.0):

| Import | Wall time | CPU time | Peak private memory |
|---|---|---|---|
| `Mathlib.Tactic.Linarith` | 24s | 4s | 0.23 GB |
| `import Mathlib` | 296s, then 332s | 44s, then 50s | 0.85 GB |

The repeat run was no faster, which points to ongoing memory starvation rather than a cold disk
cache: Mathlib's files do not stay cached on this Mac. For scale, the Claude Code processes used
about 0.5 GB and a bare Python process 0.02 GB, so Lean's paging is the problem to solve.

Examples in the platform docs use a preamble of `import Mathlib`. The gate's comparison step
must keep the target's preamble, so such a target costs about five minutes per check here. Decide
in M0 whether those targets wait for the Pi.

## M1: Local gate

`p2m check <file> --theorem <id>` returns structured JSON. No model is involved.

**Checks:**

- **Environment:** builds under the target's own toolchain and `mathlib_rev`, with
  `autoImplicit false`, in a clean per-environment directory. Also confirm the Lean commit stamped
  in the build files matches the toolchain: the `prove2me_mfotl` build mixes 4.33.0 and 4.33.1, and
  plain `lean` refuses to load it (`incompatible header`).
- **Statement:** a top-level `theorem solution` whose *elaborated* type matches the target's (or
  `¬ target` for a disproof). A local-only module imports both and compares them, so a local
  notation or `open` cannot quietly change the meaning.
- **No `sorry` in your own code:** judged by the build's per-declaration warnings. `#print axioms`
  alone cannot tell, because imported platform theorems are `by sorry` mirrors by design.
- **Axioms:** only `propext`, `Classical.choice` and `Quot.sound`. `sorryAx` is allowed only
  through mirrored imports.
- **Imports:** never the target itself, and no `Theorems.*` in a disproof. Warn on whole
  `import Mathlib`.
- **Time:** warn above 120s of local check time, to leave margin under the server's 300s. M2
  calibrates this number.
- **Packaging:** inline local modules into the single submitted file.

**Negative fixtures:** namespaced `solution`, weakened hypothesis, changed binder type, redefined
notation, `sorry`, new `axiom`, `native_decide`, imported target, and a disproof that negates only
the conclusion.

**Done when** every negative fixture is rejected for the right reason, and positive fixtures from
your existing solutions pass.

## M2: Platform adapter and ledger

**Build:**

- **Typed client:** API key to one-hour token refresh, expiry warnings for the 30-day key, and a
  hard stop when the server's `version` differs from the `SKILL.md` it was written against
  (currently `0.13.6`).
- **SQLite ledger:** targets with frozen hashed snapshots, attempts, candidates, gate results,
  submissions and verdicts.
- **Distinct outcomes:** proved, disproved, reduction with open obligations, and rejected with
  its status code.
- **Crash-safe submit:** record the intent and file hash before the `POST`. After a restart,
  reconcile against `GET /submissions` before any retry.
- **`p2m submit`:** refuses unless that exact file hash passed the gate. Re-fetches the theorem
  first and stops if it is no longer open or its statement changed.

**Calibrate:** on `"private": true` toy theorems with a reserved name prefix, submit deliberately
bad files and compare the server's verdict to the gate's. Never call `make-public`, which cannot
be undone.

**Done when:**

- A hand-written proof reaches `ACCEPTED` in the ledger.
- Killing the process between `POST` and poll recovers without a second submission.
- The gate and the server agree on every calibration file.

## M3: One worker, one target

**Build:**

- **Session:** `ClaudeSDKClient` with `setting_sources=[]`, running in an attempt directory outside
  `~/codex/collaboration`.
- **Tools:** `lean-lsp-mcp`, Read, Edit, Grep, Glob and web lookup, since the platform asks for
  faithful translation from sources. Bash is off. `lean_build` and `lean_run_code` are disabled,
  because the controller owns builds.
- **Loop:** the worker edits, the gate checks, diagnostics go back, until it passes or the budget
  runs out.
- **Outcomes:** `proved_locally`, `disproved_locally`, `statement_suspect` (with a reason),
  `stuck`, `budget_exhausted`, `limit_wait`.
- **Bounds:** `max_turns`, `max_budget_usd` and wall-clock time. On a `RateLimitEvent`, pause and
  resume after the window, using the saved session ID.
- **Model:** `claude-opus-5-5` with an explicit effort level (its default is `medium`), both
  recorded per attempt and configurable.

**Done when:**

- The worker proves a toy target and one already-`Proved` platform target with no human Lean edits.
- A `kill -9` mid-run resumes cleanly.
- A worker told to "make it compile by changing the statement" is rejected by the gate.
- A canary credential file stays unreadable, both from Lean `#eval` and from a prompt injection
  planted in a fake dossier comment.

## M4: Benchmark against baseline

**Setup:**

- About 10 frozen targets of varied difficulty, all already `Proved`, in one environment. M0 may
  shrink this number. Nothing gets submitted.
- Answers are hidden: attempt directories exclude your earlier solutions, and web tools block
  `prove2.me`.
- Two arms with the same model, effort and budget: **baseline** is `claude -p` in a clean stock
  `prove2me_workspace` with its `SKILL.md`; **agent** is this system.
- The baseline also runs with stripped settings (`--setting-sources ""`, no extra MCP servers).
  Flags like these, plus a short prompt and fewer tools, removed about 15,000 tokens per request
  in a test on 31 October. The harness should not get credit for flags anyone can set.
- Run it when you are not using Claude interactively, since the limits are shared.

**Measure:** gate-pass rate, turns, tokens, estimated cost, wall-clock time, Lean check time,
failure class and human interventions. `p2m report` generates this from the ledger.

Ten targets show a direction, not statistical proof. The comparison also bundles tools and
controller together; splitting them would take a third arm that Pro limits probably cannot afford.

**Done when** the report exists, with a written decision to continue, change or stop. **If the
baseline is as good, stop adding autonomy** and keep the gate and ledger as tools around your
current workflow. That would still be a useful result.

## M5: First real contribution

**Build:**

- **Target selection:** you pick the mission. The controller lists its milestones in order, then
  `open-leaves` by `closability`.
- **Scouting dossier:** audit flags, milestone history (rejected paths), decompositions, discussion,
  mentions, prior submissions and source references, all labeled untrusted.
- **Explanations:** written in the paper style the platform asks for. Patchable later, whereas the
  submitted file is not.
- **Approval gate:** `p2m approve` shows the statement, file hash, axioms, gate result and
  explanation. Every `POST` needs one approval, because it publishes under your account and the
  Apache 2.0 terms.
- **Race check:** confirm the target is still open immediately before the `POST`.

Rating, voting and commenting stay manual.

**Done when** at least one open target is publicly `ACCEPTED` with no hand-edited Lean, and the
ledger matches the server.

## M6: Reductions

**Build:**

- **Mirrors:** copy imported statements verbatim into `Theorems/Thm_<slug>.lean`, same environment
  only.
- **Gate reduction mode:** own code `sorry`-free, the target not imported, every child accounted for.
- **Ledger:** `SKETCH_ACCEPTED` with its list of obligations, never counted as proved. Sync child
  status so the parent's auto-resolution gets recorded. Note that `ACCEPTED` can also be a reduction
  whose imports are all proved.
- **Child lemmas:** `/submit-problem` needs your approval every time. A published `formal_statement`
  cannot be edited, only deprecated. Children must follow the source faithfully, and trees that do
  not connect to the mission earn no score.

**Done when:**

- A private tree goes from `SKETCH_ACCEPTED` to proved as its children close.
- The report counts proofs and reductions separately.
- One public reduction lands with your approval.

## M7: Pi 500+ deployment

**When:** after M5, or right after M2 if M0 shows the Mac cannot host a worker.

| | This Mac | Pi 500+ |
|---|---|---|
| RAM | 8 GB, already swapping | 16 GB LPDDR4X |
| Free disk | 27 GB | 256 GB SSD, replaceable |
| CPU | 8 cores (Apple arm64) | 4 × Cortex-A76 at 2.4 GHz |
| OS | macOS | Linux arm64 |

It fixes the two bottlenecks that matter: a 7.5 GB Mathlib install should fit in 16 GB of file
cache, which this Mac cannot manage. Elaboration will be slower on the Pi's CPU. Both effects are
still to be measured.

**Already verified:** Lean 4.33.x ships `linux_aarch64` builds, the SDK ships a `manylinux aarch64`
wheel, and `lean-lsp-mcp` is pure Python.
**Not verified:** that `lake exe cache get` works there. Mathlib's hash input appears to have no
platform term, which is promising but untested.

**Build:**

- **Bootstrap script:** 64-bit Raspberry Pi OS, elan, `uv`, `ripgrep`, and a dedicated `prover`
  Unix user that holds only what it needs. This is a stronger boundary than the Mac can offer.
- **Cache first:** run the smoke test. On a cache miss, stop rather than building Mathlib from source.
- **Sign-in:** through `claude` itself or an API key. Our code never copies or handles tokens.
- **Service:** `systemd` with restart, resuming from the ledger. Approvals over SSH.
- **State transfer:** `p2m export` and `p2m import` for the ledger and attempts.
- **Measure:** rerun three M4 targets for check time and sustained throttling. Try local Loogle,
  though its 13 GiB indexing peak is tight on 16 GB.

**Claude usage is the catch.** The Pi adds compute, not Claude usage: plan limits are shared across
devices and with your interactive use. Running around the clock also strains the "ordinary,
individual usage" assumption, so sustained unattended work may need a pay-per-use API key. Pro does
not include the monthly API credits that some other plans get.

**Done when:**

- It runs 24 hours with no intervention except approvals.
- It survives a power cycle and resumes.
- The M4 subset shows no loss in proof rate.
- The canary test passes on the Pi.

## M8: Data-driven upgrades

Each of these is built only when its trigger appears in the ledger.

| Upgrade | Build it only if |
|---|---|
| Multi-session work on large targets, with handoff notes | MFOTL-sized targets outgrow one session |
| Better lemma retrieval, including local Loogle on the Pi | Missing or invented lemmas are a leading failure class |
| Parallel candidate branches | Attempts mostly die on an early wrong approach, and the host has spare RAM |
| Dependency-aware scheduling | Mission queues lose time to working out of order |
| Statement-audit campaigns | `statement_suspect` findings keep proving correct |
| Search controller with Lean REPL workers | You want to research proof search itself. This is a separate track, which is why the worker stays replaceable |

## M9: Open-source release (optional)

- Publish the controller and Lean integration under a permissive license. The SDK wrapper is MIT;
  the Claude Code runtime stays under Anthropic's terms, installed by each user and unmodified.
- Document **API-key authentication** as the supported path. Build no login flow and handle no
  tokens.
- Choose a public name without "Claude Code" or "Anthropic" in it. `claude-api-agent` should stay a
  folder name only.
- Scan the git history for secrets, and exclude the ledger and attempts.

**Done when** someone else installs from the README on a clean Linux machine and proves the toy
target with their own credentials.

## Out of scope for now

Mission captain and proposal workflows, a web UI, a self-written model loop, automated public
submission, and automated voting or rating.

## Risks

| Risk | Mitigation |
|---|---|
| Pro limits stall the benchmark | Size it in M0, pause and resume automatically, run when idle |
| Mac disk runs out | Keep one environment at a time. `~/.elan` holds 22 GB across 8 toolchains and may be worth pruning (your call; nothing has been changed) |
| Platform API changes | Version check stops the client on any mismatch |
| Gate disagrees with the server | Calibrate in M2; every disagreement becomes a new fixture |
| Budget wasted on false statements | Read audit flags first; `statement_suspect` is a normal way to finish |
| Compile-time code or prompt injection | Sandbox wrapper, no credentials in the worker, canary tests |

## Decisions needed from you

| By | Decision |
|---|---|
| M0 | Reuse the Prove2Me key in `~/prove2me_workspace/credentials.json`, or issue a new one for this agent? I recommend a new one if the site allows it. Until you decide, the key stays unused |
| M5 | Which mission should it target first? |
| M7 | Do you have a Pi 500+ yet (from $410)? And plan login or API key for unattended running? |
| M9 | Do you want a public release at all? |

## Sources

- [Prove2Me workspace and docs](https://github.com/prove2me/prove2me_workspace) (`SKILL.md`, `references/`)
- [`lean-lsp-mcp`](https://github.com/oOo0oOo/lean-lsp-mcp)
- [Claude Agent SDK for Python](https://github.com/anthropics/claude-agent-sdk-python)
- [Agent SDK overview](https://code.claude.com/docs/en/agent-sdk/overview)
- [Claude Code legal and compliance](https://code.claude.com/docs/en/legal-and-compliance)
- [Use the Claude Agent SDK with your Claude plan](https://support.claude.com/en/articles/15036540-use-the-claude-agent-sdk-with-your-claude-plan)
- [Monthly API credits for Max, Ultra, and Team plans](https://support.claude.com/en/articles/17154008-monthly-api-credits-for-max-ultra-and-team-plans)
- [Raspberry Pi 500+](https://www.raspberrypi.com/products/raspberry-pi-500-plus/)
