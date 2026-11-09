"""Offline TMDB matching tests against the supplied Apple TV export."""

import copy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import tv_enrich as e


class FakeTMDB:
	"""Deterministic fixture API: NO internet traffic or credentials."""

	def __init__(self):
		self.calls = []
		self.films = {
			62: ("2001: A Space Odyssey", 1968, ["Stanley Kubrick"]),
			78: ("Blade Runner", 1982, ["Ridley Scott"]),
			348: ("Alien", 1979, ["Ridley Scott"]),
			1930: ("The Amazing Spider-Man", 2012, ["Marc Webb"]),
			400: ("Garden State", 2004, ["Zach Braff"]),
			999: ("The Amazing Spider-Man", 2012, ["Someone Else"]),
			1000: ("Cape Fear", 1991, ["Martin Scorsese"]),
		}

	def summary(self, mid):
		title, film_year, _ = self.films[mid]
		return {"id": mid, "title": title, "original_title": title,
			"release_date": f"{film_year}-01-01"}

	def get(self, path, **params):
		self.calls.append((path, params))
		if path != "/search/movie":
			raise AssertionError(f"Unexpected search route {path}")
		query = e.normalize(params["query"])
		ids = {
			"2001 a space odyssey": [62],
			"blade runner": [78],
			"alien": [348],
			"the amazing spider man": [1930, 999],
			"garden state": [400],
			"cape fear": [1000],
		}.get(query, [])
		return {"results": [self.summary(i) for i in ids]}

	def details(self, movie_id, language):
		self.calls.append(("details", movie_id))
		title, yr, directors = self.films[movie_id]
		return {
			"id": movie_id, "title": title, "original_title": title,
			"release_date": f"{yr}-01-01", "overview": f"TMDB summary of {title}",
			"runtime": 100, "poster_path": "/example.jpg", "backdrop_path": None,
			"genres": [{"id": 1, "name": "Drama"}],
			"credits": {
				"crew": [{"job": "Director", "name": n} for n in directors],
				"cast": [{"name": "Test Actor", "character": "Lead", "order": 0, "id": 1}],
			},
		}


