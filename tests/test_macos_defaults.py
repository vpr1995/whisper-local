# test_macos_defaults.py
# The shipped defaults must work on a Mac, not only on Windows: " | macos:"
# values resolve inside voice commands, and a "win+..." combo means Command
# (as the dashboard draws it).

import sys
import unittest
import unittest.mock as mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from whisper_key import config_manager  # noqa: E402


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
