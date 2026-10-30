#!/usr/bin/env bash
# issue.sh: file, link, board and check mailtester-cli issues the same way every time.
#
#   issue.sh create <kind> --title <t> --body-file <f> [--label <area>]... [--parent <n>] [--priority P0..P3] [--status <s>]
#   issue.sh link <parent> <child>
#   issue.sh board <n> [--priority P0..P3] [--status Backlog|Todo|"In Progress"]
#   issue.sh check
#
# Conventions and ids: .claude/skills/issues/SKILL.md. Needs gh (scopes repo, project) and jq.
# Every step is idempotent: create reuses an open issue with the same kind and title, link is a
# no-op when the child is already linked, board finds the item through the issue itself (the
# board auto-adds new issues; `gh project item-add` errors once that has happened and
# `gh project item-list` lags behind it).

set -euo pipefail

REPO=austinogilvie/mailtester-cli
OWNER=austinogilvie
NAME=mailtester-cli
PROJECT_NUMBER=1
PROJECT_ID=PVT_kwHOAAtDj84BpeSL
STATUS_FIELD=PVTSSF_lAHOAAtDj84BpeSLzhn5x6g
PRIORITY_FIELD=PVTSSF_lAHOAAtDj84BpeSLzhn5yBE
KINDS="epic spec feature bug task"
LOOKUP_TRIES=6 # the auto-add usually lands within a few seconds; each lookup is one API call

# Option ids as functions, not associative arrays: macOS ships bash 3.2.
status_id() { case $1 in Backlog) echo 515707e2 ;; Todo) echo 8d3f7a9d ;; "In Progress") echo 885bc860 ;; Done) echo 6b745e3d ;; *) echo "" ;; esac; }
priority_id() { case $1 in P0) echo 7a2aa1a6 ;; P1) echo aa36ece0 ;; P2) echo ab2cc94a ;; P3) echo 0c8ede3f ;; *) echo "" ;; esac; }

die() { echo "issue.sh: $*" >&2; exit 1; }
usage() { sed -n '2,8p' "$0" | sed 's/^# \{0,1\}//' >&2; exit 1; }
is_kind() { [[ " $KINDS " == *" $1 "* ]]; }

graphql() { gh api graphql -f query="$1" "${@:2}"; }

# ---------------------------------------------------------------- lookups

node_id() { gh issue view "$1" --repo "$REPO" --json id -q .id; }

project_item() { # issue number -> project item id, or empty
    graphql 'query($o:String!,$r:String!,$n:Int!){ repository(owner:$o,name:$r){ issue(number:$n){
        projectItems(first:10){ nodes { id project { number } } } } } }' \
        -F o="$OWNER" -F r="$NAME" -F n="$1" \
        -q ".data.repository.issue.projectItems.nodes[] | select(.project.number==$PROJECT_NUMBER) | .id"
}

item_fields() { # issue number -> "Priority=P2 Status=Backlog"
    graphql 'query($o:String!,$r:String!,$n:Int!){ repository(owner:$o,name:$r){ issue(number:$n){
        projectItems(first:10){ nodes { project { number } fieldValues(first:20){ nodes {
          ... on ProjectV2ItemFieldSingleSelectValue { field { ... on ProjectV2SingleSelectField { name } } name } } } } } } } }' \
        -F o="$OWNER" -F r="$NAME" -F n="$1" \
        -q ".data.repository.issue.projectItems.nodes[] | select(.project.number==$PROJECT_NUMBER)
            | [.fieldValues.nodes[] | select(.name != null) | \"\(.field.name)=\(.name)\"] | join(\" \")"
}

sub_issues() { # parent number -> child numbers, one per line
    graphql 'query($o:String!,$r:String!,$n:Int!){ repository(owner:$o,name:$r){ issue(number:$n){
        subIssues(first:100){ nodes { number } } } } }' \
        -F o="$OWNER" -F r="$NAME" -F n="$1" -q '.data.repository.issue.subIssues.nodes[].number'
}

# ---------------------------------------------------------------- board

cmd_board() {
    [ $# -ge 1 ] || usage
    local n=$1 priority="" status=""; shift
    while [ $# -gt 0 ]; do
        case $1 in
            --priority) priority=$2; shift 2 ;;
            --status) status=$2; shift 2 ;;
            *) die "board: unknown option $1" ;;
        esac
    done
    [ -z "$priority" ] || [ -n "$(priority_id "$priority")" ] || die "board: priority must be one of P0, P1, P2, P3"
    [ -z "$status" ] || [ -n "$(status_id "$status")" ] || die "board: status must be one of Backlog, Todo, In Progress, Done"
    [ "$status" != "Done" ] || die "board: never set Done by hand; close the issue and the board follows"

    local item="" i
    for ((i = 0; i < LOOKUP_TRIES; i++)); do
        item=$(project_item "$n")
        [ -z "$item" ] || break
    done
    if [ -z "$item" ]; then
        # The automation did not add it; add it ourselves. If that races with the automation, look again.
        item=$(gh project item-add "$PROJECT_NUMBER" --owner "$OWNER" --url "https://github.com/$REPO/issues/$n" --format json 2>/dev/null | jq -r .id || true)
        [ -n "$item" ] && [ "$item" != null ] || item=$(project_item "$n")
    fi
    [ -n "$item" ] || die "board: #$n is not on the board and could not be added"

    if [ -n "$priority" ]; then
        gh project item-edit --project-id "$PROJECT_ID" --id "$item" --field-id "$PRIORITY_FIELD" \
            --single-select-option-id "$(priority_id "$priority")" >/dev/null
    fi
    if [ -n "$status" ]; then
        gh project item-edit --project-id "$PROJECT_ID" --id "$item" --field-id "$STATUS_FIELD" \
            --single-select-option-id "$(status_id "$status")" >/dev/null
    fi
    echo "#$n board: $(item_fields "$n")"
}

