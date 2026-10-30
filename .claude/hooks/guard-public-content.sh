#!/bin/bash
# PreToolUse hook on Bash: nothing private reaches the PUBLIC repo through an issue, PR, comment or release note.
#
#   gh issue create|edit|comment, gh pr create|edit|comment|review, gh release create|edit, a writing
#   gh api (-f/-F/--input/-X), and <anything>/issue.sh create are checked; every other command passes untouched.
#
# The text checked is the whole command (titles, inline bodies, -f body=..., heredocs through `-F -` are all in
# it) plus the contents of every --body-file / --notes-file / -F path, resolved against the hook input's cwd. A
# file the hook cannot read (a variable in the path, not written yet) is reported as additionalContext, not
# blocked: the command text itself was still checked. The rules live in lib/public-content.sh, shared with
# `issue.sh check`. A hit blocks (exit 2) with the category and a masked value on stderr. The hook acts on the
# command in command position (the start, or after ; & | ( or $( ). Conventions: .claude/skills/issues/SKILL.md.

input=$(cat)
cmd=$(jq -r '.tool_input.command // empty' <<<"$input")
cwd=$(jq -r '.cwd // empty' <<<"$input")
publishes='(^|[;&|(]|\$\()[[:space:]]*(gh[[:space:]]+(issue[[:space:]]+(create|edit|comment)|pr[[:space:]]+(create|edit|comment|review)|release[[:space:]]+(create|edit))|[^[:space:];&|()]*issue\.sh[[:space:]]+create)([[:space:]]|$)'
# `gh api` writes too, when it carries a field, an input file, or a method: a bare GET passes.
api_write='(^|[;&|(]|\$\()[[:space:]]*gh[[:space:]]+api[[:space:]].*([[:space:]](-f|-F|--field|--raw-field|--input|-X|--method)[ =])'
grep -qE "$publishes" <<<"$cmd" || grep -qE "$api_write" <<<"$cmd" || exit 0

# shellcheck source=lib/public-content.sh
. "$(dirname "$0")/lib/public-content.sh"

values() { # every value of the given options (`--body-file x`, `-F=x`, quoted or not), one per line
    grep -oE -- "(^| )($1)[ =]+(\"[^\"]*\"|'[^']*'|[^ ]+)" <<<"$cmd" \
        | sed -E "s/^ ?($1)[ =]+//; s/^[\"']//; s/[\"']\$//"
}

text=$cmd
unread=""
while IFS= read -r file; do
    [ -n "$file" ] && [ "$file" != "-" ] || continue # `-` is a heredoc: already in the command text
    [[ "$file" == /* || "$file" == *\$* ]] || file="${cwd:-.}/$file"
    if [[ "$file" != *\$* ]] && [ -f "$file" ]; then
        text="$text"$'\n'"$(cat "$file")"
    else
        unread="$unread${unread:+, }$file"
    fi
done <<<"$(values '--body-file|--notes-file|--input|-F' | grep -v '=')" # `-F key=value` is a field, already in the text

if findings=$(scan_text "$text"); then
    if [ -n "$unread" ]; then
        jq -cn --arg f "$unread" '{
            hookSpecificOutput: {
                hookEventName: "PreToolUse",
                additionalContext: ("The public-content guard could not read " + $f + " (a variable in the path, or no such file yet), so only the command text was checked. This repo is public: no real addresses, probed domains, company names or keys in that file (.claude/skills/issues/SKILL.md, Public repo).")
            }
        }'
    fi
    exit 0
fi

{
    echo "Blocked: this would publish to the public repo austinogilvie/mailtester-cli and the text contains:"
    sed 's/^/  /' <<<"$findings"
    echo "Use example.com, .invalid or the redacted values from tests/fixtures/api/ instead, and never a key or a"
    echo "domain probed during discovery (.claude/skills/issues/SKILL.md, Public repo). The denylist comes from"
    echo ".claude/scripts/build-denylist.sh; a false positive is fixed by editing .claude/local/denylist.txt."
} >&2
exit 2
