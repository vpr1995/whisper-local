# Tests for the dashboard's Models, Style and About API mixins. Heavy dependencies
# (the config on disk, model downloads, the doctor, the self-test stages) are mocked,
# and nothing here writes to the real ~/.whisperkey or touches the network.

import contextlib
import tempfile
import threading
import time
import unittest
import unittest.mock as mock
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from whisper_key.dashboard.api import about as about_api  # noqa: E402
from whisper_key.dashboard.api import models as models_api  # noqa: E402
from whisper_key.dashboard.api import style as style_api  # noqa: E402


# ── Shared fakes ─────────────────────────────────────────────────────────────

# Stand-in for ConfigManager: a plain dict plus a recording update_user_setting.
class FakeConfig:
    def __init__(self, whisper=None, postprocess=None):
        self.config = {'whisper': whisper or {}, 'postprocess': postprocess or {}, 'hotkey': {}}
        self.update_user_setting = mock.MagicMock()

    def get_whisper_config(self):
        return dict(self.config['whisper'])

    def get_log_file_path(self):
        return '/nonexistent/app.log'


WHISPER_CONFIG = {
    'backend': 'faster_whisper', 'device': 'cpu', 'model': 'auto', 'language': 'en',
    'models': {
        'base': {'label': 'Base (141MB)', 'group': 'official'},
        'tiny.en': {'label': 'Tiny.En (English, 74MB)', 'group': 'custom'},
        'parakeet-v3': {'source': 'csukuangfj/sherpa-onnx-nemo-parakeet-tdt-0.6b-v3-int8',
                        'label': 'Parakeet V3 (25 European languages, 670MB)', 'group': 'parakeet',
                        'backend': 'parakeet', 'files': {'encoder': 'encoder.int8.onnx'}},
    },
}


# A page API with its config replaced by `fake_config`.
def make_api(mixin_class, fake_config):
    class TestApi(mixin_class):
        def _load_config(self):
            return fake_config
    return TestApi()


# ── Models ───────────────────────────────────────────────────────────────────

class ModelLabelParsingTests(unittest.TestCase):
    def test_size_and_default_language(self):
        self.assertEqual(models_api.parse_model_label('Base (141MB)', 'official'), ('Base', 'Multilingual', '141MB'))

    def test_language_and_size(self):
        self.assertEqual(models_api.parse_model_label('Tiny.En (English, 74MB)', 'custom'), ('Tiny.En', 'English', '74MB'))
        self.assertEqual(models_api.parse_model_label('Parakeet V3 (25 European languages, 670MB)', 'parakeet'),
                         ('Parakeet V3', '25 European languages', '670MB'))

    def test_label_without_parentheses(self):
        self.assertEqual(models_api.parse_model_label('My Model', 'custom'), ('My Model', '', ''))

    def test_blurbs(self):
        self.assertIn('Fastest', models_api.model_blurb('tiny'))
        self.assertIn('English-only', models_api.model_blurb('small.en'))
        self.assertEqual(models_api.model_blurb('my-own-model'), '')


class DownloadTrackerTests(unittest.TestCase):
    def wait_until_not_running(self, tracker):
        deadline = time.time() + 3
        while tracker.snapshot()['state'] == 'running' and time.time() < deadline:
            time.sleep(0.01)

    def test_progress_then_done(self):
        tracker = models_api.DownloadTracker()
        release = threading.Event()

        def run(on_progress):
            on_progress(50, 200)
            release.wait(2)

        self.assertTrue(tracker.start('base', 'Base', run))
        deadline = time.time() + 2
        while tracker.snapshot()['done_bytes'] != 50 and time.time() < deadline:
            time.sleep(0.01)
        snapshot = tracker.snapshot()
        self.assertEqual((snapshot['state'], snapshot['percent'], snapshot['key']), ('running', 25, 'base'))
        self.assertFalse(tracker.start('tiny', 'Tiny', run), "a second download must be refused")
        release.set()
        self.wait_until_not_running(tracker)
        self.assertEqual(tracker.snapshot()['state'], 'done')
        self.assertEqual(tracker.snapshot()['percent'], 100)
        tracker.acknowledge()
        self.assertEqual(tracker.snapshot()['state'], 'idle')

    def test_failure_is_reported(self):
        tracker = models_api.DownloadTracker()

        def run(on_progress):
            raise RuntimeError("no network")

        tracker.start('base', 'Base', run)
        self.wait_until_not_running(tracker)
        snapshot = tracker.snapshot()
        self.assertEqual((snapshot['state'], snapshot['error']), ('error', 'no network'))

    def test_unknown_total_has_no_percent(self):
        tracker = models_api.DownloadTracker()
        release = threading.Event()
        tracker.start('base', 'Base', lambda on_progress: (on_progress(10, 0), release.wait(2)))
        time.sleep(0.1)
        self.assertIsNone(tracker.snapshot()['percent'])
        release.set()


@unittest.skipUnless(
    __import__('importlib').util.find_spec('faster_whisper'), "faster-whisper not installed")