# ---------------------------------------------------------------- link

cmd_link() {
    [ $# -eq 2 ] || usage
    local parent=$1 child=$2
    if sub_issues "$parent" | grep -qx "$child"; then
        echo "#$child already a sub-issue of #$parent"
        return
    fi
    local p c
    p=$(node_id "$parent"); c=$(node_id "$child")
    graphql 'mutation($p:ID!,$c:ID!){ addSubIssue(input:{issueId:$p, subIssueId:$c}){ issue { number } } }' \
        -F p="$p" -F c="$c" >/dev/null
    echo "#$child linked as a sub-issue of #$parent"
}

# ---------------------------------------------------------------- create

cmd_create() {
    [ $# -ge 1 ] || usage
    local kind=$1 title="" body="" parent="" priority="" status="Backlog"; shift
    local -a areas=()
    is_kind "$kind" || die "create: kind must be one of $KINDS"
    while [ $# -gt 0 ]; do
        case $1 in
            --title) title=$2; shift 2 ;;
            --body-file) body=$2; shift 2 ;;
            --label) is_kind "$2" && die "create: '$2' is a kind; pass it as the first argument, not as --label"; areas+=("$2"); shift 2 ;;
            --parent) parent=$2; shift 2 ;;
            --priority) priority=$2; shift 2 ;;
            --status) status=$2; shift 2 ;;
            *) die "create: unknown option $1" ;;
        esac
    done
    [ -n "$title" ] || die "create: --title is required"
    [ -n "$body" ] && [ -f "$body" ] || die "create: --body-file must name a file (write the body to a file, not inline)"
    case $kind in
        epic) [[ $title == "[Epic] "* ]] || title="[Epic] $title" ;;
        spec) [[ $title == "[Spec] "* ]] || title="[Spec] $title" ;;
        feature) [[ $title == "[Feature] "* ]] || title="[Feature] $title" ;;
        bug) [[ $title == "[Bug] "* ]] || title="[Bug] $title" ;;
    esac
    if [ -n "$parent" ]; then
        case $kind in
            spec) ;;
            task) ;;
            *) die "create: only a spec (under an epic) or a task (under a spec or feature) takes --parent" ;;
        esac
    fi

    # Reuse an open issue with the same kind and title rather than filing a twin.
    local existing
    existing=$(gh issue list --repo "$REPO" --label "$kind" --state open --limit 200 --json number,title \
        | jq -r --arg t "$title" '.[] | select(.title == $t) | .number' | head -1)
    local number url
    if [ -n "$existing" ]; then
        number=$existing
        url="https://github.com/$REPO/issues/$number"
        echo "#$number already exists with this title; reusing it" >&2
    else
        local -a label_args=(--label "$kind")
        local a; for a in "${areas[@]:-}"; do [ -n "$a" ] && label_args+=(--label "$a"); done
        url=$(gh issue create --repo "$REPO" "${label_args[@]}" --title "$title" --body-file "$body")
        number=${url##*/}
    fi

    if [ -n "$parent" ]; then
        cmd_link "$parent" "$number" >&2
        # A child inherits its parent's priority unless told otherwise.
        if [ -z "$priority" ]; then
            priority=$(item_fields "$parent" | grep -oE 'Priority=P[0-3]' | cut -d= -f2 || true)
        fi
    fi
    local -a board_args=("$number" --status "$status")
    [ -z "$priority" ] || board_args+=(--priority "$priority")
    cmd_board "${board_args[@]}" >&2
    echo "#$number $url"
}

# ---------------------------------------------------------------- check

