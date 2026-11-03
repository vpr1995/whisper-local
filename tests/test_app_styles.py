# tests/test_app_styles.py
# Style per app: an app with a row in postprocess.app_styles gets that style,
# and every other app keeps the global postprocess.style.

import unittest

from whisper_key.styles import resolve_style, with_app_style

CONFIG = {
    'style': 'formal',
    'app_styles': {
        'com.tinyspeck.slackmacgap': {'name': 'Slack', 'style': 'casual'},
        'code.exe': {'name': 'Visual Studio Code', 'style': 'verbatim'},
        'broken.exe': {'name': 'Broken', 'style': 'deleted_custom_style'},
    },
}


class AppWithRowTests(unittest.TestCase):
    def test_app_with_row_gets_its_style(self):
        self.assertEqual(with_app_style(CONFIG, 'com.tinyspeck.slackmacgap')['style'], 'casual')
        self.assertEqual(with_app_style(CONFIG, 'code.exe')['style'], 'verbatim')

    def test_app_id_case_does_not_matter(self):
        self.assertEqual(with_app_style(CONFIG, 'Code.EXE')['style'], 'verbatim')

    def test_input_config_is_not_changed(self):
        with_app_style(CONFIG, 'code.exe')
        self.assertEqual(CONFIG['style'], 'formal')

    def test_unknown_style_in_row_leaves_formatting_unchanged(self):
        # resolve_style logs the unknown name and returns no toggles.
        style = with_app_style(CONFIG, 'broken.exe')['style']
        self.assertEqual(resolve_style(style, CONFIG), {})


class AppWithoutRowTests(unittest.TestCase):
    def test_app_without_row_keeps_global_style(self):
        self.assertEqual(with_app_style(CONFIG, 'notes.exe')['style'], 'formal')

    def test_empty_app_id_keeps_global_style(self):
        self.assertEqual(with_app_style(CONFIG, '')['style'], 'formal')
        self.assertEqual(with_app_style(CONFIG, None)['style'], 'formal')

    def test_missing_or_bad_app_styles_keeps_global_style(self):
        self.assertEqual(with_app_style({'style': 'casual'}, 'code.exe')['style'], 'casual')
        self.assertEqual(with_app_style({'style': 'casual', 'app_styles': None}, 'code.exe')['style'], 'casual')
        bad_row = {'style': 'casual', 'app_styles': {'code.exe': 'verbatim'}}
        self.assertEqual(with_app_style(bad_row, 'code.exe')['style'], 'casual')


if __name__ == '__main__':
    unittest.main()
