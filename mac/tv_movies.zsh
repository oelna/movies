#!/bin/zsh
# Native macOS exporter for Apple TV Movies (cloud-only items included if TV lists them).
# Requires: TV app, /usr/bin/osascript, /usr/bin/sqlite3, /usr/bin/mktemp, /bin/mv.
# Usage: /bin/zsh tv_movies.zsh [movies.json] [minimum-movie-count]
# No Python, third-party packages, or access to proprietary Library.tvdb.

set -e
set -u
set -o pipefail

function die() {
	print -u2 -- "Error: $*"
	exit 1
}

OUT="${1:-movies.json}"
MIN_COUNT="${2:-1}"
SCRIPT="${0:A:h}/tv_movies.applescript"
FORMATTER="${0:A:h}/json_pretty.awk"

[[ -r "$SCRIPT" ]] || die "Missing companion AppleScript: $SCRIPT"
[[ -r "$FORMATTER" ]] || die "Missing JSON formatter: $FORMATTER"
[[ -x /usr/bin/osascript ]] || die "osascript not found"
[[ -x /usr/bin/sqlite3 ]] || die "sqlite3 not found"
[[ "$MIN_COUNT" == <-> ]] || die "Minimum count must be a nonnegative integer"
[[ -d "${OUT:h}" ]] || die "Output directory does not exist: ${OUT:h}"

# JSON1 is a standard SQLite extension on current macOS installations.
/usr/bin/sqlite3 -batch ':memory:' "SELECT json_object('ok',1);" >/dev/null || die "sqlite3 lacks JSON support"

TEMP_ROOT="${TMPDIR:-/tmp}"
TMP_DIR="$(/usr/bin/mktemp -d "${TEMP_ROOT%/}/tv-movies.XXXXXXXX")"
OUT_TMP="$(/usr/bin/mktemp "${OUT}.tmp.XXXXXXXX")"
trap '/bin/rm -rf -- "$TMP_DIR"; /bin/rm -f -- "$OUT_TMP"' EXIT

# The AppleScript removes embedded tabs and newlines from the metadata.
/usr/bin/osascript "$SCRIPT" > "$TMP_DIR/movies.tsv" || die "Apple TV query failed; previous JSON left unchanged"

# Validate the five-column interchange stream before importing it. Movie
# titles/descriptions are allowed to contain literal quotation marks; the
# companion AppleScript replaces only tabs and line endings in each field.
RAW_COUNT="$(/usr/bin/awk -F '\t' '
	NF != 5 {
		if (bad < 5) printf "Malformed AppleScript row %d (found %d fields, expected 5)\n", NR, NF > "/dev/stderr"
		bad++
	}
	END {
		if (bad) exit 1
		print NR
	}
' "$TMP_DIR/movies.tsv")" || die "Malformed AppleScript output; existing JSON unchanged"
[[ "$RAW_COUNT" == <-> ]] || die "Could not count the AppleScript records"
(( RAW_COUNT >= MIN_COUNT )) || die "Only $RAW_COUNT movies returned (minimum $MIN_COUNT); existing JSON unchanged"

# SQLite's .mode tabs import is actually CSV-style: it tries to dequote movie
# titles/descriptions containing double quotes, corrupting the rows.  In ASCII
# mode, the .import parser does NOT interpret quotes.  Explicitly set the field
# and row separators to match our plain (unquoted) tab-separated stream.
# The SQLite database exists only in memory and never reads Library.tvdb.
/usr/bin/sqlite3 -batch -noheader ':memory:' > "$TMP_DIR/result.txt" <<SQL || die "SQLite JSON conversion failed"
.bail on
.mode ascii
.separator "\t" "\n"
CREATE TABLE movies (title TEXT, year TEXT, genre TEXT, description TEXT, director TEXT);
.import "$TMP_DIR/movies.tsv" movies
.mode list
SELECT count(*) FROM movies WHERE trim(title) <> '';
SELECT json_object(
	'generated_at', strftime('%Y-%m-%dT%H:%M:%SZ','now'),
	'source', 'applescript',
	'count', (SELECT count(*) FROM movies WHERE trim(title) <> ''),
	'movies', json((
		SELECT json_group_array(json_object(
			'title', title,
			'store_id', NULL,
			'year', CASE WHEN year GLOB '[12][0-9][0-9][0-9]' AND CAST(year AS INTEGER) BETWEEN 1880 AND 2100 THEN CAST(year AS INTEGER) ELSE NULL END,
			'genre', nullif(genre, ''),
			'description', nullif(description, ''),
			'director', nullif(director, '')
		))
		FROM (SELECT * FROM movies WHERE trim(title) <> '' ORDER BY title COLLATE NOCASE)
	))
);
SQL

COUNT="$(/usr/bin/head -n 1 "$TMP_DIR/result.txt")"
[[ "$COUNT" == <-> ]] || die "Could not determine the movie count; existing JSON unchanged"
(( COUNT == RAW_COUNT )) || die "Import mismatch: AppleScript emitted $RAW_COUNT rows but SQLite imported $COUNT; existing JSON unchanged"
(( COUNT >= MIN_COUNT )) || die "Only $COUNT movies found (minimum $MIN_COUNT); existing JSON unchanged"

/usr/bin/tail -n +2 "$TMP_DIR/result.txt" > "$TMP_DIR/compact.json"
/usr/bin/awk -f "$FORMATTER" "$TMP_DIR/compact.json" > "$OUT_TMP" || die "JSON formatting failed; existing JSON unchanged"
[[ -s "$OUT_TMP" ]] || die "Generated JSON was empty; existing JSON unchanged"
/bin/mv -f -- "$OUT_TMP" "$OUT"
print -u2 -- "Wrote $COUNT movies to $OUT"