class ModelsApiTests(unittest.TestCase):
    def setUp(self):
        from whisper_key.model_registry import ModelRegistry
        cached = mock.patch.object(ModelRegistry, 'is_model_cached', lambda self, key: key == 'base')
        pick = mock.patch('whisper_key.auto_model.pick_model', return_value=('base', 'covers en'))
        self.addCleanup(cached.stop)
        self.addCleanup(pick.stop)
        cached.start()
        pick.start()
        self.config = FakeConfig(whisper=dict(WHISPER_CONFIG))
        self.api = make_api(models_api.ModelsApi, self.config)

    def test_groups_and_status(self):
        data = self.api.get_models()
        self.assertEqual([group['id'] for group in data['groups']], ['official', 'custom', 'parakeet'])
        base = data['groups'][0]['models'][0]
        self.assertEqual((base['name'], base['size'], base['cached']), ('Base', '141MB', True))
        # model: auto resolves to the mocked pick, which is therefore "auto-picked" and in use
        self.assertTrue(data['auto_selected'])
        self.assertTrue(base['in_use'] and base['auto_picked'] and not base['selected'])
        tiny = data['groups'][1]['models'][0]
        self.assertEqual((tiny['languages'], tiny['cached']), ('English', False))
        self.assertTrue(tiny['downloadable'])
        self.assertEqual(data['auto']['key'], 'base')

    def test_tiles_get_a_short_tagline(self):
        data = self.api.get_models()
        self.assertEqual(data['groups'][0]['models'][0]['tagline'], 'Balanced')  # curated
        self.assertEqual(data['groups'][1]['models'][0]['tagline'], 'English')   # falls back to language
        self.assertIsNone(data['downloading'])

    def test_explicit_model_is_selected(self):
        self.config.config['whisper']['model'] = 'tiny.en'
        data = self.api.get_models()
        tiny = data['groups'][1]['models'][0]
        self.assertTrue(tiny['selected'] and tiny['in_use'] and not tiny['auto_picked'])
        self.assertFalse(data['auto_selected'])

    def test_save_model_setting_writes_model(self):
        result = self.api.save_model_setting('whisper.model', 'tiny.en')
        self.assertTrue(result['ok'] and result['needs_restart'])
        self.config.update_user_setting.assert_called_once_with('whisper', 'model', 'tiny.en')

    def test_save_model_setting_accepts_auto_and_rejects_unknown(self):
        self.assertTrue(self.api.save_model_setting('whisper.model', 'auto')['ok'])
        self.config.update_user_setting.reset_mock()
        self.assertFalse(self.api.save_model_setting('whisper.model', 'no-such-model')['ok'])
        self.config.update_user_setting.assert_not_called()

    def test_save_model_setting_validates_values(self):
        self.assertFalse(self.api.save_model_setting('whisper.device', 'tpu')['ok'])
        self.assertFalse(self.api.save_model_setting('whisper.beam_size', 99)['ok'])
        self.assertFalse(self.api.save_model_setting('whisper.beam_size', '2.5')['ok'])
        self.config.update_user_setting.assert_not_called()
        self.assertTrue(self.api.save_model_setting('whisper.device', 'cuda')['ok'])
        self.config.update_user_setting.assert_called_once_with('whisper', 'device', 'cuda')

    def test_save_model_setting_stores_spoken_languages_as_list(self):
        result = self.api.save_model_setting('whisper.languages', 'en, fr')
        self.assertTrue(result['ok'])
        self.config.update_user_setting.assert_called_once_with('whisper', 'languages', ['en', 'fr'])

    def test_save_model_setting_refuses_other_settings(self):
        result = self.api.save_model_setting('hotkey.recording_hotkey', 'ctrl+x')
        self.assertFalse(result['ok'])
        self.config.update_user_setting.assert_not_called()

    def test_get_model_settings_lists_values_and_choices(self):
        self.config.config['whisper'].update(languages=['fr', 'sv'], beam_size=5)
        settings = self.api.get_model_settings()
        self.assertNotIn('whisper.model', settings)  # the tile grid owns it
        self.assertEqual(settings['whisper.device']['value'], 'cpu')
        self.assertEqual([option['value'] for option in settings['whisper.device']['options']], ['cpu', 'cuda'])
        self.assertEqual(settings['whisper.beam_size']['max'], 10)
        spoken = settings['whisper.languages']
        self.assertEqual(spoken['value'], ['fr', 'sv'])
        choice_codes = [option['value'] for option in spoken['options']]
        self.assertNotIn('auto', choice_codes)
        self.assertIn('sv', choice_codes, "a stored code outside the dropdown still shows as a chip")
        self.assertEqual(settings['whisper.language']['options'][0]['value'], 'auto')

    def test_download_runs_in_background_and_reports_progress(self):
        tracker = models_api.DownloadTracker()
        calls = []

        def fake_download(repo_id, label, allow_patterns=None, on_progress=None):
            calls.append((repo_id, allow_patterns))
            on_progress(10, 100)

        with mock.patch.object(models_api, '_download_tracker', tracker), \
             mock.patch('whisper_key.model_download.download_snapshot', fake_download):
            self.assertTrue(self.api.start_model_download('tiny.en')['ok'])
            deadline = time.time() + 3
            while tracker.snapshot()['state'] == 'running' and time.time() < deadline:
                time.sleep(0.01)
            progress = self.api.get_download_progress()
            self.assertEqual(self.api.get_download_progress()['state'], 'idle', "done is reported once")
        self.assertEqual(progress['state'], 'done')
        repo_id, patterns = calls[0]
        self.assertTrue(repo_id.startswith('Systran/') and 'model.bin' in patterns)

    def test_cannot_download_unknown_model(self):
        self.assertFalse(self.api.start_model_download('nope')['ok'])

    def test_open_cache_folder_needs_existing_folder(self):
        with tempfile.TemporaryDirectory() as tmp, \
             mock.patch('whisper_key.model_registry.ModelRegistry.get_hf_cache_path', return_value=tmp), \
             mock.patch('whisper_key.utils.open_file') as open_file:
            self.assertTrue(self.api.open_model_cache_folder())
            open_file.assert_called_once_with(tmp)
        with mock.patch('whisper_key.model_registry.ModelRegistry.get_hf_cache_path', return_value='/no/such/dir'):
            self.assertFalse(self.api.open_model_cache_folder())


