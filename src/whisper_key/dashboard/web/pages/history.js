// pages/history.js
// History page: live search over every saved transcript, grouped by day, with Copy,
// "Fix this everywhere" (-> Vocabulary replacements), Delete and Export. Data comes
// from api/history.py, 200 entries at a time ("Load more").

const HISTORY_PAGE_SIZE = 200;
const SEARCH_DEBOUNCE_MS = 150;
const DELETE_CONFIRM_MS = 3000;

// ── Formatting ───────────────────────────────────────────────────────────────

const pluralize = (count, noun) => `${count.toLocaleString()} ${noun}${count === 1 ? '' : 's'}`;

// "2026-10-28" in local time: the key that decides which day group a row joins.
function dayKeyOf(date) {
  return `${date.getFullYear()}-${date.getMonth()}-${date.getDate()}`;
}

// "Today", "Yesterday", else "Tuesday, Oct 21" (year only when it isn't this year).
function dayLabelOf(date) {
  const now = new Date();
  const yesterday = new Date(now.getFullYear(), now.getMonth(), now.getDate() - 1);
  if (dayKeyOf(date) === dayKeyOf(now)) return 'Today';
  if (dayKeyOf(date) === dayKeyOf(yesterday)) return 'Yesterday';
  return date.toLocaleDateString(undefined, {
    weekday: 'long', month: 'short', day: 'numeric',
    year: date.getFullYear() === now.getFullYear() ? undefined : 'numeric',
  });
}

const timeLabelOf = (date) => date.toLocaleTimeString(undefined, { hour: 'numeric', minute: '2-digit' });

// Splits `text` around case-insensitive matches of `query` so matches can be
// wrapped in <mark>. Built from DOM nodes only: transcripts are untrusted text.
function highlightMatches(text, query) {
  const needle = query.trim().toLowerCase();
  if (!needle) return [text];
  const lowerText = text.toLowerCase();
  const parts = [];
  let cursor = 0;
  for (let hit = lowerText.indexOf(needle); hit !== -1; hit = lowerText.indexOf(needle, cursor)) {
    if (hit > cursor) parts.push(text.slice(cursor, hit));
    parts.push(el('mark', { class: 'history-mark', text: text.slice(hit, hit + needle.length) }));
    cursor = hit + needle.length;
  }
  if (cursor < text.length) parts.push(text.slice(cursor));
  return parts;
}

// ── Row ──────────────────────────────────────────────────────────────────────

// Inline editor under a row: the user types what Whisper heard and what they meant.
function buildFixForm(entry, api, onClose) {
  const wrongInput = el('input', { class: 'history-input', type: 'text', placeholder: 'What Whisper heard' });
  const rightInput = el('input', { class: 'history-input', type: 'text', placeholder: 'What you meant' });

  const save = async () => {
    const saved = await api.call('add_correction', wrongInput.value, rightInput.value).catch(() => null);
    if (saved === null) return;
    if (!saved) return ui.toast('Nothing saved. Enter the text to replace and a different result.', 'error');
    ui.toast('Saved. This fix applies to your next dictations.', 'success');
    onClose();
  };
  for (const input of [wrongInput, rightInput]) {
    input.addEventListener('keydown', (event) => {
      if (event.key === 'Enter') save();
      if (event.key === 'Escape') onClose();
    });
  }
  setTimeout(() => wrongInput.focus(), 0);

  return el('div', { class: 'history-fix' },
    el('div', { class: 'history-fix-fields' }, wrongInput, el('span', { class: 'history-fix-arrow', text: '→' }), rightInput),
    el('div', { class: 'history-fix-actions' },
      el('span', { class: 'history-fix-hint', text: 'Whole-word, case-insensitive. Manage all fixes under Vocabulary.' }),
      ui.button('Cancel', onClose, { variant: 'ghost' }),
      ui.button('Save fix', save, { variant: 'primary' })));
}

// One transcript: text (click to expand), meta line and the action buttons.
function buildHistoryRow(entry, query, api, onDeleted) {
  const date = new Date(entry.timestamp);
  const meta = [entry.app, Number.isNaN(date.getTime()) ? '' : timeLabelOf(date), pluralize(entry.words, 'word')]
    .filter(Boolean).join(' · ');

  const textElement = el('div', { class: 'history-text selectable clamped', title: 'Click to expand' },
    highlightMatches(entry.text, query));
  textElement.addEventListener('click', () => {
    if (!window.getSelection().toString()) textElement.classList.toggle('clamped'); // a drag-select isn't a click
  });

  const copyButton = ui.button('Copy', async (event, button) => {
    const copied = await api.call('copy_text', entry.text).catch(() => false);
    if (!copied) return;
    ui.toast('Copied to clipboard', 'success');
    button.replaceChildren(ui.icon('check'), el('span', { text: 'Copied' }));
    setTimeout(() => button.replaceChildren(ui.icon('copy'), el('span', { text: 'Copy' })), 1500);
  }, { icon: 'copy', variant: 'ghost' });

  // Delete needs a second click: entries can't be recovered.
  let confirmTimer = null;
  const deleteButton = ui.button('Delete', async (event, button) => {
    if (!confirmTimer) {
      button.querySelector('span').textContent = 'Click again to delete';
      confirmTimer = setTimeout(() => {
        confirmTimer = null;
        button.querySelector('span').textContent = 'Delete';
      }, DELETE_CONFIRM_MS);
      return;
    }
    clearTimeout(confirmTimer);
    const deleted = await api.call('delete_history_entry', entry.timestamp, entry.text).catch(() => false);
    if (deleted) onDeleted(row);
    else ui.toast('That entry was already gone.', 'error');
  }, { variant: 'ghost' });
  deleteButton.classList.add('history-delete');

  let fixForm = null;
  const closeFixForm = () => { if (fixForm) fixForm.remove(); fixForm = null; };
  const fixButton = ui.button('Fix this everywhere…', () => {
    if (fixForm) return closeFixForm();
    fixForm = buildFixForm(entry, api, closeFixForm);
    row.append(fixForm);
  }, { variant: 'ghost' });

  const row = el('div', { class: 'history-row' },
    textElement,
    el('div', { class: 'history-footer' },
      el('span', { class: 'history-meta', text: meta }),
      el('span', { class: 'history-actions' }, copyButton, fixButton, deleteButton)));
  return row;
}

