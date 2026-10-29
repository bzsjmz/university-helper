from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from app.services.course.english import welearn_page


@pytest.fixture
def content(monkeypatch):
    clock = SimpleNamespace(now=0.0)
    monkeypatch.setattr(welearn_page, 'time', SimpleNamespace(monotonic=lambda: clock.now))
    monkeypatch.setattr(welearn_page, 'frame_visible', lambda frame: True)
    frame = Mock(url='https://centercourseware.sflep.com/lesson.html')
    frame.evaluate.return_value = True
    page = Mock(frames=[frame])
    page.wait_for_timeout.side_effect = lambda ms: setattr(clock, 'now', clock.now + ms / 1000)
    return page, frame, clock


def test_loaded_content_remains_open_ten_seconds(content):
    page, frame, clock = content
    assert welearn_page.wait_content(page, 30) == [frame]
    assert clock.now == pytest.approx(10.0)
    assert max(call.args[0] for call in page.wait_for_timeout.call_args_list) <= 200


def test_ten_seconds_start_after_content_becomes_ready(content):
    page, frame, clock = content
    frame.evaluate.side_effect = lambda script: clock.now >= 2
    assert welearn_page.wait_content(page, 30) == [frame]
    assert clock.now >= 12.0


def test_budget_expiry_does_not_return_content_before_minimum(content):
    page, frame, clock = content
    assert welearn_page.wait_content(page, 2) == []
    assert clock.now == pytest.approx(2.0)


def test_unloaded_content_is_not_skipped_immediately(content):
    page, frame, clock = content
    frame.evaluate.return_value = False
    assert welearn_page.wait_content(page, 30) == []
    assert clock.now == pytest.approx(25.0)


def test_closed_page_interrupts_wait_without_repeated_attempts(content):
    page, frame, clock = content
    page.wait_for_timeout.side_effect = RuntimeError('page closed by stop')
    with pytest.raises(RuntimeError, match='page closed'):
        welearn_page.wait_content(page, 30)
    page.wait_for_timeout.assert_called_once()
