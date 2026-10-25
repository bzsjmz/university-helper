"""Offline, source-backed questions kept separate from generated answer caches.

Choices match their complete text set, then map to the current option labels.
There is no fuzzy matching and no network access in this module.
"""
import hashlib
import html
import json
import os
from pathlib import Path
import re
import sqlite3
import tempfile
import unicodedata

from .answer_quality import is_usable_answer

DEFAULT_BANK = '/state/imported-question-bank.sqlite'
_KINDS = {'single', 'multiple', 'judgement', 'completion', 'shortanswer'}
_TRUE = {'正确', '对', '是', '√', 'true', 't'}
_FALSE = {'错误', '错', '否', '×', 'false', 'f'}


def text_key(value):
    value = html.unescape(str(value)).replace('\r\n', '\n')
    value = re.sub(r'</?(?:p|div|span|br|strong|em|b|i)(?:\s[^>]*)?/?>', ' ', value, flags=re.I)
    value = unicodedata.normalize('NFC', value).strip()
    # Whitespace inside code/string literals may change the question's meaning.
    if any(c in value for c in ('"', "'", '`')):
        return value
    return re.sub(r'\s+', ' ', value)


def title_key(value):
    value = text_key(value)
    value = re.sub(r'^\d+[.、．]\s+(?=\S)', '', value)
    value = re.sub(r'^[【（(\[]\s*(?:单选题|多选题|判断题|填空题|简答题)\s*[】）)\]]\s*', '', value)
    return re.sub(r'\s*[（(]\d+(?:\.\d+)?分[）)]$', '', value).strip()


def options_with_labels(value):
    if isinstance(value, str):
        value = value.splitlines()
    if not isinstance(value, (list, tuple)):
        return []
    result = []
    for index, item in enumerate(value):
        if not isinstance(item, str) or not item.strip() or index >= 26:
            return []
        match = re.match(r'^\s*([A-Z])[.．、:：,，]\s*(.*)$', item, re.S)
        label, text = (match[1], match[2]) if match else (chr(65 + index), item)
        result.append((label, text_key(text)))
    if len({a for a, _ in result}) != len(result) or len({b for _, b in result}) != len(result):
        return []
    return result


def question_key(kind, title, options):
    value = [kind, title_key(title), sorted(options) if kind in ('single', 'multiple') else []]
    return hashlib.sha256(json.dumps(value, ensure_ascii=False).encode()).hexdigest()


def validate_record(row):
    kind, title = row.get('type'), row.get('title', row.get('question', ''))
    if kind not in _KINDS or not isinstance(title, str) or len(title_key(title)) < 4:
        raise ValueError('invalid_title_or_type')
    if re.search(r'<img\b|!\[.*?\]\(', title, re.I):
        raise ValueError('image_question')
    answers = row.get('answers')
    if not isinstance(answers, list) or not answers or len(answers) > 32 or not all(is_usable_answer(a) for a in answers):
        raise ValueError('invalid_answer')
    answers = [text_key(a) for a in answers]
    options = []
    if kind in ('single', 'multiple'):
        parsed = options_with_labels(row.get('options'))
        options = [text for _, text in parsed]
        if any(re.search(r'<img\b|!\[.*?\]\(', text, re.I) for text in options):
            raise ValueError('image_option')
        if not 2 <= len(options) <= 26 or (kind == 'single' and len(answers) != 1):
            raise ValueError('missing_or_ambiguous_options')
        if len(set(answers)) != len(answers) or not set(answers) <= set(options):
            raise ValueError('answer_not_in_options')
        answers.sort()
    elif kind == 'judgement':
        if len(answers) != 1 or answers[0].lower() not in _TRUE | _FALSE:
            raise ValueError('invalid_judgement')
        answers = ['正确' if answers[0].lower() in _TRUE else '错误']
    if any(len(a) > 8192 for a in answers):
        raise ValueError('answer_too_large')
    if not isinstance(row.get('source'), str) or not row['source'].strip():
        raise ValueError('missing_source')
    return question_key(kind, title, options), kind, title_key(title), options, answers


