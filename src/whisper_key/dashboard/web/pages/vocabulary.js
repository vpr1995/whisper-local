// pages/vocabulary.js
// Vocabulary page: teach Whisper your language. A Words card (hotwords that bias spelling,
// shown as removable chips, with suggestions mined from history) above two side-by-side
// compact lists: Replacements (wrong -> right, applied after transcription) and Snippets
// (spoken trigger -> typed text). Backed by api/vocabulary.py; every change is saved at
// once, hot-reloads in the running app and updates the page in place.

// Wrapped in a function so these helper names stay private: all page scripts share one global scope.
(() => {

const WORDS_VISIBLE_COUNT = 18;       // word chips shown before "+ N more" (about two rows)
const SUGGESTIONS_VISIBLE_COUNT = 8;  // history suggestions shown (they are low priority)
const LIST_VISIBLE_COUNT = 4;         // replacement / snippet rows shown before "+ N more"

// ── Small shared pieces ──────────────────────────────────────────────────────

// A single-line text field; Enter runs `onEnter`.
function vocabInput(placeholder, onEnter) {
  const input = el('input', { class: 'vocab-input', type: 'text', placeholder, 'aria-label': placeholder,
    autocomplete: 'off', spellcheck: 'false' });
  input.addEventListener('keydown', (event) => { if (event.key === 'Enter' && onEnter) onEnter(); });
  return input;
}

// Small "x" button that removes one entry (a word chip or a list row).
const removeButton = (label, onRemove) => el('button', { type: 'button', class: 'vocab-remove',
  title: label, 'aria-label': label, text: '×', onclick: (event) => { event.stopPropagation(); onRemove(); } });

// Runs an API call that returns True/False. A False result (empty or duplicate input) is
// explained with `failureText` instead of failing silently; True runs `onSaved`.
// api.call already toasts real errors, so a thrown call just returns false here.
async function saveOrExplain(api, methodName, args, { successText, failureText, onSaved }) {
  const worked = await api.call(methodName, ...args).catch(() => null);
  if (worked === null) return false;
  if (worked) {
    if (successText) ui.toast(successText, 'success');
    await onSaved();
  } else if (failureText) {
    ui.toast(failureText, 'error');
  }
  return worked;
}

// ── Words ────────────────────────────────────────────────────────────────────

// Card with a header (title, count, add field) and a chip area that is redrawn in place.
// The header is built once, so the add field keeps focus while several words are typed in a row.
function buildWordsCard(api, refresh) {
  let words = [];
  let suggestions = [];
  let showAllWords = false;

  const countLabel = el('span', { class: 'vocab-count' });
  const chipArea = el('div', { class: 'vocab-words-body' });

  const addWord = async (word) => {
    const trimmed = word.trim();
    if (!trimmed) return false;
    return saveOrExplain(api, 'add_word', [trimmed], {
      successText: `Added “${trimmed}”`, failureText: `“${trimmed}” is already in your words.`, onSaved: refresh });
  };
  const removeWord = (word) => saveOrExplain(api, 'remove_word', [word], {
    successText: `Removed “${word}”`, onSaved: refresh });

  const wordInput = vocabInput('Add a name or term…', () => submitTypedWord());
  const submitTypedWord = async () => { if (await addWord(wordInput.value)) wordInput.value = ''; };

  const wordChip = (word) => el('span', { class: 'vocab-word' },
    el('span', { class: 'vocab-word-text selectable', text: word }),
    removeButton(`Remove ${word}`, () => removeWord(word)));

  const suggestionChip = (suggestion) => el('button', { type: 'button', class: 'vocab-suggestion',
    title: `Heard ${suggestion.count} times in your history. Click to add.`,
    onclick: () => addWord(suggestion.word) }, `+ ${suggestion.word}`);

  const redrawChips = () => {
    countLabel.textContent = words.length ? String(words.length) : '';
    const hiddenCount = Math.max(0, words.length - WORDS_VISIBLE_COUNT);
    const visibleWords = showAllWords ? words : words.slice(0, WORDS_VISIBLE_COUNT);

    const known = new Set(words.map((word) => word.toLowerCase()));
    const openSuggestions = suggestions.filter((item) => !known.has(item.word.toLowerCase()))
      .slice(0, SUGGESTIONS_VISIBLE_COUNT);

    chipArea.replaceChildren(
      words.length
        ? el('div', { class: 'vocab-chips' }, visibleWords.map(wordChip),
            hiddenCount > 0 ? el('button', { type: 'button', class: 'link-button vocab-more',
              text: showAllWords ? 'Show less' : `+ ${hiddenCount} more`,
              onclick: () => { showAllWords = !showAllWords; redrawChips(); } }) : null)
        : el('div', { class: 'compact-empty', text: 'No words yet. Add names, products and jargon your dictation gets wrong.' }),
      openSuggestions.length ? el('div', { class: 'vocab-suggestions' },
        el('span', { class: 'vocab-suggestions-label', text: 'Heard often' }), openSuggestions.map(suggestionChip)) : null);
  };

  const card = el('section', { class: 'card compact-card' },
    el('div', { class: 'vocab-words-head' },
      el('h3', { class: 'compact-title', text: 'Words' }), countLabel,
      el('span', { class: 'vocab-words-add' }, wordInput, ui.button('Add', submitTypedWord, { variant: 'primary' }))),
    chipArea);

  return {
    node: card,
    // Called after every save with the freshly loaded lists.
    update(vocabulary, newSuggestions) {
      words = vocabulary.words;
      if (newSuggestions) suggestions = newSuggestions;
      redrawChips();
    },
  };
}

// ── Replacements and snippets: list card + inline form ───────────────────────

// A compact list card whose header "Add" link opens an inline form under the header.
// `form` = { node, openBlank() } is built once so half-typed text survives list redraws.
// The card itself is rebuilt on every update (ui.compactListCard takes a fixed item list);
// "+ N more" expansion is carried over so removing a row does not collapse the list.
function buildListSection({ title, emptyText, form, renderRow }) {
  const holder = el('div', { class: 'vocab-list-holder' });
  let expanded = false;

  holder.addEventListener('click', (event) => {
    const moreToggle = event.target.closest('.compact-more');
    if (moreToggle) expanded = moreToggle.textContent === 'Show less';
  });

  return {
    node: holder,
    update(items) {
      const card = ui.compactListCard({
        title, actionLabel: 'Add', onAction: form.openBlank, items,
        maxVisible: LIST_VISIBLE_COUNT, renderItem: renderRow, emptyText });
      if (items.length) {
        card.querySelector('.compact-title').append(el('span', { class: 'vocab-count', text: String(items.length) }));
      }
      card.querySelector('.compact-header').after(form.node);
      holder.replaceChildren(card);
      if (expanded) card.querySelector('.compact-more')?.click();
    },
  };
}

// Save / Cancel row shared by both inline forms.
const formButtons = (onSave, onCancel) => el('div', { class: 'vocab-form-buttons' },
  ui.button('Cancel', onCancel, { variant: 'ghost' }), ui.button('Save', onSave, { variant: 'primary' }));

// Hidden-until-needed form node; Escape inside it closes it.
function formShell(...children) {
  const node = el('div', { class: 'vocab-form' }, ...children);
  node.hidden = true;
  return node;
}

// ── Replacements ─────────────────────────────────────────────────────────────

function buildReplacementsSection(api, refresh) {
  const wrongInput = vocabInput('When it hears…', () => rightInput.focus());
  const rightInput = vocabInput('Write this instead', () => saveReplacement());

  const closeForm = () => { form.node.hidden = true; wrongInput.value = ''; rightInput.value = ''; };
  const openWith = (wrongText, rightText) => {
    wrongInput.value = wrongText; rightInput.value = rightText;
    form.node.hidden = false;
    (wrongText ? rightInput : wrongInput).focus();
  };
  const saveReplacement = async () => {
    const worked = await saveOrExplain(api, 'add_replacement', [wrongInput.value, rightInput.value], {
      successText: 'Replacement saved',
      failureText: 'Nothing saved. Enter the text to replace and a different result, or change something.', onSaved: refresh });
    if (worked) closeForm();
  };

  const form = {
    node: formShell(
      el('div', { class: 'vocab-form-pair' }, wrongInput, el('span', { class: 'vocab-arrow', text: '→' }), rightInput),
      formButtons(saveReplacement, closeForm)),
    openBlank: () => openWith('', ''),
  };
  form.node.addEventListener('keydown', (event) => { if (event.key === 'Escape') closeForm(); });

  // Clicking a row loads it into the form; saving with the same "wrong" text updates it.
  const renderRow = (pair) => el('div', { class: 'compact-row vocab-row', title: 'Click to edit',
    onclick: () => openWith(pair.from, pair.to) },
    el('span', { class: 'compact-row-name vocab-row-from selectable', text: pair.from }),
    el('span', { class: 'vocab-arrow', text: '→' }),
    el('span', { class: 'vocab-row-to selectable', text: pair.to || '(nothing)' }),
    removeButton(`Remove ${pair.from}`, () => saveOrExplain(api, 'remove_replacement', [pair.from], {
      successText: 'Replacement removed', onSaved: refresh })));

  return buildListSection({ title: 'Replacements', form, renderRow,
    emptyText: 'No replacements yet. Fix a mis-heard word after it is typed.' });
}

// ── Snippets ─────────────────────────────────────────────────────────────────

function buildSnippetsSection(api, refresh) {
  const triggerInput = vocabInput('Say this… (e.g. my signature)', () => expansionInput.focus());
  const expansionInput = el('textarea', { class: 'vocab-input vocab-textarea', rows: '3', 'aria-label': 'Text to type',
    placeholder: 'Type this. ${date}, ${time} and ${clipboard} are filled in.' });
  const aloneToggle = ui.toggle(false);

  const closeForm = () => {
    form.node.hidden = true; triggerInput.value = ''; expansionInput.value = ''; aloneToggle.checked = false;
  };
  const openWith = (snippet) => {
    triggerInput.value = snippet?.trigger || '';
    expansionInput.value = snippet?.expansion || '';
    aloneToggle.checked = snippet?.mode === 'alone';
    form.node.hidden = false;
    (snippet ? expansionInput : triggerInput).focus();
  };
  const saveSnippet = async () => {
    const worked = await saveOrExplain(api, 'save_snippet',
      [triggerInput.value, expansionInput.value, aloneToggle.checked ? 'alone' : 'anywhere'], {
        successText: 'Snippet saved', failureText: 'Nothing saved. Enter a trigger and the text to type, or change something.', onSaved: refresh });
    if (worked) closeForm();
  };

  const form = {
    node: formShell(
      triggerInput, expansionInput,
      el('div', { class: 'vocab-form-footer' },
        el('label', { class: 'vocab-alone' }, aloneToggle, el('span', { text: 'Only when it is the whole dictation' })),
        formButtons(saveSnippet, closeForm))),
    openBlank: () => openWith(null),
  };
  form.node.addEventListener('keydown', (event) => { if (event.key === 'Escape') closeForm(); });

  // Same edit-on-click behaviour as replacements; saving with the same trigger updates it.
  const renderRow = (snippet) => el('div', { class: 'compact-row vocab-row', onclick: () => openWith(snippet),
    title: `${snippet.expansion}${snippet.mode === 'alone' ? '\n(only when it is the whole dictation)' : ''}` },
    el('span', { class: 'compact-row-name vocab-row-from selectable', text: snippet.trigger }),
    el('span', { class: 'vocab-arrow', text: '→' }),
    el('span', { class: 'vocab-row-to selectable', text: snippet.expansion.replace(/\s+/g, ' ') }),
    snippet.mode === 'alone' ? el('span', { class: 'vocab-badge', text: 'alone' }) : null,
    removeButton(`Remove ${snippet.trigger}`, () => saveOrExplain(api, 'remove_snippet', [snippet.trigger], {
      successText: 'Snippet removed', onSaved: refresh })));

  return buildListSection({ title: 'Snippets', form, renderRow,
    emptyText: 'No snippets yet. Say a short trigger to type longer text.' });
}

// ── Page ─────────────────────────────────────────────────────────────────────

registerPage('vocabulary', {
  title: 'Vocabulary',
  async render(container, api) {
    container.append(el('p', { class: 'page-subtitle', text:
      'Changes save immediately and apply to your next dictation. No restart needed.' }));

    // One refresh reloads the (small) lists from disk and redraws only the lists, never the forms.
    const refresh = async () => {
      const vocabulary = await api.call('get_vocabulary');
      wordsCard.update(vocabulary);
      replacements.update(vocabulary.replacements);
      snippets.update(vocabulary.snippets);
    };
    const wordsCard = buildWordsCard(api, refresh);
    const replacements = buildReplacementsSection(api, refresh);
    const snippets = buildSnippetsSection(api, refresh);

    container.append(
      wordsCard.node,
      el('p', { class: 'vocab-footnote', text:
        'Words help the model spell names and jargon. Replacements fix the finished text; History can add one with “Fix this everywhere”.' }),
      el('div', { class: 'vocab-duo' }, replacements.node, snippets.node));

    // Suggestions are mined from history once (can be slow) and reused until the page is reopened.
    const [vocabulary, suggestions] = await Promise.all([
      api.call('get_vocabulary'), api.call('suggest_words').catch(() => [])]);
    wordsCard.update(vocabulary, suggestions);
    replacements.update(vocabulary.replacements);
    snippets.update(vocabulary.snippets);
  },
});

})();
