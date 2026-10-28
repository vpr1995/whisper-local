# tests/test_dashboard_settings.py
# Tests for the dashboard's Settings API (dashboard/api/settings.py): the schema
# builds, saves validate and coerce, values round-trip through user_settings.yaml
# in a temporary config directory, and restart-vs-hot-reload is reported correctly.
# The real ~/.whisperkey is never touched and autostart is always mocked.

import os
import sys
import tempfile
import unittest
import unittest.mock as mock
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))


class SettingsApiTests(unittest.TestCase):
    def setUp(self):
        from whisper_key.dashboard.api import DashboardApi, settings as settings_module
        self.settings_module = settings_module
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

        # Point every "where is the config dir" lookup at the temp directory.
        for target in ('whisper_key.config_manager.get_user_app_data_path',
                       'whisper_key.utils.get_user_app_data_path'):
            patcher = mock.patch(target, return_value=self.tmp.name)
            patcher.start()
            self.addCleanup(patcher.stop)
        # No audio hardware in tests; autostart must never touch the real OS.
        patcher = mock.patch.object(settings_module, '_microphone_options',
                                    return_value=[{'value': 'default', 'label': 'System default'},
                                                  {'value': '3', 'label': 'Test mic'}])
        patcher.start()
        self.addCleanup(patcher.stop)
        import whisper_key.autostart as real_autostart
        self.autostart = mock.Mock(spec=real_autostart)
        mock.patch.dict(sys.modules, {'whisper_key.autostart': self.autostart}).start()
        mock.patch.object(sys.modules['whisper_key'], 'autostart', self.autostart, create=True).start()
        self.autostart.is_supported.return_value = True
        self.autostart.is_enabled.return_value = False
        self.autostart.enable.return_value = True
        self.autostart.disable.return_value = True
        self.addCleanup(mock.patch.stopall)

        self.api = DashboardApi()

    # The settings file's overrides as a plain dict ({} when nothing is saved).
    def saved(self):
        from ruamel.yaml import YAML
        path = os.path.join(self.tmp.name, 'user_settings.yaml')
        with open(path, encoding='utf-8') as f:
            return YAML().load(f) or {}

    def row(self, path):
        for section in self.api.get_settings_schema()['sections']:
            for row in section['rows']:
                if row['path'] == path:
                    return row
        self.fail(f'{path} not in schema')

    # ── Schema ──

    def test_schema_builds_with_values_and_sections(self):
        schema = self.api.get_settings_schema()
        paths = set()
        for section in schema['sections']:
            self.assertIn(section['view'], ['main', *schema['views']])
            for row in section['rows']:
                self.assertIn(row['control'], ('toggle', 'select', 'number', 'text', 'hotkey'))
                self.assertTrue(row['label'] and row['caption'], row['path'])
                self.assertIn('value', row)
                self.assertNotIn(row['path'], paths, 'duplicate row')
                paths.add(row['path'])
                if row['control'] == 'select':
                    self.assertIn(row['value'], [option['value'] for option in row['options']], row['path'])
        # Settings the old Tk window offered and that stayed here are still reachable.
        for kept_path in ('hotkey.recording_mode', 'clipboard.auto_paste', 'history.retention_days',
                          'stats.typing_wpm', 'audio.noise_suppression.strength', 'vad.vad_silence_timeout_seconds'):
            self.assertIn(kept_path, paths)

    def test_top_level_is_short_and_links_to_every_drill_in(self):
        schema = self.api.get_settings_schema()
        top_level = [section for section in schema['sections'] if section['view'] == 'main']
        self.assertEqual([section['title'] for section in top_level],
                         ['Shortcuts', 'Microphone', 'After you speak', 'App'])
        self.assertLessEqual(sum(len(section['rows']) for section in top_level), 16)
        # Each drill-in view is opened by exactly one "more" link and holds at least one card.
        linked_views = sorted(section['more']['view'] for section in top_level if section['more'])
        self.assertEqual(linked_views, sorted(schema['views']))
        for view in schema['views']:
            self.assertTrue([section for section in schema['sections'] if section['view'] == view], view)
        shortcut_titles = {row['label'] for section in schema['sections'] if section['view'] == 'more-shortcuts'
                           for row in section['rows']}
        self.assertTrue({'Stop', 'Cancel', 'Voice command', 'Pause shortcuts'} <= shortcut_titles)

    def test_model_and_polish_settings_moved_off_this_page(self):
        paths = {row['path'] for section in self.api.get_settings_schema()['sections'] for row in section['rows']}
        from whisper_key.settings_store import SETTING_RULES
        self.assertFalse(paths & set(SETTING_RULES), paths & set(SETTING_RULES))
        self.assertFalse(self.api.save_setting('whisper.model', 'tiny')['ok'])
        self.assertFalse(self.api.save_setting('postprocess.ollama.enabled', True)['ok'])
        self.assertEqual(self.saved() if os.path.exists(os.path.join(self.tmp.name, 'user_settings.yaml')) else {}, {})

    def test_style_page_owns_cleanup_and_style(self):
        paths = {row['path'] for section in self.api.get_settings_schema()['sections'] for row in section['rows']}
        self.assertNotIn('postprocess.cleanup', paths)
        self.assertNotIn('postprocess.style', paths)

    def test_every_catalogue_path_exists_in_the_config(self):
        from whisper_key.config_manager import ConfigManager
        config = ConfigManager(quiet=True).config
        missing = [path for path in (row['path'] for section in
                                     self.settings_module._build_catalogue(config) for row in section['rows'])
                   if path != self.settings_module.START_ON_LOGIN_PATH
                   and self.settings_module._dig(config, path) is None
                   and path != 'history.retention_days']
        self.assertEqual(missing, [])

    def test_start_on_login_reads_the_real_state(self):
        self.autostart.is_enabled.return_value = True
        self.assertTrue(self.row('app.start_on_login')['value'])

    # ── Saving ──

    def test_number_is_coerced_and_round_trips(self):
        result = self.api.save_setting('stats.typing_wpm', '55')
        self.assertEqual(result['ok'], True)
        self.assertEqual(result['value'], 55)
        self.assertEqual(self.saved()['stats']['typing_wpm'], 55)
        self.assertEqual(self.row('stats.typing_wpm')['value'], 55)

    def test_float_setting_stays_float(self):
        self.assertTrue(self.api.save_setting('audio.noise_suppression.strength', '0.5')['ok'])
        self.assertEqual(self.saved()['audio']['noise_suppression']['strength'], 0.5)

    def test_bad_numbers_are_rejected_and_not_saved(self):
        for path, bad in (('stats.typing_wpm', 'fast'), ('stats.typing_wpm', '9999'),
                          ('audio.max_duration', '2.5'), ('audio.max_duration', ''),
                          ('audio.noise_suppression.strength', '1.5')):
            result = self.api.save_setting(path, bad)
            self.assertFalse(result['ok'], (path, bad))
            self.assertTrue(result['error'])
        self.assertEqual(self.saved(), {})

    def test_blank_retention_means_keep_forever(self):
        self.assertTrue(self.api.save_setting('history.retention_days', '7')['ok'])
        self.assertEqual(self.saved()['history']['retention_days'], 7)
        self.assertTrue(self.api.save_setting('history.retention_days', '')['ok'])
        self.assertNotIn('history', self.saved())  # null is the default, so no override remains

    def test_unknown_setting_and_bad_option_are_rejected(self):
        self.assertFalse(self.api.save_setting('nope.nothing', 'x')['ok'])
        self.assertFalse(self.api.save_setting('logging.level', 'LOUD')['ok'])
        self.assertFalse(self.api.save_setting('clipboard.auto_paste', 'yes')['ok'])

    def test_microphone_must_be_a_listed_device(self):
        self.assertTrue(self.api.save_setting('audio.input_device', '3')['ok'])
        self.assertEqual(self.saved()['audio']['input_device'], 3)
        self.assertFalse(self.api.save_setting('audio.input_device', '99')['ok'])

    def test_start_on_login_calls_autostart_not_the_config(self):
        self.assertTrue(self.api.save_setting('app.start_on_login', True)['ok'])
        self.autostart.enable.assert_called_once()
        self.autostart.enable.return_value = False
        self.assertFalse(self.api.save_setting('app.start_on_login', True)['ok'])
        self.assertFalse(os.path.exists(os.path.join(self.tmp.name, 'user_settings.yaml'))
                         and self.saved().get('app'))

    # ── Hotkeys ──

    def test_hotkey_validation(self):
        with mock.patch.object(self.settings_module, '_known_key_names',
                               return_value={'ctrl', 'shift', 'option', 'space', 'z', 'fn', 'cmd', 'alt', 'esc', 'win'}):
            self.assertTrue(self.api.save_setting('hotkey.paste_last_hotkey', 'Ctrl+Shift+Z')['ok'])
            self.assertEqual(self.saved()['hotkey']['paste_last_hotkey'], 'ctrl+shift+z')
            self.assertFalse(self.api.save_setting('hotkey.paste_last_hotkey', 'ctrl++z')['ok'])
            self.assertFalse(self.api.save_setting('hotkey.paste_last_hotkey', 'ctrl+banana')['ok'])
            self.assertFalse(self.api.save_setting('hotkey.paste_last_hotkey', 'ctrl+ctrl')['ok'])
            # Optional shortcuts may be blank, required ones may not.
            self.assertTrue(self.api.save_setting('hotkey.rephrase_hotkey', '')['ok'])
            self.assertFalse(self.api.save_setting('hotkey.recording_hotkey', '')['ok'])

    def test_hotkey_cannot_duplicate_another_shortcut(self):
        with mock.patch.object(self.settings_module, '_known_key_names', return_value=None):
            self.assertTrue(self.api.save_setting('hotkey.cancel_combination', 'ctrl+shift+x')['ok'])
            clash = self.api.save_setting('hotkey.paste_last_hotkey', 'shift+ctrl+x')  # same keys, any order
            self.assertFalse(clash['ok'])
            self.assertIn('already used', clash['error'])

    # ── Restart rules ──

    def test_hot_reload_keys_do_not_need_a_restart(self):
        self.assertFalse(self.api.save_setting('postprocess.capitalize_first', True)['needs_restart'])
        self.assertFalse(self.api.save_setting('history.retention_days', '30')['needs_restart'])
        self.assertFalse(self.api.save_setting('app.start_on_login', True)['needs_restart'])

    def test_startup_only_keys_need_a_restart(self):
        for path, value in (('audio.max_duration', '60'), ('stats.typing_wpm', '50'), ('overlay.enabled', False)):
            self.assertTrue(self.api.save_setting(path, value)['needs_restart'], path)

    def test_schema_flags_match_save_results(self):
        self.assertFalse(self.row('postprocess.capitalize_first')['needs_restart'])
        self.assertTrue(self.row('hotkey.recording_hotkey')['needs_restart'])

    # ── Whole-file actions ──

    def test_reset_keeps_hotwords_and_drops_overrides(self):
        from ruamel.yaml import YAML
        self.api.save_setting('stats.typing_wpm', '60')
        path = os.path.join(self.tmp.name, 'user_settings.yaml')
        data = self.saved()
        data.setdefault('whisper', {})['hotwords'] = ['Kubernetes']
        with open(path, 'w', encoding='utf-8') as f:
            YAML().dump(data, f)
        result = self.api.reset_settings()
        self.assertTrue(result['ok'] and result['needs_restart'])
        after = self.saved()
        self.assertEqual(list(after['whisper']['hotwords']), ['Kubernetes'])
        self.assertNotIn('stats', after)


