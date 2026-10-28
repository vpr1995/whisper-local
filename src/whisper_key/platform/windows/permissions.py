# platform/windows/permissions.py
# Permission checks are a macOS concern (Accessibility/Input Monitoring);
# Windows needs none, so these stubs always report granted.
# macOS mirror: platform/macos/permissions.py (the real implementation).
def check_accessibility_permission() -> bool:
    return True


def request_accessibility_permission():
    pass


def ensure_accessibility(on_granted) -> bool:
    return True
