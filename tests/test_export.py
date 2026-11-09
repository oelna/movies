"""Native exporter checks with fixture TV output; never query the user's library."""

import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(sys.platform == "darwin", "Requires native macOS export tools")
class ExportTests(unittest.TestCase):
	def setUp(self):
		self.temp = tempfile.TemporaryDirectory()
		self.addCleanup(self.temp.cleanup)
		self.work = Path(self.temp.name)
		for name in ("tv_movies.applescript", "json_pretty.awk"):
			shutil.copy(ROOT / "mac" / name, self.work / name)
		# Substitute only the TV query; run the real validation/import/JSON pipeline.
		self.exporter = self.work / "tv_movies.zsh"
		self.exporter.write_text((ROOT / "mac/tv_movies.zsh").read_text().replace(
			'/usr/bin/osascript "$SCRIPT"', '/bin/cat "${SCRIPT:h}/fixture.tsv"'))
		self.output = self.work / "movies.json"

	def export(self, tsv):
		(self.work / "fixture.tsv").write_text(tsv, encoding="utf-8")
		return subprocess.run(["/bin/zsh", str(self.exporter), str(self.output), "1"],
			capture_output=True, text=True)

	def test_dates_and_missing_dates_survive_json_conversion(self):
		result = self.export(
			'Quoted "Movie"\t2020\tDrama\tA "quoted" description\tDirector\t2020-07-01T10:34:56Z\n'
			'No date\t2021\t\t\t\t\n')
		self.assertEqual(result.returncode, 0, result.stderr)
		payload = json.loads(self.output.read_text())
		self.assertEqual(payload["count"], 2)
		movies = {movie["title"]: movie for movie in payload["movies"]}
		self.assertEqual(movies['Quoted "Movie"']["addedDate"], "2020-07-01T10:34:56Z")
		self.assertEqual(movies['Quoted "Movie"']["description"], 'A "quoted" description')
		self.assertIsNone(movies["No date"]["addedDate"])

	def test_missing_column_preserves_previous_export(self):
		self.output.write_text("previous export")
		result = self.export("Title\t2020\tDrama\tDescription\tDirector\n")
		self.assertNotEqual(result.returncode, 0)
		self.assertIn("expected 6", result.stderr)
		self.assertEqual(self.output.read_text(), "previous export")

	def test_applescript_formats_dates_in_utc(self):
		helpers = (ROOT / "mac/tv_movies.applescript").read_text().split("on run\n", 1)[0]
		script = helpers + '''
set formatter to current application's NSISO8601DateFormatter's alloc()'s init()
set results to {}
repeat with epochSeconds in {0, 1577880000, 1593604800}
	set sampleDate to (current application's NSDate's dateWithTimeIntervalSince1970:epochSeconds) as date
	set end of results to my formatAddedDate(sampleDate, formatter)
end repeat
set end of results to "missing=" & my formatAddedDate(missing value, formatter)
set AppleScript's text item delimiters to linefeed
return results as text
'''
		result = subprocess.run(["/usr/bin/osascript", "-"], input=script,
			capture_output=True, text=True)
		self.assertEqual(result.returncode, 0, result.stderr)
		self.assertEqual(result.stdout.splitlines(), ["1970-01-01T00:00:00Z",
			"2020-01-01T12:00:00Z", "2020-07-01T12:00:00Z", "missing="])
