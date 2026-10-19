"""PC-wide characters shared by every VoiceDrama project and game project.

Characters live in workspace/characters/characters.json and their reference
audio in workspace/characters/reference_audio. A VoiceDrama line or a game
voice whose speaker matches a character's name or alias uses that character,
so a voice is set up once for every work. Importing copies casts out of a
project (keeping reference audio IDs, so existing takes stay current); the
source project is only changed when asked to use the imported character.
"""
from __future__ import annotations
import copy
import json
import os
import re
import shutil
import threading
import unicodedata
import uuid
from datetime import datetime
from pathlib import Path

import core

LOCK = threading.RLock()
ENGINES = ('irodori', 'qwen')
MODES = ('clone', 'design')
LANGUAGES = ('Japanese', 'English', 'Chinese', 'Auto')
DEFAULT_MODELS = {'irodori': 'Aratako/Irodori-TTS-v4.1-Small', 'qwen': 'Qwen/Qwen3-TTS-12Hz-1.7B-Base'}
QWEN_DESIGN_MODEL = 'Qwen/Qwen3-TTS-12Hz-1.7B-VoiceDesign'
AUDIO_EXTS = ('.wav', '.mp3', '.flac', '.ogg', '.m4a', '.aac', '.opus')
CHARACTER_ID = re.compile(r'chr_[0-9a-f]{16}')
ASSET_FILE = re.compile(r'((?:asset|ref|cref)_[0-9a-f]{16})(\.(?:wav|mp3|flac|ogg|m4a|aac|opus))')
VOICE_FIELDS = ('engine', 'model', 'mode', 'language', 'description', 'refText')
MAX_CHARACTERS = 500
MAX_ALIASES = 30
MAX_REFS = 20
MAX_REFERENCE_BYTES = 50 * 1024 * 1024
CONFLICT_MODES = ('skip', 'overwrite', 'rename')


def root():
    return core.WORKSPACE / 'characters'


def registry_path():
    return root() / 'characters.json'


def key(name):
    """Names and aliases match regardless of width and letter case."""
    return unicodedata.normalize('NFKC', str(name)).strip().casefold()


def now():
    return datetime.now().astimezone().isoformat(timespec='seconds')


def load():
    """Read the registry; an absent file is an empty registry (nothing is created)."""
    path = registry_path()
    if not path.is_file():
        return {'revision': 0, 'characters': [], 'assets': []}
    data = json.loads(path.read_text(encoding='utf-8'))
    if (not isinstance(data, dict) or not isinstance(data.get('characters'), list)
            or not isinstance(data.get('assets'), list)):
        raise ValueError('共通キャラクターの管理ファイルが破損しています（workspace/characters/characters.json）')
    data.setdefault('revision', 0)
    return data


def _write(data):
    root().mkdir(parents=True, exist_ok=True)
    temp = registry_path().with_name('characters_' + uuid.uuid4().hex + '.tmp')
    try:
        temp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
        os.replace(temp, registry_path())
    finally:
        temp.unlink(missing_ok=True)


def _check_revision(data, expected_revision):
    if expected_revision is None or int(expected_revision) != int(data['revision']):
        raise RuntimeError('共通キャラクターが別の画面で変更されました。再読み込みしてください。')


def _commit(data):
    data['revision'] = int(data['revision']) + 1
    _write(data)
    return data


def is_character(voice):
    return bool(voice) and isinstance(voice.get('id'), str) and bool(CHARACTER_ID.fullmatch(voice['id']))


def by_id(data, character_id):
    return next((c for c in data['characters'] if c.get('id') == character_id), None)


def match(data, names):
    """The character whose name or alias equals the first matching name."""
    for name in names:
        if not isinstance(name, str) or not name.strip():
            continue
        wanted = key(name)
        for character in data['characters']:
            if key(character['name']) == wanted or any(key(a) == wanted for a in character.get('aliases', [])):
                return character
    return None


def asset_path(asset):
    filename = str(asset.get('filename', ''))
    found = ASSET_FILE.fullmatch(filename)
    if not found or found.group(1) != asset.get('id'):
        raise ValueError('共通キャラクターの参照音声ファイル名が不正です')
    return root() / 'reference_audio' / filename


def reference_paths(data, character):
    """Files for a character's references; a missing file is an error, not silence."""
    assets = {a['id']: a for a in data['assets']}
    paths = []
    for aid in character.get('refs', []):
        asset = assets.get(aid)
        path = asset_path(asset) if asset else None
        if path is None or not path.is_file():
            raise ValueError(f'{character["name"]}の参照音声ファイルがありません: {(asset or {}).get("name", aid)}')
        paths.append(str(path))
    return paths


