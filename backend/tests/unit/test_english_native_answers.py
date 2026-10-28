from unittest.mock import Mock
import threading

import pytest

from app.services.course.english import answering
from app.services.course.english.answering import EnglishSolver
from app.services.course.english.native_answers import NativeAnswers, parse_qti, qti_answer, valid_native, MAX_XML

QUESTION = {'type': 'single', 'title': 'Choose a color.', 'options': ['red', 'blue'], 'count': 1}
XML = b'''<assessmentItem xmlns="http://www.imsglobal.org/xsd/imsqti_v2p1" identifier="q1">
<responseDeclaration identifier="R" cardinality="single" baseType="identifier"><correctResponse><value>c2</value></correctResponse></responseDeclaration>
<itemBody><choiceInteraction responseIdentifier="R"><prompt>Choose a color.</prompt>
<simpleChoice identifier="c1">red</simpleChoice><simpleChoice identifier="c2">blue</simpleChoice>
</choiceInteraction></itemBody></assessmentItem>'''
BINDING = {'root_keys': ['q1'], 'control_keys': [['R'], ['R']], 'option_keys': [['c1'], ['c2']]}


@pytest.fixture
def solver(tmp_path, monkeypatch):
    from app.services.course.chaoxing.answer_cache import CacheDAO
    path = str(tmp_path / 'native-cache.json')
    monkeypatch.setattr(answering, 'CacheDAO', lambda: CacheDAO(path))
    provider = Mock(_httpx_client=None)
    provider._query.return_value = 'B'
    monkeypatch.setattr(answering, 'get_provider', lambda: provider)
    monkeypatch.setattr(answering.control, 'sleep', Mock())
    return EnglishSolver('welearn', 'course', True), provider


def test_native_solution_overrides_ai_cache_then_is_reused_without_model_or_paid_ai(solver, monkeypatch):
    engine, provider = solver
    assert engine.solve(QUESTION) == {'answer': [1], 'source': 'ai'}
    assert engine.solve({**QUESTION, 'native_answer': [0]}) == {'answer': [0], 'source': 'native'}
    monkeypatch.setenv('CHAOXING_AI_MODEL', 'different-model')
    engine.allow_ai = False
    assert engine.solve(QUESTION) == {'answer': [0], 'source': 'cache'}
    provider._query.assert_called_once()
    # An option reorder must not reuse the old canonical letter.
    assert engine.solve({**QUESTION, 'options': ['blue', 'red']})['answer'] is None


def test_native_blanks_are_cached_with_exact_count_and_other_platforms_are_unchanged(solver):
    engine, provider = solver
    engine.allow_ai = False
    q = {'type': 'completion', 'title': 'Fill two.', 'count': 2, 'options': []}
    assert engine.solve({**q, 'native_answer': ['one', 'two']})['source'] == 'native'
    assert engine.solve(q) == {'answer': ['one', 'two'], 'source': 'cache'}
    assert engine.solve({**q, 'count': 1})['answer'] is None
    engine.platform = 'unipus'
    assert engine.solve({**QUESTION, 'native_answer': [0]})['answer'] is None
    provider._query.assert_not_called()


def test_native_hit_does_not_wait_for_same_question_ai_and_supersedes_late_ai_result(solver):
    engine, provider = solver
    entered, release, native_done = threading.Event(), threading.Event(), threading.Event()
    results = {}
    def slow_ai(q):
        entered.set()
        assert release.wait(3)
        return 'B'
    provider._query.side_effect = slow_ai
    ai_thread = threading.Thread(target=lambda: results.update(ai=engine.solve(QUESTION)))
    def native_run():
        results['native'] = engine.solve({**QUESTION, 'native_answer': [0]})
        native_done.set()
    native_thread = threading.Thread(target=native_run)
    ai_thread.start()
    try:
        assert entered.wait(2)
        native_thread.start()
        assert native_done.wait(1)
    finally:
        release.set()
        ai_thread.join(2)
        if native_thread.ident is not None:
            native_thread.join(2)
    assert results['native'] == {'answer': [0], 'source': 'native'}
    assert results['ai'] == {'answer': [0], 'source': 'cache'}
    provider._query.assert_called_once()