# ── Style ────────────────────────────────────────────────────────────────────

class StyleHelperTests(unittest.TestCase):
    def test_describe_profile_overrides(self):
        sentences = style_api.describe_profile_overrides({
            'whisper': {'model': 'base', 'task': 'transcribe', 'initial_prompt': ''},
            'clipboard': {'auto_paste': False, 'delivery_method': 'paste'},
            'custom': {'knob': 3},
        })
        self.assertEqual(sentences, ['Model: base', 'Copies to the clipboard only', 'custom.knob = 3'])

    def test_translate_task_is_described(self):
        self.assertEqual(style_api.describe_override('whisper', 'task', 'translate'), 'Translates speech to English')

    def test_summarize_rule(self):
        summary = style_api.summarize_rule({'id': 'chat-apps', 'match': ['slack.exe'], 'auto_send': True,
                                            'style': 'casual', 'auto_paste': False})
        self.assertEqual(summary['name'], 'Chat apps')
        self.assertEqual(summary['apps'], ['slack.exe'])
        self.assertEqual(summary['effects'], ['Style: Casual', 'Copies only, no paste', 'Presses Enter after pasting'])

    def test_user_rule_without_id_and_string_match(self):
        summary = style_api.summarize_rule({'match': 'obsidian.exe', 'suppress': True})
        self.assertEqual((summary['name'], summary['apps'], summary['effects']),
                         ('Your rule', ['obsidian.exe'], ['Never dictates here']))


