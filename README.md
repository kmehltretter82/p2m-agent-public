# p2m-agent

A personal proof agent for [Prove2Me](https://prove2.me), an open platform for formalizing
mathematics in Lean 4. It takes a theorem, works on a proof, and only ever submits a file that
has passed deterministic local checks and that a person has approved.

Powered by Claude, through the Claude Agent SDK. This is an independent project, not made or
endorsed by Anthropic or Prove2Me.

**Status: early and still being measured.** See [PLAN.md](PLAN.md) for the milestones and
[docs/findings.md](docs/findings.md) for what has been measured so far.

## How it works

```
frozen target snapshot
        |
        v
  worker (Agent SDK + lean-lsp-mcp) ---> candidate proof file
        ^                                       |
        |                                       v
        +------ diagnostics <------ local gate (no model)
                                                | pass
                                                v
                            your approval -> submit -> poll -> ledger
```

- **The local gate** compares the candidate's *elaborated* statement with the target's, so a local
  notation or instance cannot change what is being proved. It also checks axioms, `sorry` and
  imports. It runs code the worker wrote, so it filters mistakes rather than stopping attacks. The
  server's verdict is what counts.
- **The worker** can edit one file and holds no platform credentials. It has no way to submit.
- **Lean runs in a sandbox** (`sandbox-exec` on macOS, `bwrap` on Linux), because Lean executes code
  while compiling. The home directory is hidden except for named paths.
- **The ledger** is an SQLite file recording every target, attempt, candidate, gate result and
  verdict. Proofs and reductions with open obligations are always counted separately.
- **Submitting is crash-safe.** Intent is recorded before the `POST`. After a crash, the file's hash
  is matched against what the server stored before anything is retried.

## Requirements

- macOS or 64-bit Linux, including Raspberry Pi OS. `bubblewrap` on Linux.
- [`uv`](https://docs.astral.sh/uv/) and [`elan`](https://github.com/leanprover/elan).
- About 8 GB of disk per Lean environment. Targets whose imports reach whole `import Mathlib` need
  far more than 8 GB of RAM to run at a useful speed.
- A Claude credential of your own, and a Prove2Me API key if you want to talk to the platform.

## Authentication

**Use your own Anthropic API key**, set as `ANTHROPIC_API_KEY`. This project has no login flow and
never handles Claude credentials; Claude Code does its own authentication. If you run this for
yourself on your own subscription, usage draws on your plan's limits, shared with your other Claude
use. See Anthropic's [Agent SDK terms](https://code.claude.com/docs/en/legal-and-compliance).

The Prove2Me key goes in `~/.local/share/p2m-agent/secrets/credentials.json` as
`{"api_key": "..."}`, readable only by you. It stays out of the worker's reach.

## Usage

```bash
uv sync --frozen

# Create a pinned environment: the Lean version and Mathlib commit a target uses.
uv run p2m env leanprover/lean4:v4.33.1 0df444a360eaa60ab8c11dca51a86af692955474

# Freeze a target, then let the worker try it. Nothing is submitted.
uv run p2m target add <theorem-id>
uv run p2m attempt run <target> --max-budget 3

# Review, approve one exact file, submit, and fetch the verdict.
uv run p2m approve <candidate>                      # shows everything; approves nothing
uv run p2m approve <candidate> --yes --by "Your Name"
uv run p2m submit <candidate>
uv run p2m poll

uv run p2m report
```

The worker pauses at 70% of any plan window, resumably. Set `P2M_STOP_AT_UTILIZATION` to change that.
Run `p2m --help` for the rest, including `frontier`, `dossier`, `benchmark`, `reconcile` and `sync`.

## Tests

```bash
uv run pytest -m "not mathlib"   # needs the Lean toolchain only
uv run pytest -m mathlib         # needs a built environment and local mirrors
```

## License

MIT, for this project's own code. The Claude Agent SDK's bundled Claude Code runtime stays under
Anthropic's terms. Proofs you publish on Prove2Me fall under that platform's terms.