@pytest.mark.parametrize('answer', [[], [True], [-1], [2], [0, 0], ['A'], [0, 1]])
def test_invalid_native_choice_cannot_bypass_normal_fallback(solver, answer):
    engine, provider = solver
    assert valid_native(QUESTION, answer) is None
    assert engine.solve({**QUESTION, 'native_answer': answer}) == {'answer': [1], 'source': 'ai'}
    provider._query.assert_called_once()


def test_qti_requires_item_prompt_response_and_option_identity_not_question_order():
    record = parse_qti(XML)[0]
    assert qti_answer(QUESTION, BINDING, record) == [1]
    assert qti_answer({**QUESTION, 'options': ['blue', 'red']},
        {**BINDING, 'option_keys': [['c2'], ['c1']]}, record) == [0]
    assert qti_answer(QUESTION, {**BINDING, 'root_keys': ['another-question']}, record) is None
    assert qti_answer({**QUESTION, 'title': 'Different question.'}, BINDING, record) is None
    assert qti_answer(QUESTION, {**BINDING, 'control_keys': [['other'], ['other']]}, record) is None
    assert qti_answer({**QUESTION, 'options': ['red', 'green']}, BINDING, record) is None


def test_qti_multiple_blanks_follow_explicit_identifiers_even_when_declarations_reorder():
    raw = b'''<assessmentItem identifier="q1"><prompt>Fill two.</prompt>
    <responseDeclaration identifier="R2"><correctResponse><value>second</value></correctResponse></responseDeclaration>
    <responseDeclaration identifier="R1"><correctResponse><value>first</value></correctResponse></responseDeclaration>
    </assessmentItem>'''
    q = {'type': 'completion', 'title': 'Fill two.', 'options': [], 'count': 2}
    binding = {'root_keys': ['q1'], 'control_keys': [['R1'], ['R2']]}
    assert qti_answer(q, binding, parse_qti(raw)[0]) == ['first', 'second']
    assert qti_answer(q, {**binding, 'control_keys': [['R1'], ['R1']]}, parse_qti(raw)[0]) is None


@pytest.mark.parametrize('raw', [b'<broken', b'x' * (MAX_XML + 1),
    b'<!DOCTYPE x [<!ENTITY x SYSTEM "file:///etc/passwd">]><x>&x;</x>',
    '<!DOCTYPE x><x/>'.encode('utf-16')])
def test_unsafe_or_oversize_xml_is_a_miss(raw):
    assert parse_qti(raw) == []


def resource(frame, raw=XML, **kwargs):
    request = Mock(method='GET', resource_type='xhr', frame=frame)
    response = Mock(url='https://centercourseware.sflep.com/course/item.xml', status=200,
        headers={'content-length': str(len(raw))}, request=request, **kwargs)
    response.body.return_value = raw
    return response


def test_passive_resource_capture_is_bound_to_current_document_and_conflicts_are_misses():
    frame = Mock(url='https://centercourseware.sflep.com/course/current.html')
    frame.evaluate.return_value = {'candidates': [], 'binding': BINDING}
    response = resource(frame)
    native = NativeAnswers()
    native.observe(response)
    response.body.assert_not_called()
    assert native.answer(frame, QUESTION) == [1]
    frame.evaluate.return_value = {'candidates': [[0]], 'binding': BINDING}
    assert native.answer(frame, QUESTION) is None
    frame.url += '?different-document'
    assert native.answer(frame, QUESTION) == [0]  # Only its own DOM remains eligible.
    frame.evaluate.return_value = {'candidates': [[0], [1]], 'binding': BINDING}
    assert native.answer(frame, QUESTION) is None


@pytest.mark.parametrize('change', [
    {'url': 'https://evil.example/item.xml'}, {'url': 'http://centercourseware.sflep.com/item.xml'},
    {'url': 'https://user@centercourseware.sflep.com/item.xml'},
    {'url': 'https://centercourseware.sflep.com:8000/item.xml'},
    {'headers': {}}, {'headers': {'content-length': str(MAX_XML + 1)}}, {'status': 302}])
def test_resource_capture_does_not_fetch_unrelated_or_unknown_size_content(change):
    response = resource(Mock(url='https://centercourseware.sflep.com/lesson'))
    for key, value in change.items():
        setattr(response, key, value)
    native = NativeAnswers()
    native.observe(response)
    assert not native.resources
    response.body.assert_not_called()
