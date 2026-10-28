// pages/style.js
// Style page: how dictation is shaped. A profile tile grid (preset bundles of settings),
// the writing style chips, the clean-up level, the optional Ollama polish, and two
// compact lists (AI transforms, per-app rules) that open their YAML for editing.
// Everything except transforms and app rules is saved from here. Data and actions
// come from api/style.py.

// Wrapped in a function so these helper names stay private: all page scripts share one global scope.
(() => {

const COMPACT_VISIBLE_ITEMS = 3; // rows shown in the transforms / app-rules cards before "+ N more"

// Tile icon per shipped profile; user-defined profiles fall back to the sparkle.
const PROFILE_ICONS = { dictation: 'mic', chat: 'chat', code: 'code', notes: 'notes', translate: 'globe' };
const FALLBACK_PROFILE_ICON = 'style';

// ── Small helpers ────────────────────────────────────────────────────────────

const tag = (text) => el('span', { class: 'style-tag', text });

// Appends a full stop to a description that has none ("General-purpose voice typing" -> "...typing.").
const asSentence = (text) => (!text || /[.!?]$/.test(text) ? text : `${text}.`);

// One boxed section, like a Settings card, so each group reads as its own unit.
const sectionCard = (...children) => ui.card(null, el('div', { class: 'pad style-section' }, children));

// Runs a save call and shows its error toast; returns the result only when it succeeded.
async function saveOrToast(api, methodName, ...args) {
  const result = await api.call(methodName, ...args).catch(() => null);
  if (!result) return null;
  if (!result.ok) { ui.toast(result.error, 'error'); return null; }
  return result;
}

// ── Profile ──────────────────────────────────────────────────────────────────

// Tile grid plus the strip under it describing the selected profile. Applying a profile
// overwrites settings that are read at startup, so a successful pick asks for a restart.
function buildProfileSection(api, data, noticeSlot) {
  let activeId = data.active;
  const profileById = (profileId) => data.profiles.find((profile) => profile.id === profileId);

  const strip = el('div', { class: 'style-strip' });
  const showProfile = (profileId) => {
    const profile = profileById(profileId);
    strip.replaceChildren(
      el('span', { class: 'style-strip-text', text: asSentence(profile.description) || profile.name }),
      profile.changes.length ? el('span', { class: 'style-strip-tags' },
        el('span', { class: 'style-strip-label', text: 'Sets' }), profile.changes.map(tag)) : null);
  };

  const grid = ui.tileGrid(
    data.profiles.map((profile) => ({
      id: profile.id, title: profile.name, icon: PROFILE_ICONS[profile.id] || FALLBACK_PROFILE_ICON,
    })),
    {
      selectedId: activeId,
      columns: 5,
      onSelect: async (profileId) => {
        if (profileId === activeId) return;
        const result = await saveOrToast(api, 'set_profile', profileId);
        if (!result) return; // keep the previous tile selected
        activeId = profileId;
        grid.setSelected(profileId);
        showProfile(profileId);
        ui.toast('Profile applied. Restart to apply.', 'success');
        noticeSlot.replaceChildren(ui.restartNotice(api, 'Profiles change settings that Whisper Local reads at startup.'));
      },
    });

  showProfile(activeId);
  return sectionCard(grid, strip,
    el('p', { class: 'style-footnote', text: 'Applying one overwrites those settings, including the model.' }));
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

// ── AI polish (Ollama) ───────────────────────────────────────────────────────

// Toggle plus, when on, model / server address / timeout fields. Each field saves when
// it loses focus or on Enter, and snaps back to the saved value if the API rejects it.
function buildPolishSection(api, saved) {
  const fields = el('div', { class: 'style-polish-fields' });
  fields.hidden = !saved.enabled;

  const toggle = ui.toggle(saved.enabled, async (enabled) => {
    const result = await saveOrToast(api, 'set_ollama_setting', 'enabled', enabled);
    if (!result) { toggle.checked = !enabled; return; }
    saved.enabled = enabled;
    fields.hidden = !enabled;
    ui.toast(enabled ? 'AI polish is on. It applies to your next dictation.' : 'AI polish is off.', 'success');
  });

  // One labelled input row for a postprocess.ollama.<key> field.
  const fieldRow = (key, label, caption, inputProps, suffix) => {
    const input = el('input', { class: 'style-input', type: 'text', spellcheck: 'false', ...inputProps });
    input.value = saved[key];
    input.addEventListener('change', async () => {
      const result = await saveOrToast(api, 'set_ollama_setting', key, input.value);
      if (result) { saved[key] = input.value.trim(); input.value = saved[key]; ui.toast(`${label} saved.`, 'success'); }
      else input.value = saved[key];
    });
    return ui.settingRow(label, caption, el('span', { class: 'style-input-group' }, input, suffix));
  };

  fields.append(
    fieldRow('model', 'Model', 'The Ollama model that does the polishing.', { class: 'style-input style-input-text' }),
    fieldRow('endpoint', 'Server address', 'Where Ollama is listening.', { class: 'style-input style-input-text' }),
    fieldRow('timeout', 'Give up after', 'Falls back to your plain text if Ollama is slow.',
      { class: 'style-input style-input-number', inputmode: 'numeric' }, el('span', { class: 'setting-caption', text: 'sec' })));

  return ui.card(null, [
    ui.settingRow('Polish with a local AI (Ollama)',
      'Fixes punctuation with a model on your own computer. Needs Ollama running.', toggle),
    fields,
  ]);
}

// ── Transforms and per-app rules ─────────────────────────────────────────────

function buildTransformsCard(api, transforms) {
  return ui.compactListCard({
    title: 'AI transforms',
    actionLabel: 'Edit file',
    onAction: () => api.call('open_transforms_file'),
    items: transforms,
    maxVisible: COMPACT_VISIBLE_ITEMS,
    emptyText: 'No transforms yet. Add some in transforms.yaml.',
    renderItem: (transform) => el('div', { class: `compact-row${transform.keys.length ? '' : ' compact-row-dim'}`, title: transform.description },
      el('span', { class: 'compact-row-name', text: transform.name }),
      transform.keys.length ? ui.hotkey(transform.keys) : el('span', { class: 'compact-row-sub', text: 'no hotkey' })),
  });
}

function buildAppRulesCard(api, rules) {
  return ui.compactListCard({
    title: 'Per-app rules',
    actionLabel: 'Edit file',
    onAction: () => api.call('open_app_rules_file'),
    items: rules,
    maxVisible: COMPACT_VISIBLE_ITEMS,
    emptyText: 'No app rules yet. Add some in app_rules.yaml.',
    renderItem: (rule) => {
      const appCount = `${rule.apps.length} ${rule.apps.length === 1 ? 'app' : 'apps'}`;
      const firstEffect = (rule.effects[0] || '').replace(/^Style: /, '');
      return el('div', { class: 'compact-row', title: [rule.apps.join(', '), rule.effects.join(' · ')].filter(Boolean).join('\n') },
        el('span', { class: 'compact-row-name', text: rule.name }),
        el('span', { class: 'compact-row-sub', text: [appCount, firstEffect].filter(Boolean).join(' · ') }));
    },
  });
}

// ── Page ─────────────────────────────────────────────────────────────────────

registerPage('style', {
  title: 'Style',
  async render(container, api) {
    const noticeSlot = el('div', { class: 'notice-slot' });
    container.append(
      el('p', { class: 'page-subtitle', text: 'How your dictation is shaped before it is typed.' }),
      noticeSlot);

    // Independent blocks: one failing call must not blank the page.
    const [profiles, styles, cleanup, polish, transforms, appRules] = await Promise.all([
      api.call('get_profiles').catch(() => null),
      api.call('get_writing_styles').catch(() => null),
      api.call('get_cleanup_levels').catch(() => null),
      api.call('get_ollama_polish').catch(() => null),
      api.call('get_transforms').catch(() => []),
      api.call('get_app_rules').catch(() => []),
    ]);
    const unavailable = () => ui.card(null, ui.emptyState('Could not load this section'));

    container.append(...[
      ui.sectionHeader('Profile', ui.button('Add your own', () => api.call('open_profiles_file'), { variant: 'ghost' })),
      profiles ? buildProfileSection(api, profiles, noticeSlot) : unavailable(),
      ui.sectionHeader('Writing style', el('span', { class: 'section-caption', text: 'Per-app rules can override it.' })),
      styles ? buildStyleSection(api, styles) : unavailable(),
      ui.sectionHeader('Clean-up', el('span', { class: 'section-caption',
        text: 'How much to tidy what you said before it is typed.' })),
      cleanup ? buildCleanupSection(api, cleanup) : unavailable(),
      ui.sectionHeader('AI polish'),
      polish ? buildPolishSection(api, polish) : unavailable(),
      el('div', { class: 'style-duo' }, buildTransformsCard(api, transforms), buildAppRulesCard(api, appRules)),
      el('p', { class: 'style-footnote',
        text: 'Select text, press a transform\'s hotkey and a local Ollama model rewrites it.' }),
    ]);
  },
});

})();
