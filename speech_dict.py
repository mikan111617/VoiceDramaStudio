"""Pronunciation dictionary shared by VoiceDrama and game-voice generation.

Pure functions only: validation, legacy-format normalization, reading
selection, substitution and merging. Storage lives in core (the PC-wide
workspace/global_settings.json). Substitution is applied only to the text
handed to the TTS worker; scripts, captions and Excel data are never edited.

Format (one word may have several reading patterns):
    {"舞黒館": {"readings": [{"label": "通常", "reading": "まいくろかん"},
                             {"label": "別読み", "reading": "ぶこくかん"}],
                "default": 0}}
The legacy format {"舞黒館": "まいくろかん"} is accepted and normalized to a
single pattern with default 0.
"""
from __future__ import annotations
import copy
import re

MAX_ENTRIES = 1000
MAX_SURFACE = 100
MAX_PATTERNS = 10
MAX_LABEL = 40
MAX_READING = 120
DEFAULT_LABEL = '通常'
FALLBACK_LABEL = '別読み'


def _has_control(value):
    return any(ord(c) < 32 for c in value)


def _surface(surface):
    if not isinstance(surface, str) or not (1 <= len(surface) <= MAX_SURFACE) or _has_control(surface):
        raise ValueError('読み方辞書の単語は1〜100文字にしてください')
    return surface


def _reading(surface, reading):
    if not isinstance(reading, str) or not (1 <= len(reading.strip()) <= MAX_READING) or _has_control(reading):
        raise ValueError(f'{surface} の読みは1〜120文字で指定してください')
    return reading.strip()


def _label(surface, label):
    if not isinstance(label, str) or not (1 <= len(label.strip()) <= MAX_LABEL) or _has_control(label):
        raise ValueError(f'{surface} の読みパターン名は1〜40文字にしてください')
    return label.strip()


def normalize_entry(surface, entry):
    """Return one validated entry in the multi-pattern format."""
    if isinstance(entry, str):
        return {'readings': [{'label': DEFAULT_LABEL, 'reading': _reading(surface, entry)}], 'default': 0}
    if not isinstance(entry, dict):
        raise ValueError(f'{surface} の読みは文字列か readings を持つオブジェクトで指定してください')
    patterns = entry.get('readings')
    if not isinstance(patterns, list) or not (1 <= len(patterns) <= MAX_PATTERNS):
        raise ValueError(f'{surface} の読みパターンは1〜10個にしてください')
    readings = []
    for pattern in patterns:
        if not isinstance(pattern, dict):
            raise ValueError(f'{surface} の読みパターンは label と reading で指定してください')
        readings.append({'label': _label(surface, pattern.get('label')),
                         'reading': _reading(surface, pattern.get('reading'))})
    default = entry.get('default', 0)
    # bool is an int subclass; true/false must not select pattern 1/0.
    if not isinstance(default, int) or isinstance(default, bool) or not 0 <= default < len(readings):
        raise ValueError(f'{surface} の既定の読み（default）が読みパターンの範囲外です')
    return {'readings': readings, 'default': default}


def normalize_dictionary(value):
    """Validate a whole dictionary (legacy or new format) into the new format."""
    if not isinstance(value, dict) or len(value) > MAX_ENTRIES:
        raise ValueError('読み方辞書は最大1000語のJSONオブジェクトで指定してください')
    return {_surface(surface): normalize_entry(surface, entry) for surface, entry in value.items()}


def normalize_choices(value):
    """Per-line {surface: pattern index}. Indexes are checked against the
    dictionary only when applied, so later dictionary edits never make an
    existing script unsavable."""
    if not isinstance(value, dict) or len(value) > MAX_ENTRIES:
        raise ValueError('読みパターンの選択は最大1000語のJSONオブジェクトで指定してください')
    result = {}
    for surface, index in value.items():
        _surface(surface)
        if not isinstance(index, int) or isinstance(index, bool) or not 0 <= index < MAX_PATTERNS:
            raise ValueError(f'{surface} の読みパターンの番号が正しくありません')
        result[surface] = index
    return result


def effective_readings(dictionary, choices=None):
    """{surface: reading}: the line's chosen pattern, otherwise the default.

    Unknown words and out-of-range or malformed choices fall back to the
    default, so a stale choice can never break generation.
    """
    choices = choices if isinstance(choices, dict) else {}
    result = {}
    for surface, entry in (dictionary or {}).items():
        readings = entry['readings']
        index = choices.get(surface)
        if not isinstance(index, int) or isinstance(index, bool) or not 0 <= index < len(readings):
            index = entry.get('default', 0)
        result[surface] = readings[index]['reading']
    return result


def speech_text(original, readings):
    """Replace only the longest original surface once; never recursively."""
    if not readings:
        return original
    tokens = sorted(readings, key=lambda x: (-len(x), x))
    pattern = re.compile('|'.join(re.escape(x) for x in tokens))

    def replacement(match):
        key = match.group(0)
        # An ASCII token such as 2F must not match inside 12F or 2Floor.
        if key.isascii() and any(c.isalnum() for c in key):
            begin, end = match.span()
            if begin and original[begin - 1].isascii() and original[begin - 1].isalnum():
                return key
            if end < len(original) and original[end].isascii() and original[end].isalnum():
                return key
        return readings[key]
    return pattern.sub(replacement, original)


def apply(text, dictionary, choices=None):
    """Spoken text for TTS: dictionary readings with optional per-line choices."""
    return speech_text(text, effective_readings(dictionary, choices))


def merge_dictionaries(base, sources, base_label='共通辞書'):
    """Merge normalized dictionaries without discarding any reading.

    sources: [(label, dictionary)] in priority order. A word new to the result
    is copied as-is. A different reading of an existing word is appended as
    another pattern (named after its source when its own name would be
    ambiguous); the existing default stays. Returns (merged, conflicts), where
    conflicts lists each word that gained a reading from another source.
    """
    merged = copy.deepcopy(base)
    origins = {surface: {p['reading']: [base_label] for p in entry['readings']}
               for surface, entry in merged.items()}
    conflicted = []
    for label, dictionary in sources:
        for surface, entry in dictionary.items():
            if surface not in merged:
                merged[surface] = copy.deepcopy(entry)
                origins[surface] = {}
                for p in entry['readings']:
                    origins[surface].setdefault(p['reading'], []).append(label)
                continue
            current = merged[surface]
            for p in entry['readings']:
                sources_of = origins[surface].setdefault(p['reading'], [])
                if label not in sources_of:
                    sources_of.append(label)
                if any(x['reading'] == p['reading'] for x in current['readings']):
                    continue
                if len(current['readings']) >= MAX_PATTERNS:
                    raise ValueError(f'「{surface}」の読みパターンが10個を超えるため統合できません')
                names = {x['label'] for x in current['readings']}
                name = p['label']
                if name == DEFAULT_LABEL or name in names:
                    name = (str(label).strip()[:MAX_LABEL] or FALLBACK_LABEL)
                current['readings'].append({'label': name, 'reading': p['reading']})
                if surface not in conflicted:
                    conflicted.append(surface)
    if len(merged) > MAX_ENTRIES:
        raise ValueError(f'統合後の語数が{len(merged)}語になり、上限の1000語を超えます')
    conflicts = [{'surface': surface,
                  'readings': [{'reading': p['reading'], 'label': p['label'],
                                'sources': origins[surface].get(p['reading'], [])}
                               for p in merged[surface]['readings']]}
                 for surface in conflicted]
    return merged, conflicts
