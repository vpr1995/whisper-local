# dashboard/api/__init__.py
# Composes the one object pywebview exposes to JavaScript (window.pywebview.api).
# Each page owns ONE mixin module here so page work never touches shared files;
# to add a page's methods, edit only that page's mixin.
#
# Rules for mixin methods: public methods are callable from JS (arguments and
# return values must be JSON-serialisable); prefix helpers with "_" to keep them
# private. Mixins are stateless: the child process has no StateManager, so read
# and write config/data files directly (see ShellApi._load_config).

from .about import AboutApi
from .history import HistoryApi
from .home import HomeApi
from .models import ModelsApi
from .settings import SettingsApi
from .shell import ShellApi
from .style import StyleApi
from .vocabulary import VocabularyApi


class DashboardApi(ShellApi, HomeApi, HistoryApi, ModelsApi, VocabularyApi,
                   StyleApi, SettingsApi, AboutApi):
    pass
