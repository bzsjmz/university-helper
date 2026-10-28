"""English answering reuses the existing AI cost controls in the parent process.

Browser children never receive server API keys. Cache identities include platform,
course, question type, context and ordered options; unverified shapes never enter
this cache, and failures are shared only with current concurrent waiters.
"""
import json
import os
import random
import threading

from app.services.course.chaoxing import query_control as control
from app.services.course.chaoxing.answer_cache import CacheDAO
from app.services.course.chaoxing.answer_providers.ai import AI
from .native_answers import valid_native


class InvalidQuestion(ValueError):
    pass


def validate_question(raw):
    if not isinstance(raw, dict):
        raise InvalidQuestion()
    kind = raw.get('type')
    title = raw.get('title')
    options = raw.get('options', [])
    count = raw.get('count', 1)
    context = raw.get('context', '')
    if (kind not in ('single', 'multiple', 'completion', 'shortanswer')
            or not isinstance(title, str) or not title.strip() or len(title) > 12000
            or not isinstance(context, str) or len(context) > 10000
            or type(count) is not int or not 1 <= count <= 30
            or not isinstance(options, list) or len(options) > 26
            or any(not isinstance(o, str) or not o.strip() or len(o) > 2000 for o in options)):
        raise InvalidQuestion()
    if kind in ('single', 'multiple') and (count != 1 or len(options) < 2 or len(set(options)) != len(options)):
        raise InvalidQuestion()
    return {'type': kind, 'title': title.strip(), 'context': context,
            'options': options, 'count': count}


def normalize_answer(question, answer):
    """Only exact option text/letters and exact blank counts; never random fill."""
    if isinstance(answer, str):
        try:
            parsed = json.loads(answer)
            if isinstance(parsed, dict):
                answer = parsed.get('Answer', parsed.get('answer'))
            elif isinstance(parsed, list):
                answer = parsed
        except (ValueError, TypeError):
            pass
    if isinstance(answer, str):
        answer = answer.strip().splitlines()
    if not isinstance(answer, list) or not answer or any(not isinstance(a, str) or not a.strip() or len(a) > 10000 for a in answer):
        return None
    answer = [a.strip() for a in answer]
    if question['type'] in ('single', 'multiple'):
        if len(answer) == 1 and question['type'] == 'multiple':
            text = answer[0]
            if text not in question['options']:
                import re
                if re.fullmatch(r'[A-Z](?:[ ,、]+[A-Z])*|[A-Z]{2,26}', text):
                    answer = re.findall('[A-Z]', text)
        indexes = []
        for value in answer:
            if value in question['options']:
                index = question['options'].index(value)
            elif len(value) == 1 and 'A' <= value <= 'Z':
                index = ord(value) - ord('A')
            else:
                return None
            if index >= len(question['options']) or index in indexes:
                return None
            indexes.append(index)
        if question['type'] == 'single' and len(indexes) != 1:
            return None
        return indexes
    if len(answer) != question['count']:
        return None
    return answer


_AI_GATE = threading.Semaphore(3)


def get_provider():
    candidate = AI()
    candidate.config_set({'submit': 'false', 'max_retries': 3, 'max_output_tokens': 512})
    candidate.init_tiku()
    candidate._request_semaphore = _AI_GATE
    return candidate


class EnglishSolver:
    name = '英语答题'

    def __init__(self, platform, course, allow_ai=False, should_stop=None):
        self.platform, self.course = platform, course
        self.allow_ai = allow_ai
        self.provider = None
        self.should_stop = should_stop or (lambda: False)

    @control.bounded_query
    def _solve(self, question, native=None):
        # Keep this cache namespace separate from legacy question-title entries.
        identity = json.dumps({'version': 1, 'platform': self.platform, 'course': self.course,
            'endpoint': os.environ.get('CHAOXING_AI_ENDPOINT', ''),
            'model': os.environ.get('CHAOXING_AI_MODEL', ''), **question}, sort_keys=True, ensure_ascii=False)
        cache_question = {'type': 'completion', 'title': 'english-v1:' + identity,
                          'options': [], 'scope': self.platform}
        native_identity = json.dumps({'platform': self.platform, 'course': self.course, **question},
                                     sort_keys=True, ensure_ascii=False)
        native_question = {'type': 'completion', 'title': 'english-native-v1:' + native_identity,
                           'options': [], 'scope': self.platform}
        cache = CacheDAO()
        if native is not None:
            # Native solutions must not wait behind an already-running AI call.
            canonical = [chr(65+i) for i in native] if question['type'] in ('single', 'multiple') else native
            cache.add_question_cache(native_question, json.dumps(canonical, ensure_ascii=False))
            return {'answer': native, 'source': 'native'}

        def native_cached():
            if self.platform == 'welearn':
                cached_native = cache.get_question_cache(native_question)
                if cached_native:
                    try:
                        valid = normalize_answer(question, json.loads(cached_native))
                        if valid is not None:
                            return {'answer': valid, 'source': 'cache'}
                    except (ValueError, TypeError):
                        pass
            return None

        cached = native_cached()
        if cached is not None:
            return cached
        with cache.question_lock(cache_question, {'source': 'english', 'allow_ai': self.allow_ai}) as flight:
            cached = native_cached()
            if cached is not None:
                return cached
            if flight.get('miss'):
                return {'answer': None, 'source': 'miss'}
            cached = cache.get_question_cache(cache_question)
            if cached:
                try:
                    stored = json.loads(cached)
                    # Stored choice results are canonical letters, never bare indexes.
                    valid = normalize_answer(question, stored)
                    if valid is not None:
                        return {'answer': valid, 'source': 'cache'}
                except (ValueError, TypeError):
                    pass
            if not self.allow_ai:
                flight['miss'] = True
                return {'answer': None, 'source': 'miss'}
            try:
                if self.provider is None:
                    self.provider = get_provider()
                provider = self.provider
            except Exception:
                flight['miss'] = True
                return {'answer': None, 'source': 'ai_unavailable'}
            prompt = {'type': question['type'], 'title': (
                question['context'] + '\n' + question['title'] +
                f"\n请按顺序给出恰好 {question['count']} 个答案。"),
                'options': [f'{chr(65+i)}. {text}' for i, text in enumerate(question['options'])]}
            if self.platform == 'welearn':
                # Pace each new AI question, including the first, around 30s.
                # Keep cancellation/deadlines active without occupying an API slot.
                control.sleep(random.uniform(20.0, 40.0))
                cached = native_cached()
                if cached is not None:
                    return cached
            # _query retains dual-key fallback, HTTP attempt/token limits and
            # usage telemetry, without Tiku.query's raw question/answer logging.
            raw = provider._query(prompt)
            cached = native_cached()
            if cached is not None:
                return cached
            valid = normalize_answer(question, raw)
            if valid is None:
                flight['miss'] = True
                return {'answer': None, 'source': 'miss'}
            canonical = [chr(65+i) for i in valid] if question['type'] in ('single', 'multiple') else valid
            cache.add_question_cache(cache_question, json.dumps(canonical, ensure_ascii=False))
            return {'answer': valid, 'source': 'ai'}

    def solve(self, raw):
        try:
            question = validate_question(raw)
            native = valid_native(question, raw.get('native_answer')) if self.platform == 'welearn' else None
            with control.job_scope(self.should_stop, answering=True):
                return self._solve(question, native)
        except (InvalidQuestion, control.QueryControlError, control.ProviderUnavailable):
            return {'answer': None, 'source': 'unavailable'}

    def close(self):
        if self.provider is not None and self.provider._httpx_client is not None:
            self.provider._httpx_client.close()