class StyleApiTests(unittest.TestCase):
    def setUp(self):
        self.config = FakeConfig(postprocess={'style': 'casual', 'styles': {'shouty': {'capitalize_first': True}}})
        self.api = make_api(style_api.StyleApi, self.config)

    def test_writing_styles_include_custom_and_current(self):
        data = self.api.get_writing_styles()
        ids = [style['id'] for style in data['styles']]
        self.assertEqual(data['current'], 'casual')
        self.assertEqual(ids[:4], ['formal', 'casual', 'very_casual', 'verbatim'])
        self.assertIn('shouty', ids)
        self.assertEqual(next(s for s in data['styles'] if s['id'] == 'very_casual')['name'], 'Very casual')

    def test_set_writing_style(self):
        self.assertEqual(self.api.set_writing_style('formal'), {'ok': True, 'restart_required': False})
        self.config.update_user_setting.assert_called_with('postprocess', 'style', 'formal')
        self.assertTrue(self.api.set_writing_style('')['ok'])
        self.config.update_user_setting.reset_mock()
        self.assertFalse(self.api.set_writing_style('bogus')['ok'])
        self.config.update_user_setting.assert_not_called()

    def test_cleanup_levels_default_and_current(self):
        data = self.api.get_cleanup_levels()  # config has no postprocess.cleanup -> app default
        self.assertEqual(data['current'], data['default'])
        self.assertEqual([level['id'] for level in data['levels']], ['none', 'light', 'medium', 'high'])
        self.assertTrue(all(level['name'] and level['description'] for level in data['levels']))
        self.config.config['postprocess']['cleanup'] = ' High '
        self.assertEqual(self.api.get_cleanup_levels()['current'], 'high')
        self.config.config['postprocess']['cleanup'] = 'bogus'
        self.assertEqual(self.api.get_cleanup_levels()['current'], data['default'])

    def test_set_cleanup_level_hot_reloads_and_rejects_unknown(self):
        self.assertEqual(self.api.set_cleanup_level('medium'), {'ok': True, 'restart_required': False})
        self.config.update_user_setting.assert_called_with('postprocess', 'cleanup', 'medium')
        self.config.update_user_setting.reset_mock()
        self.assertFalse(self.api.set_cleanup_level('bogus')['ok'])
        self.config.update_user_setting.assert_not_called()

    def test_profiles_list_and_apply(self):
        with tempfile.TemporaryDirectory() as tmp, \
             mock.patch('whisper_key.profiles.get_user_app_data_path', return_value=tmp):
            data = self.api.get_profiles()
            ids = [profile['id'] for profile in data['profiles']]
            self.assertEqual(ids[:2], ['dictation', 'chat'])
            self.assertEqual(data['active'], 'dictation')
            self.assertEqual(sum(profile['active'] for profile in data['profiles']), 1)

            result = self.api.set_profile('code')
            self.assertEqual(result, {'ok': True, 'restart_required': True})
            self.config.update_user_setting.assert_any_call('clipboard', 'auto_paste', False)
            self.assertEqual(self.api.get_profiles()['active'], 'code', "the active profile must persist")
            self.assertFalse(self.api.set_profile('no-such-profile')['ok'])

    # The real Apple Intelligence answer depends on the test machine, so pin it.
    def _with_apple(self, available, reason=''):
        return mock.patch('whisper_key.ai_provider.apple_availability', return_value=(available, reason))

    def test_ai_settings_defaults_and_values(self):
        with self._with_apple(False, 'not_macos'):
            data = self.api.get_ai_settings()
        self.assertEqual((data['provider'], data['model'], data['endpoint'], data['timeout']), ('auto', '', '', 5))
        self.assertEqual(data['active_provider'], 'ollama')
        self.assertEqual(data['status_text'], "Apple Intelligence is only on Macs. Using Ollama.")
        self.config.config['postprocess']['ai'] = {'provider': 'auto', 'polish': True}
        self.config.config['postprocess']['ollama'] = {
            'model': 'llama3.2', 'endpoint': 'http://localhost:11434', 'timeout': 8}
        with self._with_apple(True):
            data = self.api.get_ai_settings()
        self.assertEqual((data['model'], data['timeout']), ('llama3.2', 8))
        self.assertEqual(data['active_provider'], 'apple_intelligence')
        self.assertEqual(data['status_text'], "Using Apple Intelligence (on this Mac, offline).")

    def test_status_text_when_ollama_is_chosen_or_apple_is_off(self):
        self.config.config['postprocess']['ai'] = {'provider': 'ollama'}
        with self._with_apple(True):
            self.assertEqual(self.api.get_ai_settings()['status_text'], "Using Ollama.")
        self.config.config['postprocess']['ai'] = {'provider': 'apple_intelligence'}
        with self._with_apple(False, 'apple_intelligence_off'):
            self.assertEqual(self.api.get_ai_settings()['status_text'],
                             "Apple Intelligence is turned off in System Settings. Using Ollama.")

    # update_user_setting that really stores the value, like ConfigManager does.
    def _store_writes(self):
        def store(section, key, value):
            self.config.config.setdefault(section, {})[key] = value
        self.config.update_user_setting.side_effect = store

    def test_set_ai_setting_merges_into_nested_dict_and_returns_fresh_settings(self):
        self._store_writes()
        self.config.config['postprocess']['ai'] = {'provider': 'auto', 'polish': False, 'polish_prompt': 'p {text}'}
        self.config.config['postprocess']['ollama'] = {'model': 'llama3.2', 'timeout': 5}
        with self._with_apple(True):
            result = self.api.set_ai_setting('polish', True)
            self.assertEqual((result['ok'], result['restart_required']), (True, False))
            self.config.update_user_setting.assert_called_with(
                'postprocess', 'ai', {'provider': 'auto', 'polish': True, 'polish_prompt': 'p {text}'})
            result = self.api.set_ai_setting('provider', 'ollama')
            self.assertEqual(result['settings']['active_provider'], 'ollama')
            self.assertTrue(self.api.set_ai_setting('timeout', ' 12 ')['ok'])
        self.config.update_user_setting.assert_called_with('postprocess', 'ollama', {'model': 'llama3.2', 'timeout': 12})

    # Runs `body` with the user's app data folder (and so app_rules.yaml) in a temp dir.
    def _in_temp_app_data(self, body):
        with tempfile.TemporaryDirectory() as tmp, \
             mock.patch('whisper_key.app_rules.get_user_app_data_path', return_value=tmp), \
             self._with_apple(True):
            body(Path(tmp))

    def test_polish_categories_default_to_email_on_and_the_rest_off(self):
        def body(_):
            categories = {c['id']: c for c in self.api.get_ai_settings()['polish_in']}
            self.assertEqual([c['label'] for c in categories.values()], ["Email apps", "Chat apps", "Everywhere else"])
            self.assertEqual({k: c['enabled'] for k, c in categories.items()},
                             {'email': True, 'chat': False, 'everywhere': False})
        self._in_temp_app_data(body)

    def test_unset_app_category_follows_the_global_setting(self):
        def body(_):
            self.config.config['postprocess']['ai'] = {'polish': True}
            categories = {c['id']: c['enabled'] for c in self.api.get_ai_settings()['polish_in']}
            self.assertEqual(categories, {'email': True, 'chat': True, 'everywhere': True})
        self._in_temp_app_data(body)

    def test_set_polish_for_an_app_category_writes_the_rule_override(self):
        def body(tmp):
            self.config.update_user_setting.reset_mock()
            result = self.api.set_polish_for('chat', True)
            self.assertTrue(result['ok'])
            self.assertTrue(next(c for c in result['settings']['polish_in'] if c['id'] == 'chat')['enabled'])
            self.assertIn('- id: chat-apps\n    polish: true', (tmp / 'app_rules.yaml').read_text())
            self.assertFalse(self.api.set_polish_for('email', False)['settings']['polish_in'][0]['enabled'])
            self.config.update_user_setting.assert_not_called()      # app rules, not user_settings.yaml
        self._in_temp_app_data(body)

    def test_set_polish_for_everywhere_writes_the_global_setting(self):
        def body(_):
            self.assertTrue(self.api.set_polish_for('everywhere', True)['ok'])
            self.config.update_user_setting.assert_called_with(
                'postprocess', 'ai', {'polish': True})
        self._in_temp_app_data(body)

    def test_set_polish_for_rejects_unknown_category_and_non_booleans(self):
        def body(tmp):
            self.assertFalse(self.api.set_polish_for('code', True)['ok'])
            self.assertFalse(self.api.set_polish_for('chat', 'yes')['ok'])
            self.assertFalse((tmp / 'app_rules.yaml').exists() and 'polish' in (tmp / 'app_rules.yaml').read_text())
        self._in_temp_app_data(body)

    def test_set_ai_setting_rejects_bad_values(self):
        for key, value in [('model', '  '), ('endpoint', 'localhost:11434'), ('timeout', 'soon'),
                           ('timeout', 0), ('timeout', 500), ('provider', 'openai'), ('polish', 'yes'),
                           ('bogus', 'x')]:
            self.assertFalse(self.api.set_ai_setting(key, value)['ok'], (key, value))
        self.config.update_user_setting.assert_not_called()

    def test_transforms_list_with_keys(self):
        with tempfile.TemporaryDirectory() as tmp, \
             mock.patch('whisper_key.transforms.get_user_app_data_path', return_value=tmp):
            transforms = self.api.get_transforms()
        polish = next(t for t in transforms if t['name'] == 'Polish')
        self.assertEqual(polish['keys'], ['win', 'alt', '1'])
        self.assertTrue(any(t['keys'] == [] for t in transforms))

    def test_app_rules_list(self):
        with tempfile.TemporaryDirectory() as tmp, \
             mock.patch('whisper_key.app_rules.get_user_app_data_path', return_value=tmp):
            rules = self.api.get_app_rules()
        self.assertIn('Terminals', [rule['name'] for rule in rules])

    def test_open_user_file_only_when_it_exists(self):
        with tempfile.TemporaryDirectory() as tmp, \
             mock.patch('whisper_key.utils.get_user_app_data_path', return_value=tmp), \
             mock.patch('whisper_key.utils.open_file') as open_file:
            self.assertFalse(self.api.open_transforms_file())
            (Path(tmp) / 'transforms.yaml').write_text('transforms: []')
            self.assertTrue(self.api.open_transforms_file())
            open_file.assert_called_once()