cmd_check() {
    local fail=0 warn=0
    # Every issue with its labels, state, parent and board fields, in one paginated query.
    local rows
    rows=$(gh api graphql --paginate -f query='query($o:String!,$r:String!,$endCursor:String){ repository(owner:$o,name:$r){
        issues(first:100, after:$endCursor, orderBy:{field:CREATED_AT, direction:ASC}){
          pageInfo { hasNextPage endCursor }
          nodes { number state title labels(first:20){ nodes { name } } parent { number labels(first:20){ nodes { name } } }
            projectItems(first:10){ nodes { project { number } fieldValues(first:20){ nodes {
              ... on ProjectV2ItemFieldSingleSelectValue { field { ... on ProjectV2SingleSelectField { name } } name } } } } } } } } }' \
        -F o="$OWNER" -F r="$NAME" \
        -q ".data.repository.issues.nodes[] | {
              n: .number, state: .state, title: .title,
              kinds: [.labels.nodes[].name | select(. == (\"$KINDS\" | split(\" \")[]))],
              parent: .parent.number,
              parent_kinds: [(.parent.labels.nodes // [])[].name | select(. == (\"$KINDS\" | split(\" \")[]))],
              board: ([.projectItems.nodes[] | select(.project.number==$PROJECT_NUMBER)] | first),
            } | . + { status: ([.board.fieldValues.nodes[]? | select(.field.name==\"Status\") | .name] | first),
                      priority: ([.board.fieldValues.nodes[]? | select(.field.name==\"Priority\") | .name] | first) }
              | del(.board) | tostring")

    local line n state nkinds kind parent parent_kinds status priority
    while IFS= read -r line; do
        [ -n "$line" ] || continue # no issues yet: the query returns nothing
        n=$(jq -r .n <<<"$line"); state=$(jq -r .state <<<"$line")
        nkinds=$(jq -r '.kinds | length' <<<"$line"); kind=$(jq -r '.kinds[0] // ""' <<<"$line")
        parent=$(jq -r '.parent // ""' <<<"$line"); parent_kinds=$(jq -r '.parent_kinds | join(",")' <<<"$line")
        status=$(jq -r '.status // ""' <<<"$line"); priority=$(jq -r '.priority // ""' <<<"$line")

        if [ "$nkinds" != 1 ]; then
            echo "FAIL #$n: $nkinds kind labels ($(jq -r '.kinds | join(",")' <<<"$line")); exactly one of $KINDS"; fail=$((fail + 1))
        fi
        if [ "$state" = OPEN ]; then # hierarchy matters while the work is live; a closed stray is history
            case $kind in
                task)
                    if [ -z "$parent" ]; then echo "WARN #$n: task with no parent spec or feature"; warn=$((warn + 1))
                    elif [ "$parent_kinds" = epic ]; then echo "FAIL #$n: task hangs directly off epic #$parent"; fail=$((fail + 1)); fi ;;
                spec)
                    if [ -z "$parent" ]; then echo "WARN #$n: spec with no parent epic"; warn=$((warn + 1)); fi ;;
            esac
        fi
        if [ "$state" = OPEN ]; then
            if [ -z "$status" ] || [ -z "$priority" ]; then
                echo "FAIL #$n: open but board status='${status:-none}' priority='${priority:-none}'"; fail=$((fail + 1))
            elif [ "$status" = Done ]; then
                echo "FAIL #$n: open but board status is Done"; fail=$((fail + 1))
            fi
        elif [ -n "$status" ] && [ "$status" != Done ]; then
            echo "FAIL #$n: closed but board status is $status"; fail=$((fail + 1))
        fi
    done <<<"$rows"

    # Merged PRs name what they land: Refs #spec and Closes #task, or say they have no issue.
    local prs
    prs=$(gh pr list --repo "$REPO" --state merged --limit 50 --json number,body \
        -q '.[] | select((.body // "") | test("(?i)(closes|fixes|resolves|refs) #[0-9]+") | not) | .number')
    local pr; for pr in $prs; do echo "WARN PR #$pr: merged with no Closes/Refs line"; warn=$((warn + 1)); done

    # Public repo: no issue title, body, or open-issue comment names a real address, key, probed domain or company.
    # Same rules as the guard-public-content hook (.claude/hooks/lib/public-content.sh); a hit is a failure.
    local lib
    lib=$(cd "$(dirname "$0")" && pwd)/../../../hooks/lib/public-content.sh
    if [ -f "$lib" ]; then
        # shellcheck source=/dev/null
        . "$lib"
        local issues text hits hit
        issues=$(gh issue list --repo "$REPO" --state all --limit 500 --json number,title,body,state -q '.[] | tostring')
        while IFS= read -r line; do
            [ -n "$line" ] || continue # no issues yet
            n=$(jq -r .number <<<"$line"); state=$(jq -r .state <<<"$line")
            text=$(jq -r '"\(.title)\n\(.body // "")"' <<<"$line")
            if [ "$state" = OPEN ]; then
                text="$text"$'\n'"$(gh issue view "$n" --repo "$REPO" --json comments -q '.comments[].body' 2>/dev/null || true)"
            fi
            hits=$(scan_text "$text") || true
            while IFS= read -r hit; do
                [ -n "$hit" ] || continue
                echo "FAIL #$n: $hit"; fail=$((fail + 1))
            done <<<"$hits"
        done <<<"$issues"
    else
        echo "WARN public-content rules not found at $lib; the public sweep did not run"; warn=$((warn + 1))
    fi

    echo "check: $fail failures, $warn warnings"
    [ "$fail" -eq 0 ]
}

# ---------------------------------------------------------------- main

[ $# -ge 1 ] || usage
cmd=$1; shift
case $cmd in
    create) cmd_create "$@" ;;
    link) cmd_link "$@" ;;
    board) cmd_board "$@" ;;
    check) cmd_check "$@" ;;
    -h|--help|help) usage ;;
    *) die "unknown command '$cmd'" ;;
esac
