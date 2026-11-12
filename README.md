# Apple TV movie library → GitHub → TMDB

Dependency-free local **zsh + AppleScript** export, and Python **standard-library-only** TMDB enrichment in GitHub Actions. No Python environment, API token, or third-party software is needed on the Mac.

## Workflow

1. `mac/publish.zsh` on macOS queries Apple TV's **Movies** playlist and writes `movies.json` with the existing, working native exporter (does not read `Library.tvdb`).
2. The helper stages **only** `movies.json`, commits it when changed, and pushes it to GitHub.
3. `.github/workflows/enrich.yml` runs tests and calls TMDB for new, expired, or manually corrected entries.
4. The workflow commits `movies-enriched.json` and `movies-review.json` next to `movies.json`. No other data is committed by the workflow.

The repository contains the original uploaded **392-entry `movies.json`** as its initial dataset. Its content will be visible to anyone with read access to this repository; **create a private repository** if you do not want to disclose your collection.

## Set up the GitHub repository

1. Create an empty GitHub repository, initially **private** if desired. Clone it to your Mac and copy the contents of this package, including the hidden `.github/` folder, into that clone.
2. In GitHub go to **Settings → Secrets and variables → Actions → New repository secret** and create `TMDB_READ_TOKEN` with your TMDB API *Read Access Token* from https://www.themoviedb.org/settings/api. Do **not** commit it to a file.
3. Ensure GitHub Actions is enabled and the workflow can write repository contents. It requests `contents: write` via its YAML. Branch protection/rulesets may still prevent the bot from pushing, in which case configure an allowed publishing branch or alternate deployment flow.
4. Commit and push the initial project files and `movies.json` (or use the GitHub web UI). The workflow will start on the initial push.
5. Run **Actions → Enrich Apple TV movies with TMDB → Run workflow** to retry manually or refresh metadata after changing your token.

The action runs on a push affecting source/overrides/scripts/tests, on manual invocation, and every Monday at 05:27 UTC. GitHub-generated commits using the default `GITHUB_TOKEN` do not re-trigger the workflow, preventing recursion. No personal-access token is necessary for ordinary, permitted repository writes.

### Update from your Mac

The Apple TV app must be signed in and able to list your cloud purchases. From your cloned repo:

```zsh
/bin/zsh ./mac/publish.zsh
```

The helper first runs `git pull --rebase` to integrate GitHub's enrichment commits and replay any unpublished local exports. It then runs `mac/tv_movies.zsh`, checks that at least **300** movie rows were exported (`MIN_MOVIES` can override this), commits only `movies.json` if changed, and calls `git push` using the remote you configured. It also pushes pending commits when the export is unchanged. If the push fails, it synchronizes and retries once, without force-pushing. Commit or stash tracked changes before running it; an existing Git operation stops publishing, and a conflicting rebase started by the helper is aborted with local commits preserved.

This is an interactive command; for scheduled execution use your user `launchd` agent with an SSH key or other noninteractive Git authentication. Ensure macOS automation permission has already been granted to `/usr/bin/osascript` / Terminal for Apple TV. The helper leaves the previous JSON unchanged on failed export.

To schedule the upload, customize `mac/com.example.apple-tv-movies.plist.example` with the **absolute path** to your cloned repo and copy it to `~/Library/LaunchAgents/com.example.apple-tv-movies.plist` (remove the `.example` extension). Then load it with:

```zsh
launchctl bootstrap "gui/$(id -u)" "$HOME/Library/LaunchAgents/com.example.apple-tv-movies.plist"
```

The example runs at **04:30 local Mac time** and once on initial load. Test `mac/publish.zsh` interactively first; configure a Git remote and noninteractive SSH authentication. The login user must be able to run the TV automation at that time (sleep/locked Mac behavior depends on macOS). To deactivate it, run `launchctl bootout "gui/$(id -u)" "$HOME/Library/LaunchAgents/com.example.apple-tv-movies.plist"`.

To run export only, without Git:

```zsh
/bin/zsh ./mac/tv_movies.zsh ./movies.json 300
```

The Apple TV AppleScript track ID is **not** an iTunes Store ID. This package makes **no requests to Apple's catalog API**, and `store_id` remains unchanged.

Each movie includes `addedDate`, taken from TV's `date added` property (when the track was added to the playlist, not necessarily its purchase date). It is an ISO 8601 UTC timestamp such as `2026-11-09T12:34:56Z`, or `null` if unavailable. Run the exporter again to populate it in `movies.json`; enrichment preserves this field, including when reusing cached TMDB results.

## Enrichment, matching, and output

The GitHub workflow runs:

```bash
python3 scripts/tv_enrich.py \
	--input movies.json \
	--out movies-enriched.json \
	--review movies-review.json \
	--overrides movie-overrides.json \
	--language de-DE
```

Locally, if testing the enrichment outside Actions, set `TMDB_READ_TOKEN` and use the same command. This script uses Python's built-in `urllib`, `json`, and standard library modules. No pip installation or venv required on the GitHub runner.

* `movies.json`: original Apple TV records, **never overwritten** by enrichment.
* `movies-enriched.json`: all records, in the same order, including duplicates and editions. Matching adds `tmdb_id`, `tmdb_url`, `genres`, `directors`, `cast`, poster/backdrop URLs, `release_date`, `match`, and a `source_key`. For matched films `year` is TMDB's film release year, and the original Apple TV year is preserved as `library_year`. `description` is from TMDB when available, otherwise the library description remains.
* `movies-review.json`: unmatched or ambiguous entries, with candidate TMDB IDs where available.
* `movie-overrides.json`: manually approved film ID mappings.

To avoid incorrect matches, the matcher uses normalized or translated titles, year/year hints, and directors; a large year discrepancy can be accepted only when a film's director corroborates the match. Remakes and unresolved localized titles go to review. It never relies on popularity alone. **A correct TMDB ID identifies the film, not the exact Apple-specific cut or purchase.** The source title preserves that difference.

The initial library contains duplicates, e.g. *The Amazing Spider-Man*, *There Will Be Blood*, and *V for Vendetta*. The exporter deliberately keeps each purchased library entry. Film release years in the source can be a later Apple edition year, e.g. *2001: A Space Odyssey* is marked 2001. These cases are covered by regression tests.

### Override uncertain matches

Edit `movie-overrides.json` and commit the change:

```json
{
	"2001: A Space Odyssey|2001": { "tmdb_id": 62 },
	"Other Movie Title": { "tmdb_id": 12345 },
	"Do Not Match": { "skip": true }
}
```

The IDs above are illustrative. Verify each film ID on TMDB before adding it. The key `Title|library_year` takes precedence over `Title` alone. For duplicates that share a title and year but need different overrides, use `"source_key:<value>"` as the override key, copying `<value>` from `movies-review.json` or `movies-enriched.json`; it takes highest precedence. `skip: true` deliberately leaves a film without TMDB metadata, without flagging it for review.

### Incremental updates

The script reuses the prior `movies-enriched.json` for unchanged entries using a deterministic source identity derived from title, library year, director, and duplicate occurrence. This is the persistent cache on GitHub: no separate large API response directory is committed.

* Resolved entries refresh their TMDB detail records by ID every **30 days** by default, without searching by title again.
* Unresolved entries retry lookup after **14 days** by default.
* New or changed entries and new manual overrides are processed immediately.
* Run the workflow with the `refresh` input enabled to refresh everything immediately.
* Network/authentication errors fail the workflow **before the output files are replaced**, protecting prior published data. Without credentials use `--offline` only when everything already has usable previous results.

The initial 392-film run requires TMDB requests. Offline tests cannot verify live TMDB coverage. After the initial run, look at `movies-review.json`, approve dubious cases, and commit their IDs into `movie-overrides.json`.

## Tests

```bash
python3 -m unittest discover -s tests -v
```

Tests are network-free and use the current `movies.json`, including its duplicates, mixed-language titles and edition suffixes. Count checks follow the current library size and verify that enrichment preserves every entry. Additional mocked TMDB responses test matching, director/year conflicts, ambiguous remakes, and repeat runs.

## Publishing / licensing

A public repository exposes your movie ownership list. For a private source repository, publish only the enriched JSON via a separate deployment workflow or webserver upload step.

TMDB data and images are subject to TMDB terms and attribution requirements. See https://www.themoviedb.org/about/logos-attribution and https://www.themoviedb.org/terms-of-use. The dataset and scripts do not include actual film artwork assets; poster and backdrop fields are references to TMDB image URLs. Consider caching or proxying responsibly under TMDB's usage terms.

## Troubleshooting

* **`Missing repository Actions secret: TMDB_READ_TOKEN`:** create the secret and manually rerun the workflow.
* **`HTTP 401/403`:** check the TMDB API Read Access Token, not your website login password.
* **Push rejected:** a branch protection rule or concurrent Mac push may have moved the branch. Let a new workflow run for the new `movies.json`; do not rebase a stale generated catalog over a newer source.
* **No matching TMDB film:** review candidates, source title/years, and set an explicit override after verifying the correct TMDB film.
* **macOS automation permission:** grant Terminal/osascript access to TV, then run the exporter once interactively before scheduling it.
* **Different `movies.json` count:** a source count mismatch fails enrichment; verify the Apple TV export.
