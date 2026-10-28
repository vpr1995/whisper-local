# test_macos_defaults.py
# The shipped defaults must work on a Mac, not only on Windows: per-app rules
# match macOS bundle ids, " | macos:" values resolve inside voice commands and
# transforms, and a "win+..." combo means Command (as the dashboard draws it).

import sys
import tempfile
import unittest
import unittest.mock as mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from whisper_key import app_rules, config_manager  # noqa: E402


class MacPerAppRuleTests(unittest.TestCase):
    # Which shipped rule a macOS app (bundle id, app name) lands in.
    def _rule_id_for(self, bundle_id: str, app_name: str):
        with tempfile.TemporaryDirectory() as data_dir, \
             mock.patch.object(app_rules, 'get_user_app_data_path', return_value=data_dir):
            rules = app_rules.AppRules()
            for rule in rules.rules:
                if rules._matches(rule, bundle_id.lower(), app_name.lower()):
                    return rule.get('id')
        return None

    def test_terminals_editors_and_password_managers_match_on_macos(self):
        self.assertEqual(self._rule_id_for('com.apple.Terminal', 'Terminal'), 'terminals')
        self.assertEqual(self._rule_id_for('com.googlecode.iterm2', 'iTerm2'), 'terminals')
        self.assertEqual(self._rule_id_for('com.microsoft.VSCode', 'Code'), 'code-editors')
        self.assertEqual(self._rule_id_for('com.jetbrains.pycharm', 'PyCharm'), 'code-editors')
        self.assertEqual(self._rule_id_for('com.1password.1password', '1Password'), 'password-managers')

    def test_chat_and_email_match_on_macos(self):
        self.assertEqual(self._rule_id_for('com.tinyspeck.slackmacgap', 'Slack'), 'chat-apps')
        self.assertEqual(self._rule_id_for('com.apple.mail', 'Mail'), 'email-clients')
        self.assertEqual(self._rule_id_for('com.microsoft.Outlook', 'Microsoft Outlook'), 'email-clients')

    def test_an_unrelated_app_matches_nothing(self):
        self.assertIsNone(self._rule_id_for('com.apple.Notes', 'Notes'))


class PlatformValueTests(unittest.TestCase):
    def test_nested_lists_and_dicts_resolve_to_the_mac_value(self):
        commands = [{'trigger': 'save', 'hotkey': 'ctrl+s | macos: cmd+s',
                     'then': [{'hotkey': 'shift+alt+f | macos: shift+option+f'}]}]
        with mock.patch.object(config_manager, 'IS_MACOS', True):
            config_manager.resolve_platform_values(commands)
        self.assertEqual(commands[0]['hotkey'], 'cmd+s')
        self.assertEqual(commands[0]['then'][0]['hotkey'], 'shift+option+f')

    def test_windows_keeps_the_first_value(self):
        command = {'run': 'calc.exe | macos: open -a Calculator'}
        with mock.patch.object(config_manager, 'IS_MACOS', False):
            config_manager.resolve_platform_values(command)
        self.assertEqual(command['run'], 'calc.exe')


@unittest.skipUnless(sys.platform == 'darwin', 'macOS hotkey parser needs Quartz')
class MacHotkeyParserTests(unittest.TestCase):
    def test_win_means_command(self):
        from whisper_key.platform.macos import hotkeys
        self.assertEqual(hotkeys._parse_hotkey_string('win+alt+1'),
                         hotkeys._parse_hotkey_string('cmd+option+1'))


if __name__ == '__main__':
    unittest.main()