def _text(value, limit, label, multiline=False):
    value = '' if value is None else value
    if (not isinstance(value, str) or len(value) > limit
            or any(ord(c) < 32 and not (multiline and c in '\n\t') for c in value)):
        raise ValueError(f'{label}は{limit}文字以内で指定してください')
    return value


def _taken(data, skip_id=None):
    names = {}
    for other in data['characters']:
        if other.get('id') == skip_id:
            continue
        for name in (other['name'], *other.get('aliases', [])):
            names[key(name)] = other['name']
    return names


def normalize_character(spec, data, current=None):
    """Validate one character; names and aliases are unique across the registry."""
    if not isinstance(spec, dict):
        raise ValueError('キャラクターの設定が正しくありません')
    name = _text(spec.get('name'), 60, 'キャラクター名').strip()
    if not name:
        raise ValueError('キャラクター名を入力してください')
    raw_aliases = spec.get('aliases', [])
    if not isinstance(raw_aliases, list) or len(raw_aliases) > MAX_ALIASES:
        raise ValueError('別名は30件以内で指定してください')
    aliases, seen = [], {key(name)}
    for alias in raw_aliases:
        alias = _text(alias, 100, '別名').strip()
        if alias and key(alias) not in seen:
            seen.add(key(alias))
            aliases.append(alias)
    engine = spec.get('engine') or 'irodori'
    if engine not in ENGINES:
        raise ValueError('TTSエンジンが正しくありません')
    mode = spec.get('mode') or 'clone'
    if mode not in MODES:
        raise ValueError('生成方式が正しくありません')
    language = spec.get('language') or 'Japanese'
    if language not in LANGUAGES:
        raise ValueError('言語が正しくありません')
    model = _text(spec.get('model'), 200, '音声モデル').strip() or DEFAULT_MODELS[engine]
    refs = spec.get('refs', [])
    known = {a['id'] for a in data['assets']}
    if (not isinstance(refs, list) or len(refs) > MAX_REFS or len(refs) != len(set(refs))
            or any(ref not in known for ref in refs)):
        raise ValueError('参照音声の指定が正しくありません（1キャラクター20本まで）')
    taken = _taken(data, current['id'] if current else None)
    for label in (name, *aliases):
        if key(label) in taken:
            raise ValueError(f'「{label}」は共通キャラクター「{taken[key(label)]}」の名前か別名に使われています')
    return {'id': current['id'] if current else 'chr_' + uuid.uuid4().hex[:16],
            'name': name, 'aliases': aliases, 'engine': engine, 'model': model, 'mode': mode,
            'language': language,
            'description': _text(spec.get('description'), 2000, '声の説明', True),
            'refText': _text(spec.get('refText'), 4000, '参照音声の書き起こし', True),
            'refs': list(refs), 'updatedAt': now()}


def create_character(spec, expected_revision):
    with LOCK:
        data = load()
        _check_revision(data, expected_revision)
        if len(data['characters']) >= MAX_CHARACTERS:
            raise ValueError('共通キャラクターは500人までです')
        character = normalize_character(spec, data)
        data['characters'].append(character)
        return _commit(data), character


def update_character(character_id, spec, expected_revision):
    """Replace a character's settings. A renamed character keeps its old name
    as an alias, so scripts that use the old name still find it."""
    with LOCK:
        data = load()
        _check_revision(data, expected_revision)
        current = by_id(data, character_id)
        if current is None:
            raise FileNotFoundError('共通キャラクターが見つかりません')
        merged = {**current, **(spec if isinstance(spec, dict) else {})}
        if key(merged.get('name', '')) != key(current['name']):
            aliases = list(merged.get('aliases') or [])
            if all(key(a) != key(current['name']) for a in aliases):
                aliases.append(current['name'])
            merged['aliases'] = aliases
        character = normalize_character(merged, data, current)
        data['characters'] = [character if c['id'] == character_id else c for c in data['characters']]
        return _commit(data), character


def delete_character(character_id, expected_revision):
    """Remove a character. Its reference audio files are kept."""
    with LOCK:
        data = load()
        _check_revision(data, expected_revision)
        if by_id(data, character_id) is None:
            raise FileNotFoundError('共通キャラクターが見つかりません')
        data['characters'] = [c for c in data['characters'] if c['id'] != character_id]
        return _commit(data)


