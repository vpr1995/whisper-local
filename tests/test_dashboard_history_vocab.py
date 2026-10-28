# Tests for the dashboard History and Vocabulary APIs: search/pagination/delete on a
# temp transcripts.jsonl, hotword/replacement/snippet round-trips in a temp data
# dir and friendly_app_name cases. Nothing touches the real ~/.whisperkey.

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from whisper_key import corrections, dictionary, transcript_log  # noqa: E402
from whisper_key.dashboard.api import history as history_api  # noqa: E402
from whisper_key.dashboard.api.history import HistoryApi, friendly_app_name  # noqa: E402
from whisper_key.dashboard.api.vocabulary import VocabularyApi  # noqa: E402


# Redirects every module that resolves the data dir to a throwaway directory.
class TempDataDirTestCase(unittest.TestCase):
    def setUp(self):
        self._temp = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp.cleanup)
        self.data_dir = Path(self._temp.name)
        for module in (transcript_log, dictionary, corrections):
            patcher = mock.patch.object(module, 'get_user_app_data_path', lambda: str(self.data_dir))
            patcher.start()
            self.addCleanup(patcher.stop)


# A HistoryApi whose config reads come from a dict instead of disk.
class FakeConfigHistoryApi(HistoryApi):
    history_config = None

    def _load_config(self):
        return mock.Mock(config={'history': self.history_config})


class FriendlyAppNameTests(unittest.TestCase):
    def test_macos_bundle_ids(self):
        self.assertEqual(friendly_app_name('com.microsoft.outlook'), 'Outlook')
        self.assertEqual(friendly_app_name('com.google.chrome'), 'Chrome')
        self.assertEqual(friendly_app_name('com.apple.terminal'), 'Terminal')
        self.assertEqual(friendly_app_name('com.microsoft.vscode'), 'VS Code')
        self.assertEqual(friendly_app_name('com.tinyspeck.slackmacgap'), 'Slack')

    def test_unknown_bundle_id_uses_last_segment(self):
        self.assertEqual(friendly_app_name('com.acme.fancy-tool'), 'Fancy Tool')
        self.assertEqual(friendly_app_name('org.example.sketchpad'), 'Sketchpad')

    def test_windows_exe_names(self):
        self.assertEqual(friendly_app_name('OUTLOOK.EXE'), 'Outlook')
        self.assertEqual(friendly_app_name('chrome.exe'), 'Chrome')
        self.assertEqual(friendly_app_name('Code.exe'), 'VS Code')
        self.assertEqual(friendly_app_name('WINWORD.EXE'), 'Word')
        self.assertEqual(friendly_app_name('my_cool_app.exe'), 'My Cool App')

    def test_empty_stays_empty(self):
        self.assertEqual(friendly_app_name(''), '')
        self.assertEqual(friendly_app_name(None), '')


class HistoryApiTests(TempDataDirTestCase):
    def setUp(self):
        super().setUp()
        lines = [
            {'timestamp': f'2026-10-{day:02d}T09:00:00', 'text': text, 'app': app}
            for day, text, app in [
                (1, 'first note about Kubernetes', 'com.apple.terminal'),
                (2, 'second note', 'com.microsoft.outlook'),
                (3, 'third note about kubernetes again', 'com.microsoft.outlook'),
            ]
        ]
        (self.data_dir / 'transcripts.jsonl').write_text(
            '\n'.join(json.dumps(line) for line in lines) + '\nnot json\n', encoding='utf-8')
        self.api = FakeConfigHistoryApi()

    def test_newest_first_with_friendly_names_and_word_counts(self):
        result = self.api.get_history()
        self.assertEqual(result['total'], 3)
        self.assertEqual([e['text'] for e in result['entries']][0], 'third note about kubernetes again')
        self.assertEqual(result['entries'][0]['app'], 'Outlook')
        self.assertEqual(result['entries'][0]['words'], 5)
        self.assertTrue(result['saving_enabled'])

    def test_home_recent_transcripts_use_friendly_app_names(self):
        from whisper_key.dashboard.api.home import HomeApi
        recent_apps = {entry['app'] for entry in HomeApi().get_recent_transcripts(5)}
        self.assertEqual(recent_apps, {'Outlook', 'Terminal'})

    def test_retention_parsing_is_public_and_forgiving(self):
        parse = transcript_log.parse_retention_days
        self.assertEqual((parse(None), parse(''), parse('30 days'), parse(-3)), (None, None, None, None))
        self.assertEqual((parse(0), parse('14')), (0, 14))

    def test_search_matches_text_and_app_name_case_insensitively(self):
        self.assertEqual(self.api.get_history('KUBERNETES')['matched'], 2)
        self.assertEqual(self.api.get_history('outlook')['matched'], 2)
        self.assertEqual(self.api.get_history('zzz')['entries'], [])

    def test_pagination(self):
        first = self.api.get_history('', 0, 2)
        self.assertEqual(len(first['entries']), 2)
        self.assertTrue(first['has_more'])
        second = self.api.get_history('', 2, 2)
        self.assertEqual([e['text'] for e in second['entries']], ['first note about Kubernetes'])
        self.assertFalse(second['has_more'])

    def test_delete_removes_only_the_matching_entry(self):
        second = {'timestamp': '2026-10-02T09:00:00', 'text': 'second note'}
        self.assertEqual(self.api.delete_history_entries([second]), 1)
        remaining = [e['text'] for e in self.api.get_history()['entries']]
        self.assertEqual(len(remaining), 2)
        self.assertNotIn('second note', remaining)
        self.assertEqual(self.api.delete_history_entries([second]), 0)

    def test_multi_select_delete_removes_each_picked_entry(self):
        picked = [{'timestamp': '2026-10-01T09:00:00', 'text': 'first note about Kubernetes'},
                  {'timestamp': '2026-10-03T09:00:00', 'text': 'third note about kubernetes again'}]
        self.assertEqual(self.api.delete_history_entries(picked), 2)
        self.assertEqual([e['text'] for e in self.api.get_history()['entries']], ['second note'])

    def test_identical_dictations_delete_one_per_request(self):
        log_path = self.data_dir / 'transcripts.jsonl'
        duplicate = {'timestamp': '2026-10-02T09:00:00', 'text': 'second note'}
        log_path.write_text(log_path.read_text(encoding='utf-8') + json.dumps(duplicate) + '\n', encoding='utf-8')
        self.assertEqual(self.api.delete_history_entries([duplicate]), 1)
        self.assertIn('second note', [e['text'] for e in self.api.get_history()['entries']])

    def test_select_all_deletes_every_match_including_unloaded_rows(self):
        self.assertEqual(self.api.delete_matching_history('kubernetes'), 2)
        self.assertEqual([e['text'] for e in self.api.get_history()['entries']], ['second note'])
        self.assertEqual(self.api.delete_matching_history(''), 1)
        self.assertEqual(self.api.get_history()['total'], 0)

    def test_saving_disabled_flag_follows_retention_zero(self):
        self.api.history_config = {'retention_days': 0}
        self.assertFalse(self.api.get_history()['saving_enabled'])

    def test_export_format(self):
        entries = [{'timestamp': '2026-10-01T09:00:00', 'text': 'hello', 'app': 'OUTLOOK.EXE'}]
        self.assertIn('### 2026-10-01 09:00:00 · Outlook\n\nhello', history_api._format_export(entries, True))
        self.assertIn('[2026-10-01 09:00:00 · Outlook]\nhello', history_api._format_export(entries, False))

    def test_add_correction_round_trip(self):
        self.assertTrue(self.api.add_correction('teh', 'the'))
        self.assertEqual(corrections.list_replacements(), [{'from': 'teh', 'to': 'the'}])
        self.assertFalse(self.api.add_correction('', 'x'))