# ── About ────────────────────────────────────────────────────────────────────

SAMPLE_DOCTOR_REPORT = (
    "\n\033[1mWhisper Local — Doctor\033[0m\n========================\n\n"
    "\033[1mRuntime\033[0m\n"
    "\033[32m[OK]\033[0m  Python version  \033[2m3.12.7\033[0m\n"
    "\033[2m[i]\033[0m    Platform  \033[2mDarwin 25.0.0\033[0m\n\n"
    "\033[1mAudio\033[0m\n"
    "\033[31m[X]\033[0m   Default input device  \033[2mno input\033[0m\n"
    "\033[33m[!]\033[0m   Recent errors (last 200 lines): 2\n"
    "   \033[2m2026 ERROR first\033[0m\n"
    "   \033[2m2026 ERROR second\033[0m\n\n"
    "\033[31m\033[1m1 issue(s) found.\033[0m  Review the [X] lines above.\n"
)


class DoctorParsingTests(unittest.TestCase):
    def test_parse_sections_checks_and_details(self):
        sections = about_api.parse_doctor_report(SAMPLE_DOCTOR_REPORT)
        self.assertEqual([section['section'] for section in sections], ['Runtime', 'Audio'])
        runtime, audio = sections
        self.assertEqual(runtime['checks'][0], {'status': 'ok', 'name': 'Python version', 'detail': '3.12.7'})
        self.assertEqual(runtime['checks'][1]['status'], 'info')
        self.assertEqual(audio['checks'][0], {'status': 'fail', 'name': 'Default input device', 'detail': 'no input'})
        self.assertEqual(audio['checks'][1]['status'], 'warn')
        self.assertEqual(audio['checks'][1]['detail'], '2026 ERROR first\n2026 ERROR second')

    def test_counts(self):
        counts = about_api.count_doctor_statuses(about_api.parse_doctor_report(SAMPLE_DOCTOR_REPORT))
        self.assertEqual(counts, {'ok': 1, 'warn': 1, 'fail': 1, 'info': 1})

    def test_empty_report(self):
        self.assertEqual(about_api.parse_doctor_report(''), [])


