# word_snap.py
# Fixes misheard names after transcription: a capitalized word that sounds like
# an entry in the user's Words list (whisper.hotwords) becomes that entry. This
# gives Parakeet, which ignores hotwords, a way to use them. It is strict on
# purpose: a missed fix is better than a wrong change the user may not notice.

import re
from difflib import SequenceMatcher

# Two tiers. A word whose sound key is identical to the entry's ("Pradvi",
# "Pradve" for "Prudhvi") needs only a loose spelling match. A word that only
# sounds close needs a strong spelling match too: "Proud" and "Radvi" score the
# same against "Prudhvi", so both are left alone rather than risk the first.
# A close match must also start with the same sound: "Llama" is not "Ollama".
MIN_SPELLING_SCORE_SAME_SOUND = 0.6
MIN_SOUND_SCORE_CLOSE = 0.85
MIN_SPELLING_SCORE_CLOSE = 0.75

# Words shorter than this are too ambiguous to change ("Al" vs "Ali").
MIN_WORD_LENGTH = 4

# One word with its surrounding punctuation and an optional possessive.
_WORD_PARTS = re.compile(r"^(\W*)(\w[\w'’-]*?)((?:'s|’s)?)(\W*)$", re.UNICODE)


# ── Sound key ────────────────────────────────────────────────────────────────

# Rough sound spelling, in the spirit of Metaphone: letters that sound alike
# share one symbol and vowels count as one sound. "Prudhvi", "Pradvi" and
# "Pradve" all give "pradv"; "Prithvi" keeps its "th" and gives "pra0v".
def sound_key(word: str) -> str:
    key = re.sub(r"[^a-z]", "", word.lower())
    for spelling, sound in (('ph', 'f'), ('th', '0'), ('ck', 'k'), ('q', 'k'),
                            ('x', 'ks'), ('z', 's'), ('w', 'a')):
        key = key.replace(spelling, sound)
    key = re.sub(r"c(?=[eiy])", "s", key)
    key = key.replace('c', 'k')
    key = re.sub(r"(?<=[bdgjk])h", "", key)         # Indian names: "dh", "bh", "kh"
    key = key.replace('h', '')
    key = re.sub(r"[aeiouy]+", "a", key)            # vowels are where models slip most
    key = re.sub(r"(.)\1+", r"\1", key)
    return key.rstrip('a')                          # a final vowel is often dropped


def _similarity(first: str, second: str) -> float:
    return SequenceMatcher(None, first, second).ratio()


# ── Matching ─────────────────────────────────────────────────────────────────

# Splits one whitespace-free token into (leading punctuation, word, possessive,
# trailing punctuation), or None when it holds no word.
def _split_token(token: str):
    match = _WORD_PARTS.match(token)
    return match.groups() if match else None


# Already spelled like an entry: such a window is left alone, and no larger
# window may swallow it ("Parakeet v3" must keep its "v3").
_EXACT = object()


# The best Words entry for the words in a window, _EXACT, or None. `heard` is the
# window joined without spaces, so a name split in two ("Pra dvi") still matches.
def _best_entry(heard: str, entries: list):
    heard_key, heard_folded = sound_key(heard), heard.casefold()
    if any(heard_folded == joined.casefold() for _, joined, _ in entries):
        return _EXACT
    if len(heard) < MIN_WORD_LENGTH:
        return None
    best, best_score = None, 0.0
    for entry, entry_joined, entry_key in entries:
        sound = _similarity(heard_key, entry_key)
        spelling = _similarity(heard_folded, entry_joined.casefold())
        same_sound = sound == 1.0 and spelling >= MIN_SPELLING_SCORE_SAME_SOUND
        close_sound = (sound >= MIN_SOUND_SCORE_CLOSE and spelling >= MIN_SPELLING_SCORE_CLOSE
                       and heard_key[:1] == entry_key[:1])
        if (same_sound or close_sound) and sound > best_score:
            best, best_score = entry, sound
    return best


# ── Public entry point ───────────────────────────────────────────────────────

# Returns `text` with misheard Words entries fixed. Only windows that start
# with a capitalized word are checked: models capitalize what they take for a
# name, so ordinary words in a sentence are never touched.
def snap_to_words(text: str, words) -> str:
    entries = []
    for word in words or ():
        word = str(word).strip()
        joined = re.sub(r"\s+", "", word)
        if len(joined) >= MIN_WORD_LENGTH:
            entries.append((word, joined, sound_key(joined)))
    if not text or not entries:
        return text

    pieces = re.findall(r"\S+|\s+", text)
    token_positions = [i for i, piece in enumerate(pieces) if not piece.isspace()]
    longest_entry = max(len(entry.split()) for entry, _, _ in entries)

    index = 0
    while index < len(token_positions):
        first = _split_token(pieces[token_positions[index]])
        if not first or not first[1][:1].isupper():
            index += 1
            continue

        # Try windows from one word up to one more than the longest entry,
        # smallest first, and never across punctuation (a comma ends the name).
        replacement = None
        for size in range(1, min(longest_entry + 1, len(token_positions) - index) + 1):
            parts = [_split_token(pieces[token_positions[index + k]]) for k in range(size)]
            if any(p is None for p in parts):
                continue
            inner_punctuation = any(p[2] or p[3] for p in parts[:-1]) or any(p[0] for p in parts[1:])
            if inner_punctuation:
                continue
            entry = _best_entry(''.join(p[1] for p in parts), entries)
            if entry is _EXACT:
                break
            if entry:
                replacement = (size, f"{parts[0][0]}{entry}{parts[-1][2]}{parts[-1][3]}")
                break

        if replacement is None:
            index += 1
            continue
        size, new_token = replacement
        start, end = token_positions[index], token_positions[index + size - 1]
        pieces[start:end + 1] = [new_token] + [''] * (end - start)
        index += size

    return ''.join(pieces)
