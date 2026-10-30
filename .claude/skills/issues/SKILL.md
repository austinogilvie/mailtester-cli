---
name: issues
description: >-
  How specs, features, bugs and tasks are tracked for mailtester-cli: GitHub Issues on
  austinogilvie/mailtester-cli plus the "mailtester-cli Roadmap" project board. Use this
  whenever someone wants to file, find, update, prioritize or close an issue,
  write a spec, break a spec into tasks, or see the roadmap/backlog. Everything
  goes through the `gh` CLI — no plugin or MCP server is involved.
allowed-tools: Bash(${CLAUDE_SKILL_DIR}/scripts/issue.sh *)
---

# Issue tracking for mailtester-cli

One source of truth: **GitHub Issues** on `austinogilvie/mailtester-cli`, viewed on
the **mailtester-cli Roadmap** project board. There is no local issue store and no
plugin; the only tool is `gh` (authenticated, with the `project` scope —
`gh auth refresh -s project` if a project command is refused).

## Public repo

`austinogilvie/mailtester-cli` is public, so every issue, PR, comment and release
note is public the moment it is filed (the Roadmap project itself is private today,
but the issues on it are not). Nothing filed here may name:

- a real email address, person, customer or company, including the ones probed
  during live API discovery, and the domains they belong to;
- an API key or token of any kind (`evp_…`, `ghp_…`, bearer tokens), even expired;
- raw discovery output. The vocabulary to use is the redacted one in
  `tests/fixtures/api/`: fixture names (`finder_domain_catch_all`, `verify_no_mx`),
  `example.com` / `.invalid` addresses, and made-up companies such as the fixtures'
  `Zxqv Imaginary Holdings`. Credit counts (`12 credits`, `balance 59`) are fine; an
  account identifier is not.

Two mechanical checks back this up. The `guard-public-content` hook blocks any
`gh issue|pr|release …` or `issue.sh create` whose text (the command, plus every
`--body-file`) carries an address outside the allowlist, a key, a public IP, a
phone number, or a literal from the denylist; `issue.sh check` runs the same rules
over every filed issue and the comments of open ones and reports each hit as a
`FAIL`. The rules live in `.claude/hooks/lib/public-content.sh` (the allowlist is
the array at the top). The denylist is machine-local and never committed:
`.claude/scripts/build-denylist.sh` writes `.claude/local/denylist.txt` from the raw
discovery output under `data/` (every address, domain and company name seen in a
request or response); re-run it after each discovery run. A false positive is
fixed by editing that file, not by bypassing the hook. The guard is the floor, not
the judgment: a sentence can identify a customer without tripping any pattern.

## Vocabulary

The *kind* of issue is a label. Exactly one of these per issue:

| label     | means                                                         | template                                |
|-----------|---------------------------------------------------------------|-----------------------------------------|
| `epic`    | a multi-spec initiative; holds the why and ground rules       | `.github/ISSUE_TEMPLATE/epic.yml`       |
| `spec`    | a design/plan to refine before building; tasks hang off it    | `.github/ISSUE_TEMPLATE/spec.yml`       |
| `feature` | a new user-facing capability or enhancement                   | `.github/ISSUE_TEMPLATE/feature.yml`    |
| `bug`     | something that should work and doesn't                        | `.github/ISSUE_TEMPLATE/bug.yml`        |
| `task`    | a unit of implementation, usually a child of a spec/feature   | (free-form: goal, steps, done-when)     |

Other labels (`cli`, `client`, `research`, `tooling`, `documentation`)
describe *area*, and can be added freely alongside the kind label. Add a new area
label (`gh label create`) when a part of the system needs its own.

Status and priority live on the project board, not in labels:

- **Status**: Backlog → Todo → In Progress → Done. Closing an issue or merging a
  PR that `Closes #N` moves it to Done automatically; reopening moves it to Todo.
- **Priority**: P0 (critical) · P1 (high) · P2 (medium) · P3 (low).

## The script: file, link, board, check

`scripts/issue.sh` next to this file does the multi-step parts in one call and
is idempotent, so use it first and the raw `gh` commands below only for what it
does not cover. Five hooks in `.claude/hooks/` keep the raw commands honest and
repair what they can rather than refuse:

| command | the hook |
|---|---|
| any `gh issue|pr|release …` or `issue.sh create` whose text names a real address, key, IP, phone number or denylisted literal | blocked, with the category and a masked value (see **Public repo**) |
| `gh issue create` with no kind label | adds `--label <kind>` read off the title (`[Bug]`, `[Spec]`, `[Feature]`, `[Epic]`; no prefix is a task) |
| `gh issue create` with two kind labels | blocked: that is a choice |
| `gh issue create` succeeded | boards the issue (Backlog), links the parent its first line names (`Parent: #n`) with the parent's priority, adds the kind label if it is still missing, and reports what the board shows |
| `gh pr create` whose body names no issue | passes when the branch name carries the number, with a reminder to add `Closes #n` / `Refs #n` by `gh pr edit`; blocked when nothing says which issue |
| `gh pr merge` succeeded | runs `issue.sh check` and reports its findings |

The hooks act on a command in command position, not on those words inside a
heredoc or a quoted string. An inline `--body` is fine; a long body is easier
to get right in a file.

```bash
S=.claude/skills/issues/scripts/issue.sh

# file an issue: one kind, any areas, optional parent; links it, puts it on the board
$S create spec --title "Client retry and backoff" --body-file body.md --label client --parent 1 --priority P2
$S create task --title "Add the retry loop to _request" --body-file body.md --parent 123   # inherits the parent's priority

$S link 123 456                       # make #456 a native sub-issue of #123 (no-op when it already is)
$S board 456 --priority P1            # set priority and/or status
$S board 456 --status "In Progress"
$S check                              # audit: kind labels, hierarchy, board state, PR bodies; exit 1 on a failure
```

`create` reuses an open issue of the same kind and title instead of filing a
twin, prefixes `[Spec]` / `[Epic]` / `[Feature]` / `[Bug]` when the title lacks
it, and prints `#<number> <url>` last. It refuses a kind passed as `--label`.

## Writing an issue

Use the template's section headings as the body structure, even when creating
from the CLI, so UI-created and agent-created issues look the same. Write the
body to a file; never inline a long `--body`.

For a spec that adds or changes an interface (a command, an endpoint, a schema),
put the proposed usage or contract in **Proposed design**.

```bash
# the raw command the script wraps; with no --label the hook adds the kind the title says
gh issue create --label spec --title "[Spec] Client retry and backoff" --body-file body.md
```

## Breaking a spec into tasks

1. The spec's **Proposed design** ends with a numbered task list, in dependency
   order, each item one line: what it moves or builds and what it depends on.
2. One issue per item, kind `task`, created with `issue.sh create task ... --parent <spec>`
   so it is a native sub-issue and inherits the spec's priority. Body, free-form:
   - first line `Parent: #<spec> (task <k>). Depends on task <j> (#<n>).` naming
     every dependency by issue number, so a reader never has to open the spec;
   - `### Goal` (one paragraph), `### Steps` (numbered, naming the files and
     modules), `### Done when` (checkboxes, each verifiable; include the tests
     and the docs that must change).
3. Write the issue numbers back into the spec's task list (`1. #83 **Baseline…**`)
   so the spec and the board agree. Specs are refined in place.
4. A task reports back to its spec when it lands: anything the spec promised
   to measure or tick goes in the spec's body, by editing it.

## Reading

```bash
gh issue list --label spec --state open
gh issue list --label bug --state open --limit 50
gh issue view 123 --comments
gh search issues --repo austinogilvie/mailtester-cli "retry" --state open   # substring match: "#2" also hits "#25"
```

The board's native sub-issue list is the authoritative parent → child map
(the `parent` and `subIssues` fields in GraphQL, shown as "Sub-issues" in the
UI); a `Parent: #N` line in a body is a convenience, not the link.

## Updating

```bash
gh issue edit 123 --add-label feature --remove-label task
gh issue edit 123 --body-file body.md
gh issue comment 123 --body "..."
gh issue close 123 --reason completed      # or: --reason "not planned"
gh issue reopen 123
```

