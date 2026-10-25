import json
from unittest.mock import Mock

import pytest

from app.services.course.chaoxing import answer_base, cxsecret_font
from app.services.course.chaoxing.answer_cache import CacheDAO
from app.services.course.chaoxing.answer_providers import AI, TikuGo
from app.services.course.chaoxing.answer_quality import is_usable_answer, has_unanswered_text
from app.services.course.chaoxing.course_assessments import answer_fields
from app.services.course.chaoxing.exceptions import FontDecodeError
from app.services.course.chaoxing.font_decoder import FontDecoder
from app.services.course.chaoxing.quiz_service import QuizAnswerProcessor
from app.services.course.chaoxing import quiz_service, work_legacy_service
from app.services.course.chaoxing.work_legacy_service import ChaoxingWorkLegacyService


BAD = [
    '无法确定',
    '无法确定，题目中的字符疑似乱码或加密文本，无法识别具体问题。',
    '无法确定，题目中未提供具体纵火事件的相关信息。',
    '抱歉，无法识别题目', '答案：无法确定', '题目包含乱码，无法解答。',
    '信息不足，无法回答', '请提供完整题目',
    "I cannot determine the answer.", 'Unable to read the question',
    '第一空答案\n无法确定', '', None,
]


@pytest.mark.parametrize('answer', BAD)
def test_rejects_abstentions(answer):
    assert not is_usable_answer(answer)


@pytest.mark.parametrize('answer', [
    '保持冷静，及时报警，保护自身安全。', 'A', '正确', '输入/输出',
    '无法确定伤势时，应及时就医并寻求帮助。',
    '遇到无法识别的物品，应远离并报告。',
    '加密文本需要使用相应密钥解密。',
])
def test_substantive_answers_are_not_rejected_for_containing_marker_words(answer):
    assert is_usable_answer(answer)


@pytest.fixture
def cache(tmp_path, monkeypatch):
    dao = CacheDAO(str(tmp_path / 'answers.json'))
    monkeypatch.setattr(answer_base, 'CacheDAO', lambda: dao)
    return dao


def question():
    return {'id': '1', 'title': '如何妥善处理恋爱纠纷？', 'type': 'shortanswer',
            'options': '', 'answerField': {'answer1': '', 'answertype1': '4'}}


@pytest.mark.parametrize('bad', BAD[:-2])
def test_old_poisoned_disk_cache_is_a_miss_and_new_writes_rejected(tmp_path, bad):
    path = tmp_path / 'old.json'
    key = CacheDAO.question_key(question())
    path.write_text(json.dumps({key: bad, 'good': '有效答案'}))
    dao = CacheDAO(str(path))
    assert dao.get_question_cache(question()) is None
    dao.add_cache('new', bad)
    assert dao.get_cache('new') is None
    assert 'new' not in json.loads(path.read_text())
    assert dao.get_cache('good') == '有效答案'


@pytest.mark.parametrize('fallback', [False, True])
def test_ai_abstention_not_returned_or_cached(cache, fallback):
    ai = AI()
    ai._query = Mock(return_value=BAD[1])
    provider = answer_base.TikuFallback([ai]) if fallback else ai
    assert provider.query(question()) is None
    assert cache.get_question_cache(question()) is None
    assert not json.loads(cache.cache_file.read_text())


def test_bad_bank_result_falls_back_without_polluting_cache(cache):
    bank = TikuGo()
    bank._query = Mock(return_value='无法确定')
    ai = AI()
    ai._query = Mock(return_value='保持冷静，理性沟通。')
    assert answer_base.TikuFallback([bank, ai]).query(question()) == '保持冷静，理性沟通。'
    assert cache.get_question_cache(question()) == '保持冷静，理性沟通。'


@pytest.mark.parametrize('legacy', [False, True])
def test_processor_does_not_fill_or_count_abstention(legacy):
    tiku = Mock()
    tiku.query.return_value = BAD[2]
    found = Mock()
    q = question()
    if legacy:
        ChaoxingWorkLegacyService(tiku, 1, {})._handle_question(q, found, '')
    else:
        QuizAnswerProcessor(tiku, {}).handle_question(q, found)
    assert q['answerField']['answer1'] == ''
    found.assert_not_called()
    assert has_unanswered_text([q])


@pytest.mark.parametrize('code', ['2', '4', '6'])
def test_course_assessment_cannot_map_abstention(code):
    assert answer_fields({'id': '1', 'code': code}, BAD[1], Mock(), 'homework') is None


def test_partial_font_mapping_never_passes_garbled_characters(monkeypatch):
    dao = Mock()
    dao.find_char.side_effect = lambda value: {'known': 'uni5982'}.get(value)
    monkeypatch.setattr(cxsecret_font, 'fonthash_dao', dao)
    assert cxsecret_font.decrypt({'uni4E00': 'known'}, '一？') == '如？'
    with pytest.raises(FontDecodeError):
        cxsecret_font.decrypt({'uni4E00': 'known', 'uni4E8C': 'missing'}, '一二？')


@pytest.mark.parametrize('quote', ["'", '"'])
def test_missing_reference_map_blocks_encrypted_questions(monkeypatch, quote):
    monkeypatch.setattr(cxsecret_font.fonthash_dao, 'hash_map', {})
    decoder = FontDecoder(f'<style id="cxSecretStyle">src:url({quote}data:application/font-ttf;charset=utf-8;base64,YQ=={quote})</style>')
    with pytest.raises(FontDecodeError):
        decoder.decode('加密题干')


@pytest.mark.parametrize('quote', ["'", '"'])
def test_both_font_style_quote_formats_decode(monkeypatch, quote):
    monkeypatch.setattr(cxsecret_font.fonthash_dao, 'hash_map', {'known': 'uni5982'})
    monkeypatch.setattr(cxsecret_font, 'font2map', lambda _: {'uni4E00': 'known'})
    decoder = FontDecoder(f'<style id="cxSecretStyle">src:url({quote}data:application/font-ttf;charset=utf-8;base64,YQ=={quote})</style>')
    assert decoder.decode('一？') == '如？'


@pytest.mark.parametrize('legacy', [False, True])
@pytest.mark.parametrize('result', ['无法确定', None])
def test_retry_saves_instead_of_submitting_unanswered_written_question(monkeypatch, legacy, result):
    module = work_legacy_service if legacy else quiz_service
    monkeypatch.setattr(module, 'decode_questions_info', lambda _: {'questions': [question()]})
    tiku = Mock(DISABLE=False, COVER_RATE=0.8, wants_concurrent_query=False)
    tiku.query.return_value = result
    tiku.get_submit_params.return_value = ''
    session = Mock()
    session.get.return_value.status_code = 200
    session.get.return_value.text = '<form>synthetic</form>'
    session.post.return_value.status_code = 200
    session.post.return_value.json.return_value = {'status': True, 'msg': 'saved'}
    manager = Mock()
    manager.get_session.return_value = session
    cls = ChaoxingWorkLegacyService if legacy else quiz_service.ChaoxingQuizService
    service = cls(tiku, 1, {}, manager)
    service.study_work({'courseId': '100', 'clazzId': '200'}, {'jobid': 'work-9', 'enc': 'test'},
                       {'knowledgeid': '1', 'ktoken': 'test', 'cpi': '300'})
    data = session.post.call_args.kwargs['data']
    assert data['pyFlag'] == '1'
    assert data['answer1'] == ''
