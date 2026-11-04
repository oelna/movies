#!/usr/bin/env python3
"""TMDB enrichment for Apple TV movies.json. Python standard library only.

No iTunes Search API calls are performed. Never overwrite the source export.
Uncertain matches are collected for review instead of assigned a guessed ID.
"""

import argparse
from collections import Counter
from datetime import datetime, timedelta, timezone
import hashlib
import html
import json
import os
from pathlib import Path
import re
import sys
import tempfile
import time
import unicodedata
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

API_URL = "https://api.themoviedb.org/3"
EDITION_RE = re.compile(
	r"(?i)(?:\s*[(/-]\s*(?:the\s+)?(?:special\s+)?(?:director['’]?s?\s+cut|extended(?:\s+(?:cut|edition|version))?|"
	r"final\s+cut|uncut|remastered?\s*(?:feature|edition)?|digital\s+remastered?|"
	r"\d+(?:st|nd|rd|th)\s+anniversary\s+edition|black\s*(?:&|and)\s*chrome)\s*\)?\s*$|"
	r"\s*:\s*(?:black\s*(?:&|and)\s*chrome|final\s+cut)\s*$)"
)
YEAR_TAG = re.compile(r"\(((?:18|19|20)\d{2})\)\s*$")


class EnrichmentError(Exception):
	pass


def now_utc():
	return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def parse_time(value):
	try:
		return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
	except (TypeError, ValueError):
		return None


def year(value):
	try:
		if isinstance(value, bool):
			return None
		if isinstance(value, str):
			value = value[:4]
		n = int(value)
		return n if 1880 <= n <= 2100 else None
	except (TypeError, ValueError):
		return None


def normalize(value):
	value = unicodedata.normalize("NFKD", html.unescape(str(value or ""))).casefold()
	value = "".join(ch for ch in value if not unicodedata.combining(ch))
	return " ".join("".join(ch if ch.isalnum() else " " for ch in value).split())


def variants(title):
	"""Preserve original titles but search a film title without edition/year suffixes."""
	choices = [title.strip()]
	current = title.strip()
	for _ in range(4):
		stripped = YEAR_TAG.sub("", current).strip()
		stripped = EDITION_RE.sub("", stripped).strip(" -:/,(")
		if not stripped or stripped == current:
			break
		choices.append(stripped)
		current = stripped
	return list(dict.fromkeys(choices))


def director_names(value):
	if not value or normalize(value) in {"unknown", "unbekannt"}:
		return set()
	parts = re.split(r"\s*(?:&|,|/|\band\b|\bund\b)\s*", value, flags=re.IGNORECASE)
	return {normalize(p) for p in parts if normalize(p)}


def tmdb_directors(details):
	crew = (details.get("credits") or {}).get("crew") or []
	return list(dict.fromkeys(x["name"] for x in crew
		if isinstance(x, dict) and x.get("job") == "Director" and isinstance(x.get("name"), str)))


def json_dump(path, payload):
	with Path(path).open("w", encoding="utf-8") as handle:
		json.dump(payload, handle, ensure_ascii=False, indent=2)
		handle.write("\n")


def save_pair(out_path, enriched, review_path, review):
	"""Validate and stage both files before replacing either published file."""
	staged = []
	try:
		for path, payload in [(out_path, enriched), (review_path, review)]:
			path = Path(path)
			path.parent.mkdir(parents=True, exist_ok=True)
			with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent,
				prefix=f".{path.name}.", suffix=".tmp", delete=False) as handle:
				tmp = Path(handle.name)
				json.dump(payload, handle, ensure_ascii=False, indent=2)
				handle.write("\n")
			staged.append((tmp, path))
		for tmp, path in staged:
			os.replace(tmp, path)
	finally:
		for tmp, _ in staged:
			if tmp.exists():
				tmp.unlink()


