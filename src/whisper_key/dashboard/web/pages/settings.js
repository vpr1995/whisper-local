// pages/settings.js
// Settings page. Fully data-driven: api/settings.py returns cards of rows (label,
// caption, control type, current value) tagged with the view they belong to: the
// short top level or a drill-in ("More shortcuts ›") whose page title becomes a "Settings › view" breadcrumb.
// Search ignores views and lists every match flat. Every change
// is saved the moment it is made (no Save button); settings the running app only
// reads at startup raise a sticky "Restart" banner. Wrapped in an IIFE because all
// page scripts share one global scope.

(() => {
  const ERROR_FLASH_MS = 4500;

  // ── Controls ───────────────────────────────────────────────────────────────
  // Each builder returns { element, setValue(value) } and calls commit(newValue)
  // when the user changes it. commit resolves once the save finished.

  function buildToggleControl(spec, commit) {
    const toggle = ui.toggle(spec.value, (checked) => commit(checked));
    return { element: toggle, setValue: (value) => { toggle.checked = !!value; } };
  }

  function buildSelectControl(spec, commit) {
    const select = ui.select(spec.options, spec.value, (value) => commit(value));
    return { element: select, setValue: (value) => { select.value = value; } };
  }

  // A text-like <input> that commits on change (blur or Enter), not on every keystroke.
  function buildInputControl(spec, commit, { numeric }) {
    const input = el('input', {
      class: `settings-input ${numeric ? 'settings-input-number' : 'settings-input-text'}`,
      type: 'text',
      spellcheck: 'false',
      autocomplete: 'off',
      placeholder: spec.placeholder || '',
    });
    input.value = spec.value == null ? '' : String(spec.value);
    if (numeric) input.setAttribute('inputmode', 'decimal');
    input.addEventListener('keydown', (event) => { if (event.key === 'Enter') input.blur(); });
    input.addEventListener('change', () => commit(input.value));
    const element = spec.unit ? el('span', { class: 'settings-input-group' }, input, el('span', { class: 'settings-unit', text: spec.unit })) : input;
    return { element, setValue: (value) => { input.value = value == null ? '' : String(value); } };
  }

  // Current combo as keycaps plus a "Change" button that swaps in a text field
  // (the parser validating it lives in Python, so errors come back from save).
  function buildHotkeyControl(spec, commit) {
    const holder = el('span', { class: 'settings-hotkey' });
    let currentValue = spec.value;

    const showKeys = () => {
      const keys = currentValue
        ? ui.hotkey(currentValue)
        : el('span', { class: 'settings-unset', text: 'Not set' });
      holder.replaceChildren(keys, ui.button('Change', showEditor));
    };

    const showEditor = () => {
      const input = el('input', {
        class: 'settings-input settings-input-hotkey', type: 'text', spellcheck: 'false',
        placeholder: 'e.g. ctrl+shift+space',
      });
      input.value = currentValue || '';
      const save = async () => { await commit(input.value); showKeys(); };
      input.addEventListener('keydown', (event) => {
        if (event.key === 'Enter') save();
        if (event.key === 'Escape') showKeys();
      });
      holder.replaceChildren(input, ui.button('Save', save, { variant: 'primary' }), ui.button('Cancel', showKeys));
      input.focus();
      input.select();
    };

    showKeys();
    return { element: holder, setValue: (value) => { currentValue = value; showKeys(); } };
  }

  const CONTROL_BUILDERS = {
    toggle: buildToggleControl,
    select: buildSelectControl,
    number: (spec, commit) => buildInputControl(spec, commit, { numeric: true }),
    text: (spec, commit) => buildInputControl(spec, commit, { numeric: false }),
    hotkey: buildHotkeyControl,
  };

  // ── Rows ───────────────────────────────────────────────────────────────────

  // Builds one setting row and its state object. `onSaved(result)` lets the page
  // react (restart banner, dependent rows).
  function buildRow(spec, api, onSaved) {
    const state = { spec, value: spec.value, rowElement: null, setControlValue: null };

    // Shows an error in place of the caption for a few seconds, and in the status bar.
    let captionElement = null;
    let captionText = spec.caption;
    function flashError(message) {
      ui.toast(message, 'error');
      captionElement.textContent = message;
      captionElement.classList.add('is-error');
      setTimeout(() => {
        captionElement.textContent = captionText;
        captionElement.classList.remove('is-error');
      }, ERROR_FLASH_MS);
    }

    async function commit(newValue) {
      let result;
      try {
        result = await api.call('save_setting', spec.path, newValue);
      } catch (error) {
        state.setControlValue(state.value); // api.call already toasted
        return;
      }
      if (!result.ok) {
        state.setControlValue(state.value);
        flashError(result.error || 'Could not save that setting.');
        return;
      }
      state.value = result.value;
      state.setControlValue(result.value);
      ui.toast(result.needs_restart ? 'Saved. Restart to apply.' : 'Saved', 'success');
      onSaved(state, result);
    }

    const control = CONTROL_BUILDERS[spec.control](spec, commit);
    state.setControlValue = control.setValue;
    state.rowElement = ui.settingRow(spec.label, spec.caption, control.element);
    captionElement = state.rowElement.querySelector('.setting-caption');
    // Path is searchable too: the old settings window let power users type config keys.
    state.searchText = `${spec.label} ${spec.caption || ''} ${spec.path}`.toLowerCase();
    return state;
  }

  // ── Sections ───────────────────────────────────────────────────────────────

  // One card of rows. A top-level card may end with a "More ›" link that opens a drill-in view.
  function buildSection(section, api, onSaved, openView) {
    const rowStates = section.rows.map((spec) => buildRow(spec, api, onSaved));
    const cardChildren = rowStates.map((state) => state.rowElement);
    let moreLink = null;
    if (section.more) {
      moreLink = el('button', { type: 'button', class: 'settings-more-link', onclick: () => openView(section.more.view) },
        el('span', { text: section.more.label }), el('span', { class: 'settings-more-chevron', 'aria-hidden': 'true', text: '\u203a' }));
      cardChildren.push(moreLink);
    }
    const card = ui.card(null, cardChildren);
    card.classList.add('settings-card');
    return { section, rowStates, card, moreLink, header: ui.sectionHeader(section.title) };
  }

  // ── Page ───────────────────────────────────────────────────────────────────

  const MAIN_VIEW = 'main';
  let removeEscapeListener = () => {}; // rebound on every mount so remounts never stack listeners

  // `restartPending` re-shows the banner after a reset; `startView` opens a drill-in
  // directly (used by a #view-id URL hash, so other pages can deep-link).
  async function mount(container, api, restartPending, startView = MAIN_VIEW) {
    container.replaceChildren();
    const schema = await api.call('get_settings_schema');
    const viewTitles = schema.views || {};
    const rowStatesByPath = new Map();
    let currentView = viewTitles[startView] ? startView : MAIN_VIEW;
    let sectionEntries = [];

    // Sticky banner: shown once any saved setting needs a restart.
    const banner = el('div', { class: 'restart-banner', hidden: !restartPending },
      el('span', { class: 'restart-banner-text', text: 'Restart Whisper Local to apply your changes.' }),
      ui.button('Restart now', async () => {
        const restarted = await api.call('send_app_command', 'restart').catch(() => false);
        ui.toast(restarted ? 'Restarting Whisper Local...' : 'Restart Whisper Local from the tray to apply', 'info');
      }, { variant: 'primary' }));

    // Rows greyed out while the setting that enables them is off.
    function applyDependencies() {
      for (const state of rowStatesByPath.values()) {
        const parent = rowStatesByPath.get(state.spec.enabled_by);
        state.rowElement.classList.toggle('is-disabled', !!parent && !parent.value);
      }
    }

    function onSaved(state, result) {
      if (result.needs_restart) banner.hidden = false;
      applyDependencies();
    }

    // ── Drill-in navigation ──
    function openView(view) {
      currentView = view;
      document.getElementById('content-scroll').scrollTop = 0;
      applyFilter();
    }

    const searchInput = el('input', {
      class: 'settings-search', type: 'search', placeholder: 'Search all settings', spellcheck: 'false',
      'aria-label': 'Search settings',
    });
    const searchToolbar = el('div', { class: 'settings-toolbar' }, searchInput);
    const noResults = el('div', { hidden: true }, ui.card(null, ui.emptyState('No settings match', 'Try a different word.')));

    // Search results are flat and headed "View · Card", so a match says where it lives.
    function searchHeading(section) {
      const viewTitle = viewTitles[section.view];
      return !viewTitle || viewTitle === section.title ? section.title : `${viewTitle} \u00b7 ${section.title}`;
    }

    // One function decides what is on screen: the current view normally, or every
    // matching row across all views while searching.
    function applyFilter() {
      const query = searchInput.value.trim().toLowerCase();
      const searching = !!query;
      let anyVisible = false;
      for (const entry of sectionEntries) {
        let visibleCount = 0;
        for (const state of entry.rowStates) {
          const matches = !searching || state.searchText.includes(query);
          state.rowElement.hidden = !matches;
          if (matches) visibleCount++;
        }
        const show = searching ? visibleCount > 0 : entry.section.view === currentView;
        entry.card.hidden = entry.header.hidden = !show;
        entry.header.firstChild.textContent = searching ? searchHeading(entry.section) : entry.section.title;
        if (entry.moreLink) entry.moreLink.hidden = searching;
        if (show) anyVisible = true;
      }
      const onMain = currentView === MAIN_VIEW;
      // In a drill-in the page title becomes "Settings › <view>"; clicking "Settings" goes back.
      ui.setPageTitle(searching || onMain ? null : viewTitles[currentView], () => openView(MAIN_VIEW));
      searchToolbar.hidden = !onMain && !searching;
      noResults.hidden = anyVisible;
      // File-level actions are not settings: only on the top level, never in results.
      fileActionsHeader.hidden = fileActionsCard.hidden = searching || !onMain;
    }
    searchInput.addEventListener('input', applyFilter);
    searchInput.addEventListener('keydown', (event) => {
      if (event.key === 'Escape' && searchInput.value) { searchInput.value = ''; applyFilter(); }
    });

    // Esc goes back one level, unless the user is editing a field (Esc cancels that edit).
    removeEscapeListener();
    const onEscape = (event) => {
      if (!container.isConnected) { removeEscapeListener(); return; }
      const editing = /^(INPUT|SELECT|TEXTAREA)$/.test((event.target && event.target.tagName) || '');
      if (event.key === 'Escape' && !editing && currentView !== MAIN_VIEW) openView(MAIN_VIEW);
    };
    document.addEventListener('keydown', onEscape);
    removeEscapeListener = () => document.removeEventListener('keydown', onEscape);

    sectionEntries = schema.sections.map((section) => buildSection(section, api, onSaved, openView));
    for (const entry of sectionEntries) {
      for (const state of entry.rowStates) {
        rowStatesByPath.set(state.spec.path, state);
        // Searching a card or view name ("formatting", "logs") lists everything in it.
        state.searchText += ` ${entry.section.title} ${viewTitles[entry.section.view] || ''}`.toLowerCase();
      }
    }

    // ── Whole-file actions ──
    const resetControl = el('span', { class: 'settings-hotkey' });
    const showResetButton = () => resetControl.replaceChildren(ui.button('Reset...', showResetConfirm, { variant: 'danger' }));
    const showResetConfirm = () => resetControl.replaceChildren(
      el('span', { class: 'settings-confirm-text', text: 'Reset everything?' }),
      ui.button('Cancel', showResetButton),
      ui.button('Yes, reset', async () => {
        const result = await api.call('reset_settings').catch(() => null);
        if (!result || !result.ok) {
          ui.toast((result && result.error) || 'Could not reset settings', 'error');
          showResetButton();
          return;
        }
        ui.toast('Settings reset to defaults', 'success');
        await mount(container, api, true);
      }, { variant: 'danger' }));
    showResetButton();

    const fileActionsHeader = ui.sectionHeader('Your settings');
    const fileActionsCard = ui.card(null, [
      ui.settingRow('Settings file', 'Everything here is saved in a plain text file you can also edit by hand.',
        ui.button('Open file', async () => {
          const opened = await api.call('open_settings_file').catch(() => false);
          if (!opened) ui.toast('Could not open the settings file', 'error');
        }, { icon: 'external' })),
      ui.settingRow('Reset all settings', 'Puts every setting back to its default. Your dictionary is kept.', resetControl),
    ]);

    container.append(
      banner,
      searchToolbar,
      ...sectionEntries.flatMap((entry) => [entry.header, entry.card]),
      noResults,
      fileActionsHeader,
      fileActionsCard);

    applyDependencies();
    applyFilter();
  }

  registerPage('settings', {
    title: 'Settings',
    async render(container, api) {
      // Optional deep link: set location.hash = '#more-shortcuts' before navigating here.
      const requestedView = location.hash.replace('#', '');
      if (requestedView) history.replaceState(null, '', location.pathname + location.search);
      await mount(container, api, false, requestedView);
    },
  });
})();
