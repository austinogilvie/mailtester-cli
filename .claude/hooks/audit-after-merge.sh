#!/bin/bash
# PostToolUse hook on Bash: after a `gh pr merge`, the tracker's audit runs (issue.sh check: kind labels, hierarchy,
# board state against issue state, merged PR bodies) and its findings go back to Claude. A merge is when the
# tracker drifts: a task left open under a closed spec, a body that said Refs where it meant Closes, a board row
# still In Progress. Read-only. The hook acts on the command in command position, not on the words inside a
# heredoc or a quoted string. Conventions: .claude/skills/issues/SKILL.md.

input=$(cat)
cmd=$(jq -r '.tool_input.command // empty' <<<"$input")
grep -qE '(^|[;&|(]|\$\()[[:space:]]*gh[[:space:]]+pr[[:space:]]+merge([[:space:]]|$)' <<<"$cmd" || exit 0
root=${CLAUDE_PROJECT_DIR:-$(cd "$(dirname "$0")/../.." && pwd)}
sleep 5 # the board's Done automation lands a few seconds after the merge closes the issue
report=$(bash "$root/.claude/skills/issues/scripts/issue.sh" check 2>&1 | tail -20)
jq -cn --arg r "$report" \
    '{hookSpecificOutput: {hookEventName: "PostToolUse", additionalContext: ("Issue tracker audit after the merge (issue.sh check):\n" + $r)}}'