class SelfTestRunTests(unittest.TestCase):
    def wait_until_done(self, run):
        deadline = time.time() + 3
        while run.snapshot()['state'] != 'done' and time.time() < deadline:
            time.sleep(0.01)

    def test_stages_report_in_order_with_failures_and_crashes(self):
        def crashing_stage():
            raise ValueError("boom")

        stages = [
            ('a', 'Stage A', lambda: (True, 'fine')),
            ('b', 'Stage B', lambda: (False, ('it broke', 'try X'))),
            ('c', 'Stage C', crashing_stage),
        ]
        run = about_api.SelfTestRun()
        self.assertTrue(run.start(stages))
        self.wait_until_done(run)
        snapshot = run.snapshot()
        self.assertEqual(snapshot['state'], 'done')
        self.assertEqual([stage['status'] for stage in snapshot['stages']], ['ok', 'fail', 'fail'])
        self.assertEqual(snapshot['stages'][0]['detail'], 'fine')
        self.assertEqual((snapshot['stages'][1]['detail'], snapshot['stages'][1]['fix']), ('it broke', 'try X'))
        self.assertIn('boom', snapshot['stages'][2]['detail'])


class AboutApiTests(unittest.TestCase):
    def setUp(self):
        self.config = FakeConfig()
        self.api = make_api(about_api.AboutApi, self.config)

    def test_run_diagnostics_captures_doctor_output(self):
        def fake_doctor():
            print(SAMPLE_DOCTOR_REPORT)
            return 1

        with mock.patch('whisper_key.doctor.run_doctor', fake_doctor):
            report = self.api.run_diagnostics()
        self.assertEqual(report['counts']['fail'], 1)
        self.assertEqual(len(report['sections']), 2)

    def test_bundle_logs_goes_to_desktop(self):
        with tempfile.TemporaryDirectory() as home:
            (Path(home) / 'Desktop').mkdir()
            written = []

            def fake_bundle(output_path):
                written.append(output_path)
                Path(output_path).write_bytes(b'zip')
                print("noise that must not reach the real stdout")
                return 0

            with mock.patch.object(Path, 'home', return_value=Path(home)), \
                 mock.patch('whisper_key.bundle_logs.bundle_logs', fake_bundle):
                result = self.api.bundle_logs_to_desktop()
            self.assertTrue(result['ok'])
            self.assertEqual(result['folder'], str(Path(home) / 'Desktop'))
            self.assertTrue(result['path'].endswith('.zip') and Path(result['path']).exists())
            self.assertEqual(written, [result['path']])

    def test_bundle_output_folder_falls_back_to_home(self):
        with tempfile.TemporaryDirectory() as home, mock.patch.object(Path, 'home', return_value=Path(home)):
            self.assertEqual(about_api.bundle_output_folder(), Path(home))

    def test_bundle_failure_is_reported(self):
        with tempfile.TemporaryDirectory() as home, \
             mock.patch.object(Path, 'home', return_value=Path(home)), \
             mock.patch('whisper_key.bundle_logs.bundle_logs', side_effect=OSError("disk full")):
            result = self.api.bundle_logs_to_desktop()
        self.assertEqual((result['ok'], result['error']), (False, 'disk full'))

    def test_selftest_refuses_without_model(self):
        with mock.patch.object(self.api, '_selected_model_is_cached', return_value=False):
            result = self.api.start_selftest()
        self.assertFalse(result['ok'])
        self.assertIn('Download', result['error'])

    def test_selftest_runs_stages(self):
        fake_run = about_api.SelfTestRun()
        stages = [('a', 'A', lambda: (True, 'ok'))]
        with mock.patch.object(self.api, '_selected_model_is_cached', return_value=True), \
             mock.patch.object(about_api, '_selftest_run', fake_run), \
             mock.patch('whisper_key.selftest.STAGES', stages):
            self.assertTrue(self.api.start_selftest()['ok'])
            deadline = time.time() + 3
            while self.api.get_selftest_progress()['state'] != 'done' and time.time() < deadline:
                time.sleep(0.01)
            self.assertEqual(self.api.get_selftest_progress()['stages'][0]['status'], 'ok')

    def test_cheat_sheet_includes_unset_hotkeys(self):
        self.config.config['hotkey'] = {'recording_hotkey': 'ctrl+alt+r'}
        fake_transforms = mock.MagicMock()
        fake_transforms.return_value.list_transforms.return_value = [
            {'name': 'polish', 'hotkey': 'win+alt+1', 'prompt': 'Polish it'}]
        with mock.patch('whisper_key.transforms.TransformsManager', fake_transforms):
            rows = self.api.get_hotkey_cheat_sheet()
        record = next(row for row in rows if row['label'] == 'Record / dictate')
        self.assertEqual(record['keys'], ['ctrl', 'alt', 'r'])
        cancel = next(row for row in rows if row['label'] == 'Cancel recording')
        self.assertEqual(cancel['keys'], [])
        self.assertEqual(rows[-1]['group'], 'transform')

    def test_open_log_file_needs_a_log(self):
        with mock.patch('whisper_key.utils.open_file') as open_file:
            self.assertFalse(self.api.open_log_file())
            open_file.assert_not_called()

    def test_about_info_has_links(self):
        with mock.patch('whisper_key.utils.get_user_app_data_path', return_value='/tmp/x'):
            info = self.api.get_about_info()
        self.assertTrue(info['links']['issues'].endswith('/issues'))
        self.assertTrue(info['version'])


