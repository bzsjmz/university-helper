import json,time
from unittest.mock import Mock
from app.services.course.chaoxing.api_usage import record_response


def test_usage_is_allowlisted_and_failure_does_not_raise(monkeypatch,tmp_path):
    path=tmp_path/'usage.jsonl';monkeypatch.setenv('CHAOXING_API_USAGE_FILE',str(path))
    response=Mock(status_code=200)
    response.json.return_value={'usage':{'prompt_tokens':100,'completion_tokens':20,'total_tokens':120,'key':'never-log'},'choices':[{'content':'private question'}]}
    record_response('AI','fallback',2,time.monotonic(),response)
    event=json.loads(path.read_text())
    assert event['total_tokens']==120 and event['key_role']=='fallback'
    assert 'never-log' not in path.read_text() and 'private question' not in path.read_text()
    assert path.stat().st_mode & 0o777 == 0o600
    monkeypatch.setenv('CHAOXING_API_USAGE_FILE',str(tmp_path/'missing'/'usage'))
    record_response('AI','primary',1,time.monotonic(),response)
