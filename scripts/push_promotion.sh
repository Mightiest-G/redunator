#!/usr/bin/env bash
# push_promotion.sh — push a promoted monthly retrain to GitHub so the
# Cloud Build trigger rebuilds + redeploys the backend.
#
# Usage: scripts/push_promotion.sh [RUN_DIR]
#   RUN_DIR defaults to the newest directory under retrain_runs/.
#
# Reads RUN_DIR/promotion_report.json:
#   decision != PROMOTE -> exit 0, nothing to do (a DO NOT PROMOTE or
#     FAILED run is a normal outcome; nothing is pushed).
#   decision == PROMOTE -> git add the promoted model files +
#     live_model.json, commit, and push to origin main via the retrain
#     deploy key (~/.ssh/redunator-retrain).
#
# Fail-safe: any failure exits non-zero WITHOUT touching the live
# backend — the promotion stays local and the service keeps serving
# the old model until a push + redeploy lands. The script never
# touches v1 files (promotion writes versioned vN.* files only) and
# refuses to commit if the working tree has any other uncommitted
# changes (so unrelated edits never ride along with a promotion).
#
# Prerequisite (one-time, browser): the public key in
# ~/.ssh/redunator-retrain.pub must be added to the Mightiest-G /
# redunator repo's Deploy keys with Write access. Until then the push
# fails with a permission error and the cron reports it.
set -euo pipefail

# Overridable for tests: RETRAIN_REPO / RETRAIN_DEPLOY_KEY / RETRAIN_SSH_URL.
REPO="${RETRAIN_REPO:-$HOME/workspace/redunator}"
KEY="${RETRAIN_DEPLOY_KEY:-$HOME/.ssh/redunator-retrain}"
SSH_URL="${RETRAIN_SSH_URL:-git@github.com:Mightiest-G/redunator.git}"

RUN_DIR="${1:-$(ls -dt "$REPO"/retrain_runs/*/ 2>/dev/null | head -1)}"
if [ -z "${RUN_DIR:-}" ] || [ ! -d "$RUN_DIR" ]; then
  echo "push_promotion: no retrain run dir found (tried: ${RUN_DIR:-none})" >&2
  exit 1
fi
REPORT="$RUN_DIR/promotion_report.json"
if [ ! -f "$REPORT" ]; then
  echo "push_promotion: no promotion_report.json in $RUN_DIR" >&2
  exit 1
fi

read_report() {
  python3 -c "import json,sys; print(json.load(open('$REPORT'))$1)"
}

DECISION="$(read_report "['decision']")"
if [ "$DECISION" != "PROMOTE" ]; then
  echo "push_promotion: decision is $DECISION — nothing to push."
  exit 0
fi
VERSION="$(read_report "['promoted_version']")"
FILES="$(read_report "['promoted_files']")"
echo "push_promotion: PROMOTE as v$VERSION ($FILES) from $RUN_DIR"

cd "$REPO"
if [ "$(git rev-parse --abbrev-ref HEAD)" != "main" ]; then
  echo "push_promotion: not on main — refusing to push a promotion" >&2
  exit 1
fi
if [ ! -f "$KEY" ]; then
  echo "push_promotion: deploy key missing: $KEY" >&2
  echo "Add ~/.ssh/redunator-retrain.pub to the repo's Deploy keys (Write access) first." >&2
  exit 1
fi

# The exact files a promotion may commit: the versioned model files
# plus the pointer. Nothing else.
ADD_FILES=(live_model.json)
for f in $(python3 -c "import json; print(' '.join(json.load(open('$REPORT'))['promoted_files'].values()))"); do
  if [ ! -f "$REPO/$f" ]; then
    echo "push_promotion: promoted file missing: $f" >&2
    exit 1
  fi
  ADD_FILES+=("$f")
done

# Refuse if the tree has any other uncommitted changes.
DIRTY="$(git status --porcelain -- "${ADD_FILES[@]}" | wc -l)"
TOTAL="$(git status --porcelain | wc -l)"
if [ "$DIRTY" != "$TOTAL" ]; then
  echo "push_promotion: working tree has changes outside the promotion — refusing" >&2
  git status --porcelain >&2
  exit 1
fi

git add "${ADD_FILES[@]}"
if git diff --cached --quiet; then
  # Nothing new to commit — a previous run already made the commit.
  # Push anyway (a no-op if the remote already has it).
  echo "push_promotion: promotion already committed — pushing."
else
  git commit -m "Promote model v$VERSION (monthly retrain $(basename "$RUN_DIR"))"
fi
GIT_SSH_COMMAND="ssh -i $KEY -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new" \
  git push "$SSH_URL" main
echo "push_promotion: pushed v$VERSION — the Cloud Build trigger rebuilds + redeploys."
