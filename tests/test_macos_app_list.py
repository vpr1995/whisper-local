# tests/test_macos_app_list.py
# The macOS Style-per-app picker list: a scan of fake Applications folders must
# find every bundle with an id, one folder deep, never inside a bundle.

import plistlib
import sys
import tempfile
import unittest
from pathlib import Path


# Creates <folder>/<name>.app/Contents/Info.plist with the given keys.
def make_bundle(folder: Path, name: str, **info):
    contents = folder / f'{name}.app' / 'Contents'
    contents.mkdir(parents=True)
    with open(contents / 'Info.plist', 'wb') as plist_file:
        plistlib.dump(info, plist_file)
    return contents


@unittest.skipUnless(sys.platform == 'darwin', 'macOS app scan')
class InstalledAppScanTests(unittest.TestCase):
    def setUp(self):
        from whisper_key.platform.macos.apps import list_pickable_apps
        self.list_pickable_apps = list_pickable_apps
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.applications = self.root / 'Applications'
        self.user_applications = self.root / 'UserApplications'
        self.applications.mkdir()
        self.user_applications.mkdir()

    def tearDown(self):
        self.temp.cleanup()

    def scan(self):
        return self.list_pickable_apps((self.applications, self.user_applications))

    def test_lists_apps_by_name_with_lowercase_bundle_id(self):
        make_bundle(self.applications, 'Slack', CFBundleIdentifier='com.tinyspeck.slackmacgap', CFBundleName='Slack')
        make_bundle(self.applications, 'Notes', CFBundleIdentifier='com.apple.Notes', CFBundleDisplayName='Notes')
        self.assertEqual(self.scan(), [{'id': 'com.apple.notes', 'name': 'Notes'},
                                       {'id': 'com.tinyspeck.slackmacgap', 'name': 'Slack'}])

    def test_name_falls_back_to_folder_name(self):
        make_bundle(self.applications, 'Zed', CFBundleIdentifier='dev.zed.Zed')
        self.assertEqual(self.scan(), [{'id': 'dev.zed.zed', 'name': 'Zed'}])

    def test_finds_apps_one_folder_down(self):
        make_bundle(self.applications / 'Utilities', 'Terminal', CFBundleIdentifier='com.apple.Terminal')
        self.assertEqual([app['id'] for app in self.scan()], ['com.apple.terminal'])

    def test_skips_bundles_without_id_or_plist(self):
        make_bundle(self.applications, 'NoId', CFBundleName='No Id')
        (self.applications / 'Broken.app').mkdir()
        self.assertEqual(self.scan(), [])

    def test_skips_helper_apps_inside_a_bundle(self):
        contents = make_bundle(self.applications, 'Chrome', CFBundleIdentifier='com.google.Chrome')
        make_bundle(contents / 'Frameworks', 'Helper', CFBundleIdentifier='com.google.Chrome.helper')
        self.assertEqual([app['id'] for app in self.scan()], ['com.google.chrome'])

    def test_first_folder_wins_for_duplicate_ids(self):
        make_bundle(self.applications, 'Slack', CFBundleIdentifier='com.tinyspeck.slackmacgap', CFBundleName='Slack')
        make_bundle(self.user_applications, 'Slack Copy', CFBundleIdentifier='com.tinyspeck.slackmacgap',
                    CFBundleName='Old Slack')
        self.assertEqual(self.scan(), [{'id': 'com.tinyspeck.slackmacgap', 'name': 'Slack'}])

    def test_missing_folder_is_skipped(self):
        self.assertEqual(self.list_pickable_apps((self.root / 'Nowhere',)), [])


if __name__ == '__main__':
    unittest.main()