def _store_reference(source, display_name, asset_id=None):
    """Copy one reference file into the shared folder (no registry change)."""
    from render import duration_of
    extension = Path(str(display_name).replace('\\', '/')).suffix.lower() or Path(source).suffix.lower()
    if extension not in AUDIO_EXTS:
        raise ValueError('WAV / MP3 / FLAC / OGG / M4A / AAC / OPUSのみ対応')
    asset_id = asset_id or 'cref_' + uuid.uuid4().hex[:16]
    asset = {'id': asset_id, 'filename': asset_id + extension,
             'name': Path(str(display_name).replace('\\', '/')).name[:160] or asset_id + extension}
    target = asset_path(asset)
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        raise RuntimeError(f'参照音声の保存先が使用されています: {target.name}')
    shutil.copy2(source, target)
    try:
        asset['duration'] = round(duration_of(target), 3)
    except Exception:
        target.unlink(missing_ok=True)
        raise
    asset['createdAt'] = now()
    return asset


def add_reference(character_id, source, display_name, expected_revision=None, *,
                  replace_asset_id=None, design_text=None):
    """Register a reference recording for a character.

    replace_asset_id: an earlier result to unlink (regenerating a Voice
    Design); its file stays. design_text marks a Voice Design result: the
    character then clones from it, like a VoiceDrama cast after a design.
    """
    with LOCK:
        data = load()
        if expected_revision is not None:
            _check_revision(data, expected_revision)
        character = by_id(data, character_id)
        if character is None:
            raise FileNotFoundError('共通キャラクターが見つかりません')
        refs = [ref for ref in character.get('refs', []) if ref != replace_asset_id]
        if len(refs) >= MAX_REFS:
            raise ValueError('1キャラクターに登録できる参照音声は20本までです')
        asset = _store_reference(source, display_name)
        try:
            data['assets'].append(asset)
            character['refs'] = refs + [asset['id']]
            if design_text is not None:
                character['refText'] = design_text
                character['mode'] = 'clone'
                if character.get('engine') == 'qwen':
                    character['model'] = DEFAULT_MODELS['qwen']
            character['updatedAt'] = now()
            return _commit(data), asset
        except Exception:
            asset_path(asset).unlink(missing_ok=True)
            raise


def remove_reference(character_id, asset_id, expected_revision):
    """Unlink a reference from a character; the audio file is kept."""
    with LOCK:
        data = load()
        _check_revision(data, expected_revision)
        character = by_id(data, character_id)
        if character is None or asset_id not in character.get('refs', []):
            raise FileNotFoundError('このキャラクターの参照音声が見つかりません')
        character['refs'] = [ref for ref in character['refs'] if ref != asset_id]
        character['updatedAt'] = now()
        return _commit(data)


# --- importing from projects --------------------------------------------------

def _drama_candidates(project_id):
    state = core.load_project(project_id)
    assets = {a['id']: a for a in state['assets']}
    result = []
    for voice in state.get('voices', []):
        name = str(voice.get('name') or '').strip()
        if not name:
            continue
        refs = [(aid, core.asset_path(project_id, assets[aid]), assets[aid].get('name', aid))
                for aid in voice.get('refs', []) if aid in assets]
        result.append({'key': name, 'name': name, 'aliases': [], 'hasVoice': True, 'suggested': True,
                       'voice': {k: voice.get(k) for k in VOICE_FIELDS}, 'refFiles': refs,
                       'speakers': [name]})
    return state, result


def _source(source):
    """(kind, project state, import candidates) for one VoiceDrama or game project."""
    if not isinstance(source, dict) or source.get('kind') not in ('drama', 'game'):
        raise ValueError('取り込み元を選んでください')
    if source['kind'] == 'drama':
        state, items = _drama_candidates(str(source.get('id', '')))
        return 'drama', state, items
    import game_voice  # game_voice imports this module
    state = game_voice.get_game(str(source.get('id', '')))
    return 'game', state, game_voice.character_candidates(state)


def _public(item, data):
    existing = match(data, [item['name'], *item['aliases']])
    voice = item.get('voice') or {}
    return {'key': item['key'], 'name': item['name'], 'aliases': item['aliases'],
            'hasVoice': item['hasVoice'], 'suggested': item['suggested'],
            'engine': voice.get('engine') or 'irodori', 'model': voice.get('model') or '',
            'refs': len(item['refFiles']), 'existing': existing['name'] if existing else None}


def list_sources():
    """Characters each project can contribute, without opening the project."""
    import game_voice
    data = load()
    result = []
    for project in core.list_projects():
        try:
            _, items = _drama_candidates(project['id'])
        except (OSError, ValueError, KeyError):
            continue
        if items:
            result.append({'kind': 'drama', 'id': project['id'], 'title': project['title'],
                           'characters': [_public(item, data) for item in items]})
    for entry in game_voice.list_games():
        try:
            items = game_voice.character_candidates(game_voice.get_game(entry['id']))
        except (OSError, ValueError, KeyError):
            continue
        if items:
            result.append({'kind': 'game', 'id': entry['id'], 'title': entry['title'],
                           'characters': [_public(item, data) for item in items]})
    return result


