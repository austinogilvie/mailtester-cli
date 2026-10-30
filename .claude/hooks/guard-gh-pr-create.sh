#!/bin/bash
# PreToolUse hook on Bash: a `gh pr create` names the issue it lands, or the hook can tell which one it means.
#
#   the body names an issue (Closes/Fixes/Resolves/Refs #n) or says "No issue"   passes
#   it names none and the branch name carries a number (spec-82-warehouse)       passes, with a reminder to add
#                                                                                 Closes/Refs #n through gh pr edit
#   it names none and the branch has no number                                   blocked: which issue, or none, is a choice
#
# The body is read from --body-file (a path, or `-` for a heredoc), from an inline --body, or for --fill from the
# commits the PR would carry. A merge moves a Closes'd issue to Done on the board; that is why the line matters.
# The hook acts on the command in command position (the start, or after ; & | ( or $( ), not on the words inside
# a heredoc or a quoted string. Exit 2 blocks the command and sends stderr to Claude.
# Conventions: .claude/skills/issues/SKILL.md.

input=$(cat)
cmd=$(jq -r '.tool_input.command // empty' <<<"$input")
cwd=$(jq -r '.cwd // empty' <<<"$input")
grep -qE '(^|[;&|(]|\$\()[[:space:]]*gh[[:space:]]+pr[[:space:]]+create([[:space:]]|$)' <<<"$cmd" || exit 0

value() { # the first value of the given options, quotes stripped
    grep -oE -- "(^| )($1)[ =]+(\"[^\"]*\"|'[^']*'|[^ ]+)" <<<"$cmd" | head -1 \
        | sed -E "s/^ ?($1)[ =]+//; s/^[\"']//; s/[\"']\$//"
}

body=""
file=$(value '--body-file|-F')
if [ -n "$file" ]; then
    if [ "$file" = "-" ]; then
        body=$cmd # a heredoc on stdin: the text is in the command itself
    else
        [[ "$file" == /* || "$file" == *\$* ]] || file="${cwd:-.}/$file"
        if [[ "$file" != *\$* ]] && [ -f "$file" ]; then
            body=$(cat "$file")
        else # a path through a variable the hook cannot expand, or a file gh will refuse itself
            jq -cn --arg f "$file" '{
                hookSpecificOutput: {
                    hookEventName: "PreToolUse",
                    additionalContext: ("The PR hook could not read the body file " + $f + " (a variable in the path, or no such file), so it did not check it. The body must carry Closes #n / Refs #n, or the line No issue: <why>.")
                }
            }'
            exit 0
        fi
    fi
elif grep -qE -- '(^| )(--body|-b)[ =]' <<<"$cmd"; then
    body=$cmd # an inline body: the text is in the command itself
elif grep -qE -- '(^| )--fill(-first|-verbose)?( |$)' <<<"$cmd"; then
    base=$(value '--base|-B')
    body=$(git -C "${cwd:-.}" log --format=%B "${base:-main}..HEAD" 2>/dev/null)
fi

if grep -qiE '(closes|fixes|resolves|refs) #[0-9]+' <<<"$body" || grep -qi 'no issue' <<<"$body"; then
    exit 0
fi

branch=$(git -C "${cwd:-.}" rev-parse --abbrev-ref HEAD 2>/dev/null)
number=$(grep -oE '[0-9]+' <<<"$branch" | head -1)
if [ -n "$number" ]; then
    jq -cn --arg n "$number" --arg b "$branch" '{
        hookSpecificOutput: {
            hookEventName: "PreToolUse",
            additionalContext: ("The PR body names no issue; the branch " + $b + " points at #" + $n + ". Once the PR exists, run gh pr edit <pr> --body-file with `Closes #" + $n + "` (this PR finishes it) or `Refs #" + $n + "` (it does not) added, or the merge will not move the issue to Done.")
        }
    }'
    exit 0
fi
{
    echo "Blocked: the PR body names no issue and the branch name (${branch:-unknown}) carries no issue number."
    echo "Add 'Closes #<task>' per task it finishes and 'Refs #<spec>' for the spec it lands under,"
    echo "or the line 'No issue: <why>' when there is none (.claude/skills/issues/SKILL.md)."
    echo "With no body at all (--fill with no commits, an editor), write one to a file and pass --body-file."
} >&2
exit 2
