"""Exercise publishing against local Git repositories without TV or GitHub."""

import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(Path('/bin/zsh').exists(), 'Requires zsh')
class PublishTests(unittest.TestCase):
	def setUp(self):
		self.temp = tempfile.TemporaryDirectory()
		self.addCleanup(self.temp.cleanup)
		self.base = Path(self.temp.name)
		self.remote = self.base / 'remote.git'
		self.local = self.base / 'local'
		self.other = self.base / 'action'
		self.env = dict(os.environ, GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL=os.devnull,
			GIT_AUTHOR_NAME='Test', GIT_AUTHOR_EMAIL='test@example.invalid',
			GIT_COMMITTER_NAME='Test', GIT_COMMITTER_EMAIL='test@example.invalid',
			GIT_TERMINAL_PROMPT='0', GIT_EDITOR='true')
		self.git(self.base, 'init', '--bare', '--initial-branch=main', str(self.remote))
		self.git(self.base, 'clone', str(self.remote), str(self.local))
		(self.local / 'mac').mkdir()
		shutil.copy(ROOT / 'mac/publish.zsh', self.local / 'mac/publish.zsh')
		(self.local / 'mac/tv_movies.zsh').write_text('''#!/bin/zsh
set -euo pipefail
print -r -- "${PUBLISH_TEST_EXPORT:-new export}" > "$1"
if [[ -n "${PUBLISH_TEST_ACTION:-}" ]]; then
	print -r -- "concurrent enrichment" > "$PUBLISH_TEST_ACTION/movies-enriched.json"
	/usr/bin/git -C "$PUBLISH_TEST_ACTION" add movies-enriched.json
	/usr/bin/git -C "$PUBLISH_TEST_ACTION" commit -m "Concurrent enrichment"
	/usr/bin/git -C "$PUBLISH_TEST_ACTION" push
fi
''')
		(self.local / 'movies.json').write_text('initial export\n')
		(self.local / 'movies-enriched.json').write_text('initial enrichment\n')
		self.git(self.local, 'add', '.')
		self.git(self.local, 'commit', '-m', 'Initial library')
		self.git(self.local, 'push', '-u', 'origin', 'main')
		self.git(self.base, 'clone', str(self.remote), str(self.other))

	def git(self, cwd, *args, check=True):
		return subprocess.run(['/usr/bin/git', *args], cwd=cwd, env=self.env,
			capture_output=True, text=True, check=check)

	def commit_file(self, repo, name, content):
		(repo / name).write_text(content)
		self.git(repo, 'add', name)
		self.git(repo, 'commit', '-m', 'Update ' + name)
		return self.git(repo, 'rev-parse', 'HEAD').stdout.strip()

	def publish(self, **env):
		return subprocess.run(['/bin/zsh', str(self.local / 'mac/publish.zsh')],
			cwd=self.local, env=dict(self.env, **env), capture_output=True, text=True)

	def remote_content(self, name):
		return self.git(self.remote, 'show', 'main:' + name).stdout

	def assert_success(self, result):
		self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
		self.assertEqual(self.git(self.local, 'status', '--porcelain').stdout, '')
		self.assertEqual(self.git(self.local, 'rev-parse', 'HEAD').stdout,
			self.git(self.remote, 'rev-parse', 'main').stdout)

	def test_rebases_unpublished_exports_onto_enrichment_before_exporting(self):
		self.commit_file(self.local, 'movies.json', 'unpublished export one\n')
		self.commit_file(self.local, 'movies.json', 'unpublished export two\n')
		remote_commit = self.commit_file(self.other, 'movies-enriched.json', 'latest enrichment\n')
		self.git(self.other, 'push')
		self.assert_success(self.publish())
		self.assertEqual(self.remote_content('movies.json'), 'new export\n')
		self.assertEqual(self.remote_content('movies-enriched.json'), 'latest enrichment\n')
		self.git(self.local, 'merge-base', '--is-ancestor', remote_commit, 'HEAD')
		self.assertEqual(self.git(self.local, 'rev-list', '--count', 'HEAD').stdout.strip(), '5')

	def test_unchanged_export_still_pushes_pending_commit(self):
		self.commit_file(self.local, 'movies.json', 'new export\n')
		self.assert_success(self.publish())
		self.assertEqual(self.remote_content('movies.json'), 'new export\n')
		self.assertEqual(self.git(self.local, 'rev-list', '--count', 'HEAD').stdout.strip(), '2')

	def test_remote_update_during_export_is_rebased_and_retried(self):
		result = self.publish(PUBLISH_TEST_ACTION=str(self.other))
		self.assert_success(result)
		self.assertIn('retrying once', result.stderr)
		self.assertEqual(self.remote_content('movies.json'), 'new export\n')
		self.assertEqual(self.remote_content('movies-enriched.json'), 'concurrent enrichment\n')

	def test_dirty_tracked_work_is_preserved_without_export(self):
		for staged in (False, True):
			with self.subTest(staged=staged):
				(self.local / 'movies-enriched.json').write_text('user edits\n')
				if staged:
					self.git(self.local, 'add', 'movies-enriched.json')
				before = self.git(self.local, 'status', '--porcelain').stdout
				result = self.publish()
				self.assertNotEqual(result.returncode, 0)
				self.assertIn('Commit or stash', result.stderr)
				self.assertEqual(self.git(self.local, 'status', '--porcelain').stdout, before)
				self.assertEqual((self.local / 'movies-enriched.json').read_text(), 'user edits\n')
				self.assertEqual((self.local / 'movies.json').read_text(), 'initial export\n')

	def test_rebase_conflict_is_aborted_and_local_commit_preserved(self):
		local_commit = self.commit_file(self.local, 'movies.json', 'local export\n')
		self.commit_file(self.other, 'movies.json', 'different remote export\n')
		self.git(self.other, 'push')
		result = self.publish()
		self.assertNotEqual(result.returncode, 0)
		self.assertIn('local commits preserved', result.stderr)
		self.assertEqual(self.git(self.local, 'rev-parse', 'HEAD').stdout.strip(), local_commit)
		self.assertEqual((self.local / 'movies.json').read_text(), 'local export\n')
		self.assertEqual(self.git(self.local, 'status', '--porcelain').stdout, '')
		self.assertFalse((self.local / '.git/rebase-merge').exists())
		self.assertEqual(self.remote_content('movies.json'), 'different remote export\n')

	def test_existing_rebase_is_not_aborted(self):
		self.commit_file(self.local, 'movies.json', 'local export\n')
		self.commit_file(self.other, 'movies.json', 'different remote export\n')
		self.git(self.other, 'push')
		self.assertNotEqual(self.git(self.local, 'pull', '--rebase', check=False).returncode, 0)
		before = (self.local / 'movies.json').read_text()
		result = self.publish()
		self.assertNotEqual(result.returncode, 0)
		self.assertIn('existing Git operation', result.stderr)
		self.assertTrue((self.local / '.git/rebase-merge').exists())
		self.assertEqual((self.local / 'movies.json').read_text(), before)

	def test_unavailable_remote_stops_before_export(self):
		self.git(self.local, 'remote', 'set-url', 'origin', str(self.base / 'missing.git'))
		result = self.publish()
		self.assertNotEqual(result.returncode, 0)
		self.assertEqual((self.local / 'movies.json').read_text(), 'initial export\n')
		self.assertEqual(self.git(self.local, 'status', '--porcelain').stdout, '')
