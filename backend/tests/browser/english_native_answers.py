"""Real browser DOM/XML -> private solver -> native save, with no paid provider."""
import json
from contextlib import contextmanager
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from playwright.sync_api import sync_playwright
from app.services.course.english.answering import EnglishSolver
from app.services.course.english.forms import answer_page
from app.services.course.english.native_answers import NativeAnswers
from app.services.course.chaoxing.answer_cache import CacheDAO


class NativeBrowserTest(unittest.TestCase):
    def run_page(self, html, *, xml=None, iframe=False, submit=False):
        with tempfile.TemporaryDirectory() as temporary, sync_playwright() as p:
            browser = p.chromium.launch(headless=True, chromium_sandbox=True)
            try:
                context = browser.new_context(service_workers='block')
                native = NativeAnswers()
                context.on('response', native.observe)
                page = context.new_page()
                page.set_default_timeout(5000)
                def route(request):
                    url = request.request.url
                    if url.endswith('item.xml'):
                        request.fulfill(status=200, content_type='application/xml',
                            headers={'content-length': str(len(xml))}, body=xml)
                    elif iframe and url.endswith('current.html'):
                        request.fulfill(status=200, content_type='text/html; charset=utf-8', body=
                            '<iframe src="https://centercourseware.sflep.com/lesson.html" style="width:800px;height:600px"></iframe>')
                    elif url.endswith('hidden.html'):
                        request.fulfill(status=200, content_type='text/html', body=
                            '<div class="question"><h3>Not selected</h3><input type="password"></div>')
                    else:
                        request.fulfill(status=200, content_type='text/html; charset=utf-8', body=html)
                page.route('**/*', route)
                page.goto('https://centercourseware.sflep.com/current.html', wait_until='load')
                if xml:
                    target = page.frame(url='https://centercourseware.sflep.com/lesson.html') if iframe else page
                    target.wait_for_function('window.xmlLoaded === true')
                cache_path = str(Path(temporary) / 'cache.json')
                with patch('app.services.course.english.answering.CacheDAO', lambda: CacheDAO(cache_path)), \
                        patch('app.services.course.english.answering.get_provider',
                              side_effect=AssertionError('Native hit must never initialize paid AI')):
                    solver = EnglishSolver('welearn', 'fixture', True)
                    result = answer_page(page, solver.solve, native=native, submit=submit)
                self.assertEqual(result['status'], 'finished', result)
                self.assertEqual(result['counts']['submitted' if submit else 'saved'], 1, result)
                self.assertEqual(result['counts']['ai_answers'], 0, result)
                self.assertEqual(result['counts']['saved' if submit else 'submitted'], 0, result)
                return result
            finally:
                browser.close()

    def test_welearn_dialogue_blanks_submit_with_native_confirmation_without_playing_video(self):
        rows = ''.join(f'<p>Speaker {i}: Complete this sentence <span data-controltype="filling"><input data-itemtype="input"><span data-itemtype="result" hidden>word{i}</span></span> now.</p>' for i in range(5))
        result = self.run_page('<html><body><video></video>' + rows +
            '<div data-controltype="submit" onclick="document.querySelector(\'.cmd_submit\').style.display=\'block\'">Submit</div>'
            '<button class="cmd_submit" style="display:none" onclick="document.body.setAttribute(\'data-submitted\',\'true\')">Submit</button>'
            '</body></html>', submit=True)
        self.assertEqual(result['counts']['questions'], 5)
        self.assertEqual(result['counts']['native_answers'], 5)
        self.assertEqual(result['counts']['filled'], 5)

    def test_uai_course_card_catalog_and_native_save_in_browser(self):
        from app.services.course.english import uai
        @contextmanager
        def browser_fixture(credential, platform):
            self.assertEqual(platform, 'unipus_ai')
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True, chromium_sandbox=True)
                try:
                    context = browser.new_context()
                    def route(r):
                        if 'uai.unipus.cn' in r.request.url:
                            body = '<button onclick="document.querySelector(\'a\').style.display=\'block\'">Class English</button><a style="display:none" href="https://ucontent.unipus.cn/_explorationpc_default/pc.html">AI English</a>'
                        else:
                            body = '<div data-group-id="leaf1" onclick="document.querySelector(\'.question\').hidden=false">Lesson 1</div><div class="question" hidden><h3 class="stem">Fill the word</h3><input type="text"></div><button onclick="document.querySelector(\'.result\').textContent=\'保存成功\'">保存</button><div class="result"></div>'
                        r.fulfill(content_type='text/html; charset=utf-8', body=body)
                    context.route('**/*', route)
                    yield context, context.new_page()
                finally: browser.close()
        with patch('app.services.course.english.clients.unipus_browser', browser_fixture), \
             patch('app.services.course.english.uai.courses', return_value=[{'id':'ai-1','name':'AI English','group_name':'Class English'}]):
            catalog = uai.run('private-state', 'ai-1')['items']
            self.assertEqual(catalog, [{'id':'0','name':'Lesson 1','progress':''}])
            result = uai.run('private-state', 'ai-1', item='0', expected_name='Lesson 1', seconds=30,
                             solve=lambda q: {'answer':['word'], 'source':'cache'})
            self.assertEqual(result['status'], 'finished', result)
            self.assertEqual(result['counts']['saved'], 1)

    def test_et_blank_contenteditable_uses_own_hidden_key_and_native_input_events(self):
        html = '<et-item><video></video>' + ''.join(
            f'<p>Sentence {i} <et-blank id="q{i}"><span class="wrapper"><span class="blank" style="display:inline-block;min-width:80px;min-height:20px" contenteditable="" ng-model="blank.value"></span><span class="key" style="display:none">word{i}</span></span></et-blank>.</p>'
            for i in range(5)) + '<button onclick="document.querySelector(\'.result\').textContent=\'保存成功\'">保存</button><div class="result"></div></et-item>'
        result = self.run_page(html)
        self.assertEqual(result['counts']['questions'], 5)
        self.assertEqual(result['counts']['native_answers'], 5)
        self.assertEqual(result['counts']['filled'], 5)

    def test_native_et_tab_next_reaches_editable_questions_without_leaving_lesson(self):
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True, chromium_sandbox=True)
            try:
                page = browser.new_page()
                page.set_content('''<et-item><et-tab><ul class="headerTab"><li class="active">Words</li><li>Exercise</li></ul></et-tab>
                  <et-button class="nextTab-btn" action="item.switchTab(0)"><button onclick="document.querySelectorAll('li')[0].className='';document.querySelectorAll('li')[1].className='active';document.querySelector('#exercise').hidden=false;this.parentElement.hidden=true">Next</button></et-button>
                  <div id="exercise" hidden><p>Complete <et-blank><span class="blank" style="display:inline-block;min-width:80px;min-height:20px" contenteditable=""></span><span class="key" hidden>correct</span></et-blank>.</p>
                  <button onclick="document.querySelector('.result').textContent='保存成功'">保存</button><div class="result"></div></div></et-item>''')
                solve = lambda q: {'answer': q.get('native_answer'), 'source': 'native'}
                first = answer_page(page, solve, native=NativeAnswers(), allow_tabs=True)
                self.assertEqual(first['status'], 'advanced')
                second = answer_page(page, solve, native=NativeAnswers(), allow_tabs=True)
                self.assertEqual(second['status'], 'finished', second)
                self.assertEqual(second['counts']['filled'], 1)
                self.assertEqual(page.url, 'about:blank')
            finally:
                browser.close()

    def test_welearn_custom_option_rows_use_native_answer_and_verify_selection(self):
        result = self.run_page('''<html><body><div data-controltype="choice"><h3 class="stem">Pick blue</h3>
          <ul data-itemtype="options"><li onclick="this.classList.add('selected')">red</li>
          <li data-solution="true" onclick="this.classList.add('selected')">blue</li></ul></div>
          <button onclick="document.querySelector('.result').textContent='保存成功'">保存</button><div class="result"></div></body></html>''')
        self.assertEqual(result['counts']['native_answers'], 1)

    def test_dom_solutions_save_without_cache_or_ai(self):
        result = self.run_page('''<html><body>
          <div data-controltype="choice" data-solution="blue"><h3 class="stem">Choose blue.</h3>
          <label><input type="radio" name="R" value="red">A. red</label>
          <label><input type="radio" name="R" value="blue">B. blue</label></div>
          <div data-controltype="filling"><h3 class="stem">Fill two.</h3>
          <input type="text" data-solution="one"><input type="text" data-solution="two"></div>
          <button onclick="document.querySelector('.result').textContent='保存成功'">保存</button>
          <div class="result"></div></body></html>''')
        self.assertEqual(result['counts']['native_answers'], 2)

    def test_visible_iframe_xml_uses_item_response_and_reordered_choice_ids(self):
        xml = b'''<assessmentItem identifier="Q1"><responseDeclaration identifier="R">
          <correctResponse><value>blue-id</value></correctResponse></responseDeclaration>
          <itemBody><choiceInteraction responseIdentifier="R"><prompt>Choose blue.</prompt>
          <simpleChoice identifier="red-id">red</simpleChoice><simpleChoice identifier="blue-id">blue</simpleChoice>
          </choiceInteraction></itemBody></assessmentItem>'''
        result = self.run_page('''<html><body>
          <div class="question" id="Q1"><h3 class="stem">Choose blue.</h3>
          <label><input type="radio" name="R" value="blue-id">A. blue</label>
          <label><input type="radio" name="R" value="red-id">B. red</label></div>
          <button onclick="document.querySelector('.result').textContent='保存成功'">保存</button>
          <div class="result"></div>
          <script>fetch('/item.xml').then(r => r.text()).then(() => window.xmlLoaded = true)</script>
          </body></html>''', xml=xml, iframe=True)
        self.assertEqual(result['counts']['native_answers'], 1)

    def test_hidden_preloaded_frame_is_not_answered_or_charged(self):
        result = self.run_page('''<html><body>
          <iframe src="https://centercourseware.sflep.com/hidden.html" style="display:none"></iframe>
          <div class="question"><h3 class="stem">Selected</h3><input type="text" data-solution="word"></div>
          <button onclick="document.querySelector('.result').textContent='保存成功'">保存</button>
          <div class="result"></div></body></html>''')
        self.assertEqual(result['counts']['questions'], 1)
        self.assertEqual(result['counts']['native_answers'], 1)


if __name__ == '__main__':
    unittest.main(verbosity=2)