# ── Requirements checklist ───────────────────────────────────────────────────

from whisper_key import requirements_check  # noqa: E402


# Fake platform modules: no real permission probe, subprocess or System Settings call.
def fake_permissions(microphone='granted', accessibility=True):
    permissions = mock.MagicMock()
    permissions.check_microphone_permission.return_value = microphone
    permissions.check_accessibility_permission.return_value = accessibility
    return permissions


def fake_apple_intelligence(tools_installed=True):
    apple_intelligence = mock.MagicMock()
    apple_intelligence.command_line_tools_installed.return_value = tools_installed
    return apple_intelligence


class RequirementRowTests(unittest.TestCase):
    def test_microphone_states(self):
        granted = requirements_check._microphone_row(fake_permissions('granted'))
        self.assertEqual((granted['status'], granted['fix']), ('ok', None))
        denied = requirements_check._microphone_row(fake_permissions('denied'))
        self.assertEqual(denied['status'], 'fail')
        self.assertEqual(denied['fix']['id'], requirements_check.FIX_OPEN_MICROPHONE_SETTINGS)
        asked_later = requirements_check._microphone_row(fake_permissions('not_determined'))
        self.assertEqual((asked_later['status'], asked_later['fix']), ('warn', None))

    def test_accessibility_missing_offers_settings(self):
        row = requirements_check._accessibility_row(fake_permissions(accessibility=False))
        self.assertEqual(row['status'], 'fail')
        self.assertEqual(row['fix']['id'], requirements_check.FIX_OPEN_ACCESSIBILITY_SETTINGS)

    def test_missing_model_opens_models_page(self):
        with mock.patch.object(requirements_check, 'selected_speech_model', return_value=('base', False)):
            row = requirements_check._speech_model_row({})
        self.assertEqual(row['status'], 'fail')
        self.assertEqual(row['fix'], {'kind': 'page', 'target': 'models', 'label': 'Open Models'})
        with mock.patch.object(requirements_check, 'selected_speech_model', return_value=('base', True)):
            self.assertEqual(requirements_check._speech_model_row({})['status'], 'ok')

    def apple_row(self, availability, tools_installed=True):
        with mock.patch('whisper_key.ai_provider.apple_availability', return_value=availability):
            return requirements_check._apple_intelligence_row(fake_apple_intelligence(tools_installed), False)

    def test_apple_intelligence_ready(self):
        row = self.apple_row((True, ''))
        self.assertEqual((row['status'], row['fix']), ('ok', None))

    def test_apple_intelligence_needs_tools(self):
        # The dashboard process never ran the helper build, so "not built" + no tools = tools missing.
        row = self.apple_row((False, 'not_built'), tools_installed=False)
        self.assertEqual(row['status'], 'warn')
        self.assertEqual(row['fix']['id'], requirements_check.FIX_INSTALL_COMMAND_LINE_TOOLS)
        self.assertIn('xcode-select --install', row['detail'])

    def test_apple_intelligence_off_opens_settings(self):
        row = self.apple_row((False, 'apple_intelligence_off'))
        self.assertEqual(row['fix']['id'], requirements_check.FIX_OPEN_APPLE_INTELLIGENCE_SETTINGS)

    def test_apple_intelligence_states_without_a_fix(self):
        for reason in ('needs_macos_26', 'needs_apple_silicon', 'device_not_eligible', 'model_not_ready', 'not_built'):
            row = self.apple_row((False, reason))
            self.assertEqual((row['status'], row['fix']), ('warn', None), reason)

    def test_ollama_unreachable_links_to_download(self):
        with mock.patch('whisper_key.ai_provider.probe_ollama', return_value=(False, [])):
            row = requirements_check._ollama_row({'ollama': {'endpoint': 'http://example.invalid:1'}})
        self.assertEqual(row['status'], 'warn')
        self.assertIn('example.invalid', row['detail'])
        self.assertEqual(row['fix']['url'], requirements_check.OLLAMA_DOWNLOAD_URL)
        with mock.patch('whisper_key.ai_provider.probe_ollama', return_value=(True, ['llama3.2'])):
            self.assertEqual(requirements_check._ollama_row({})['status'], 'ok')


