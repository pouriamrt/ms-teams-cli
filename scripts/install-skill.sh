#!/usr/bin/env bash
# Copy skill/SKILL.md into the user's Claude Code skills directory.
set -euo pipefail

SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SRC="$SRC_DIR/skill/SKILL.md"
DEST_DIR="${CLAUDE_SKILLS_DIR:-$HOME/.claude/skills}/teams-cli"

if [[ ! -f "$SRC" ]]; then
  echo "Source skill not found at $SRC" >&2
  exit 1
fi

mkdir -p "$DEST_DIR"
cp "$SRC" "$DEST_DIR/SKILL.md"
echo "Installed teams-cli skill to $DEST_DIR/SKILL.md"