def _free_name(data, base):
    taken = _taken(data)
    for number in range(1, 1000):
        name = f'{base}（コピー{number if number > 1 else ""}）'
        if key(name) not in taken:
            return name
    raise ValueError('別名で追加できる名前が見つかりません')


def import_characters(source, keys, conflict, expected_revision, apply_to_source=True):
    """Copy characters from a VoiceDrama or game project into the registry.

    conflict decides what happens when the name (or an alias) is already a
    character: 'skip' keeps it, 'overwrite' replaces its voice settings and
    references with the source's (aliases are merged), 'rename' adds a copy.
    Reference audio keeps its ID. With apply_to_source, the source project's
    speakers are linked to the resulting characters. Nothing is written if
    any step fails.
    """
    if conflict not in CONFLICT_MODES:
        raise ValueError('同じ名前のキャラクターの扱いが正しくありません')
    if not isinstance(keys, list) or not keys or len(keys) > MAX_CHARACTERS:
        raise ValueError('取り込むキャラクターを選んでください')
    kind, _, items = _source(source)
    by_key = {item['key']: item for item in items}
    missing = [k for k in keys if k not in by_key]
    if missing:
        raise ValueError('取り込み元にないキャラクターです: ' + '、'.join(map(str, missing[:5])))
    chosen = [by_key[k] for k in dict.fromkeys(keys)]
    summary = {'added': [], 'updated': [], 'skipped': [], 'renamed': [], 'droppedAliases': []}
    links = {}
    copied = []
    with LOCK:
        data = load()
        _check_revision(data, expected_revision)
        try:
            def references(item):
                ids = []
                known = {a['id'] for a in data['assets']}
                for aid, path, name in item['refFiles']:
                    if aid not in known:
                        if not Path(path).is_file():
                            raise FileNotFoundError(f'{item["name"]}の参照音声ファイルがありません: {name}')
                        asset = _store_reference(path, name, aid)
                        copied.append(asset_path(asset))
                        data['assets'].append(asset)
                        known.add(aid)
                    ids.append(aid)
                return ids

            def free_aliases(item, names, skip_id=None):
                taken = _taken(data, skip_id)
                kept = [a for a in names if key(a) not in taken]
                summary['droppedAliases'] += [f'{item["name"]}: {a}' for a in names if key(a) in taken]
                return kept

            for item in chosen:
                target = match(data, [item['name'], *item['aliases']])
                if target and conflict == 'skip':
                    summary['skipped'].append(item['name'])
                    links[item['key']] = target['id']
                    continue
                if target and conflict == 'overwrite':
                    spec = dict(target)
                    if item['hasVoice']:
                        spec.update({k: v for k, v in item['voice'].items() if v is not None})
                        spec['refs'] = references(item)
                    extra = [n for n in (item['name'], *item['aliases'])
                             if key(n) != key(target['name']) and all(key(n) != key(a) for a in target['aliases'])]
                    spec['aliases'] = target['aliases'] + free_aliases(item, extra, target['id'])
                    updated = normalize_character(spec, data, target)
                    data['characters'] = [updated if c['id'] == target['id'] else c for c in data['characters']]
                    summary['updated'].append(target['name'])
                    links[item['key']] = target['id']
                    continue
                name = _free_name(data, item['name']) if target else item['name']
                aliases = [] if target else free_aliases(item, item['aliases'])
                if len(data['characters']) >= MAX_CHARACTERS:
                    raise ValueError('共通キャラクターは500人までです')
                spec = {'name': name, 'aliases': aliases,
                        **({k: v for k, v in item['voice'].items() if v is not None} if item['hasVoice'] else {}),
                        'refs': references(item) if item['hasVoice'] else []}
                created = normalize_character(spec, data)
                data['characters'].append(created)
                if target:
                    summary['renamed'].append({'from': item['name'], 'to': name})
                else:
                    summary['added'].append(name)
                links[item['key']] = created['id']
            data = _commit(data)
        except Exception:
            for path in copied:
                path.unlink(missing_ok=True)
            raise
    summary['applied'] = 0
    if apply_to_source:
        speakers = {}
        for item in chosen:
            for speaker in item['speakers']:
                speakers[speaker] = links[item['key']]
        if kind == 'game':
            import game_voice
            summary['applied'] = game_voice.link_shared_characters(source['id'], speakers)
        else:
            summary['applied'] = core.link_characters(source['id'], speakers, data)
    return data, summary