class CollectRequirementsTests(unittest.TestCase):
    def collect(self, is_macos, ollama_in_use):
        config = FakeConfig()
        config.get_postprocess_config = lambda: {}
        with mock.patch('whisper_key.platform.IS_MACOS', is_macos), \
             mock.patch('whisper_key.platform.permissions', fake_permissions(), create=True), \
             mock.patch('whisper_key.platform.apple_intelligence', fake_apple_intelligence(), create=True), \
             mock.patch('whisper_key.ai_provider.apple_availability', return_value=(True, '')), \
             mock.patch('whisper_key.ai_provider.ollama_in_use', return_value=ollama_in_use), \
             mock.patch('whisper_key.ai_provider.probe_ollama', return_value=(True, [])), \
             mock.patch.object(requirements_check, 'selected_speech_model', return_value=('base', True)):
            return [row['id'] for row in requirements_check.collect_requirements(config)]

    def test_mac_rows(self):
        self.assertEqual(self.collect(True, False), ['microphone', 'accessibility', 'speech_model', 'apple_intelligence'])

    def test_windows_rows_and_ollama_only_when_in_use(self):
        self.assertEqual(self.collect(False, False), ['microphone', 'speech_model'])
        self.assertEqual(self.collect(False, True), ['microphone', 'speech_model', 'ollama'])

    def test_crashing_check_becomes_a_warning_row(self):
        config = FakeConfig()
        config.get_postprocess_config = lambda: {}
        broken_permissions = mock.MagicMock()
        broken_permissions.check_microphone_permission.side_effect = RuntimeError("boom")
        with mock.patch('whisper_key.platform.IS_MACOS', False), \
             mock.patch('whisper_key.platform.permissions', broken_permissions, create=True), \
             mock.patch('whisper_key.platform.apple_intelligence', fake_apple_intelligence(), create=True), \
             mock.patch('whisper_key.ai_provider.ollama_in_use', return_value=False), \
             mock.patch.object(requirements_check, 'selected_speech_model', return_value=('base', True)):
            rows = requirements_check.collect_requirements(config)
        self.assertEqual((rows[0]['id'], rows[0]['status']), ('microphone', 'warn'))
        self.assertEqual(rows[1]['status'], 'ok')


class RequirementFixTests(unittest.TestCase):
    def run_fix(self, fix_id):
        permissions = fake_permissions()
        apple_intelligence = fake_apple_intelligence()
        with mock.patch('whisper_key.platform.permissions', permissions, create=True), \
             mock.patch('whisper_key.platform.apple_intelligence', apple_intelligence, create=True):
            return requirements_check.run_fix(fix_id), permissions, apple_intelligence

    def test_named_fixes_open_the_matching_pane(self):
        for fix_id, pane in ((requirements_check.FIX_OPEN_MICROPHONE_SETTINGS, 'microphone'),
                             (requirements_check.FIX_OPEN_ACCESSIBILITY_SETTINGS, 'accessibility'),
                             (requirements_check.FIX_OPEN_APPLE_INTELLIGENCE_SETTINGS, 'apple_intelligence')):
            started, permissions, _apple = self.run_fix(fix_id)
            self.assertTrue(started)
            permissions.open_settings_pane.assert_called_once_with(pane)

    def test_install_tools_fix(self):
        started, _permissions, apple_intelligence = self.run_fix(requirements_check.FIX_INSTALL_COMMAND_LINE_TOOLS)
        self.assertTrue(started)
        apple_intelligence.install_command_line_tools.assert_called_once_with()

    def test_unknown_fix_does_nothing(self):
        started, permissions, apple_intelligence = self.run_fix('rm -rf /')
        self.assertFalse(started)
        permissions.open_settings_pane.assert_not_called()
        apple_intelligence.install_command_line_tools.assert_not_called()

    def test_about_api_passes_check_again_through(self):
        api = make_api(about_api.AboutApi, FakeConfig())
        with mock.patch('whisper_key.requirements_check.collect_requirements', return_value=[]) as collect:
            self.assertEqual(api.get_requirements(True), [])
        self.assertTrue(collect.call_args.kwargs['force_refresh'])


class SettingsPaneWhitelistTests(unittest.TestCase):
    # Only named panes open; nothing the page sends reaches `open`.
    def test_macos_open_settings_pane_rejects_unknown_names(self):
        if sys.platform != 'darwin':
            self.skipTest("macOS only")
        from whisper_key.platform.macos import permissions as macos_permissions
        with mock.patch.object(macos_permissions.subprocess, 'run') as run:
            self.assertFalse(macos_permissions.open_settings_pane('https://evil.example'))
            run.assert_not_called()
            self.assertTrue(macos_permissions.open_settings_pane('microphone'))
            self.assertEqual(run.call_args.args[0][1], macos_permissions.SETTINGS_PANE_URLS['microphone'])


if __name__ == '__main__':
    unittest.main()
