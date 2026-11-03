# platform/macos/apps.py
# The apps a user can give their own writing style on the dashboard Style page.
# On macOS that is every installed app: a scan of the Applications folders that
# reads each bundle's Info.plist. Windows mirror: platform/windows/apps.py.
import logging
import plistlib
from pathlib import Path

logger = logging.getLogger(__name__)

# Where macOS apps live. Each folder is scanned one level deep as well, which
# covers Utilities/ and vendor folders such as "Adobe Photoshop 2026/".
APP_FOLDERS = (
    Path('/Applications'),
    Path('/System/Applications'),
    Path.home() / 'Applications',
)


# Every installed app as {id, name}, sorted by name. The id is the lowercase
# bundle id, the same value foreground.get_foreground_app() reports as 'exe'.
def list_pickable_apps(app_folders=APP_FOLDERS) -> list:
    apps_by_id = {}
    for bundle_path in _find_app_bundles(app_folders):
        app = _read_bundle(bundle_path)
        # The first folder wins, so /Applications beats a copy in ~/Applications.
        if app and app['id'] not in apps_by_id:
            apps_by_id[app['id']] = app
    return sorted(apps_by_id.values(), key=lambda app: app['name'].lower())


# ── Helpers ──────────────────────────────────────────────────────────────────

# .app bundles at the top of each folder and one folder down. Never looks
# inside a bundle: helper apps in Contents/ are not apps the user types into.
def _find_app_bundles(app_folders):
    for folder in app_folders:
        try:
            entries = sorted(Path(folder).iterdir())
        except OSError:
            continue
        for entry in entries:
            if entry.suffix == '.app':
                yield entry
            elif entry.is_dir():
                try:
                    yield from sorted(child for child in entry.iterdir() if child.suffix == '.app')
                except OSError:
                    continue


# {id, name} from a bundle's Info.plist, or None when it has no bundle id
# (without one the foreground check could never match the app).
def _read_bundle(bundle_path: Path):
    try:
        with open(bundle_path / 'Contents' / 'Info.plist', 'rb') as plist_file:
            info = plistlib.load(plist_file)
    except Exception as e:
        logger.debug(f"Skipped {bundle_path.name}: {e}")
        return None
    bundle_id = str(info.get('CFBundleIdentifier') or '').strip().lower()
    if not bundle_id:
        return None
    name = info.get('CFBundleDisplayName') or info.get('CFBundleName') or bundle_path.stem
    return {'id': bundle_id, 'name': str(name).strip() or bundle_path.stem}
