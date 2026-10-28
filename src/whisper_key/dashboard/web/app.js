// app.js
// Dashboard shell: a tiny router, the page registry, the Python-API helper and the
// shared `ui` component helpers. No framework, no build step.
//
// ── PAGE CONTRACT (page agents rely on this) ────────────────────────────────
//   registerPage(id, { title, render(container, api), header })
//     id        one of: home history models vocabulary style settings about
//     title     shown as the page's <h1> (set header:false to draw your own header)
//     render    may be async; fill `container` (an empty <div class="page-body">).
//               Throwing shows an error card and logs to dashboard.log.
//     api       { call(methodName, ...args) } -> Promise. Wraps
//               window.pywebview.api[methodName]; on failure shows a toast and
//               rethrows, so use try/catch when you want to handle it yourself.
//   window.dashboard.navigate(id)  switch page (e.g. a "See all" link)
//
// ── ui HELPERS (window.ui; all DOM-based, text goes through textContent) ────
//   ui.card(title|null, children)                  rounded card; children = Node | Node[]
//   ui.sectionHeader(text, actionNode?)            quiet header above a card
//   ui.settingRow(label, caption, controlEl)       label+caption left, control right
//   ui.toggle(checked, onChange(checked))          switch; element has .checked get/set
//   ui.select(options, value, onChange(value))    options: ["a","b"] or [{value,label}]
//   ui.button(label, onClick, {variant, icon})     variant: default|primary|danger|ghost
//   ui.keycap(text)                                one key
//   ui.toast(message, kind)                        status-bar message; kind: info|success|error.
//                                                  At rest the bar shows the app's state (Ready, Sleeping…).
//   ui.emptyState(title, text)
//   ui.restartNotice(api, text)                    "Restart to apply" card with a Restart now
//                                                  button (asks the app; falls back to a tray hint)
//   ui.tileGrid(tiles, {selectedId, onSelect(id), columns})
//                                                  selectable tiles; tiles = [{id, title, subtitle?, badge?,
//                                                  icon?}] (icon = an ICON_PATHS name). `columns` (default 3) is the
//                                                  column count; windows under ~960px wide use at most 3.
//                                                  The selected tile gets an accent ring + check.
//                                                  Returns the grid; grid.setSelected(id) re-marks the selection.
//   ui.chipGroup(options, value, onChange(value), {actionLabel, onAction})
//                                                  single-select pills; options as in ui.select. The optional
//                                                  dashed trailing chip ("+ Custom") runs onAction and is never
//                                                  selected. Element has .value get/set.
//   ui.segmented(options, value, onChange(value))  segmented control (options as in ui.select); .value get/set
//   ui.compactListCard({title, actionLabel, onAction, items, maxVisible=3, renderItem(item) -> Node, emptyText})
//                                                  card with a header link (e.g. "Edit file"), the first
//                                                  maxVisible items and a "+ N more" toggle that expands inline.
//                                                  Style rows with .compact-row > .compact-row-name + .compact-row-sub.
//   extras: ui.el(tag, props, ...children)  ui.icon(name)  ui.hotkey(combo|parts[])
//           ui.statTile(label, value, hint?)  ui.timeAgo(isoString)  ui.platform
//   Free-form content inside a card should be wrapped in <div class="pad">.
//   Never use innerHTML with user content (transcripts etc.): use textContent / ui.el.

// ── DOM helper ───────────────────────────────────────────────────────────────

// Builds an element. props: class, text, on<event> handlers, anything else becomes
// an attribute. Children may be Nodes, strings (become text nodes) or arrays.
function el(tag, props, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(props || {})) {
    if (value == null || value === false) continue;
    if (key === 'class') node.className = value;
    else if (key === 'text') node.textContent = value;
    else if (key.startsWith('on') && typeof value === 'function') node.addEventListener(key.slice(2), value);
    else node.setAttribute(key, value === true ? '' : value);
  }
  appendChildren(node, children);
  return node;
}

