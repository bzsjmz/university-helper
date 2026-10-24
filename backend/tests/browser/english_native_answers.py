"""Real browser DOM/XML -> private solver -> native save, with no paid provider."""
import json
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
    def run_page(self, html, *, xml=None):
        with tempfile.TemporaryDirectory() as temporary, sync_playwright() as p:
            browser = p.chromium.launch(headless=True, chromium_sandbox=True)
            try:
                context = browser.new_context(service_workers='block')
                native = NativeAnswers()
                context.on('response', native.observe)
                page = context.new_page()
                page.set_default_timeout(5000)
                def route(request):
                    if request.request.url.endswith('item.xml'):
                        request.fulfill(status=200, content_type='application/xml',
                            headers={'content-length': str(len(xml))}, body=xml)
                    else:
                        request.fulfill(status=200, content_type='text/html; charset=utf-8', body=html)
                page.route('**/*', route)
                page.goto('https://centercourseware.sflep.com/current.html', wait_until='load')
                if xml:
                    page.wait_for_function('window.xmlLoaded === true')
                cache_path = str(Path(temporary) / 'cache.json')
                with patch('app.services.course.english.answering.CacheDAO', lambda: CacheDAO(cache_path)), \
                        patch('app.services.course.english.answering.get_provider',
                              side_effect=AssertionError('Native hit must never initialize paid AI')):
                    solver = EnglishSolver('welearn', 'fixture', True)
                    result = answer_page(page, solver.solve, native=native)
                self.assertEqual(result['status'], 'finished', result)
                self.assertEqual(result['counts']['saved'], 1, result)
                self.assertEqual(result['counts']['ai_answers'], 0, result)
                self.assertEqual(result['counts']['submitted'], 0, result)
                return result
            finally:
                browser.close()

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

    def test_loaded_xml_uses_item_response_and_reordered_choice_ids(self):
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
          </body></html>''', xml=xml)
        self.assertEqual(result['counts']['native_answers'], 1)


if __name__ == '__main__':
    unittest.main(verbosity=2)