class TMDB:
	def __init__(self, token, offline=False):
		self.token = token
		self.offline = offline
		self.responses = {}
		self.requests = 0

	def get(self, route, **parameters):
		key = (route, tuple(sorted(parameters.items())))
		if key in self.responses:
			return self.responses[key]
		if self.offline:
			raise EnrichmentError("TMDB request required in --offline mode: " + route)
		if not self.token:
			raise EnrichmentError("Set TMDB_READ_TOKEN in your environment or GitHub Actions secrets")
		url = API_URL + route + "?" + urlencode(parameters)
		headers = {"Authorization": f"Bearer {self.token}", "Accept": "application/json", "User-Agent": "AppleTVMovieCatalog/2.0"}
		last_error = None
		for attempt in range(5):
			try:
				with urlopen(Request(url, headers=headers), timeout=25) as response:
					data = json.load(response)
				if not isinstance(data, dict):
					raise EnrichmentError("Unexpected TMDB JSON document")
				if route == "/search/movie" and not isinstance(data.get("results"), list):
					raise EnrichmentError("Unexpected TMDB movie search response")
				if route.startswith("/movie/") and not isinstance(data.get("id"), int):
					raise EnrichmentError("Unexpected TMDB movie detail response")
				self.responses[key] = data
				self.requests += 1
				return data
			except HTTPError as exc:
				last_error = f"HTTP {exc.code}"
				if exc.code in (401, 403):
					raise EnrichmentError("TMDB token was rejected; check TMDB_READ_TOKEN") from exc
				if exc.code not in (429, 500, 502, 503, 504):
					break
				delay = min(30, 2 ** attempt * 2)
				if exc.code == 429:
					try:
						delay = max(delay, min(60, int(exc.headers.get("Retry-After", "0"))))
					except ValueError:
						pass
			except (URLError, TimeoutError, OSError, ValueError) as exc:
				last_error = type(exc).__name__
				delay = min(30, 2 ** attempt * 2)
			if attempt < 4:
				time.sleep(delay)
		raise EnrichmentError(f"TMDB {route} failed ({last_error}); published files untouched")

	def details(self, movie_id, language):
		data = self.get(f"/movie/{movie_id}", language=language, append_to_response="credits")
		if data.get("id") != movie_id:
			raise EnrichmentError(f"TMDB returned a different movie ID for {movie_id}")
		if not data.get("overview") and language.lower() != "en-us":
			fallback = self.get(f"/movie/{movie_id}", language="en-US", append_to_response="credits")
			data = dict(data)
			data["overview"] = fallback.get("overview") or ""
		return data


def match_candidates(movie, candidates, api, language, candidate_limit=5):
	"""Require title/year agreement, or director corroboration for year-conflicted editions.

	No popularity-based guessing. Return (movie details, method, review suggestions).
	"""
	base_title = movie["title"].strip()
	acceptable_titles = {normalize(t) for t in variants(base_title)}
	library_year = year(movie.get("year"))
	year_hint_match = YEAR_TAG.search(base_title)
	year_hint = int(year_hint_match.group(1)) if year_hint_match else None
	library_directors = director_names(movie.get("director"))
	unique = {x["id"]: x for x in candidates if isinstance(x, dict) and type(x.get("id")) is int and x["id"] > 0}
	preliminary = []
	for candidate in unique.values():
		film_year = year(candidate.get("release_date"))
		title_hit = bool(acceptable_titles & {normalize(candidate.get("title")), normalize(candidate.get("original_title"))})
		diffs = [abs(film_year - yy) for yy in (year_hint, library_year) if film_year and yy]
		gap = min(diffs) if diffs else None
		# Avoid fetching unrelated film details solely because TMDB ranked them highly.
		if not title_hit and (gap is None or gap > 1):
			continue
		pre_score = (60 if title_hit else 0) + (30 if gap == 0 else 20 if gap == 1 else 0)
		preliminary.append((pre_score, candidate))
	preliminary.sort(key=lambda v: (-v[0], v[1]["id"]))
	accepted, suggestions = [], []
	for _, candidate in preliminary[:candidate_limit]:
		details = api.details(candidate["id"], language)
		film_year = year(details.get("release_date")) or year(candidate.get("release_date"))
		title_hit = bool(acceptable_titles & {normalize(details.get("title")), normalize(details.get("original_title")),
			normalize(candidate.get("title")), normalize(candidate.get("original_title"))})
		diffs = [abs(film_year - yy) for yy in (year_hint, library_year) if film_year and yy]
		gap = min(diffs) if diffs else None
		tmdb_names = tmdb_directors(details)
		director_hit = bool(library_directors & {normalize(x) for x in tmdb_names})
		director_conflict = bool(library_directors and tmdb_names and not director_hit)
		method = None
		if title_hit and gap is not None and gap <= 1 and not director_conflict:
			method = "title_year_director" if director_hit else "title_year"
		elif title_hit and director_hit and not director_conflict:
			method = "title_director_year_conflict"
		elif not title_hit and director_hit and gap is not None and gap <= 1:
			method = "alternate_title_year_director"
		score = (60 if title_hit else 0) + (30 if gap == 0 else 20 if gap == 1 else 0) + (30 if director_hit else 0)
		suggestions.append({
			"tmdb_id": candidate["id"], "title": details.get("title") or candidate.get("title"),
			"year": film_year, "directors": tmdb_names, "title_agrees": title_hit,
			"year_gap": gap, "director_agrees": director_hit,
		})
		if method:
			accepted.append((score, candidate["id"], details, method))
	accepted.sort(key=lambda v: (-v[0], v[1]))
	if not accepted:
		return None, "no_safe_match" if preliminary else "no_plausible_candidates", suggestions
	if len(accepted) > 1 and accepted[0][0] - accepted[1][0] < 20:
		return None, "ambiguous", suggestions
	return accepted[0][2], accepted[0][3], suggestions


