#!/usr/bin/env bash
# One-time GitHub configuration for KERP. Run with an admin `gh` login:
#   gh auth login   (once)
#   bash scripts/setup_github.sh
# Idempotent: re-running updates the same settings/rulesets.
#
# Note: no bypass actors on dev — the agents act as the repo owner, so an admin
# bypass would let them merge without the review gate. To break glass, disable
# the ruleset in Settings → Rules, then re-run this script.
set -euo pipefail
REPO="${REPO:-kingdonk-ops/Kerp-}"
R="repos/$REPO"
echo "Configuring $REPO"

# 1. Merge settings: squash only (one commit per PR on dev → promotion counts PRs), tidy branches.
gh api -X PATCH "$R" \
  -F allow_squash_merge=true -F allow_merge_commit=false -F allow_rebase_merge=false \
  -F allow_auto_merge=true -F delete_branch_on_merge=true -F allow_update_branch=true \
  -f squash_merge_commit_title=PR_TITLE -f squash_merge_commit_message=PR_BODY \
  -f default_branch=dev \
  -f description="KERP — Field Operations Platform, built by an agent pipeline: task → PR → dev (CI + independent review) → main every 5 merges." \
  -q '"default=\(.default_branch) squash=\(.allow_squash_merge) merge_commit=\(.allow_merge_commit) rebase=\(.allow_rebase_merge) auto_merge=\(.allow_auto_merge)"'

# 2. Actions: GITHUB_TOKEN may write (promotion pushes main + tags).
gh api -X PUT "$R/actions/permissions/workflow" -f default_workflow_permissions=write -F can_approve_pull_request_reviews=false
echo "actions token: write"

# 3. Variables used by the workflows.
setvar() {
  gh api -X POST "$R/actions/variables" -f name="$1" -f value="$2" >/dev/null 2>&1 \
    || gh api -X PATCH "$R/actions/variables/$1" -f name="$1" -f value="$2" >/dev/null
  echo "var $1=$2"
}
setvar KERP_PROMOTE_EVERY 5
setvar KERP_ALLOWED_REVIEWERS "$(gh api user -q .login)"

# 4. Rulesets (create or replace by name).
upsert_ruleset() {
  local name="$1" body="$2" id
  id=$(gh api "$R/rulesets" -q ".[] | select(.name==\"$name\") | .id" || true)
  if [ -n "$id" ]; then
    gh api -X PUT "$R/rulesets/$id" --input - <<<"$body" -q '"updated ruleset \(.name)"'
  else
    gh api -X POST "$R/rulesets" --input - <<<"$body" -q '"created ruleset \(.name)"'
  fi
}

# dev: PR required (squash only), ci-gate + review-gate required, no force-push/deletion.
upsert_ruleset "dev" '{
  "name": "dev", "target": "branch", "enforcement": "active",
  "conditions": {"ref_name": {"include": ["refs/heads/dev"], "exclude": []}},
  "bypass_actors": [],
  "rules": [
    {"type": "deletion"},
    {"type": "non_fast_forward"},
    {"type": "pull_request", "parameters": {
      "required_approving_review_count": 0, "dismiss_stale_reviews_on_push": false,
      "require_code_owner_review": false, "require_last_push_approval": false,
      "required_review_thread_resolution": false, "allowed_merge_methods": ["squash"]}},
    {"type": "required_status_checks", "parameters": {
      "strict_required_status_checks_policy": false,
      "required_status_checks": [{"context": "ci-gate"}, {"context": "review-gate"}]}}
  ]}'

# main: only fast-forwards of CI-green commits (the promotion workflow). No PR rule, so the workflow can push.
upsert_ruleset "main" '{
  "name": "main", "target": "branch", "enforcement": "active",
  "conditions": {"ref_name": {"include": ["refs/heads/main"], "exclude": []}},
  "bypass_actors": [],
  "rules": [
    {"type": "deletion"},
    {"type": "non_fast_forward"},
    {"type": "required_status_checks", "parameters": {
      "strict_required_status_checks_policy": false,
      "required_status_checks": [{"context": "ci-gate"}]}}
  ]}'

# Release tags are immutable once created.
upsert_ruleset "release-tags" '{
  "name": "release-tags", "target": "tag", "enforcement": "active",
  "conditions": {"ref_name": {"include": ["refs/tags/release-*"], "exclude": []}},
  "bypass_actors": [],
  "rules": [{"type": "deletion"}, {"type": "non_fast_forward"}, {"type": "update"}]}'

# 5. Labels the agents may use.
for l in "agent:implementer:1d76db" "agent:reviewer:5319e7" "agent:planner:0e8a16" "blocked:b60205" "needs-human:d93f0b"; do
  n="${l%:*}"; c="${l##*:}"
  gh api -X POST "$R/labels" -f name="$n" -f color="$c" >/dev/null 2>&1 || true
done
echo "labels ok"
echo "Done. dev ← PR + ci-gate + review-gate (squash). main ← fast-forward of CI-green commits only."
