from datetime import datetime
from unittest.mock import Mock, patch

import pytest

from app.services.course.english.clients import PlatformError
from app.services.course.english import welearn_class_tasks as tasks
from tests.unit.test_welearn_workbench import bench, prepare, config, finished  # noqa: F401


NOW = datetime(2026, 11, 5, 12, tzinfo=tasks.TZ)


def row(**changes):
    return {'TaskName': 'Example quiz', 'TaskTypeName': '测试', 'BeginDateStr': '2026/11/01 00:00',
            'EndDateStr': '2026/11/08 23:59', 'Status': '未完成',
            'TaskHref': 'StartTaskClassTest(123)', 'TaskAction': '<a>开始</a>', **changes}


@pytest.mark.parametrize('changes,state', [
    ({}, 'available'),
    ({'BeginDateStr': '2026/11/10 15:00', 'EndDateStr': '2026/11/16 23:59', 'TaskHref': 'DoNothing();', 'TaskAction': '未开始'}, 'upcoming'),
    ({'Status': '80 分', 'TaskHref': '', 'TaskAction': '已结束'}, 'completed'),
    ({'Status': '0 分'}, 'completed'),
    ({'Status': '待批阅'}, 'completed'),
    ({'EndDateStr': '2026/11/03 23:59', 'TaskHref': ''}, 'expired'),
    ({'TaskTypeName': '口语训练', 'TaskHref': 'StartTaskOralTraining(123)'}, 'manual'),
    ({'TaskTypeName': '口语训练', 'Status': '92 分'}, 'completed'),
    ({'TaskHref': 'StartTaskClassTest(123); sendCookie()'}, 'manual'),
    ({'TaskHref': 'RedoClassTest(123)'}, 'manual'),
    ({'TaskAction': '重新开始'}, 'manual'),
    ({'Status': '未知'}, 'manual'),
    ({'BeginDateStr': '2026/11/09 00:00'}, 'unknown'),
])
def test_fresh_platform_status_controls_admission(changes, state):
    result = tasks.normalize(row(**changes), NOW)
    assert result['availability'] == state
    assert result['selectable'] == (state == 'available')
    assert '_task_id' not in tasks.public_task(result)


def test_identity_stable_when_task_closes_and_has_no_native_entry():
    pending = tasks.normalize(row(), NOW)
    graded = tasks.normalize(row(Status='80 分', TaskHref='', TaskAction='已结束'), NOW)
    assert pending['id'] == graded['id']
    assert '123' not in str(tasks.public_task(graded))


@pytest.mark.parametrize('changes', [{'TaskName': ''}, {'BeginDateStr': '明天'}, {'Status': {}}, {'TaskTypeName': 'x'*13000}])
def test_ambiguous_fields_fail_closed(changes):
    with pytest.raises(PlatformError):
        tasks.normalize(row(**changes), NOW)


def client_for(pages):
    client = Mock()
    client.courses.return_value = [{'id': 'c'}]
    client.request.side_effect = ['{"uid":123,"classid":"45"}', *pages]
    return client


def test_catalogue_paginates_and_uses_only_current_course_identity():
    client = client_for([{'ret': 0, 'count': 2, 'list': [row()]},
                         {'ret': 0, 'count': 2, 'list': [row(TaskName='Another')]}])
    assert len(tasks.catalogue(client, 'c')) == 2
    call = client.request.call_args_list[-1]
    assert call.args[0] == '/ajax/task.aspx'
    assert call.kwargs['params'] == {'action': 'gettasklist', 'cid': 'c', 'uid': '123', 'classid': '45', 's': 100, 'pi': 1}


@pytest.mark.parametrize('pages', [
    [{'ret': 1, 'count': 0, 'list': []}],
    [{'ret': 0, 'count': 2, 'list': [row(), row()]}],
    [{'ret': 0, 'count': 2, 'list': []}],
    [{'ret': 0, 'count': 1, 'list': [row(), row(TaskName='Other')]}],
    [{'ret': 0, 'count': 1001, 'list': []}],
])
def test_incomplete_or_ambiguous_catalogue_is_not_a_selectable_subset(pages):
    with pytest.raises(PlatformError):
        tasks.catalogue(client_for(pages), 'c')


def test_course_membership_checked_before_details():
    client = client_for([])
    with pytest.raises(PlatformError): tasks.catalogue(client, 'other')
    client.request.assert_not_called()


@pytest.mark.parametrize('state', ['completed', 'upcoming', 'expired', 'manual', 'available'])
def test_no_browser_or_paid_solver_when_not_explicitly_starting(state):
    selected = tasks.normalize(row(), NOW)
    selected.update(availability=state, selectable=state == 'available')
    solver = Mock(side_effect=AssertionError('No paid query'))
    with patch.object(tasks, 'Welearn'), patch.object(tasks, 'catalogue', return_value=[selected]), \
            patch('app.services.course.english.clients.unipus_browser', side_effect=AssertionError('No browser')):
        result = tasks.answer('private', 'c', selected['id'], 60, solver)
    assert result['status'] == ('finished' if state == 'completed' else 'needs_manual')
    solver.assert_not_called()


def prepare_class(bench):
    account = prepare(bench)
    previous = bench.engine.execute.side_effect
    bench.engine.execute.side_effect = lambda req, task=None: ({'items': [{'id': 't', 'name': 'Quiz', 'selectable': True, 'availability': 'available'}]}
        if req['action'] == 'class_tasks' else previous(req, task))
    bench.class_tasks('alice', account['id'], 'c')
    return account


def class_config():
    return {**config(['t']), 'selections': [{'course': 'c', 'items': ['t'], 'kind': 'class_task'}], 'start_class_tests': True}


