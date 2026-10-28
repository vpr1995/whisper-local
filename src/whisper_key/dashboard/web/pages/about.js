// pages/about.js
// About page: app identity and links, app controls (restart, retry, pause hotkeys),
// diagnostics tools (doctor report, self-test, log bundle, usage summary) and the full
// hotkey cheat sheet. Data and actions come from api/about.py; the controls go through
// send_app_command, which talks to the running app.

// Wrapped in a function so these helper names stay private: all page scripts share one global scope.
(() => {

const SELFTEST_POLL_MS = 500;
const TRAY_HINT = "The app isn't reachable. Use the tray menu instead.";

// ── Small helpers ────────────────────────────────────────────────────────────

// Disables a button and swaps its label while `work` runs; always restores it.
async function runWithBusyButton(button, busyLabel, work) {
  const labelNode = button.querySelector('span');
  const originalLabel = labelNode.textContent;
  button.setAttribute('disabled', '');
  labelNode.textContent = busyLabel;
  try {
    return await work();
  } finally {
    button.removeAttribute('disabled');
    labelNode.textContent = originalLabel;
  }
}

// A titled results block shown under the Diagnostics card.
const resultBlock = (title, body) => el('div', { class: 'result-block' }, ui.sectionHeader(title), body);

const statusDot = (status) => el('span', { class: `status-mark status-mark-${status}`, 'aria-label': status });

// ── Identity ─────────────────────────────────────────────────────────────────

function buildIdentityCard(info, api) {
  const open = (url) => () => api.call('open_external_url', url);
  const link = (label, handler) => ui.button(label, handler, { variant: 'ghost', icon: 'external' });
  return ui.card(null, el('div', { class: 'pad about-identity' },
    el('img', { class: 'about-mark', src: 'brand.svg', alt: '', width: 64, height: 64 }),
    el('div', { class: 'about-identity-text' },
      el('div', { class: 'about-name', text: 'Whisper Local' }),
      el('div', { class: 'setting-caption', text: `Version ${info.version}. Speech to text that runs on this computer; your audio never leaves it.` }),
      el('div', { class: 'about-links' },
        link('GitHub', open(info.links.github)),
        link('Changelog', () => api.call('open_changelog')),
        link('Report an issue', open(info.links.issues)),
        link('Discussions', open(info.links.discussions))))));
}

// ── Controls ─────────────────────────────────────────────────────────────────

function buildControlsCard(api) {
  // Sends a command to the running app; a false answer means it could not be reached.
  const commandButton = (label, commandName, doneMessage, variant) => ui.button(label, async (event, button) => {
    const reached = await runWithBusyButton(button, 'Working…', () => api.call('send_app_command', commandName).catch(() => false));
    ui.toast(reached ? doneMessage : TRAY_HINT, reached ? 'success' : 'error');
  }, { variant });
  return ui.card(null, [
    ui.settingRow('Restart Whisper Local', 'Reloads settings and the speech model.',
      commandButton('Restart', 'restart', 'Restarting Whisper Local…')),
    ui.settingRow('Retry last dictation', 'Types your last dictation again, for example after it landed in the wrong window.',
      commandButton('Retry', 'retry_last_dictation', 'Retrying your last dictation…')),
    ui.settingRow('Pause or resume hotkeys', 'Turns every Whisper Local hotkey off or back on.',
      commandButton('Pause / resume', 'toggle_pause_hotkeys', 'Hotkeys toggled.')),
  ]);
}

// ── Diagnostics results ──────────────────────────────────────────────────────

// One doctor section as a collapsible group; sections with a problem start open.
function buildDoctorSection(section) {
  const needsAttention = section.checks.some((check) => check.status === 'warn' || check.status === 'fail');
  const rows = section.checks.map((check) => el('div', { class: 'check-row' },
    statusDot(check.status),
    el('span', { class: 'check-name', text: check.name }),
    check.detail ? el('span', { class: 'check-detail selectable', text: check.detail }) : null));
  return el('details', { class: 'check-section', open: needsAttention }, el('summary', { text: section.section }), rows);
}

function buildDoctorReport(report) {
  const { ok, warn, fail } = report.counts;
  const summary = fail ? `${fail} ${fail === 1 ? 'problem' : 'problems'} found`
    : (warn ? `${warn} ${warn === 1 ? 'warning' : 'warnings'}` : 'Everything looks healthy');
  return ui.card(null, [
    el('div', { class: 'pad doctor-summary' }, statusDot(fail ? 'fail' : (warn ? 'warn' : 'ok')),
      el('strong', { text: summary }), el('span', { class: 'setting-caption', text: `${ok} checks passed` })),
    ...report.sections.map(buildDoctorSection),
  ]);
}

function buildSelfTestRows(progress) {
  return ui.card(null, progress.stages.map((stage) => el('div', { class: 'check-row selftest-row' },
    statusDot(stage.status === 'running' ? 'running' : (stage.status === 'pending' ? 'info' : stage.status)),
    el('span', { class: 'check-name', text: stage.title }),
    el('span', { class: 'check-detail selectable', text: [stage.detail, stage.fix ? `Fix: ${stage.fix}` : ''].filter(Boolean).join('  ') }))));
}

function buildUsageSummary(stats) {
  const minutes = Math.round(stats.minutes_saved);
  return ui.card(null, el('div', { class: 'pad' }, el('div', { class: 'stat-grid' },
    ui.statTile('Words today', (stats.today_words || 0).toLocaleString()),
    ui.statTile('Total words', (stats.total_words || 0).toLocaleString(), `${(stats.sessions || 0).toLocaleString()} dictations`),
    ui.statTile('Day streak', String(stats.streak_days), `Longest: ${stats.longest_streak_days}`),
    ui.statTile('Time saved', minutes < 60 ? `${minutes} min` : `${Math.floor(minutes / 60)}h ${String(minutes % 60).padStart(2, '0')}m`, 'vs. typing'))));
}

// ── Diagnostics card ─────────────────────────────────────────────────────────

function buildDiagnosticsCard(api, showResult) {
  const runDiagnostics = ui.button('Run diagnostics', async (event, button) => {
    const report = await runWithBusyButton(button, 'Checking…', () => api.call('run_diagnostics').catch(() => null));
    if (report) showResult('Diagnostics report', buildDoctorReport(report));
  });

  const runSelfTest = ui.button('Run self-test', async (event, button) => {
    const started = await api.call('start_selftest').catch(() => null);
    if (!started) return;
    if (!started.ok) return ui.toast(started.error, 'error');
    await runWithBusyButton(button, 'Testing…', () => new Promise((resolve) => {
      const timer = setInterval(async () => {
        const progress = await api.call('get_selftest_progress').catch(() => null);
        if (!progress) { clearInterval(timer); return resolve(); }
        showResult('Self-test', buildSelfTestRows(progress));
        if (progress.state === 'done') {
          clearInterval(timer);
          const failed = progress.stages.filter((stage) => stage.status === 'fail').length;
          ui.toast(failed ? `Self-test: ${failed} check(s) failed` : 'Self-test passed', failed ? 'error' : 'success');
          resolve();
        }
      }, SELFTEST_POLL_MS);
    }));
  });

  const bundleLogs = ui.button('Bundle logs', async (event, button) => {
    const result = await runWithBusyButton(button, 'Bundling…', () => api.call('bundle_logs_to_desktop').catch(() => null));
    if (!result) return;
    if (!result.ok) return ui.toast(`Could not bundle logs: ${result.error}`, 'error');
    const fileName = result.path.split(/[\\/]/).pop();
    ui.toast(`Saved ${fileName} to your Desktop`, 'success');
    showResult('Log bundle', ui.card(null, ui.settingRow(fileName,
      'Usernames, emails and sensitive settings are redacted. Attach this file to your bug report.',
      ui.button('Show in folder', () => api.call('show_bundle_folder'), { icon: 'external' }))));
  }, { variant: 'primary' });

  const viewUsage = ui.button('View', async () => {
    const stats = await api.call('get_home_stats').catch(() => null);
    if (stats) showResult('Usage summary', buildUsageSummary(stats));
  });

  const openLog = ui.button('Open', async () => {
    if (!(await api.call('open_log_file').catch(() => null))) ui.toast('No log file yet.', 'info');
  });
  const openConfig = ui.button('Open', () => api.call('open_config_folder'));

  return ui.card(null, [
    ui.settingRow('Health check', 'Checks the microphone, model, permissions and settings.', runDiagnostics),
    ui.settingRow('Self-test', 'Records half a second, transcribes silence and tests the clipboard. Needs the model downloaded.', runSelfTest),
    ui.settingRow('Bundle logs for a bug report', 'Saves a redacted zip of your logs and settings to the Desktop.', bundleLogs),
    ui.settingRow('Usage summary', 'Words dictated, streak and time saved.', viewUsage),
    ui.settingRow('Log file', 'Opens app.log in your default text editor.', openLog),
    ui.settingRow('Config folder', 'Your settings, dictionary, transforms and history.', openConfig),
  ]);
}

// ── Hotkey cheat sheet ───────────────────────────────────────────────────────

function buildCheatSheetCard(hotkeys) {
  return ui.card(null, hotkeys.map((hotkey) => ui.settingRow(
    hotkey.label, hotkey.help,
    hotkey.keys.length ? ui.hotkey(hotkey.keys) : el('span', { class: 'setting-caption', text: 'Not set' }))));
}

// ── Page ─────────────────────────────────────────────────────────────────────

registerPage('about', {
  title: 'About',
  async render(container, api) {
    // Independent blocks: one failing call must not blank the page.
    const [info, hotkeys] = await Promise.all([
      api.call('get_about_info'),
      api.call('get_hotkey_cheat_sheet').catch(() => []),
    ]);

    // Each tool's output replaces the previous one in this slot, under the Diagnostics card.
    const resultSlot = el('div', { class: 'result-slot' });
    let shownTitle = null;
    const showResult = (title, body) => {
      resultSlot.replaceChildren(resultBlock(title, body));
      // Bring a newly started tool's output into view (not on every self-test poll).
      if (title !== shownTitle) resultSlot.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
      shownTitle = title;
    };

    container.append(
      buildIdentityCard(info, api),
      ui.sectionHeader('Controls'),
      buildControlsCard(api),
      ui.sectionHeader('Diagnostics'),
      buildDiagnosticsCard(api, showResult),
      resultSlot,
      ui.sectionHeader('Hotkey cheat sheet'),
      buildCheatSheetCard(hotkeys));
  },
});

})();
