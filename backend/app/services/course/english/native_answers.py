"""Solutions already delivered to the selected WE Learn page; no guessed URLs.

Independent implementation of DOM bindings and QTI correctResponse semantics.
Unrelated resources, ambiguous indexes and conflicting solutions are misses.
"""
import re
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.parse import urlsplit

SCRIPT = Path(__file__).with_suffix('.js').read_text(encoding='utf-8')
MAX_XML = 512_000


def norm(value):
    return re.sub(r'\s+', ' ', value or '').strip()


def valid_native(question, answer):
    if not isinstance(answer, list) or not answer:
        return None
    if question['type'] in ('single', 'multiple'):
        if (any(type(i) is not int or not 0 <= i < len(question['options']) for i in answer)
                or len(set(answer)) != len(answer)
                or question['type'] == 'single' and len(answer) != 1):
            return None
        return sorted(answer)
    if (len(answer) != question['count'] or any(not isinstance(v, str) or not v.strip()
            or len(v) > 10000 for v in answer)):
        return None
    return [v.strip() for v in answer]


def parse_qti(raw):
    if (not isinstance(raw, bytes) or len(raw) > MAX_XML or b'\x00' in raw
            or re.search(br'<!\s*(?:DOCTYPE|ENTITY)', raw, flags=re.I)):
        return []
    try:
        root = ET.fromstring(raw)
    except (ET.ParseError, ValueError):
        return []
    local = lambda e: e.tag.rsplit('}', 1)[-1] if isinstance(e.tag, str) else ''
    records = []
    for item in root.iter():
        if local(item) not in ('assessmentItem', 'assessment-item', 'item'):
            continue
        item_id = item.get('identifier') or item.get('ident') or item.get('id')
        prompts = [norm(''.join(e.itertext())) for e in item.iter() if local(e) == 'prompt']
        if not item_id or len(item_id) > 256 or len(prompts) != 1 or not prompts[0]:
            continue
        choices = {}
        duplicate = False
        for node in item.iter():
            if local(node) not in ('simpleChoice', 'simple-choice'):
                continue
            key = node.get('identifier')
            if not key or key in choices:
                duplicate = True
                break
            choices[key] = norm(''.join(node.itertext()))
        if duplicate:
            continue
        declarations = {}
        for declaration in item.iter():
            if local(declaration) not in ('responseDeclaration', 'response-declaration'):
                continue
            response_id = declaration.get('identifier')
            correct = [e for e in declaration if local(e) in ('correctResponse', 'correct-response')]
            if not response_id or len(correct) != 1 or response_id in declarations:
                declarations = {}
                break
            values = [norm(''.join(e.itertext())) for e in correct[0] if local(e) == 'value']
            if not values or any(not v or len(v) > 10000 for v in values):
                continue
            declarations[response_id] = values
        if declarations:
            records.append({'item_id': item_id, 'prompt': prompts[0], 'choices': choices,
                            'responses': declarations})
        if len(records) >= 100:
            break
    return records


def qti_answer(question, binding, record):
    if record['item_id'] not in binding.get('root_keys', []):
        return None
    title = norm(question['title'])
    if title != record['prompt']:
        return None
    control_keys = binding.get('control_keys', [])
    root_keys = binding.get('root_keys', [])
    responses = record['responses']
    if question['type'] in ('single', 'multiple'):
        keys = set(root_keys + [v for group in control_keys for v in group])
        matches = [values for key, values in responses.items() if key in keys]
        if len(matches) != 1:
            return None
        option_keys = binding.get('option_keys', [])
        choices = record['choices']
        if len(option_keys) != len(question['options']) or len(choices) != len(option_keys):
            return None
        mapped = {}
        for key, label in choices.items():
            indexes = [i for i, aliases in enumerate(option_keys) if key in aliases
                       and norm(question['options'][i]) == label]
            if len(indexes) != 1 or indexes[0] in mapped.values():
                return None
            mapped[key] = indexes[0]
        if any(key not in mapped for key in matches[0]):
            return None
        return valid_native(question, [mapped[key] for key in matches[0]])
    if len(control_keys) != question['count']:
        return None
    answers, used = [], set()
    for aliases in control_keys:
        matches = [(key, value) for key, value in responses.items() if key in aliases]
        if len(matches) != 1 or len(matches[0][1]) != 1 or matches[0][0] in used:
            return None
        used.add(matches[0][0])
        answers.append(matches[0][1][0])
    return valid_native(question, answers)


class NativeAnswers:
    def __init__(self):
        self.resources = []
        self.used_bytes = 0

    def observe(self, response):
        # Passive capture only: never enumerate manifests or request other lessons.
        try:
            parsed = urlsplit(response.url)
            host = parsed.hostname or ''
            request = response.request
            length = response.headers.get('content-length', '')
            if (len(self.resources) >= 12 or response.status != 200
                    or parsed.scheme != 'https' or not host.endswith('.sflep.com')
                    or parsed.port not in (None, 443) or parsed.username or parsed.password
                    or request.method != 'GET' or request.resource_type not in ('xhr', 'fetch')
                    or not parsed.path.lower().endswith('.xml')
                    or not length.isdecimal() or not 0 < int(length) <= MAX_XML):
                return
            self.resources.append({'response': response, 'frame': request.frame,
                                   'document': request.frame.url, 'records': None})
        except Exception:
            return

    def answer(self, frame, question):
        try:
            extracted = frame.evaluate(SCRIPT, question)
            candidates = [v for a in extracted['candidates']
                          if (v := valid_native(question, a)) is not None]
            for entry in self.resources:
                if entry['frame'] != frame or entry['document'] != frame.url:
                    continue
                if entry['records'] is None:
                    entry['records'] = []
                    if self.used_bytes >= 2_000_000:
                        continue
                    raw = entry['response'].body()
                    self.used_bytes += len(raw)
                    if self.used_bytes <= 2_000_000:
                        entry['records'] = parse_qti(raw)
                for record in entry['records']:
                    value = qti_answer(question, extracted['binding'], record)
                    if value is not None:
                        candidates.append(value)
            distinct = {tuple(answer) for answer in candidates}
            return list(next(iter(distinct))) if len(distinct) == 1 else None
        except Exception:
            return None