function appendChildren(node, children) {
  for (const child of children.flat(Infinity)) {
    if (child == null || child === false) continue;
    node.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
}

// ── Icons ────────────────────────────────────────────────────────────────────

// Simple 24x24 stroke icons (trusted static path data, drawn for this app).
const ICON_PATHS = {
  home: ['M3 11l9-8 9 8', 'M5 10v10h14V10', 'M10 20v-6h4v6'],
  history: ['M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18z', 'M12 7v5l3 2'],
  models: ['M7 6h10a1 1 0 0 1 1 1v10a1 1 0 0 1-1 1H7a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1z', 'M10 10h4v4h-4z',
           'M9 2v4M15 2v4M9 18v4M15 18v4M2 9h4M2 15h4M18 9h4M18 15h4'],
  vocabulary: ['M4 19.5A2.5 2.5 0 0 1 6.5 17H20', 'M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2z'],
  style: ['M12 3l1.8 5.2L19 10l-5.2 1.8L12 17l-1.8-5.2L5 10l5.2-1.8z', 'M19 16v4M17 18h4'],
  settings: ['M4 6h8M18 6h2M4 12h2M12 12h8M4 18h10M20 18h0', 'M15 6a2.5 2.5 0 1 0 0 .01', 'M9 12a2.5 2.5 0 1 0 0 .01',
             'M17 18a2.5 2.5 0 1 0 0 .01'],
  about: ['M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18z', 'M12 16v-4M12 8h.01'],
  copy: ['M11 9h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2h-8a2 2 0 0 1-2-2v-8a2 2 0 0 1 2-2z', 'M5 15V6a2 2 0 0 1 2-2h9'],
  check: ['M5 12l5 5L20 7'],
  external: ['M14 4h6v6M20 4l-9 9', 'M18 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1h5'],
  mic: ['M12 3a3 3 0 0 0-3 3v6a3 3 0 0 0 6 0V6a3 3 0 0 0-3-3z', 'M5 11a7 7 0 0 0 14 0', 'M12 18v3'],
  chat: ['M21 12a8 8 0 0 1-11.6 7.1L4 20l1-4.6A8 8 0 1 1 21 12z'],
  code: ['M8 7l-5 5 5 5', 'M16 7l5 5-5 5', 'M14 4l-4 16'],
  notes: ['M6 3h9l4 4v14H6z', 'M14 3v5h5', 'M9 13h7M9 17h5'],
  globe: ['M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18z', 'M3 12h18', 'M12 3c3 3 3 15 0 18M12 3c-3 3-3 15 0 18'],
};

// Returns an <svg class="icon"> for a name from ICON_PATHS; inherits currentColor.
function icon(name) {
  const svgNamespace = 'http://www.w3.org/2000/svg';
  const svg = document.createElementNS(svgNamespace, 'svg');
  svg.setAttribute('viewBox', '0 0 24 24');
  svg.setAttribute('fill', 'none');
  svg.setAttribute('stroke', 'currentColor');
  svg.setAttribute('stroke-width', '1.8');
  svg.setAttribute('stroke-linecap', 'round');
  svg.setAttribute('stroke-linejoin', 'round');
  svg.setAttribute('class', 'icon');
  svg.setAttribute('aria-hidden', 'true');
  for (const pathData of ICON_PATHS[name] || []) {
    const path = document.createElementNS(svgNamespace, 'path');
    path.setAttribute('d', pathData);
    svg.append(path);
  }
  return svg;
}

// ── ui components ────────────────────────────────────────────────────────────

const ui = {
  el,
  icon,
  platform: 'macos', // replaced with the real value once get_app_info answers

  card(title, children) {
    return el('section', { class: 'card' }, title ? el('h3', { class: 'card-title', text: title }) : null, children);
  },

  sectionHeader(text, actionNode) {
    return el('h2', { class: 'section-header' }, el('span', { text }), actionNode);
  },

  settingRow(label, caption, controlEl) {
    return el('div', { class: 'setting-row' },
      el('div', { class: 'setting-text' },
        el('div', { class: 'setting-label', text: label }),
        caption ? el('div', { class: 'setting-caption', text: caption }) : null),
      el('div', { class: 'setting-control' }, controlEl));
  },

  toggle(checked, onChange) {
    const input = el('input', { type: 'checkbox', role: 'switch' });
    input.checked = !!checked;
    input.addEventListener('change', () => onChange && onChange(input.checked));
    const root = el('label', { class: 'switch' }, input,
      el('span', { class: 'switch-track' }, el('span', { class: 'switch-thumb' })));
    Object.defineProperty(root, 'checked', { get: () => input.checked, set: (value) => { input.checked = !!value; } });
    return root;
  },

  select(options, value, onChange) {
    const select = el('select', { class: 'select' });
    for (const option of options) {
      const { value: optionValue, label } = typeof option === 'object' ? option : { value: option, label: option };
      select.append(el('option', { value: optionValue, text: label }));
    }
    select.value = value;
    select.addEventListener('change', () => onChange && onChange(select.value));
    return select;
  },

  button(label, onClick, { variant = 'default', icon: iconName } = {}) {
    const button = el('button', { type: 'button', class: `button${variant === 'default' ? '' : ' button-' + variant}` },
      iconName ? icon(iconName) : null, el('span', { text: label }));
    button.addEventListener('click', (event) => onClick && onClick(event, button));
    return button;
  },

  keycap(text) {
    return el('kbd', { class: 'keycap', text });
  },

  // A combo like "ctrl+option" (or ["ctrl","option"]) as a row of keycaps, with
  // platform-appropriate labels (⌥ ⌘ ⇧ and the word "ctrl" on macOS; Ctrl/Win/Alt/Shift on Windows).
  hotkey(combo) {
    const parts = Array.isArray(combo) ? combo : String(combo).split('+');
    return el('span', { class: 'keys' }, parts.map((part) => {
      const cap = ui.keycap(keyLabel(part.trim()));
      cap.title = part.trim(); // hover shows the plain word behind a symbol like ⌥
      return cap;
    }));
  },

  // ── Selection components (tiles, chips, segmented) ──

  // Grid of selectable tiles. Real <button>s with aria-pressed, so Tab/Enter/Space
  // work for free; arrow keys also move focus between tiles. Selecting calls
  // onSelect(id); the CALLER decides whether to accept it and calls grid.setSelected(id).
  tileGrid(tiles, { selectedId = null, onSelect, columns = 3 } = {}) {
    const grid = el('div', { class: 'tile-grid', role: 'group' });
    grid.style.setProperty('--tile-columns', columns);
    grid.style.setProperty('--tile-columns-narrow', Math.min(columns, 3));
    const buttons = tiles.map((tile) => {
      const button = el('button', { type: 'button', class: 'tile', 'data-id': tile.id },
        tile.icon ? el('span', { class: 'tile-icon' }, icon(tile.icon)) : null,
        el('span', { class: 'tile-text' },
          el('span', { class: 'tile-title', text: tile.title }),
          tile.subtitle ? el('span', { class: 'tile-subtitle', text: tile.subtitle }) : null),
        tile.badge ? el('span', { class: 'tile-badge', text: tile.badge }) : null,
        el('span', { class: 'tile-check' }, icon('check')));
      button.addEventListener('click', () => onSelect && onSelect(tile.id));
      return button;
    });
    grid.append(...buttons);
    grid.setSelected = (id) => buttons.forEach((button) => button.setAttribute('aria-pressed', String(button.dataset.id === id)));
    grid.setSelected(selectedId);
    grid.addEventListener('keydown', (event) => {
      const step = { ArrowRight: 1, ArrowDown: 1, ArrowLeft: -1, ArrowUp: -1 }[event.key];
      const at = buttons.indexOf(document.activeElement);
      if (!step || at < 0) return;
      event.preventDefault();
      buttons[(at + step + buttons.length) % buttons.length].focus();
    });
    return grid;
  },

  // Shared by chipGroup and segmented: a row of aria-pressed buttons, one pressed at a time.
  _pressedButtons(root, options, value, onChange, buttonClass) {
    const normalized = options.map((option) => (typeof option === 'object' ? option : { value: option, label: option }));
    const buttons = normalized.map((option) => {
      const button = el('button', { type: 'button', class: buttonClass, text: option.label, 'data-value': option.value });
      button.addEventListener('click', () => {
        if (root.value === option.value) return;
        root.value = option.value;
        onChange && onChange(option.value);
      });
      return button;
    });
    let current = value;
    Object.defineProperty(root, 'value', {
      get: () => current,
      set: (next) => {
        current = next;
        buttons.forEach((button) => button.setAttribute('aria-pressed', String(button.dataset.value === String(next))));
      },
    });
    root.value = value;
    return buttons;
  },

  chipGroup(options, value, onChange, { actionLabel, onAction } = {}) {
    const root = el('div', { class: 'chip-group', role: 'group' });
    root.append(...ui._pressedButtons(root, options, value, onChange, 'choice-chip'));
    if (actionLabel) root.append(el('button', { type: 'button', class: 'choice-chip choice-chip-action', text: actionLabel,
      onclick: () => onAction && onAction() }));
    return root;
  },

  segmented(options, value, onChange) {
    const root = el('div', { class: 'segmented', role: 'group' });
    root.append(...ui._pressedButtons(root, options, value, onChange, 'segmented-option'));
    return root;
  },

  // Card with a title + link header, the first `maxVisible` items and an inline "+ N more" toggle.
  compactListCard({ title, actionLabel, onAction, items = [], maxVisible = 3, renderItem, emptyText = 'Nothing here yet.' }) {
    const list = el('div', { class: 'compact-list' });
    const hiddenCount = Math.max(0, items.length - maxVisible);
    let expanded = false;
    const toggle = el('button', { type: 'button', class: 'link-button compact-more' });
    const redraw = () => {
      list.replaceChildren(...(expanded ? items : items.slice(0, maxVisible)).map(renderItem));
      toggle.textContent = expanded ? 'Show less' : `+ ${hiddenCount} more`;
      toggle.setAttribute('aria-expanded', String(expanded));
    };
    toggle.addEventListener('click', () => { expanded = !expanded; redraw(); });
    redraw();
    return el('section', { class: 'card compact-card' },
      el('div', { class: 'compact-header' },
        el('h3', { class: 'compact-title', text: title }),
        actionLabel ? el('button', { type: 'button', class: 'link-button compact-action', text: actionLabel,
          onclick: () => onAction && onAction() }) : null),
      items.length ? list : el('div', { class: 'compact-empty', text: emptyText }),
      hiddenCount > 0 ? toggle : null);
  },

  statTile(label, value, hint) {
    return el('div', { class: 'stat-tile' },
      el('div', { class: 'stat-label', text: label }),
      el('div', { class: 'stat-value', text: value }),
      hint ? el('div', { class: 'stat-hint', text: hint }) : null);
  },

  emptyState(title, text) {
    return el('div', { class: 'empty-state' },
      el('div', { class: 'empty-state-title', text: title }),
      text ? el('div', { class: 'empty-state-text', text }) : null);
  },

  // Turns the current page's title into a breadcrumb for a drill-in view, e.g.
  // "Settings › More shortcuts" (clicking the parent goes back), so the page name is not repeated in a separate
  // back link. Pass no `subTitle` to go back to the plain page title.
  setPageTitle(subTitle, onBack) {
    const title = document.querySelector('#page-mount .page-title');
    if (!title) return;
    const pageName = pages.get(currentPageId)?.title || '';
    if (!subTitle) { title.replaceChildren(pageName); return; }
    title.replaceChildren(
      el('button', { type: 'button', class: 'page-crumb', 'aria-label': `Back to ${pageName}`, onclick: onBack }, pageName),
      el('span', { class: 'page-crumb-sep', 'aria-hidden': 'true' }, '\u203a'),
      subTitle);
  },

  // "Restart to apply" card shared by every page whose change is read at startup.
  // `text` says what will happen after the restart. If the app can't be reached
  // (e.g. dashboard launched from a terminal) the user is pointed at the tray menu.
  restartNotice(api, text) {
    const restart = ui.button('Restart now', async () => {
      const reached = await api.call('send_app_command', 'restart').catch(() => false);
      ui.toast(reached ? 'Restarting Whisper Local…' : TRAY_RESTART_HINT, reached ? 'info' : 'error');
    }, { variant: 'primary' });
    return el('div', { class: 'restart-notice' }, ui.card(null, ui.settingRow('Restart to apply', text, restart)));
  },

  // Messages go to the status bar at the bottom of the content pane and fade back to "Ready".
  // At rest the bar shows the app's state (see setAppState).
  toast(message, kind = 'info') {
    const bar = document.getElementById('status-bar');
    document.getElementById('status-text').textContent = message;
    bar.dataset.kind = kind;
    clearTimeout(ui._toastTimer);
    ui._toastTimer = setTimeout(() => {
      bar.dataset.kind = '';
      showAppState();
    }, kind === 'error' ? 8000 : 4000);
  },

  // "just now", "5 min ago", "yesterday", else a short date. Input: local ISO string.
  timeAgo(isoString) {
    const then = new Date(isoString);
    if (Number.isNaN(then.getTime())) return '';
    const minutes = Math.floor((Date.now() - then.getTime()) / 60000);
    if (minutes < 1) return 'just now';
    if (minutes < 60) return `${minutes} min ago`;
    const hours = Math.floor(minutes / 60);
    if (hours < 24) return `${hours} hr ago`;
    const days = Math.floor(hours / 24);
    if (days === 1) return 'yesterday';
    if (days < 7) return `${days} days ago`;
    return then.toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
  },
};
window.ui = ui;

const TRAY_RESTART_HINT = "The app isn't reachable. Use the tray menu to restart Whisper Local.";

// Display text for one key token. macOS keeps the ⌥ ⌘ ⇧ glyphs people know, but control
// is written "ctrl": the ⌃ glyph reads like a caret. Windows uses plain words.
const MAC_KEY_SYMBOLS = { ctrl: 'ctrl', control: 'ctrl', alt: '⌥', option: '⌥', cmd: '⌘', command: '⌘', win: '⌘', shift: '⇧' };
const WINDOWS_KEY_NAMES = { ctrl: 'Ctrl', control: 'Ctrl', alt: 'Alt', option: 'Alt', cmd: 'Win', command: 'Win', win: 'Win', shift: 'Shift' };
function keyLabel(token) {
  const lower = token.toLowerCase();
  const symbols = ui.platform === 'macos' ? MAC_KEY_SYMBOLS : WINDOWS_KEY_NAMES;
  if (symbols[lower]) return symbols[lower];
  if (lower === 'fn') return 'fn';
  if (lower === 'esc' || lower === 'escape') return 'Esc';
  return lower.length === 1 ? lower.toUpperCase() : lower.charAt(0).toUpperCase() + lower.slice(1);
}

// ── App state in the status bar ──────────────────────────────────────────────

// Resting text of the status bar for each state the running app reports (the
// same states as the tray icon). The dot colour / moon icon come from
// styles.css via data-state. null = no app launched this window.
const APP_STATE_LABELS = {
  idle: 'Ready',
  sleeping: 'Sleeping · the model is unloaded to save memory; your next dictation wakes it',
  recording: 'Listening…',
  processing: 'Transcribing…',
};
let currentAppState = null;

// Shows the app's state, unless a toast is on screen (it falls back here when it fades).
function showAppState() {
  const bar = document.getElementById('status-bar');
  bar.dataset.state = currentAppState || '';
  if (bar.dataset.kind) return;
  document.getElementById('status-text').textContent = APP_STATE_LABELS[currentAppState] || '';
}

// Called by the Python side whenever the app changes state.
function setAppState(state) {
  currentAppState = state || null;
  showAppState();
}

// ── Python API ───────────────────────────────────────────────────────────────

// Resolves once pywebview has injected its api object (event, with a poll as a
// safety net in case the event fired before this script ran).
const apiReady = new Promise((resolve) => {
  const isReady = () => window.pywebview && window.pywebview.api && typeof window.pywebview.api.get_app_info === 'function';
  if (isReady()) return resolve();
  window.addEventListener('pywebviewready', resolve, { once: true });
  const poll = setInterval(() => { if (isReady()) { clearInterval(poll); resolve(); } }, 50);
});

function errorMessage(error) {
  return (error && (error.message || String(error))) || 'Unknown error';
}

const api = {
  async call(methodName, ...args) {
    await apiReady;
    try {
      const method = window.pywebview.api[methodName];
      if (typeof method !== 'function') throw new Error(`No such API method: ${methodName}`);
      return await method(...args);
    } catch (error) {
      ui.toast(`${methodName} failed: ${errorMessage(error)}`, 'error');
      throw error;
    }
  },
};

// ── JS error reporting ───────────────────────────────────────────────────────

// Sends uncaught errors to Python (dashboard.log) so a broken page is visible without
// devtools. Calls pywebview directly (not api.call) so a failure here can't loop.
async function reportJsError(message, source, line, column, stack) {
  console.error(message, source, line, column);
  try {
    await apiReady;
    await window.pywebview.api.log_js_error(String(message), String(source || ''), line || 0, column || 0, String(stack || ''));
  } catch (ignored) { /* nothing else to do */ }
}
window.addEventListener('error', (event) => {
  reportJsError(event.message, event.filename, event.lineno, event.colno, event.error && event.error.stack);
});
window.addEventListener('unhandledrejection', (event) => {
  const reason = event.reason;
  reportJsError(`Unhandled promise rejection: ${errorMessage(reason)}`, '', 0, 0, reason && reason.stack);
});

// ── Page registry & router ───────────────────────────────────────────────────

const pages = new Map();
function registerPage(id, definition) {
  pages.set(id, definition);
}
window.registerPage = registerPage;

// Sidebar structure: a group label (or none) and its pages, in display order.
const NAV_GROUPS = [
  { label: null, items: [['home', 'Home'], ['history', 'History']] },
  { label: 'Personalize', items: [['models', 'Models'], ['vocabulary', 'Vocabulary'], ['style', 'Style']] },
  { label: 'App', items: [['settings', 'Settings'], ['about', 'About']] },
];
const FALLBACK_PAGE = 'home';
const LEAVE_MS = 110;

let currentPageId = null;
let navigationToken = 0; // lets a newer click cancel an older, still-rendering page

const prefersReducedMotion = () => window.matchMedia('(prefers-reduced-motion: reduce)').matches;
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

// Shows page `id` with a short fade/slide; ignores clicks on the page already shown.
async function navigate(id, { force = false } = {}) {
  if (!pages.has(id)) id = FALLBACK_PAGE;
  if (id === currentPageId && !force) return;
  const token = ++navigationToken;
  const definition = pages.get(id);
  const mount = document.getElementById('page-mount');
  const hadPage = currentPageId !== null;
  currentPageId = id;
  markActiveNavItem(id);

  if (hadPage && !prefersReducedMotion()) {
    mount.classList.add('leaving');
    await sleep(LEAVE_MS);
    if (token !== navigationToken) return;
  }

  const body = el('div', { class: 'page-body' });
  const page = el('div', { class: 'page entering', 'data-page': id },
    definition.header === false ? null : el('h1', { class: 'page-title', text: definition.title }), body);
  mount.classList.remove('leaving');
  mount.replaceChildren(page);
  document.getElementById('content-scroll').scrollTop = 0;
  document.title = `${definition.title} · Whisper Local`;
  void page.offsetWidth; // commit the "entering" state so removing it animates
  page.classList.remove('entering');

  try {
    await definition.render(body, api);
  } catch (error) {
    reportJsError(`Page "${id}" failed to render: ${errorMessage(error)}`, '', 0, 0, error && error.stack);
    body.replaceChildren(ui.card(null, ui.emptyState('This page could not load', errorMessage(error))));
  }
}
window.dashboard = { navigate, setAppState };

// ── Sidebar ──────────────────────────────────────────────────────────────────

function buildSidebar() {
  const sidebar = document.getElementById('sidebar');
  sidebar.append(el('div', { class: 'brand' }, el('img', { class: 'brand-mark', src: 'brand.svg', alt: '', width: 28, height: 28 }), 'Whisper Local'));
  for (const group of NAV_GROUPS) {
    if (group.label) sidebar.append(el('div', { class: 'nav-group-label', text: group.label }));
    for (const [id, label] of group.items) {
      sidebar.append(el('button', { type: 'button', class: 'nav-item', 'data-page': id, onclick: () => navigate(id) },
        icon(id), el('span', { text: label })));
    }
  }
  sidebar.append(el('div', { class: 'sidebar-footer', id: 'sidebar-footer' }, el('span', { id: 'version-text', text: 'Version …' })));
}

// Fills the "Version X · What's new" footer once the app info arrives.
function fillSidebarFooter(version) {
  document.getElementById('version-text').textContent = `Version ${version} · `;
  document.getElementById('sidebar-footer').append(
    el('button', { type: 'button', class: 'link-button', text: "What's new",
      onclick: () => api.call('open_changelog').catch(() => {}) }));
}

function markActiveNavItem(id) {
  for (const item of document.querySelectorAll('.nav-item')) {
    if (item.dataset.page === id) item.setAttribute('aria-current', 'page');
    else item.removeAttribute('aria-current');
  }
}

// ── Boot ─────────────────────────────────────────────────────────────────────

// Runs after every pages/*.js has registered (they load after this file).
document.addEventListener('DOMContentLoaded', async () => {
  buildSidebar();
  let info = { version: '', platform: 'macos', initial_page: FALLBACK_PAGE };
  try {
    info = await api.call('get_app_info');
  } catch (ignored) { /* toast already shown; fall back to defaults */ }
  ui.platform = info.platform;
  if (info.app_state) setAppState(info.app_state);
  fillSidebarFooter(info.version);
  navigate(info.initial_page);
});