class VocabularyApiTests(TempDataDirTestCase):
    def setUp(self):
        super().setUp()
        self.api = VocabularyApi()

    def test_hotword_add_remove_round_trip(self):
        self.assertTrue(self.api.add_word('Kubernetes'))
        self.assertFalse(self.api.add_word('Kubernetes'))
        self.assertEqual(self.api.get_vocabulary()['words'], ['Kubernetes'])
        self.assertTrue(self.api.remove_word('Kubernetes'))
        self.assertEqual(self.api.get_vocabulary()['words'], [])

    def test_replacement_add_update_remove(self):
        self.assertTrue(self.api.add_replacement('teh', 'the'))
        self.assertTrue(self.api.add_replacement('TEH', 'THE'))  # same rule, new target
        self.assertEqual(self.api.get_vocabulary()['replacements'], [{'from': 'TEH', 'to': 'THE'}])
        self.assertTrue(self.api.remove_replacement('teh'))
        self.assertEqual(self.api.get_vocabulary()['replacements'], [])
        self.assertFalse(self.api.remove_replacement('teh'))

    def test_snippet_save_update_remove(self):
        self.assertTrue(self.api.save_snippet('my signature', 'Best,\nRohit'))
        self.assertFalse(self.api.save_snippet('my signature', 'Best,\nRohit'))  # unchanged
        self.assertTrue(self.api.save_snippet('My Signature', 'Cheers', 'alone'))
        self.assertEqual(self.api.get_vocabulary()['snippets'],
                         [{'trigger': 'my signature', 'expansion': 'Cheers', 'mode': 'alone'}])
        self.assertTrue(self.api.remove_snippet('MY SIGNATURE'))
        self.assertEqual(self.api.get_vocabulary()['snippets'], [])
        self.assertFalse(self.api.save_snippet('', 'x'))

    def test_vocabulary_payload_lists_everything_the_page_draws(self):
        # The page draws all three cards from this one call: words sorted case-insensitively,
        # replacement rows as from/to and snippet rows with a normalised mode.
        for word in ('beta', 'Alpha', 'gamma'):
            self.api.add_word(word)
        self.api.add_replacement('teh', 'the')
        self.api.save_snippet('sig', 'Best', 'anywhere')
        vocabulary = self.api.get_vocabulary()
        self.assertEqual(vocabulary['words'], ['Alpha', 'beta', 'gamma'])
        self.assertEqual(vocabulary['replacements'], [{'from': 'teh', 'to': 'the'}])
        self.assertEqual(vocabulary['snippets'], [{'trigger': 'sig', 'expansion': 'Best', 'mode': 'anywhere'}])

    def test_edits_keep_other_settings(self):
        (self.data_dir / 'user_settings.yaml').write_text('audio:\n  host: auto\n', encoding='utf-8')
        self.api.add_word('Zed')
        self.api.save_snippet('hi', 'hello')
        text = (self.data_dir / 'user_settings.yaml').read_text(encoding='utf-8')
        self.assertIn('host: auto', text)
        self.assertIn('Zed', text)

    def test_suggest_words_shape(self):
        with mock.patch.object(dictionary, 'suggest_hotwords_from_history', return_value=[('Foo', 4)]):
            self.assertEqual(self.api.suggest_words(), [{'word': 'Foo', 'count': 4}])


if __name__ == '__main__':
    unittest.main()
