#!/usr/bin/env bash
# Prepare a Raspberry Pi 500+ (64-bit Raspberry Pi OS) to run p2m-agent.
#
# Run as a user with sudo, on the Pi. Creates a dedicated `prover` account holding only what
# the agent needs, so the Unix account is a boundary on top of the Lean sandbox.
# Safe to re-run. It stops, rather than building Mathlib from source, if the cache misses.
set -euo pipefail

PROVER_USER="${PROVER_USER:-prover}"

if [ "$(uname -m)" != "aarch64" ]; then
  echo "expected a 64-bit Arm system, found $(uname -m)" >&2
  exit 1
fi

sudo apt-get update -y
sudo apt-get install -y git curl zstd bubblewrap rsync

if ! id "$PROVER_USER" >/dev/null 2>&1; then
  sudo adduser --disabled-password --gecos "" "$PROVER_USER"
fi

# Let the same SSH key reach the prover account, for syncing and for approvals.
home_dir="$(getent passwd "$PROVER_USER" | cut -d: -f6)"
sudo install -d -m 700 -o "$PROVER_USER" -g "$PROVER_USER" "$home_dir/.ssh"
if [ -f "$HOME/.ssh/authorized_keys" ]; then
  sudo install -m 600 -o "$PROVER_USER" -g "$PROVER_USER" \
    "$HOME/.ssh/authorized_keys" "$home_dir/.ssh/authorized_keys"
fi

sudo -iu "$PROVER_USER" bash -s <<'AS_PROVER'
set -euo pipefail
command -v uv >/dev/null || curl -LsSf https://astral.sh/uv/install.sh | sh
command -v elan >/dev/null || curl -sSf https://elan.lean-lang.org/elan-init.sh \
  | sh -s -- -y --default-toolchain none
mkdir -p "$HOME/.local/share/p2m-agent/secrets"
chmod 700 "$HOME/.local/share/p2m-agent/secrets"
AS_PROVER

echo "Ready. Sync the code to $PROVER_USER@$(hostname):p2m-agent, then run on the Pi:"
echo "  uv sync --frozen && uv run p2m env <toolchain> <mathlib revision>"
echo "Sign in to Claude there through Anthropic's own flow, or set an API key."