class LibraryTests(unittest.TestCase):
	@classmethod
	def setUpClass(cls):
		cls.source = json.loads((ROOT / "movies.json").read_text(encoding="utf-8"))
		cls.movies = cls.source["movies"]
		cls.by_title = {m["title"]: m for m in cls.movies}

	def test_real_library_is_valid(self):
		self.assertGreater(len(self.movies), 0)
		self.assertEqual(self.source["count"], len(self.movies))
		self.assertTrue(all(isinstance(m.get("title"), str) and m["title"] for m in self.movies))
		self.assertTrue(all("year" in m and "director" in m for m in self.movies))
		self.assertTrue(all(m.get("store_id") is None for m in self.movies))

	def test_duplicates_are_preserved_as_distinct_records(self):
		dupes = [m for m in self.movies if m["title"] == "The Amazing Spider-Man"]
		self.assertEqual(len(dupes), 2)
		seen = {}
		keys = []
		for m in dupes:
			base = e.source_key(m, 1)
			seen[base] = seen.get(base, 0) + 1
			keys.append(e.source_key(m, seen[base]))
		self.assertEqual(len(set(keys)), 2)

	def test_real_edition_titles_normalize(self):
		cases = {
			"Alien (Director’s Cut)": "Alien",
			"Blade Runner (The Final Cut)": "Blade Runner",
			"Das Boot - The Director’s Cut": "Das Boot",
			"Furiosa: A Mad Max Saga: Black & Chrome": "Furiosa: A Mad Max Saga",
			"The Hobbit: An Unexpected Journey (Extended Edition)": "The Hobbit: An Unexpected Journey",
			"Antichrist (2009)": "Antichrist",
		}
		for title, expected in cases.items():
			with self.subTest(title=title):
				self.assertIn(expected, e.variants(title))

	def test_director_can_resolve_library_edition_year(self):
		api = FakeTMDB()
		movie = self.by_title["2001: A Space Odyssey"]
		details, method, _ = e.search_match(movie, api, "de-DE")
		self.assertEqual(details["id"], 62)
		self.assertEqual(method, "title_director_year_conflict")
		data = e.assemble(movie, details, method, "item", "2026-11-04T00:00:00Z")
		self.assertEqual(data["year"], 1968)
		self.assertEqual(data["library_year"], 2001)
		self.assertEqual(data["director"], "Stanley Kubrick")

	def test_recut_film_match_keeps_source_title(self):
		api = FakeTMDB()
		film = self.by_title["Blade Runner (The Final Cut)"]
		details, method, _ = e.search_match(film, api, "de-DE")
		self.assertEqual(details["id"], 78)
		self.assertEqual(method, "title_director_year_conflict")
		result = e.assemble(film, details, method, "key", "2026-11-04T00:00:00Z")
		self.assertEqual(result["title"], "Blade Runner (The Final Cut)")
		self.assertEqual(result["year"], 1982)
		self.assertEqual(result["library_year"], 2007)

	def test_unknown_director_and_year_conflict_is_reviewed(self):
		api = FakeTMDB()
		film = self.by_title["Garden State"]
		details, status, _ = e.search_match(film, api, "de-DE")
		self.assertIsNone(details)
		self.assertEqual(status, "no_safe_match")

	def test_two_same_title_candidates_use_director(self):
		film = {"title": "The Amazing Spider-Man", "year": 2012, "director": "Marc Webb"}
		api = FakeTMDB()
		details, status, _ = e.search_match(film, api, "de-DE")
		self.assertEqual(details["id"], 1930)
		self.assertIn("director", status)

	def test_same_title_without_director_is_ambiguous(self):
		film = {"title": "The Amazing Spider-Man", "year": 2012, "director": "Unknown"}
		api = FakeTMDB()
		details, status, suggestions = e.search_match(film, api, "de-DE")
		self.assertIsNone(details)
		self.assertEqual(status, "ambiguous")
		self.assertEqual(len(suggestions), 2)

	def test_overrides_and_skip(self):
		movie = self.by_title["2001: A Space Odyssey"]
		self.assertEqual(e.override_for({"2001: A Space Odyssey|2001": {"tmdb_id": 62}}, movie)["tmdb_id"], 62)
		self.assertTrue(e.override_for({"2001: A Space Odyssey": {"skip": True}}, movie)["skip"])
		self.assertEqual(e.override_for({"source_key:record-1": {"tmdb_id": 78}}, movie, "record-1")["tmdb_id"], 78)
		with self.assertRaises(e.EnrichmentError):
			e.override_for({"2001: A Space Odyssey": {"tmdb_id": "not-valid"}}, movie)

	def test_all_library_entries_stay_in_mock_enrichment(self):
		api = FakeTMDB()
		payload, review = e.process(self.source, api, {}, clock=datetime(2026, 11, 4, tzinfo=timezone.utc))
		expected_count = len(self.movies)
		self.assertEqual(payload["count"], expected_count)
		self.assertEqual(len(payload["movies"]), expected_count)
		self.assertEqual(payload["count"], payload["matched_count"] + review["count"])
		self.assertEqual([m["title"] for m in payload["movies"]], [m["title"] for m in self.movies])
		self.assertEqual(len({m["source_key"] for m in payload["movies"]}), expected_count)
		self.assertGreater(payload["matched_count"], 0)
		self.assertTrue(any(m["match"]["method"] == "title_director_year_conflict" for m in payload["movies"]))

	def test_incremental_reuses_previous_without_api_calls(self):
		api = FakeTMDB()
		source = {"movies": [self.by_title["2001: A Space Odyssey"], self.by_title["Garden State"]]}
		start = datetime(2026, 11, 4, tzinfo=timezone.utc)
		first, _ = e.process(source, api, {}, clock=start)
		api2 = FakeTMDB()
		second, _ = e.process(source, api2, {}, previous=first, clock=start + timedelta(days=1))
		self.assertEqual(api2.calls, [])
		self.assertEqual(first, second)

	def test_added_date_is_preserved_in_fresh_and_cached_enrichment(self):
		movie = dict(self.by_title["2001: A Space Odyssey"], addedDate="2020-07-01T10:34:56Z")
		source = {"movies": [movie]}
		start = datetime(2026, 11, 4, tzinfo=timezone.utc)
		first, _ = e.process(source, FakeTMDB(), {}, clock=start)
		self.assertEqual(first["movies"][0]["addedDate"], movie["addedDate"])
		# A cache created before this exporter change must pick up the new field.
		del first["movies"][0]["addedDate"]
		api = FakeTMDB()
		second, _ = e.process(source, api, {}, previous=first, clock=start + timedelta(days=1))
		self.assertEqual(api.calls, [])
		self.assertEqual(second["movies"][0]["addedDate"], movie["addedDate"])

	def test_expiration_refreshes_by_id_without_research(self):
		api = FakeTMDB()
		source = {"movies": [self.by_title["2001: A Space Odyssey"]]}
		start = datetime(2026, 11, 4, tzinfo=timezone.utc)
		first, _ = e.process(source, api, {}, clock=start)
		api2 = FakeTMDB()
		second, _ = e.process(source, api2, {}, previous=first, clock=start + timedelta(days=32))
		self.assertEqual([c[0] for c in api2.calls], ["details"])
		self.assertEqual(second["movies"][0]["tmdb_id"], 62)

	def test_override_change_immediately_reprocesses_match(self):
		api = FakeTMDB()
		source = {"movies": [self.by_title["2001: A Space Odyssey"]]}
		start = datetime(2026, 11, 4, tzinfo=timezone.utc)
		first, _ = e.process(source, api, {}, clock=start)
		api2 = FakeTMDB()
		second, _ = e.process(source, api2, {"2001: A Space Odyssey": {"skip": True}}, previous=first, clock=start + timedelta(days=1))
		self.assertIsNone(second["movies"][0]["tmdb_id"])
		self.assertEqual(second["movies"][0]["match"]["method"], "manual_skip")

	def test_failed_source_count_is_rejected(self):
		with self.assertRaises(e.EnrichmentError):
			e.process({"count": 392, "movies": [self.movies[0]]}, FakeTMDB(), {})

	def test_json_pretty_and_unicode(self):
		data = {"title": 'Die Welt — "Test"', "cast": [{"name": "Zoë", "character": "À"}]}
		with tempfile.TemporaryDirectory() as d:
			file = Path(d) / "test.json"
			e.json_dump(file, data)
			self.assertEqual(json.loads(file.read_text(encoding="utf-8")), data)
			self.assertIn("\n  ", file.read_text(encoding="utf-8"))


if __name__ == "__main__":
	unittest.main()