`gh issue delete <n> --yes` works with the repo owner's token and is for an issue
filed by mistake moments ago (a smoke test, a twin); numbers are never reused.
Anything that was discussed or linked is closed with `--reason "not planned"`
instead, so the history stays readable.

Link a PR so merge closes the issue: put `Closes #123` in the PR body. For a
PR that lands some children of a spec, use `Refs #<spec>` plus one
`Closes #<child>` per implemented child. A PR with no issue says
`No issue: <why>` in its body. `.github/pull_request_template.md` starts a PR
with those lines; a `gh pr create` whose body has none of them passes with a
reminder when the branch name carries the issue number and is blocked when it
does not.

## Project board (status / priority)

New issues are added to the board by the board's own automation, usually
within seconds of creation; closing an issue moves it to Done and reopening to
Todo. Set status by hand only to mark something **In Progress**. Priority is
set the same way. `issue.sh board` does both and is the right tool.

Two things to know when scripting against the board without it:

- `gh project item-add` **fails** once the automation has added the issue
  (`Content already exists in this project`), so a script that takes the item
  id from item-add's output gets an empty id. Look the item up through the
  issue instead (`issue.projectItems` in GraphQL), retrying a few times right
  after creation.
- `gh project item-list` **lags** the board by minutes and has been seen to
  omit items the GraphQL query returns. Never use it to decide whether an issue
  is on the board; `issue.sh check` reads through the issues.

```bash
# Board: https://github.com/users/austinogilvie/projects/1
PROJECT_OWNER=austinogilvie
PROJECT_NUMBER=1
PROJECT_ID=PVT_kwHOAAtDj84BpeSL
STATUS_FIELD=PVTSSF_lAHOAAtDj84BpeSLzhn5x6g
  BACKLOG=515707e2  TODO=8d3f7a9d  IN_PROGRESS=885bc860  DONE=6b745e3d
PRIORITY_FIELD=PVTSSF_lAHOAAtDj84BpeSLzhn5yBE
  P0=7a2aa1a6  P1=aa36ece0  P2=ab2cc94a  P3=0c8ede3f

# the item id of issue #123, the way the script finds it
ITEM=$(gh api graphql -f query='query($n:Int!){ repository(owner:"austinogilvie", name:"mailtester-cli"){ issue(number:$n){ projectItems(first:10){ nodes { id project { number } } } } } }' -F n=123 -q '.data.repository.issue.projectItems.nodes[] | select(.project.number==1) | .id')

gh project item-edit --project-id "$PROJECT_ID" --id "$ITEM" \
  --field-id "$PRIORITY_FIELD" --single-select-option-id "$P1"
gh project item-edit --project-id "$PROJECT_ID" --id "$ITEM" \
  --field-id "$STATUS_FIELD" --single-select-option-id "$IN_PROGRESS"
```

If the IDs ever drift: `gh project field-list "$PROJECT_NUMBER" --owner "$PROJECT_OWNER" --format json`,
then update the constants at the top of `scripts/issue.sh` and here.

To make an issue a native sub-issue by hand (what `issue.sh link` does):

```bash
PARENT=$(gh issue view 123 --json id -q .id)
CHILD=$(gh issue view 456 --json id -q .id)
gh api graphql -f query='mutation($p:ID!,$c:ID!){ addSubIssue(input:{issueId:$p, subIssueId:$c}){ issue { number } } }' -F p="$PARENT" -F c="$CHILD"
```

## Rules

- One kind label per issue; don't invent new kinds.
- Hierarchy is epic → spec → task. Specs are sub-issues of an epic; tasks are
  sub-issues of a spec. Never hang a task directly off an epic. A standalone
  tooling task with no spec is allowed and `issue.sh check` only warns about it.
- An epic's body stays short: why, ground rules, the list of specs, done-when.
  Design detail lives in the specs.
- Don't edit the kind of someone else's issue without saying so in a comment.
- Specs are refined in place (edit the body); don't open a second issue to revise one.
- Never set Status to Done by hand — close the issue (or merge the PR) and let the board follow.
- Run `issue.sh check` after a batch of filing and before closing a spec; fix what it reports.
