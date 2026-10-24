#!/usr/bin/env bash
# Publish a built folder as the only commit of a branch, which a git-connected
# Cloudflare Pages project deploys as-is. The branch is force-pushed, so it never
# accumulates history (or old copies of the demo).
#
# Usage: GH_TOKEN=... scripts/publish-branch.sh <dir> <branch> <message>
# Uses GITHUB_REPOSITORY (owner/repo), as set in GitHub Actions.
set -euo pipefail

DIR="$1" BRANCH="$2" MSG="$3"
: "${GH_TOKEN:?GH_TOKEN is required}" "${GITHUB_REPOSITORY:?GITHUB_REPOSITORY is required}"

cd "$DIR"
rm -rf .git
git init -q -b "$BRANCH"
git add -A
git -c user.name="github-actions[bot]" \
  -c user.email="41898283+github-actions[bot]@users.noreply.github.com" \
  commit -q -m "$MSG"
git push -q -f "https://x-access-token:${GH_TOKEN}@github.com/${GITHUB_REPOSITORY}.git" "$BRANCH"
rm -rf .git
echo "Published $DIR to $BRANCH"