class MovedSettingsStoreTests(unittest.TestCase):
    """The rules the Models and Style pages reuse for settings that left this page."""

    def setUp(self):
        from whisper_key import settings_store
        from whisper_key.config_manager import ConfigManager
        self.store = settings_store
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        patcher = mock.patch('whisper_key.config_manager.get_user_app_data_path', return_value=self.tmp.name)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.cm = ConfigManager(quiet=True)

    def saved(self):
        from ruamel.yaml import YAML
        with open(os.path.join(self.tmp.name, 'user_settings.yaml'), encoding='utf-8') as f:
            return YAML().load(f) or {}

    def test_every_rule_path_exists_in_the_config(self):
        for path in self.store.SETTING_RULES:
            self.assertIsNotNone(self.store.get_config_value(self.cm.config, path), path)

    def test_options_and_display_values(self):
        language_values = [option['value'] for option in self.store.language_options()]
        self.assertEqual(language_values[0], 'auto')
        self.assertIn('fr', language_values)
        model_values = [option['value'] for option in self.store.model_options(self.cm.config)]
        self.assertEqual(model_values[0], 'auto')
        for path, rule in self.store.SETTING_RULES.items():
            if rule['kind'] == 'select':
                shown = self.store.display_setting_value(path, self.cm.config)
                self.assertIn(shown, [option['value'] for option in self.store.setting_options(path, self.cm.config)], path)

    def test_save_validates_coerces_and_reports_restart(self):
        saved = self.store.save_validated_setting(self.cm, 'whisper.beam_size', '3')
        self.assertEqual((saved['ok'], saved['value'], saved['needs_restart']), (True, 3, True))
        self.assertEqual(self.saved()['whisper']['beam_size'], 3)
        saved = self.store.save_validated_setting(self.cm, 'whisper.languages', 'EN, fr fr')
        self.assertEqual(saved['value'], 'en, fr')
        saved = self.store.save_validated_setting(self.cm, 'postprocess.ollama.model', 'qwen')
        self.assertEqual((saved['ok'], saved['needs_restart']), (True, False))
        self.assertEqual(self.saved()['postprocess']['ollama']['model'], 'qwen')

    def test_bad_values_are_rejected(self):
        for path, bad in (('whisper.beam_size', '2.5'), ('whisper.beam_size', '99'), ('whisper.beam_size', ''),
                          ('whisper.device', 'tpu'), ('whisper.language', 'klingon'), ('whisper.model', 'nope'),
                          ('whisper.prompt_from_selection', 'yes'), ('postprocess.ollama.endpoint', 'localhost'),
                          ('postprocess.ollama.model', ''), ('postprocess.ollama.timeout', '0'), ('whisper.nope', 'x')):
            result = self.store.save_validated_setting(self.cm, path, bad)
            self.assertFalse(result['ok'], (path, bad))
            self.assertTrue(result['error'])

    def test_a_custom_stored_model_stays_selectable(self):
        self.cm.config['whisper']['model'] = 'my-finetune'
        self.assertIn('my-finetune', [o['value'] for o in self.store.setting_options('whisper.model', self.cm.config)])


if __name__ == '__main__':
    unittest.main()
