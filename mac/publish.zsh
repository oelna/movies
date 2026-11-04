#!/bin/zsh
# Export cloud movies locally and commit ONLY the source movies.json.
# GitHub Actions enriches it after the push.
set -euo pipefail

SCRIPT_DIR="${0:A:h}"
REPO="${SCRIPT_DIR:h}"
cd "$REPO"

[[ -d .git ]] || { print -u2 -- "This directory is not a Git repository"; exit 1; }
[[ -f "$SCRIPT_DIR/tv_movies.zsh" ]] || { print -u2 -- "Missing Apple TV exporter"; exit 1; }

/bin/zsh "$SCRIPT_DIR/tv_movies.zsh" "$REPO/movies.json" "${MIN_MOVIES:-300}"
/usr/bin/git add -- movies.json

if /usr/bin/git diff --cached --quiet -- movies.json; then
	print -- "No changes in movies.json; nothing to push"
	exit 0
fi

/usr/bin/git commit --only -m "Update Apple TV movie library" -- movies.json
/usr/bin/git push
print -- "Pushed movies.json; GitHub Actions will perform enrichment"
