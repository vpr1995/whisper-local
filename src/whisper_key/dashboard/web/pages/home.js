// pages/home.js
// Home page: greeting, usage stat tiles, the user's hotkeys and the latest
// transcripts. Data comes from api/home.py; every block degrades to an empty
// state, so a first launch with no history still looks intentional.

// "Good morning" etc. from the local hour.
function greetingForNow() {
  const hour = new Date().getHours();
  if (hour < 5) return 'Hello';
  if (hour < 12) return 'Good morning';
  if (hour < 18) return 'Good afternoon';
  return 'Good evening';
}

// 75 -> "1h 15m", 12 -> "12 min", 0 -> "0 min".
function formatMinutes(totalMinutes) {
  const minutes = Math.round(totalMinutes);
  if (minutes < 60) return `${minutes} min`;
  const hours = Math.floor(minutes / 60);
  return `${hours}h ${String(minutes % 60).padStart(2, '0')}m`;
}

const formatCount = (number) => (number || 0).toLocaleString();

// ── Blocks ───────────────────────────────────────────────────────────────────

function buildStatTiles(stats) {
  const streakHint = stats.longest_streak_days > stats.streak_days ? `Longest: ${stats.longest_streak_days}` : null;
  return el('div', { class: 'stat-grid' },
    ui.statTile('Words today', formatCount(stats.today_words)),
    ui.statTile('Total words', formatCount(stats.total_words), `${formatCount(stats.sessions)} dictations`),
    ui.statTile('Day streak', formatCount(stats.streak_days), streakHint),
    ui.statTile('Time saved', formatMinutes(stats.minutes_saved), 'vs. typing'));
}

function buildHotkeysCard(hotkeys) {
  if (!hotkeys.length) {
    return ui.card(null, ui.emptyState('No hotkeys configured', 'Set them under Settings.'));
  }
  return ui.card(null, hotkeys.map((hotkey) => ui.settingRow(hotkey.label, hotkey.help, ui.hotkey(hotkey.keys))));
}

function buildRecentRow(transcript, api) {
  const copyButton = ui.button('Copy', async (event, button) => {
    // api.call already toasts a failure; catch only so the button stays usable.
    const copied = await api.call('copy_text', transcript.text).catch(() => false);
    if (!copied) return;
    ui.toast('Copied to clipboard', 'success');
    button.replaceChildren(ui.icon('check'), el('span', { text: 'Copied' }));
    setTimeout(() => button.replaceChildren(ui.icon('copy'), el('span', { text: 'Copy' })), 1500);
  }, { icon: 'copy' });

  const meta = [transcript.app, ui.timeAgo(transcript.timestamp), `${transcript.words} ${transcript.words === 1 ? 'word' : 'words'}`].filter(Boolean).join(' · ');
  return el('div', { class: 'recent-row' },
    el('div', { class: 'recent-main' },
      el('div', { class: 'recent-text selectable', text: transcript.text }),
      el('div', { class: 'recent-meta', text: meta })),
    copyButton);
}

function buildRecentCard(recent, api) {
  if (!recent.length) {
    return ui.card(null, ui.emptyState('Nothing dictated yet', 'Hold your record hotkey and say something. It will show up here.'));
  }
  return ui.card(null, recent.map((transcript) => buildRecentRow(transcript, api)));
}

// ── Page ─────────────────────────────────────────────────────────────────────

registerPage('home', {
  title: 'Home',
  header: false, // draws its own greeting
  async render(container, api) {
    // Independent blocks: one failing call must not blank the whole page.
    const [stats, hotkeys, recent] = await Promise.all([
      api.call('get_home_stats').catch(() => null),
      api.call('get_hotkeys').catch(() => []),
      api.call('get_recent_transcripts', 5).catch(() => []),
    ]);

    const recordHotkey = hotkeys.find((hotkey) => hotkey.label.startsWith('Record'));
    container.append(
      el('h1', { class: 'page-title', text: greetingForNow() }),
      el('p', { class: 'page-subtitle' },
        recordHotkey ? ['Use ', ui.hotkey(recordHotkey.keys), ' to dictate in any app.'] : 'Dictate in any app with your record hotkey.'));

    if (stats) container.append(buildStatTiles(stats));

    container.append(
      ui.sectionHeader('Recent',
        recent.length ? ui.button('See all', () => window.dashboard.navigate('history'), { variant: 'ghost' }) : null),
      buildRecentCard(recent, api),
      ui.sectionHeader('Your hotkeys'),
      buildHotkeysCard(hotkeys));
  },
});
