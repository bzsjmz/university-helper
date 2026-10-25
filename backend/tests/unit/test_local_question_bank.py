"""Source imports must never guess option mappings or poison generated caches."""
import sqlite3
from unittest.mock import Mock

import pytest

from app.services.course.chaoxing.local_question_bank import build_bank, lookup


def record(**changes):
    row = dict(type='single', title='2026年测试题目？', options=['甲', '乙', '丙'],
               answers=['乙'], source='https://example.org/pinned-source')
    row.update(changes)
    return row


@pytest.fixture
def bank(tmp_path, monkeypatch):
    path = tmp_path / 'bank.sqlite'
    monkeypatch.setenv('CHAOXING_LOCAL_BANK_FILE', str(path))
    return path


def test_full_options_remap_after_shuffle(bank):
    build_bank([record()], bank)
    assert lookup(record(options=['A. 丙', 'B. 甲', 'C. 乙'])) == 'C'
    assert lookup(record(options=['A. 乙', 'B. 甲', 'C. 丙'])) == 'A'


@pytest.mark.parametrize('changes', [
    {'type': 'multiple'}, {'options': ['甲', '乙']}, {'options': ['甲', '乙', '丁']},
    {'title': '2025年测试题目？'}, {'title': '2026年测试题目不正确？'},
    {'options': ['A. 甲', 'A. 乙', 'C. 丙']}, {'options': ['甲', '乙', '乙']},
])
def test_context_mismatch_never_hits(bank, changes):
    build_bank([record()], bank)
    assert lookup(record(**changes)) is None


def test_multiple_answers_remap_in_current_order(bank):
    build_bank([record(type='multiple', answers=['甲', '丙'])], bank)
    assert lookup(record(type='multiple', options=['丙', '乙', '甲'])) == 'AC'


def test_conflicts_quarantine_every_variant_even_if_repeated(bank):
    result = build_bank([record(), record(answers=['甲']), record()], bank)
    assert result['conflicts'] == 1 and result['accepted'] == 0
    assert lookup(record()) is None


def test_same_title_with_different_option_sets_is_separate(bank):
    other = record(options=['丁', '戊'], answers=['丁'])
    result = build_bank([record(), other], bank)
    assert result['accepted'] == 2
    assert lookup(other) == 'A'


@pytest.mark.parametrize('changes', [
    {'answers': ['无法确定']}, {'answers': ['无法确定，题目乱码']},
    {'answers': ['甲', '乙']}, {'answers': ['丁']}, {'answers': []},
    {'options': []}, {'source': ''}, {'type': 'unknown'},
    {'title': '<img src="https://example.org/q.png">测试'},
    {'options': ['<img src="https://example.org/q.png">', '乙']},
    {'type': 'judgement', 'answers': ['也许正确']},
])
def test_bad_imports_are_rejected(bank, changes):
    result = build_bank([record(**changes)], bank)
    assert result['rejected'] == 1 and result['accepted'] == 0


def test_preserves_c_string_spaces_and_years(bank):
    row = record(title='printf("a  b"); 输出是什么？')
    build_bank([row], bank)
    assert lookup(row) == 'B'
    assert lookup(record(title='printf("a b"); 输出是什么？')) is None


def test_judgements_and_multiblank_text(bank):
    rows = [record(type='judgement', answers=['对']),
            record(type='completion', answers=['输入', '输出'])]
    build_bank(rows, bank)
    assert lookup(rows[0]) == '正确'
    assert lookup(rows[1]) == '输入\n输出'


def test_duplicate_sources_retained(bank):
    result = build_bank([record(), record(source='https://example.org/other')], bank)
    assert result['accepted'] == 1 and result['conflicts'] == 0
    with sqlite3.connect(bank) as con:
        assert 'other' in con.execute('SELECT sources FROM questions').fetchone()[0]


def test_atomic_replacement_is_seen_without_restart(bank):
    build_bank([record()], bank)
    assert lookup(record()) == 'B'
    build_bank([record(answers=['甲'])], bank)
    assert lookup(record()) == 'A'


def test_missing_corrupt_and_wrong_version_banks_fall_through(bank):
    assert lookup(record()) is None
    bank.write_bytes(b'not a database')
    assert lookup(record()) is None
    bank.unlink()
    build_bank([record()], bank)
    with sqlite3.connect(bank) as con:
        con.execute('UPDATE metadata SET version=99')
    assert lookup(record()) is None


@pytest.mark.parametrize('chain', [False, True])
def test_import_hit_precedes_generated_cache_and_ai(bank, monkeypatch, chain):
    from app.services.course.chaoxing.answer_base import TikuFallback
    from app.services.course.chaoxing.answer_providers import AI
    from app.services.course.chaoxing.answer_cache import CacheDAO
    from app.services.course.chaoxing.answer_providers.local_cache import TikuLocalCache

    build_bank([record()], bank)
    ai = AI()
    ai._query = Mock(side_effect=AssertionError('No external requests on imported hit'))
    tiku = TikuFallback([TikuLocalCache(), ai]) if chain else ai
    generated = Mock(side_effect=AssertionError('Imported hit must not read generated cache'))
    monkeypatch.setattr(CacheDAO, 'get_question_cache', generated)
    write = Mock()
    monkeypatch.setattr(CacheDAO, 'add_question_cache', write)
    assert tiku.query(record(options=['丙', '乙', '甲'])) == 'B'
    ai._query.assert_not_called()
    write.assert_not_called()


def test_import_miss_preserves_local_to_ai_fallback(bank, monkeypatch):
    from app.services.course.chaoxing.answer_base import Tiku
    from app.services.course.chaoxing.answer_cache import CacheDAO
    from app.services.course.chaoxing.answer_providers import AI

    monkeypatch.setenv('CHAOXING_CACHE_FILE', str(bank.parent / 'generated.json'))
    monkeypatch.setattr(CacheDAO, 'get_question_cache', lambda *a: None)
    monkeypatch.setattr(CacheDAO, 'add_question_cache', lambda *a: None)
    tiku = Tiku()
    tiku.config_set({'provider': 'LocalCache,AI'})
    tiku = tiku.get_tiku_from_config()
    monkeypatch.setattr(AI, '_init_tiku', lambda *a: None)
    tiku.init_tiku()
    assert [type(p).__name__ for p in tiku.providers] == ['TikuLocalCache', 'AI']
    with pytest.MonkeyPatch.context() as patcher:
        patcher.setattr(AI, '_query', lambda *a: '甲')
        assert tiku.query(record()) is not None


def test_custom_judgement_tokens_are_respected(bank):
    from app.services.course.chaoxing.answer_base import Tiku

    build_bank([record(type='judgement', answers=['错误'])], bank)
    tiku = Tiku()
    tiku.true_list, tiku.false_list = ['yes'], ['no']
    answer = tiku.query(record(type='judgement'))
    assert answer == 'no'
    assert tiku.judgement_select(answer) is False