def search_match(movie, api, language, max_candidates=5):
	all_candidates = {}
	queries = variants(movie["title"])
	# Titles in the library can be localized; TMDB searches translated and original titles.
	languages = list(dict.fromkeys([language, "en-US"]))
	status, suggestions = "no_results", []
	for query in queries:
		for lang in languages:
			response = api.get("/search/movie", query=query, language=lang, page=1, include_adult="true")
			for candidate in response["results"]:
				if isinstance(candidate, dict) and type(candidate.get("id")) is int and candidate["id"] > 0:
					all_candidates.setdefault(candidate["id"], candidate)
			if all_candidates:
				details, status, suggestions = match_candidates(movie, list(all_candidates.values()), api, language, max_candidates)
				if details:
					return details, status, suggestions
	return None, status, suggestions


def source_key(movie, occurrence):
	identity = [movie["title"].strip(), year(movie.get("year")), movie.get("director") or ""]
	digest = hashlib.sha256(json.dumps(identity, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()[:20]
	return digest + (f"-{occurrence}" if occurrence > 1 else "")


def override_for(overrides, movie, record_key=None):
	key = f"{movie['title'].strip()}|{year(movie.get('year'))}"
	value = overrides.get("source_key:" + record_key, overrides.get(key, overrides.get(movie["title"].strip(), {}))) if record_key else overrides.get(key, overrides.get(movie["title"].strip(), {}))
	if isinstance(value, int) and not isinstance(value, bool):
		value = {"tmdb_id": value}
	if not isinstance(value, dict):
		raise EnrichmentError(f"Invalid override for {movie['title']}")
	movie_id = value.get("tmdb_id")
	if movie_id is not None and (isinstance(movie_id, bool) or not str(movie_id).isdigit() or int(movie_id) <= 0):
		raise EnrichmentError(f"Invalid tmdb_id override for {movie['title']}")
	return {"tmdb_id": int(movie_id) if movie_id is not None else None, "skip": value.get("skip") is True}


def assemble(movie, details, method, key, checked_at, suggestions=None, cast_limit=10, override_id=None):
	result = dict(movie)
	result["source_key"] = key
	result["library_year"] = year(movie.get("year"))
	result["tmdb_id"] = details.get("id") if details else None
	result["tmdb_url"] = f"https://www.themoviedb.org/movie/{details['id']}" if details else None
	result["tmdb_checked_at"] = checked_at
	result["match"] = {"status": "matched" if details else "unmatched", "method": method, "override_id": override_id}
	if not details:
		result["genres"] = [movie["genre"]] if movie.get("genre") else []
		result["directors"] = [movie["director"]] if movie.get("director") and normalize(movie["director"]) != "unknown" else []
		result["cast"] = []
		result["review_candidates"] = suggestions or []
		return result
	result["original_title"] = details.get("original_title") or None
	result["release_date"] = details.get("release_date") or None
	result["year"] = year(details.get("release_date")) or year(movie.get("year"))
	result["runtime_minutes"] = details.get("runtime") if type(details.get("runtime")) is int else None
	result["genres"] = [g["name"] for g in details.get("genres", []) if isinstance(g, dict) and isinstance(g.get("name"), str)]
	result["directors"] = tmdb_directors(details)
	credits = details.get("credits") or {}
	cast = [x for x in credits.get("cast", []) if isinstance(x, dict) and isinstance(x.get("name"), str)]
	cast.sort(key=lambda x: (x.get("order") if type(x.get("order")) is int else 99999, x.get("id", 0)))
	result["cast"] = [{"name": x["name"], "character": x.get("character") or None} for x in cast[:cast_limit]]
	if details.get("overview"):
		result["description"] = details["overview"]
	result["poster_url"] = "https://image.tmdb.org/t/p/w500" + details["poster_path"] if isinstance(details.get("poster_path"), str) and details["poster_path"].startswith("/") else None
	result["backdrop_url"] = "https://image.tmdb.org/t/p/w780" + details["backdrop_path"] if isinstance(details.get("backdrop_path"), str) and details["backdrop_path"].startswith("/") else None
	return result


def reuse(movie, previous):
	result = dict(previous)
	# Up-to-date input fields, without overwriting TMDB-supplied details.
	for field, value in movie.items():
		if field not in {"description", "year"}:
			result[field] = value
	result["library_year"] = year(movie.get("year"))
	if not result.get("tmdb_id"):
		result["year"] = movie.get("year")
		result["description"] = movie.get("description")
	return result


def old_enough(previous, days, current):
	stamp = parse_time(previous.get("tmdb_checked_at"))
	return stamp is None or (current - stamp) >= timedelta(days=days)


def process(source, api, overrides, previous=None, language="de-DE", refresh_days=30, retry_days=14,
	cast_limit=10, refresh=False, clock=None, progress=False):
	if not isinstance(source, dict) or not isinstance(source.get("movies"), list) or not source["movies"]:
		raise EnrichmentError("movies.json must contain a nonempty movies array")
	movies = source["movies"]
	if source.get("count") is not None and source["count"] != len(movies):
		raise EnrichmentError("Source count does not match movie list; refusing to publish")
	if not isinstance(overrides, dict):
		raise EnrichmentError("movie-overrides.json must be an object")
	current = clock or datetime.now(timezone.utc)
	stamp = current.isoformat(timespec="seconds").replace("+00:00", "Z")
	previous_movies = {m["source_key"]: m for m in (previous or {}).get("movies", [])
		if isinstance(m, dict) and isinstance(m.get("source_key"), str)}
	occurrences = Counter()
	results, reviews = [], []
	for idx, movie in enumerate(movies, 1):
		if not isinstance(movie, dict) or not isinstance(movie.get("title"), str) or not movie["title"].strip():
			raise EnrichmentError(f"Invalid movie title at record {idx}")
		base_key = source_key(movie, 1)
		occurrences[base_key] += 1
		key = source_key(movie, occurrences[base_key])
		override = override_for(overrides, movie, key)
		old = previous_movies.get(key)
		old_id = old.get("tmdb_id") if old else None
		old_override_id = (old.get("match") or {}).get("override_id") if old else None
		was_skipped = bool(old and (old.get("match") or {}).get("method") == "manual_skip")
		if old and not refresh and old_override_id == override["tmdb_id"] and was_skipped == override["skip"] and (
			(old_id and not old_enough(old, refresh_days, current)) or
			(not old_id and (override["skip"] or not old_enough(old, retry_days, current)))):
			result = reuse(movie, old)
		else:
			if override["skip"]:
				result = assemble(movie, None, "manual_skip", key, stamp)
			elif override["tmdb_id"]:
				details = api.details(override["tmdb_id"], language)
				result = assemble(movie, details, "manual_override", key, stamp, cast_limit=cast_limit, override_id=override["tmdb_id"])
			elif old_id and old_override_id is None:
				# Refresh metadata by verified TMDB ID, not a new title-search match.
				details = api.details(old_id, language)
				result = assemble(movie, details, "existing_id", key, stamp, cast_limit=cast_limit)
			else:
				details, method, suggestions = search_match(movie, api, language)
				result = assemble(movie, details, method, key, stamp, suggestions, cast_limit=cast_limit)
		results.append(result)
		if not result.get("tmdb_id") and result["match"]["method"] != "manual_skip":
			reviews.append({"source_key": key, "title": movie["title"], "library_year": movie.get("year"),
				"director": movie.get("director"), "reason": result["match"]["method"],
				"candidates": result.get("review_candidates", [])})
		if progress:
			print(f"[{idx}/{len(movies)}] {movie['title']}: {result['match']['method']}", file=sys.stderr)
	payload = {
		"source": "apple_tv_and_tmdb", "source_generated_at": source.get("generated_at"),
		"language": language, "count": len(results),
		"matched_count": sum(bool(m.get("tmdb_id")) for m in results),
		"review_count": len(reviews), "movies": results,
	}
	review_payload = {"source_generated_at": source.get("generated_at"), "count": len(reviews), "review": reviews}
	if payload["count"] != len(movies):
		raise EnrichmentError("Enrichment changed the number of movie records")
	return payload, review_payload


def main(argv=None):
	p = argparse.ArgumentParser(description=__doc__)
	p.add_argument("--input", type=Path, default=Path("movies.json"))
	p.add_argument("--out", type=Path, default=Path("movies-enriched.json"))
	p.add_argument("--review", type=Path, default=Path("movies-review.json"))
	p.add_argument("--overrides", type=Path, default=Path("movie-overrides.json"))
	p.add_argument("--language", default="de-DE")
	p.add_argument("--cast-limit", type=int, default=10)
	p.add_argument("--refresh-days", type=int, default=30)
	p.add_argument("--retry-days", type=int, default=14)
	p.add_argument("--refresh", action="store_true", help="Recheck all records now")
	p.add_argument("--offline", action="store_true", help="Reuse existing enriched records only (fails if an API request is necessary)")
	p.add_argument("--quiet", action="store_true", help="Only print summary and errors")
	args = p.parse_args(argv)
	try:
		if args.cast_limit < 0 or args.cast_limit > 100 or args.refresh_days < 0 or args.retry_days < 0:
			raise EnrichmentError("Invalid cast/refresh/retry limit")
		paths = [args.input.resolve(), args.out.resolve(), args.review.resolve()]
		if len(set(paths)) != 3:
			raise EnrichmentError("Source, enriched and review file paths must differ")
		with args.input.open(encoding="utf-8") as stream:
			source = json.load(stream)
		with args.overrides.open(encoding="utf-8") as stream:
			overrides = json.load(stream)
		previous = None
		if args.out.exists():
			with args.out.open(encoding="utf-8") as stream:
				previous = json.load(stream)
		if previous and previous.get("language") != args.language:
			previous = None
		api = TMDB(os.environ.get("TMDB_READ_TOKEN"), offline=args.offline)
		payload, review = process(source, api, overrides, previous=previous, language=args.language,
			refresh_days=args.refresh_days, retry_days=args.retry_days, cast_limit=args.cast_limit,
			refresh=args.refresh, progress=not args.quiet)
		save_pair(args.out, payload, args.review, review)
		print(f"Complete: {payload['matched_count']}/{payload['count']} matched, "
			f"{payload['review_count']} for review, {api.requests} API requests", file=sys.stderr)
		return 0
	except (EnrichmentError, OSError, ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
		print(f"Enrichment failed: {exc}", file=sys.stderr)
		return 1


if __name__ == "__main__":
	sys.exit(main())
