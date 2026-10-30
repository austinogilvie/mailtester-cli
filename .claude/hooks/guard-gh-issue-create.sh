#!/bin/bash
# PreToolUse hook on Bash: a `gh issue create` leaves this hook with exactly one kind label.
#
#   one kind label    passes untouched
#   no kind label     the kind is read off the title ([Bug], [Spec], [Feature], [Epic]; no prefix is a task) and
#                     `--label <kind>` is added to the command (updatedInput with allow: the command is the one
#                     Claude wrote plus a label). A title the hook cannot read: blocked, with the fix.
#   two or more       blocked: that is a choice, not a repair.
#
# Nothing else is checked; an inline --body is fine. Once the issue exists, board-new-issue.sh places it on the
# board, links its parent and adds the kind label if the rewrite did not stick. The hook acts on the command in
# command position (the start, or after ; & | ( or $( ), not on the words inside a heredoc or a quoted string.
# Exit 2 blocks the command and sends stderr to Claude. Conventions: .claude/skills/issues/SKILL.md.

input=$(cat)
cmd=$(jq -r '.tool_input.command // empty' <<<"$input")
grep -qE '(^|[;&|(]|\$\()[[:space:]]*gh[[:space:]]+issue[[:space:]]+create([[:space:]]|$)' <<<"$cmd" || exit 0

values() { # every value of the given options (`--label x`, `--label=x`, `-l x`, quoted or not), one per line
    grep -oE -- "(^| )($1)[ =]+(\"[^\"]*\"|'[^']*'|[^ ]+)" <<<"$cmd" \
        | sed -E "s/^ ?($1)[ =]+//; s/^[\"']//; s/[\"']\$//"
}
block() {
    {
        echo "Blocked: gh issue create does not follow .claude/skills/issues/SKILL.md: $1"
        echo "The skill's script does it right by construction:"
        echo '  .claude/skills/issues/scripts/issue.sh create <kind> --title "..." --body-file body.md [--label <area>] [--parent <n>] [--priority P2]'
    } >&2
    exit 2
}

kinds=$(values '--label|-l' | grep -cxE 'epic|spec|feature|bug|task')
case $kinds in
    1) exit 0 ;;
    0) ;;
    *) block "it carries $kinds kind labels; exactly one of epic, spec, feature, bug, task is required" ;;
esac

title=$(values '--title|-t' | head -1)
if [ -z "$title" ] || [[ "$title" == \$* ]]; then # absent, or a variable the hook cannot see into
    block "it has no kind label and the title is not readable in the command, so the kind cannot be inferred; add --label <epic|spec|feature|bug|task>"
fi
case $title in
    "[Bug]"*) kind=bug ;;
    "[Spec]"*) kind=spec ;;
    "[Feature]"*) kind=feature ;;
    "[Epic]"*) kind=epic ;;
    *) kind=task ;;
esac
updated=$(sed -E "s/(^|[;&|(]|\\$\\()([[:space:]]*gh[[:space:]]+issue[[:space:]]+create)/\\1\\2 --label $kind/" <<<"$cmd")
jq -cn --arg c "$updated" --arg k "$kind" --argjson t "$(jq -c '.tool_input' <<<"$input")" '{
    hookSpecificOutput: {
        hookEventName: "PreToolUse",
        permissionDecision: "allow",
        updatedInput: ($t + {command: $c}),
        additionalContext: ("The issue hook added --label " + $k + ", read off the title; the board hook places the issue once it exists.")
    }
}'
