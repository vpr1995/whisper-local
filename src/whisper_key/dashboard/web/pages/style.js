// pages/style.js
// Style page: how dictation is shaped. The writing style chips, the clean-up level,
// and the AI provider with its polish switch. Every control saves as it changes.
// Data and actions come from api/style.py.

// Wrapped in a function so these helper names stay private: all page scripts share one global scope.
(() => {

// ── Small helpers ────────────────────────────────────────────────────────────

// One boxed section, like a Settings card, so each group reads as its own unit.
const sectionCard = (...children) => ui.card(null, el('div', { class: 'pad style-section' }, children));

// Runs a save call and shows its error toast; returns the result only when it succeeded.
async function saveOrToast(api, methodName, ...args) {
  const result = await api.call(methodName, ...args).catch(() => null);
  if (!result) return null;
  if (!result.ok) { ui.toast(result.error, 'error'); return null; }
  return result;
}

// ── Writing style ────────────────────────────────────────────────────────────

// Chips for every style, with a sample sentence and a description for the selected one.
function buildStyleSection(api, data) {
  const noStyle = {
    id: '', name: 'None', sample: '',
    description: 'No preset: your individual capitalization and punctuation settings apply.',
  };
  const allStyles = [noStyle, ...data.styles];
  const styleById = (styleId) => allStyles.find((style) => style.id === styleId) || noStyle;

  const sampleText = el('span', { class: 'style-sample' });
  const sampleDescription = el('span', { class: 'style-sample-description' });
  const showStyle = (styleId) => {
    const style = styleById(styleId);
    sampleText.textContent = style.sample ? `“${style.sample}”` : 'Your individual settings apply';
    sampleText.dataset.empty = style.sample ? 'false' : 'true';
    sampleDescription.textContent = style.description;
  };

  let savedId = data.current;
  const chips = ui.chipGroup(
    allStyles.map((style) => ({ value: style.id, label: style.name })), savedId,
    async (styleId) => {
      const result = await saveOrToast(api, 'set_writing_style', styleId);
      if (!result) { chips.value = savedId; return; } // revert the chip
      savedId = styleId;
      showStyle(styleId);
      ui.toast('Style saved. It applies to your next dictation.', 'success');
    },
    {
      actionLabel: '+ Custom',
      onAction: async () => {
        const opened = await api.call('open_user_settings_file').catch(() => false);
        ui.toast(opened ? 'Add your style under postprocess.styles, then reopen this page.'
          : 'Could not open user_settings.yaml.', opened ? 'info' : 'error');
      },
    });

  showStyle(savedId);
  return sectionCard(chips, el('div', { class: 'style-strip style-sample-bar' }, sampleText, sampleDescription));
}

// ── Clean-up ─────────────────────────────────────────────────────────────────

// One row: what the current level does, and the segmented control that changes it.
function buildCleanupSection(api, data) {
  const levelById = (levelId) => data.levels.find((level) => level.id === levelId);
  const description = el('div', { class: 'setting-caption style-cleanup-text' });
  const showLevel = (levelId) => { description.textContent = levelById(levelId).description; };

  let savedId = data.current;
  const segmented = ui.segmented(
    data.levels.map((level) => ({ value: level.id, label: level.name })), savedId,
    async (levelId) => {
      const result = await saveOrToast(api, 'set_cleanup_level', levelId);
      if (!result) { segmented.value = savedId; return; }
      savedId = levelId;
      showLevel(levelId);
      ui.toast('Clean-up saved. It applies to your next dictation.', 'success');
    });

  showLevel(savedId);
  return ui.card(null, el('div', { class: 'setting-row style-compact-row' },
    el('div', { class: 'setting-text' }, description),
    el('div', { class: 'setting-control' }, segmented)));
}

// ── AI (provider, polish, Ollama server) ─────────────────────────────────────

const AI_PROVIDER_OPTIONS = [
  { value: 'auto', label: 'Auto' },
  { value: 'apple_intelligence', label: 'Apple Intelligence' },
  { value: 'ollama', label: 'Ollama' },
];

// Provider choice with a live status caption, the polish toggle, and the Ollama server
// fields (only while Ollama is the AI that answers). Each control saves on change and
// snaps back to the saved value if the API rejects it.
function buildAiSection(api, saved) {
  const statusCaption = el('div', { class: 'setting-caption', text: saved.status_text });
  const ollamaFields = el('div', { class: 'style-ai-fields' });
  const refresh = (settings) => {
    Object.assign(saved, settings);
    statusCaption.textContent = saved.status_text;
    polishToggle.checked = saved.polish;
    ollamaFields.hidden = saved.active_provider !== 'ollama';
  };
  ollamaFields.hidden = saved.active_provider !== 'ollama';

  const providerChoice = ui.segmented(AI_PROVIDER_OPTIONS, saved.provider, async (provider) => {
    const result = await saveOrToast(api, 'set_ai_setting', 'provider', provider);
    if (!result) { providerChoice.value = saved.provider; return; }
    refresh(result.settings);
    ui.toast('AI provider saved. It applies right away.', 'success');
  });

  // One switch: polish every dictation with the AI, or none.
  const polishToggle = ui.toggle(saved.polish, async (enabled) => {
    const result = await saveOrToast(api, 'set_ai_setting', 'polish', enabled);
    if (!result) { polishToggle.checked = !enabled; return; }
    refresh(result.settings);
    ui.toast(`AI polish is ${enabled ? 'on' : 'off'}. It applies to your next dictation.`, 'success');
  });

  // One labelled input row for an Ollama field.
  const fieldRow = (key, label, caption, inputProps, suffix) => {
    const input = el('input', { class: 'style-input', type: 'text', spellcheck: 'false', ...inputProps });
    input.value = saved[key];
    input.addEventListener('change', async () => {
      const result = await saveOrToast(api, 'set_ai_setting', key, input.value);
      if (result) { refresh(result.settings); input.value = saved[key]; ui.toast(`${label} saved.`, 'success'); }
      else input.value = saved[key];
    });
    return ui.settingRow(label, caption, el('span', { class: 'style-input-group' }, input, suffix));
  };

  ollamaFields.append(
    fieldRow('model', 'Ollama model', 'The model that answers.', { class: 'style-input style-input-text' }),
    fieldRow('endpoint', 'Ollama server address', 'Where Ollama is listening.', { class: 'style-input style-input-text' }),
    fieldRow('timeout', 'Give up after', 'Polish keeps your plain text if Ollama is slower.',
      { class: 'style-input style-input-number', inputmode: 'numeric' }, el('span', { class: 'setting-caption', text: 'sec' })));

  return ui.card(null, [
    el('div', { class: 'setting-row' },
      el('div', { class: 'setting-text' }, el('div', { class: 'setting-label', text: 'AI provider' }), statusCaption),
      el('div', { class: 'setting-control' }, providerChoice)),
    ui.settingRow('AI polish', 'Removes ums, repeats and false starts and fixes punctuation with the AI. Adds about half a second.', polishToggle),
    ollamaFields,
  ]);
}

// ── Page ─────────────────────────────────────────────────────────────────────

registerPage('style', {
  title: 'Style',
  async render(container, api) {
    container.append(
      el('p', { class: 'page-subtitle', text: 'How your dictation is shaped before it is typed.' }));

    // Independent blocks: one failing call must not blank the page.
    const [styles, cleanup, aiSettings] = await Promise.all([
      api.call('get_writing_styles').catch(() => null),
      api.call('get_cleanup_levels').catch(() => null),
      api.call('get_ai_settings').catch(() => null),
    ]);
    const unavailable = () => ui.card(null, ui.emptyState('Could not load this section'));

    container.append(...[
      ui.sectionHeader('Writing style'),
      styles ? buildStyleSection(api, styles) : unavailable(),
      ui.sectionHeader('Clean-up', el('span', { class: 'section-caption',
        text: 'How much to tidy what you said before it is typed.' })),
      cleanup ? buildCleanupSection(api, cleanup) : unavailable(),
      ui.sectionHeader('AI', el('span', { class: 'section-caption',
        text: 'Used by polish and rephrase.' })),
      aiSettings ? buildAiSection(api, aiSettings) : unavailable(),
    ]);
  },
});

})();
