// pages/models.js
// Models page: everything about the speech engine in one place. A tile grid picks the
// model (Auto or a specific one) and downloads it in the background; below it are the
// language, performance and storage cards, and an "Advanced ›" drill-in for the engine
// and tuning settings. Data and saves come from api/models.py. Every whisper.* setting
// is read at startup, so each successful save raises the shared "Restart to apply" notice.

// Wrapped in a function so these helper names stay private: all page scripts share one global scope.
(() => {

const DOWNLOAD_POLL_MS = 500;
const ERROR_FLASH_MS = 4500;
const MODEL_PATH = 'whisper.model';
const AUTO_ID = 'auto';
const MAIN_VIEW = 'main';
const ADVANCED_VIEW = 'advanced';
const TILE_COLUMNS = 3;
const MAIN_GROUP_IDS = ['official', 'parakeet']; // registry groups shown up front; the rest are behind "show more"

// Shorter labels for the "Run on" control than the Settings-style ones from the API.
const DEVICE_LABELS = { cpu: 'CPU', cuda: 'GPU' };

let removeEscapeListener = () => {}; // rebound on every mount so remounts never stack listeners

// ── Small helpers ────────────────────────────────────────────────────────────

// 313_000_000 -> "313 MB".
const formatMegabytes = (bytes) => `${Math.round(bytes / 1_000_000).toLocaleString()} MB`;

// "141MB" -> "141 MB" (the registry label has no space).
const spacedSize = (size) => size.replace(/(\d)\s*([KMG]B)$/i, '$1 $2');

const joinFacts = (...facts) => facts.filter(Boolean).join(' · ');

// ── Model tiles ──────────────────────────────────────────────────────────────

// Tile for one registry model: size and speed under the name, download state as the badge.
function buildModelTile(model) {
  return {
    id: model.key,
    title: model.name,
    subtitle: joinFacts(spacedSize(model.size), model.tagline),
    badge: model.downloading ? 'Downloading' : (model.cached ? 'Ready' : ''), // "Ready": on disk, works offline
  };
}

// The "Auto" tile: what it would pick on this computer is part of its subtitle.
function buildAutoTile(auto) {
  return { id: AUTO_ID, title: 'Auto', subtitle: `Picks ${auto.name}`, badge: 'Recommended' };
}

// ── Page ─────────────────────────────────────────────────────────────────────

registerPage('models', {
  title: 'Models',
  async render(container, api) {
    const noticeSlot = el('div', { class: 'notice-slot' });
    const modelMount = el('div', { class: 'models-picker' });
    const progressRefs = { fill: null, label: null }; // the download bar currently on screen
    let pollTimer = null;
    let selectedId = null; // 'auto' or a model key; updated as soon as a save succeeds

    // ── Saving ──
    // Saves one whisper.* setting. Returns the API result on success; on failure shows the
    // error (toast plus `onError`, which flashes it in the row) and returns null so the
    // caller can put its control back.
    async function saveSetting(path, value, onError) {
      const result = await api.call('save_model_setting', path, value).catch(() => null);
      if (!result) return null; // api.call already toasted
      if (!result.ok) {
        const message = result.error || 'Could not save that setting.';
        ui.toast(message, 'error');
        if (onError) onError(message);
        return null;
      }
      ui.toast('Saved. Restart to apply.', 'success');
      noticeSlot.replaceChildren(ui.restartNotice(api, 'Whisper Local loads these speech settings the next time it starts.'));
      return result;
    }

    // ── Model section: tile grids, the detail strip and the download bar ──

    // Builds the "Model" section from disk state; called again after a download finishes.
    function renderModelSection(data) {
      selectedId = data.configured;
      const modelByKey = new Map(data.groups.flatMap((group) => group.models).map((model) => [model.key, model]));

      // ── Detail strip: facts and the Download action for whatever is selected ──
      const strip = el('div', { class: 'models-strip' });
      const downloadBar = el('div', { class: 'models-download', hidden: !data.downloading });
      const barFill = el('div', { class: 'progress-fill' });
      const barLabel = el('div', { class: 'progress-text', text: 'Starting…' });
      downloadBar.append(
        el('div', { class: 'models-download-title', text: data.downloading ? `Downloading ${data.downloading.name}` : '' }),
        el('div', { class: 'progress' }, barFill), barLabel);
      progressRefs.fill = barFill;
      progressRefs.label = barLabel;

      const renderStrip = () => {
        const isAuto = selectedId === AUTO_ID;
        const effectiveKey = isAuto ? data.auto.key : selectedId;
        const model = modelByKey.get(effectiveKey);
        const state = isAuto ? data.auto : model;
        if (!state) { // a model named in the settings file that is not in the registry list
          strip.replaceChildren(el('div', { class: 'models-strip-text' },
            el('div', { class: 'models-strip-title', text: selectedId }),
            el('div', { class: 'setting-caption', text: 'A custom model from your settings file.' })));
          return;
        }
        const title = isAuto ? `Auto: ${data.auto.name}` : model.name;
        const description = isAuto
          ? `Picks the best model for this computer and the language you speak (${data.auto.reason}). ${model ? model.blurb : ''}`.trim()
          : model.blurb;

        let action;
        if (state.cached) {
          action = el('span', { class: 'models-ready' }, ui.icon('check'), 'Downloaded');
        } else if (state.downloadable) {
          const button = ui.button('Download', () => startDownload(effectiveKey));
          button.disabled = !!data.downloading; // the backend runs one download at a time
          action = button;
        } else {
          action = el('span', { class: 'setting-caption', text: 'Not available to download' });
        }

        strip.replaceChildren(
          el('div', { class: 'models-strip-text' },
            el('div', { class: 'models-strip-title', text: title }),
            model ? el('div', { class: 'setting-caption', text: joinFacts(model.languages, spacedSize(model.size)) }) : null,
            description ? el('div', { class: 'setting-caption', text: description }) : null,
            model && model.warning ? el('div', { class: 'setting-caption models-warning', text: model.warning }) : null),
          el('div', { class: 'models-strip-action' }, action));
      };

      // ── Grids: Auto plus the main models in one grid; English-only and custom models
      // sit behind a "show more" toggle (open from the start when one of them is selected).
      // Selection is shared across both grids. ──
      const mainModels = data.groups.filter((group) => MAIN_GROUP_IDS.includes(group.id)).flatMap((group) => group.models);
      const extraModels = data.groups.filter((group) => !MAIN_GROUP_IDS.includes(group.id)).flatMap((group) => group.models);
      const grids = [];
      const buildGrid = (tiles) => {
        const grid = ui.tileGrid(tiles, {
          selectedId, columns: TILE_COLUMNS,
          onSelect: async (tileId) => {
            if (tileId === selectedId) return;
            const result = await saveSetting(MODEL_PATH, tileId);
            if (!result) return; // keep the previous tile selected
            selectedId = tileId;
            grids.forEach((each) => each.setSelected(tileId));
            renderStrip();
          },
        });
        grids.push(grid);
        return grid;
      };
      const mainGrid = buildGrid([buildAutoTile(data.auto), ...mainModels.map(buildModelTile)]);

      let extraSection = null;
      if (extraModels.length) {
        const extraGrid = buildGrid(extraModels.map(buildModelTile));
        const extraOpenAtStart = extraModels.some((model) => model.key === selectedId);
        extraGrid.hidden = !extraOpenAtStart;
        const toggle = el('button', { type: 'button', class: 'link-button models-more-toggle' });
        const paintToggle = () => {
          toggle.textContent = extraGrid.hidden
            ? `Show English-only and distilled models (${extraModels.length})` : 'Hide English-only and distilled models';
        };
        toggle.addEventListener('click', () => { extraGrid.hidden = !extraGrid.hidden; paintToggle(); });
        paintToggle();
        extraSection = [toggle, extraGrid];
      }

      renderStrip();
      modelMount.replaceChildren(mainGrid, ...(extraSection || []), strip, downloadBar);
      if (data.downloading) startPolling();
    }

    async function refreshModels() {
      renderModelSection(await api.call('get_models'));
    }

    // ── Downloading ──
    async function startDownload(modelKey) {
      const result = await api.call('start_model_download', modelKey).catch(() => null);
      if (!result) return;
      if (!result.ok) return ui.toast(result.error, 'error');
      await refreshModels(); // shows the bar and the "Downloading" badge
      startPolling();
    }

    // Updates the bar every DOWNLOAD_POLL_MS; stops when the page is left or the download ends.
    function startPolling() {
      if (pollTimer) return;
      pollTimer = setInterval(async () => {
        if (!container.isConnected) { clearInterval(pollTimer); pollTimer = null; return; }
        const progress = await api.call('get_download_progress').catch(() => null);
        if (!progress) return;
        if (progress.state === 'running') return showProgress(progress);
        clearInterval(pollTimer);
        pollTimer = null;
        if (progress.state === 'done') ui.toast(`${progress.label} downloaded`, 'success');
        if (progress.state === 'error') ui.toast(`Download failed: ${progress.error}`, 'error');
        await refreshModels();
      }, DOWNLOAD_POLL_MS);
    }

    function showProgress(progress) {
      if (!progressRefs.fill) return;
      const known = progress.percent !== null;
      progressRefs.fill.classList.toggle('progress-indeterminate', !known);
      progressRefs.fill.style.width = known ? `${progress.percent}%` : '';
      progressRefs.label.textContent = known
        ? `${progress.percent}% · ${formatMegabytes(progress.done_bytes)} of ${formatMegabytes(progress.total_bytes)}`
        : `${formatMegabytes(progress.done_bytes)} downloaded`;
    }

    // ── Setting rows (language, performance, advanced) ──

    // Control factories. Each takes (spec, commit) and returns an element; commit(newValue)
    // saves and resolves to the stored value, or null when the save failed (put the control back).
    const controlFactories = {
      select(spec, commit) {
        let savedValue = spec.value;
        const select = ui.select(spec.options, spec.value, async (value) => {
          const stored = await commit(value);
          if (stored === null) select.value = savedValue; else savedValue = stored;
        });
        return select;
      },

      // "Run on": the same choices as a select, drawn as a segmented control.
      segmented(spec, commit) {
        let savedValue = spec.value;
        const options = spec.options.map((option) => ({ value: option.value, label: DEVICE_LABELS[option.value] || option.label }));
        const segmented = ui.segmented(options, spec.value, async (value) => {
          const stored = await commit(value);
          if (stored === null) segmented.value = savedValue; else savedValue = stored;
        });
        return segmented;
      },

      toggle(spec, commit) {
        let savedValue = !!spec.value;
        const toggle = ui.toggle(savedValue, async (checked) => {
          const stored = await commit(checked);
          if (stored === null) toggle.checked = savedValue; else savedValue = stored;
        });
        return toggle;
      },

      // Number or text field: saves on blur or Enter, snaps back to the last saved value on a bad one.
      input(spec, commit) {
        const isNumber = spec.kind === 'number';
        let savedValue = spec.value == null ? '' : spec.value;
        const input = el('input', {
          class: `models-input ${isNumber ? 'models-input-number' : 'models-input-text'}`,
          type: 'text', spellcheck: 'false', autocomplete: 'off',
          inputmode: isNumber ? 'decimal' : null,
          placeholder: spec.placeholder || '',
        });
        input.value = savedValue;
        input.addEventListener('keydown', (event) => { if (event.key === 'Enter') input.blur(); });
        input.addEventListener('change', async () => {
          const stored = await commit(input.value);
          if (stored === null) input.value = savedValue; else { savedValue = stored; input.value = stored; }
        });
        return spec.unit
          ? el('span', { class: 'models-input-group' }, input, el('span', { class: 'models-unit', text: spec.unit }))
          : input;
      },

      // "Languages you speak": toggle-able chips (several can be on; none = any language).
      chips(spec, commit) {
        let selected = [...spec.value];
        const group = el('div', { class: 'chip-group' });
        const chipByCode = new Map(spec.options.map((option) => [option.value,
          el('button', { type: 'button', class: 'choice-chip', text: option.label, 'data-value': option.value })]));
        const paint = () => chipByCode.forEach((chip, code) => chip.setAttribute('aria-pressed', String(selected.includes(code))));
        chipByCode.forEach((chip, code) => chip.addEventListener('click', async () => {
          const next = selected.includes(code) ? selected.filter((each) => each !== code) : [...selected, code];
          const stored = await commit(next.join(', '));
          if (stored !== null) selected = String(stored).split(',').map((each) => each.trim()).filter(Boolean);
          paint();
        }));
        group.append(...chipByCode.values());
        paint();
        return group;
      },
    };

    // One labelled row bound to a whisper.* path. A failed save flashes the error in the caption.
    // Returns the row plus its control, so callers can react to saved values.
    function buildSettingRow({ path, label, caption, spec, factory, onStored, rowClass }) {
      const row = ui.settingRow(label, caption, null);
      if (rowClass) row.classList.add(rowClass);
      const captionNode = row.querySelector('.setting-caption');
      let flashTimer = null;
      const flashError = (message) => {
        captionNode.textContent = message;
        captionNode.classList.add('models-error');
        clearTimeout(flashTimer);
        flashTimer = setTimeout(() => { captionNode.textContent = caption; captionNode.classList.remove('models-error'); }, ERROR_FLASH_MS);
      };
      const commit = async (value) => {
        const result = await saveSetting(path, value, flashError);
        if (!result) return null;
        if (onStored) onStored(result.value);
        return result.value;
      };
      row.querySelector('.setting-control').append(factory(spec, commit));
      return row;
    }

    // ── Views: main, and the Advanced drill-in ──
    const mainView = el('div', { class: 'models-view' });
    const advancedView = el('div', { class: 'models-view', hidden: true });
    let currentView = MAIN_VIEW;

    // The page title becomes the breadcrumb "Models › Advanced" (clicking "Models"
    // returns here), so no separate back link or heading is needed.
    function openView(view) {
      currentView = view;
      mainView.hidden = view !== MAIN_VIEW;
      advancedView.hidden = view !== ADVANCED_VIEW;
      subtitle.hidden = view === ADVANCED_VIEW;
      ui.setPageTitle(view === ADVANCED_VIEW ? 'Advanced' : null, () => openView(MAIN_VIEW));
      document.getElementById('content-scroll').scrollTop = 0;
    }

    // Esc goes back one level, unless the user is editing a field (Esc cancels that edit).
    removeEscapeListener();
    const onEscape = (event) => {
      if (!container.isConnected) { removeEscapeListener(); return; }
      const editing = /^(INPUT|SELECT|TEXTAREA)$/.test((event.target && event.target.tagName) || '');
      if (event.key === 'Escape' && !editing && currentView !== MAIN_VIEW) openView(MAIN_VIEW);
    };
    document.addEventListener('keydown', onEscape);
    removeEscapeListener = () => document.removeEventListener('keydown', onEscape);

    // ── Mount ──
    const subtitle = el('p', { class: 'page-subtitle', text: 'Pick the speech model and how it runs. Models download once, then work offline.' });
    container.append(
      subtitle, noticeSlot, mainView, advancedView);

    // Independent loads: one failing must not blank the page.
    const [modelData, settings] = await Promise.all([
      api.call('get_models').catch(() => null),
      api.call('get_model_settings').catch(() => null),
    ]);
    const unavailable = () => ui.card(null, ui.emptyState('Could not load this section'));

    mainView.append(
      ui.sectionHeader('Model', el('span', { class: 'section-caption', text: 'Auto picks the best one for this computer.' })),
      modelMount,
      ...(settings ? buildSettingsSections(settings) : [unavailable()]),
      ui.sectionHeader('Storage'),
      ui.card(null, ui.settingRow('Model cache folder',
        'Downloaded models live here. Delete a model\'s folder to free the space.',
        ui.button('Open folder', async () => {
          const opened = await api.call('open_model_cache_folder').catch(() => null);
          if (opened === false) ui.toast('No models downloaded yet.', 'info');
        }, { icon: 'external' }))));
    if (modelData) renderModelSection(modelData); else modelMount.append(unavailable());

    // Optional deep link: location.hash = '#advanced' before navigating here.
    const requestedView = location.hash.replace('#', '');
    if (requestedView) history.replaceState(null, '', location.pathname + location.search);
    if (settings && requestedView === ADVANCED_VIEW) openView(ADVANCED_VIEW);

    // ── Language, performance and Advanced sections ──
    // Fills the Advanced view as a side effect and returns the main-view nodes.
    function buildSettingsSections(settings) {
      const row = (path, label, caption, factory, extra = {}) =>
        buildSettingRow({ path, label, caption, spec: settings[path], factory, ...extra });

      // Languages you speak only matter while Language is Auto-detect.
      const spokenRow = row('whisper.languages', 'Languages you speak',
        'Keeps auto-detection to these, so a short or accented clip is not mistaken for another language. None selected means any language.',
        controlFactories.chips);
      const muteSpokenRow = (languageValue) => spokenRow.classList.toggle('models-row-muted', languageValue !== 'auto');
      const languageRow = row('whisper.language', 'Language',
        'Auto-detect works for most people. Pick one to force it.', controlFactories.select,
        { onStored: muteSpokenRow });
      muteSpokenRow(settings['whisper.language'].value);

      const deviceCaption = ui.platform === 'macos'
        ? 'Macs have no CUDA graphics card support here, so leave this on CPU.'
        : 'GPU needs a supported graphics card and its drivers. See the GPU setup guide.';
      const performanceCard = ui.card(null, [
        row('whisper.device', 'Run on', deviceCaption, controlFactories.segmented),
        row('whisper.unload_after_minutes', 'Free memory when idle',
          'Unloads the model after this many idle minutes and reloads it on your next recording. 0 keeps it loaded. CPU only.',
          controlFactories.input),
      ]);

      advancedView.append(ui.card(null, [
        row('whisper.backend', 'Engine', 'faster-whisper suits most computers. whisper.cpp and Parakeet are optional installs.', controlFactories.select),
        row('whisper.compute_type', 'Compute type', 'int8 is smallest and fastest. float16 needs a GPU.', controlFactories.select),
        row('whisper.beam_size', 'Beam size', 'Higher is a little more accurate but slower (1 to 10). Parakeet ignores it.', controlFactories.input),
        row('whisper.initial_prompt', 'Initial prompt', 'A sentence that nudges style, spelling or language variant. Parakeet ignores it.', controlFactories.input),
        row('whisper.prompt_from_selection', 'Use selected text as context',
          'Reads the text you have selected when recording starts and uses it as the prompt, for edits to existing text.', controlFactories.toggle),
        row('whisper.task', 'Task', 'Translate writes everything in English whatever you speak. Parakeet cannot translate.', controlFactories.select),
      ]));

      const advancedLink = el('button', { type: 'button', class: 'settings-more-link models-advanced-link', onclick: () => openView(ADVANCED_VIEW) },
        el('span', { text: 'Advanced' }), el('span', { class: 'settings-more-chevron', 'aria-hidden': 'true', text: '›' }));

      return [
        ui.sectionHeader('Language'),
        ui.card(null, [languageRow, spokenRow]),
        ui.sectionHeader('Performance'),
        performanceCard,
        el('div', { class: 'models-advanced-link-card' }, ui.card(null, advancedLink)),
      ];
    }
  },
});

})();