// ── Page ─────────────────────────────────────────────────────────────────────

registerPage('history', {
  title: 'History',
  async render(container, api) {
    let query = '';
    let loadedCount = 0;
    let requestToken = 0; // newer searches invalidate older, slower responses
    let debounceTimer = null;

    const searchInput = el('input', {
      class: 'history-input history-search', type: 'search', placeholder: 'Search your dictations…',
      'aria-label': 'Search transcripts',
    });
    const exportButton = ui.button('Export…', async () => {
      const result = await api.call('export_history', query).catch(() => null);
      if (!result) return;
      if (result.saved) ui.toast(`Exported ${pluralize(result.count, 'transcript')}`, 'success');
      else if (!result.count) ui.toast('Nothing to export.', 'info');
    });
    const statusLine = el('div', { class: 'history-status' });
    const listMount = el('div', { class: 'history-list' });
    const loadMoreMount = el('div', { class: 'history-more' });
    container.append(el('div', { class: 'history-toolbar' }, searchInput, exportButton), statusLine, listMount, loadMoreMount);

    // Day-group bookkeeping so "Load more" continues the last card instead of restarting it.
    let currentDayKey = null;
    let currentCard = null;
    let totalCount = 0;
    let matchedCount = 0;

    const updateStatus = () => {
      statusLine.textContent = query.trim()
        ? `${pluralize(matchedCount, 'match')} of ${totalCount.toLocaleString()}`
        : `${pluralize(totalCount, 'dictation')} saved on this device`;
    };

    const appendEntries = (entries) => {
      for (const entry of entries) {
        const date = new Date(entry.timestamp);
        const dayKey = Number.isNaN(date.getTime()) ? 'unknown' : dayKeyOf(date);
        if (dayKey !== currentDayKey) {
          currentDayKey = dayKey;
          currentCard = ui.card(null, []);
          listMount.append(ui.sectionHeader(Number.isNaN(date.getTime()) ? 'Earlier' : dayLabelOf(date)), currentCard);
        }
        currentCard.append(buildHistoryRow(entry, query, api, removeRow));
      }
      loadedCount += entries.length;
    };

    // Drops a deleted row, and its whole day group once empty.
    const removeRow = (row) => {
      const card = row.parentElement;
      row.remove();
      totalCount -= 1;
      matchedCount -= 1;
      loadedCount -= 1;
      if (!card.children.length) {
        card.previousElementSibling.remove();
        card.remove();
        if (card === currentCard) { currentCard = null; currentDayKey = null; }
      }
      if (!loadedCount) showEmpty({ total: totalCount, saving_enabled: true });
      updateStatus();
    };

    const showEmpty = (result) => {
      listMount.replaceChildren();
      loadMoreMount.replaceChildren();
      let empty;
      if (!result.saving_enabled && !result.total) {
        empty = ui.emptyState('History is turned off',
          'Transcript text is not being saved (history retention is set to 0 days).');
        empty.append(el('div', { class: 'history-empty-action' },
          ui.button('Open Settings', () => window.dashboard.navigate('settings'), { variant: 'primary' })));
      } else if (query.trim()) {
        empty = ui.emptyState('No matches', `Nothing in your history contains “${query.trim()}”.`);
      } else {
        empty = ui.emptyState('Nothing dictated yet', 'Hold your record hotkey and say something. It will show up here.');
      }
      listMount.append(ui.card(null, empty));
    };

    const renderLoadMore = (hasMore) => {
      loadMoreMount.replaceChildren();
      if (!hasMore) return;
      loadMoreMount.append(ui.button('Load more', async (event, button) => {
        button.setAttribute('disabled', '');
        const result = await api.call('get_history', query, loadedCount, HISTORY_PAGE_SIZE).catch(() => null);
        if (!result) return button.removeAttribute('disabled');
        appendEntries(result.entries);
        renderLoadMore(result.has_more);
      }));
    };

    // Fresh search from the top; the token drops the response if the user typed again meanwhile.
    const runSearch = async () => {
      const token = ++requestToken;
      const result = await api.call('get_history', query, 0, HISTORY_PAGE_SIZE).catch(() => null);
      if (!result || token !== requestToken) return;
      totalCount = result.total;
      matchedCount = result.matched;
      loadedCount = 0;
      currentDayKey = null;
      currentCard = null;
      listMount.replaceChildren();
      updateStatus();
      exportButton.toggleAttribute('disabled', !result.matched);
      if (!result.entries.length) return showEmpty(result);
      appendEntries(result.entries);
      renderLoadMore(result.has_more);
    };

    searchInput.addEventListener('input', () => {
      clearTimeout(debounceTimer);
      debounceTimer = setTimeout(() => { query = searchInput.value; runSearch(); }, SEARCH_DEBOUNCE_MS);
    });
    await runSearch();
    if (totalCount) searchInput.focus();
  },
});
