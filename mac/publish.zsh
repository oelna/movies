#!/bin/zsh
# Export cloud movies locally and commit ONLY the source movies.json.
# GitHub Actions enriches it after the push.
set -euo pipefail

SCRIPT_DIR="${0:A:h}"
REPO="${SCRIPT_DIR:h}"
cd "$REPO"

function die() {
	print -u2 -- "Error: $*"
	exit 1
}

/usr/bin/git rev-parse --is-inside-work-tree >/dev/null 2>&1 || die "This directory is not a Git repository"
[[ -f "$SCRIPT_DIR/tv_movies.zsh" ]] || { print -u2 -- "Missing Apple TV exporter"; exit 1; }

# Never start or abort an operation that belongs to someone else.
for operation in rebase-merge rebase-apply MERGE_HEAD CHERRY_PICK_HEAD REVERT_HEAD; do
	[[ ! -e "$(/usr/bin/git rev-parse --git-path "$operation")" ]] || die "Finish the existing Git operation before publishing"
done
/usr/bin/git diff --quiet && /usr/bin/git diff --cached --quiet || die "Commit or stash tracked changes before publishing"

function sync_from_remote() {
	# Rebase also recovers local export commits left behind by a rejected push.
	# Disable automatic stashing: the publisher must not hide uncommitted work.
	if ! /usr/bin/git -c rebase.autoStash=false pull --rebase; then
		if [[ -d "$(/usr/bin/git rev-parse --git-path rebase-merge)" || -d "$(/usr/bin/git rev-parse --git-path rebase-apply)" ]]; then
			/usr/bin/git rebase --abort || die "Could not abort the failed rebase; resolve it manually"
		fi
		die "Could not synchronize with GitHub; local commits preserved. Resolve the Git error before publishing again"
	fi
}

sync_from_remote
/bin/zsh "$SCRIPT_DIR/tv_movies.zsh" "$REPO/movies.json" "${MIN_MOVIES:-300}"
/usr/bin/git add -- movies.json

if /usr/bin/git diff --cached --quiet -- movies.json; then
	print -- "No changes in movies.json; checking for unpublished commits"
else
	/usr/bin/git commit --only -m "Update Apple TV movie library" -- movies.json
fi

# The action may finish another enrichment between our pull and push.
# Retry once after integrating it; never force-push over remote commits.
if ! /usr/bin/git push; then
	print -u2 -- "Push failed; synchronizing with GitHub and retrying once"
	sync_from_remote
	/usr/bin/git push || die "Push still failed; local commits preserved for the next run"
fi
print -- "Library synchronized with GitHub; new commits trigger enrichment"