def test_class_catalogue_is_owner_scoped_and_not_lesson_scope(bench):
    account = prepare_class(bench)
    with pytest.raises(PlatformError): bench.class_tasks('bob', account['id'], 'c')
    with pytest.raises(PlatformError): bench.class_tasks('alice', account['id'], 'other')
    with pytest.raises(PlatformError): bench.start('alice', account['id'], {**class_config(), 'start_class_tests': False})
    with pytest.raises(PlatformError): bench.start('alice', account['id'], config(['t']))


def test_mixed_scope_restarts_exact_ids_and_dispatches_class_flag(bench):
    account = prepare_class(bench)
    cfg = class_config()
    cfg['selections'].append({'course': 'c', 'items': ['1']})
    bench.engine.execute.reset_mock()
    task = bench.start('alice', account['id'], cfg)
    done = finished(bench, task['id'])
    assert done['status'] == 'finished' and done['total_items'] == 2
    answers = [c.args[0] for c in bench.engine.execute.call_args_list if c.args[0]['action'] == 'answer']
    assert [r['item'] for r in answers] == ['t', '1']
    assert answers[0]['class_task'] is True and answers[0]['start_class_tests'] is True
    assert 'class_task' not in answers[1]
    assert [r['kind'] for r in done['results']] == ['class_task', 'lesson']
    bench.engine.execute.reset_mock()
    restarted = bench.restart('alice', task['id'])
    assert finished(bench, restarted['id'])['total_items'] == 2
    answers = [c.args[0] for c in bench.engine.execute.call_args_list if c.args[0]['action'] == 'answer']
    assert [r['item'] for r in answers] == ['t', '1']


def test_start_rejects_unopened_or_unsupported_class_task(bench):
    account = prepare_class(bench)
    bench.catalogs[('alice', account['id'])]['class_tasks']['c'][0]['selectable'] = False
    with pytest.raises(PlatformError): bench.start('alice', account['id'], class_config())


def test_api_explicit_test_ack_and_kind_validation():
    from app.api.v1.welearn import TaskRequest
    from pydantic import ValidationError
    cfg = class_config()
    assert TaskRequest(**cfg).selections[0].kind == 'class_task'
    for change in ({'start_class_tests': False}, {'start_class_tests': 'true'}, {'submit': False},
                   {'selections': [{'course': 'c', 'items': ['t'], 'kind': 'anything'}]}):
        with pytest.raises(ValidationError): TaskRequest(**{**cfg, **change})


def test_catalogue_api_auth_and_private_cookie_refresh(bench, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app.api.v1 import welearn
    from app.dependencies import get_current_user_id
    account = prepare_class(bench)
    monkeypatch.setattr(welearn, 'welearn_workbench', bench)
    app = FastAPI(); app.include_router(welearn.router)
    client = TestClient(app)
    path = '/accounts/' + account['id'] + '/class-tasks'
    assert client.post(path, json={'course': 'c'}).status_code in (401, 403)
    app.dependency_overrides[get_current_user_id] = lambda: 'alice'
    bench.engine.execute.side_effect = None
    bench.engine.execute.return_value = {'items': [], '_credential': 'sid=private-renewed'}
    response = client.post(path, json={'course': 'c'})
    assert response.status_code == 200
    assert response.headers['cache-control'] == 'no-store'
    assert 'private-renewed' not in response.text and '_credential' not in response.text
    app.dependency_overrides[get_current_user_id] = lambda: 'bob'
    assert client.post(path, json={'course': 'c'}).status_code == 400


def test_worker_class_dispatch_never_falls_through_to_lesson_or_http_draft(monkeypatch, capsys):
    import io
    import json
    import sys
    from app.services.course.english import worker
    request = {'platform': 'welearn', 'action': 'answer', 'class_task': True,
               'recording_bypass': True, 'credential': 'sid=private', 'course': 'c',
               'item': 't', 'seconds': 60, 'submit': True, 'start_class_tests': True}
    monkeypatch.setattr(sys, 'stdin', Mock(buffer=io.BytesIO(json.dumps(request).encode())))
    monkeypatch.setattr(worker, 'Welearn', Mock())
    mock_answer = Mock(return_value=tasks.result('manual'))
    monkeypatch.setattr(tasks, 'answer', mock_answer)
    worker.main()
    response = json.loads(capsys.readouterr().out)
    assert response['ok'] and 'private' not in str(response)
    assert mock_answer.call_args.kwargs == {'submit': True, 'start_class_tests': True}


@pytest.mark.parametrize('confirmed', [True, False])
def test_click_is_not_completion_until_class_list_confirms(confirmed):
    from contextlib import contextmanager
    selected = tasks.normalize(row(), NOW)
    after = {**selected, 'availability': 'completed' if confirmed else 'available'}
    @contextmanager
    def browser(*args, **kwargs):
        context = Mock(); context.cookies.return_value = []
        yield context, Mock()
    with patch.object(tasks, 'Welearn') as client_type, patch.object(tasks, 'catalogue', side_effect=[[selected], [after], [after], [after]]), \
            patch('app.services.course.english.clients.unipus_browser', browser), \
            patch.object(tasks, 'answer_test', return_value=tasks.result('pending', {'submission_attempted': 1})):
        client_type.return_value.BASE = 'https://welearn.sflep.com'
        outcome = tasks.answer('private', 'c', selected['id'], 60, Mock(), True, True)
    assert outcome['status'] == ('finished' if confirmed else 'needs_manual')
    assert outcome['counts'].get('submitted', 0) == int(confirmed)
    assert outcome['counts'].get('submission_unknown', 0) == int(not confirmed)
