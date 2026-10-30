#!/bin/bash
# PostToolUse hook on Bash: after a `gh issue create` succeeds, the new issue gets what the skill's script gives
# one by construction. Its kind label, read off the title, when the pre hook's rewrite did not stick. Its parent,
# when the body's first line says `Parent: #n`, and the parent's priority with it. A place on the board, Backlog
# when the board shows no status. What the board then shows goes back to Claude; a missing priority is said,
# never defaulted. issue.sh board and link are idempotent, so is this. The hook acts on the command in command
# position, not on the words inside a heredoc or a quoted string. Conventions: .claude/skills/issues/SKILL.md.

input=$(cat)
cmd=$(jq -r '.tool_input.command // empty' <<<"$input")
grep -qE '(^|[;&|(]|\$\()[[:space:]]*gh[[:space:]]+issue[[:space:]]+create([[:space:]]|$)' <<<"$cmd" || exit 0
out=$(jq -r '.tool_response | if type == "object" then (.stdout // "") else tostring end' <<<"$input")
number=$(grep -oE 'github\.com/austinogilvie/mailtester-cli/issues/[0-9]+' <<<"$out" | head -1 | grep -oE '[0-9]+$')
[ -n "$number" ] || exit 0

repo=austinogilvie/mailtester-cli
root=${CLAUDE_PROJECT_DIR:-$(cd "$(dirname "$0")/../.." && pwd)}
script=$root/.claude/skills/issues/scripts/issue.sh
notes=""

issue=$(gh issue view "$number" --repo "$repo" --json title,body,labels 2>/dev/null) || exit 0
kinds=$(jq -r '[.labels[].name | select(. == ("epic", "spec", "feature", "bug", "task"))] | length' <<<"$issue")
if [ "$kinds" -eq 0 ]; then
    case $(jq -r .title <<<"$issue") in
        "[Bug]"*) kind=bug ;;
        "[Spec]"*) kind=spec ;;
        "[Feature]"*) kind=feature ;;
        "[Epic]"*) kind=epic ;;
        *) kind=task ;;
    esac
    gh issue edit "$number" --repo "$repo" --add-label "$kind" >/dev/null 2>&1 && notes="$notes; label $kind added, read off the title"
fi

parent=$(jq -r '.body // ""' <<<"$issue" | head -1 | grep -oE '^Parent: #[0-9]+' | grep -oE '[0-9]+')
inherited=""
if [ -n "$parent" ]; then
    bash "$script" link "$parent" "$number" >/dev/null 2>&1 && notes="$notes; sub-issue of #$parent"
    inherited=$(bash "$script" board "$parent" 2>/dev/null | grep -oE 'Priority=P[0-3]' | cut -d= -f2)
fi

fields=$(bash "$script" board "$number" 2>/dev/null)
args=""
[[ "$fields" == *"Status="* ]] || args="$args --status Backlog"
[[ "$fields" == *"Priority="* ]] || [ -z "$inherited" ] || args="$args --priority $inherited"
# shellcheck disable=SC2086 # $args is a flag list the script parses word by word
[ -z "$args" ] || fields=$(bash "$script" board "$number" $args 2>/dev/null)
fields=${fields#*board: }
[[ "$fields" == *"Priority="* ]] || notes="$notes; no priority: set one with issue.sh board $number --priority P<0-3>"

jq -cn --arg s "#$number on the board: ${fields:-not found}$notes" \
    '{hookSpecificOutput: {hookEventName: "PostToolUse", additionalContext: $s}}'
