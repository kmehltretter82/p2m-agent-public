# M0 findings

Measured on 31 October 2026 on this Mac (8 cores, 8 GB RAM), unless stated otherwise.

## Answers to the two M0 questions

**Can the Mac run one worker without heavy swapping?** Yes for targets whose import closure
avoids `import Mathlib`. Whole-Mathlib targets are not practical here. See "Lean's cost" below.
One worker, measured over a real run on a targeted target: 1.22 GB peak for the whole process tree,
0.95 GB of it Lean.

**How many benchmark targets fit in the weekly limit?** Very few at a time. At 17:30 on 31 October
the weekly window was at 51% and the five-hour window then reached 90%, mostly from the interactive
session building this project, since all of it shares one allowance. The worker now pauses at 70%
of any window (`P2M_STOP_AT_UTILIZATION`). See "Claude usage".

## Environments

| Question | Answer |
|---|---|
| Does `GET /environments` match the docs? | **Yes**: 4.33.1/`0df444a` (default), 4.30.0/`c5ea003`, 4.29.0-rc3/`777aaa6` |
| Is `db584cd` (the workspace's pin) a live platform environment? | **No.** So `prove2me_workspace` is pinned to something the server does not verify against |
| Platform version | `0.13.6` from the public `/health`, matching the docs this client was written against |

The agent's own environment is the documented default: Lean 4.33.1 with Mathlib `0df444a`, at
`~/.local/share/p2m-agent/envs/0df444a360ea`, cloned copy-on-write from `prove2me_mfotl`, whose
files were only read.

Two defects in that seed, both now detected and repaired by `ensure_env`:

- A few Batteries build files were stamped Lean 4.33.0, not 4.33.1. Plain `lean` refuses these
  with `incompatible header`.
- 7 Mathlib modules had no build file, and `lake exe cache get` still reported "already
  decompressed". Even `cache get!` did not produce them, so they were built locally. A dry run
  guards this step: it refuses to build more than 50 modules, since a full Mathlib build takes hours.

## Lean's cost

Plain `lean`, one trivial `linarith` example, Lean 4.33.0:

| Import | Wall time | CPU time | Peak private memory |
|---|---|---|---|
| `Mathlib.Tactic.Linarith` | 24s | 4s | 0.23 GB |
| `import Mathlib` | 296s, then 332s | 44s, then 50s | 0.85 GB |

The repeat run was no faster, which points to ongoing memory starvation rather than a cold cache.

**Real targets.** Of 1,100 local theorem mirrors, 605 reach whole `import Mathlib` through their
definitions. 58 avoid it and have a complete, sorry-free solution, which makes them the benchmark
pool. On those, the gate compiled real solutions in 1.5s to 5s.

## Claude usage

Agent SDK on the plan login, with `apiKeySource: none`:

- A stripped session with one tool used 1,663 context tokens. Default Claude Code used 18,732.
- `total_cost_usd` is **cumulative across a resume**, while `usage` tokens cover that run only.
  So the ledger replaces cost and adds tokens.
- `RateLimitEvent` reports `status`, `rate_limit_type` and `resets_at`. `utilization` is null while
  `allowed` and appears from `allowed_warning` onward, so the guard works only near a limit.
- Worker cost on a toy target: 23 turns and $0.32 estimated. Tokens times list prices matches
  `total_cost_usd`, so usage is reported per query.
- Overage status was `rejected` on this plan, so hitting a limit stops work rather than billing.

## SDK behavior that shaped the design

- `resume` works after the process dies from `os._exit`, and no CLI process is left behind.
- `tools=[...]` really limits the built-in tools, whereas `allowed_tools` only auto-approves.
- **`setting_sources=[]` does not exclude claude.ai connectors**: the Docs connector still appeared
  in `mcp_servers`. `strict_mcp_config=True` is needed as well.
- Default `setting_sources` loads every source, including `CLAUDE.md`.

## Tooling

There is no real `rg` binary on this machine's PATH, only a shell function that proxies Claude
Code's bundled copy, and `lean-lsp-mcp` needs a real one for local search. The project installs
one into its own virtual environment, so that it works the same on the Pi.

## Gate against the real server (M2)

Run on 1 November 2026 on three private toy theorems, since deprecated. Full results are in
[calibration.json](calibration.json). The gate and the server agreed on 14 of 15 cases:

- **Rejected by both:** compile error, extra hypothesis, changed binder type, `sorry`, `sorry` in a
  helper, importing the target, a new axiom, `native_decide`, a namespaced `solution`, a local
  instance that changes what the same text means, and a disproof negating only the conclusion.
- **Accepted by both:** a renamed binder, an implicit binder in place of an explicit one, and a
  definitionally equal statement. The gate warns on the last two, and the server does not mind.
- **The one disagreement:** `def solution` is accepted by the server and rejected by the gate. The
  gate stays stricter, because the platform's documented rule asks for a `theorem`.

**Crash recovery, live:** a `POST` reached the server and the process then behaved as if it had
crashed. The ledger stayed in `intent`, `reconcile` found the submission by matching the hash of the
stored source, and the verdict was recorded, with one copy on the server.

**Side effect on the profile:** proving a private toy counted as a real first solve, so calibration
added 1 to `num_solved_prob` and 1 to `trust`, and 3 to `num_submitted_prob`. The docs do not
mention this. Accept-cases shared one theorem to keep it to one. Other submissions from the same
account landed during the run, so the profile's total change is larger than this.

## Pi 500+ (M7)

Measured on 1 November 2026: 16 GB RAM, 4 Cortex-A76 cores, Debian 13, Lean 4.33.1.

- **The Mathlib cache works on ARM Linux.** A fresh environment took under 8 minutes including
  dependencies, used 7.5 GB, and had no missing modules or stale stamps. So the 7 missing modules
  on the Mac came from that seed copy, not from upstream.
