# install-windows-launcher.ps1
# Sets up Whisper Local to run from a source checkout without a console window:
# a Desktop and Start-menu shortcut (double-click, lives in the tray) and,
# optionally, start on login. Re-running is safe; -Uninstall removes everything.
#
#   powershell -ExecutionPolicy Bypass -File tools\install-windows-launcher.ps1
#   powershell -ExecutionPolicy Bypass -File tools\install-windows-launcher.ps1 -NoAutostart
#   powershell -ExecutionPolicy Bypass -File tools\install-windows-launcher.ps1 -Uninstall

param(
    [switch]$NoAutostart,   # shortcuts only, do not start on login
    [switch]$Uninstall      # remove shortcuts and the login entry
)

$ErrorActionPreference = 'Stop'

# ── Paths ────────────────────────────────────────────────────────────────────

$RepoRoot = Split-Path -Parent $PSScriptRoot
$VenvScripts = Join-Path $RepoRoot '.venv\Scripts'
$PythonExe = Join-Path $VenvScripts 'python.exe'
# pythonw is the GUI-subsystem interpreter: no console window ever appears.
$WindowlessPythonExe = Join-Path $VenvScripts 'pythonw.exe'
$IconPath = Join-Path $RepoRoot 'src\whisper_key\platform\windows\assets\whisperkey-icon.ico'

$ShortcutFolders = @(
    [Environment]::GetFolderPath('Desktop'),
    (Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs')
)
$ShortcutFileName = 'Whisper Local.lnk'

# ── Uninstall ────────────────────────────────────────────────────────────────

if ($Uninstall) {
    foreach ($folder in $ShortcutFolders) {
        Remove-Item (Join-Path $folder $ShortcutFileName) -ErrorAction SilentlyContinue
    }
    if (Test-Path $PythonExe) { & $PythonExe -m whisper_key.main --disable-autostart }
    Write-Host 'Removed the shortcuts and the start-on-login entry.'
    exit 0
}

# ── Checks ───────────────────────────────────────────────────────────────────

if (-not (Test-Path $WindowlessPythonExe)) {
    Write-Host "No virtual environment at $VenvScripts" -ForegroundColor Red
    Write-Host 'Create it first: uv venv --python 3.12; uv pip install -e ".[dev]"' -ForegroundColor Red
    exit 1
}

# ── Shortcuts ────────────────────────────────────────────────────────────────

$shell = New-Object -ComObject WScript.Shell
foreach ($folder in $ShortcutFolders) {
    $shortcut = $shell.CreateShortcut((Join-Path $folder $ShortcutFileName))
    $shortcut.TargetPath = $WindowlessPythonExe
    $shortcut.Arguments = '-m whisper_key.main'
    $shortcut.WorkingDirectory = $RepoRoot
    $shortcut.IconLocation = $IconPath
    $shortcut.Description = 'Whisper Local dictation (runs in the tray)'
    $shortcut.Save()
    Write-Host "Shortcut: $(Join-Path $folder $ShortcutFileName)"
}

# ── Start on login ───────────────────────────────────────────────────────────

# Uses the app's own autostart code so this, the tray item and the Settings
# toggle all control the same login entry.
if (-not $NoAutostart) {
    & $PythonExe -m whisper_key.main --enable-autostart
}

Write-Host 'Done. Double-click "Whisper Local" on the Desktop to start it in the tray.'