def build_bank(records, destination):
    """Validate, deduplicate, quarantine conflicts, and atomically publish a file."""
    accepted, rejected, conflicts = {}, [], set()
    for row in records:
        try:
            key, kind, title, options, answers = validate_record(row)
        except (ValueError, TypeError, AttributeError) as exc:
            rejected.append({'source': row.get('source') if isinstance(row, dict) else None, 'reason': str(exc)})
            continue
        if key in conflicts:
            continue
        old = accepted.get(key)
        if old and old['answers'] != answers:
            accepted.pop(key)
            conflicts.add(key)
            continue
        if old:
            old['sources'].add(row['source'])
        else:
            accepted[key] = dict(kind=kind, title=title, options=options, answers=answers, sources={row['source']})
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=destination.name + '.', dir=destination.parent)
    os.close(fd)
    try:
        with sqlite3.connect(tmp) as con:
            con.execute('CREATE TABLE metadata (version INTEGER NOT NULL)')
            con.execute('INSERT INTO metadata VALUES (1)')
            con.execute('CREATE TABLE questions (key TEXT PRIMARY KEY, kind TEXT, title TEXT, options TEXT, answers TEXT, sources TEXT)')
            con.executemany('INSERT INTO questions VALUES (?,?,?,?,?,?)', [
                (key, r['kind'], r['title'], json.dumps(r['options'], ensure_ascii=False),
                 json.dumps(r['answers'], ensure_ascii=False), json.dumps(sorted(r['sources']), ensure_ascii=False))
                for key, r in accepted.items()])
        os.replace(tmp, destination)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
    return {'accepted': len(accepted), 'rejected': len(rejected), 'conflicts': len(conflicts), 'rejections': rejected}


def lookup(question):
    """Return an exact match or None. Broken/missing banks never block fallback."""
    path = Path(os.environ.get('CHAOXING_LOCAL_BANK_FILE', DEFAULT_BANK))
    kind = question.get('type')
    try:
        if not path.is_file() or path.stat().st_size > 128 * 1024 * 1024 or kind not in _KINDS:
            return None
        parsed = options_with_labels(question.get('options', []))
        options = [text for _, text in parsed]
        if kind in ('single', 'multiple') and len(options) < 2:
            return None
        key = question_key(kind, question.get('title', ''), options)
        with sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True, timeout=0.2) as con:
            if con.execute('SELECT version FROM metadata').fetchone() != (1,):
                return None
            row = con.execute('SELECT options,answers FROM questions WHERE key=? AND kind=?', (key, kind)).fetchone()
        if not row:
            return None
        stored_options, answers = json.loads(row[0]), json.loads(row[1])
        if not isinstance(answers, list) or not answers or not all(is_usable_answer(a) for a in answers):
            return None
        if kind in ('single', 'multiple'):
            if sorted(stored_options) != sorted(options) or not set(answers) <= set(options):
                return None
            if kind == 'single' and len(answers) != 1:
                return None
            return ''.join(label for label, text in parsed if text in answers)
        if kind == 'judgement' and (len(answers) != 1 or answers[0] not in ('正确', '错误')):
            return None
        return '\n'.join(answers)
    except (OSError, sqlite3.Error, ValueError, TypeError):
        return None


if __name__ == '__main__':
    import argparse

    parser = argparse.ArgumentParser(description='Build an offline bank from source-attributed JSONL; no network or generated answers.')
    parser.add_argument('input', type=Path)
    parser.add_argument('output', type=Path)
    parser.add_argument('--report', type=Path, required=True)
    args = parser.parse_args()
    with args.input.open(encoding='utf-8') as stream:
        result = build_bank((json.loads(line) for line in stream if line.strip()), args.output)
    args.report.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({key: value for key, value in result.items() if key != 'rejections'}, ensure_ascii=False))
