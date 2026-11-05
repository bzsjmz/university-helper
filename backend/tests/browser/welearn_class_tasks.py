"""Offline native class-test navigation, preflight, filling and one submit."""
import time
import unittest
from unittest.mock import Mock

from playwright.sync_api import sync_playwright
from app.services.course.english.welearn_class_tasks import answer_test


def question(index=1, kind='radio'):
    return f'''<div class="itemDiv"><div class="test_hov"><div class="stem">Choose a word {index}</div>
    <span id="question_{index}"></span><label><input type="{kind}" name="rd{index}" id="rd{index}_1">A) Hello</label>
    <label><input type="{kind}" name="rd{index}" id="rd{index}_2">B) Goodbye</label></div></div>'''


def document(content):
    return f'''<html><body>{content}<span id="spSubmit"><a id="aSubmit" href="#" onclick="window.submits++;return false">交卷</a></span>
    <script>window.submits=0;</script></body></html>'''


class ClassTestBrowser(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.p = sync_playwright().start()
        cls.browser = cls.p.chromium.launch(headless=True, chromium_sandbox=True)

    @classmethod
    def tearDownClass(cls):
        cls.browser.close(); cls.p.stop()

    def setUp(self):
        self.context = self.browser.new_context(service_workers='block')
        self.page = self.context.new_page()
        self.page.set_default_timeout(2000)

    def tearDown(self):
        self.context.close()

    def load(self, html):
        self.page.route('**/*', lambda r: r.fulfill(status=200, content_type='text/html; charset=utf-8', body=html))
        self.page.goto('https://welearn.sflep.com/test/test.aspx?taskid=fixture&cid=fixture')

    def run_test(self, solver=None):
        return answer_test(self.page, solver or (lambda q: {'answer': [0]}), time.monotonic()+20)

    def test_choice_fill_readback_and_no_assumed_receipt(self):
        self.load(document(question()))
        result = self.run_test()
        self.assertEqual(result['counts']['filled'], 1, result)
        self.assertEqual(result['counts']['submission_attempted'], 1)
        self.assertEqual(result['counts']['submitted'], 0)
        self.assertEqual(self.page.evaluate('window.submits'), 1)
        self.assertTrue(self.page.locator('#rd1_1').is_checked())

    def test_missing_answer_never_fills_or_submits(self):
        self.load(document(question()+question(2)))
        solve = Mock(side_effect=[{'answer': [0]}, {'answer': None}])
        result = self.run_test(solve)
        self.assertEqual(result['counts']['filled'], 0)
        self.assertEqual(self.page.evaluate('window.submits'), 0)
        self.assertFalse(self.page.locator('#rd1_1').is_checked())

    def test_unsupported_in_later_part_prevents_all_ai_and_filling(self):
        self.load(document(self.parts(question(), '<div class="itemDiv"><input type="file"></div>')))
        solve = Mock(side_effect=AssertionError('Must preflight every part'))
        result = self.run_test(solve)
        solve.assert_not_called()
        self.assertGreater(result['counts']['unsupported'], 0)
        self.assertEqual(self.page.evaluate('window.submits'), 0)

    @staticmethod
    def parts(one, two):
        return f'''<ul id="ulParts"><li class="active"><a href="#" onclick="showPart(0);return false">Part 1</a></li>
        <li><a href="#" onclick="showPart(1);return false">Part 2</a></li></ul>
        <section id="part0">{one}</section><section id="part1" style="display:none">{two}</section>
        <script>function showPart(n) {{ for(let i=0;i<2;i++) {{document.querySelector('#part'+i).style.display=i===n?'block':'none';
        document.querySelectorAll('#ulParts li')[i].className=i===n?'active':'';}} }}</script>'''

    def test_all_parts_filled_and_verified_before_one_submit(self):
        self.load(document(self.parts(question(), question(2))))
        result = self.run_test()
        self.assertEqual(result['counts']['questions'], 2, result)
        self.assertEqual(result['counts']['filled'], 2, result)
        self.assertEqual(self.page.evaluate('window.submits'), 1)
        self.assertTrue(self.page.locator('#rd1_1').is_checked())
        self.assertTrue(self.page.locator('#rd2_1').is_checked())

    def test_audio_without_text_does_not_call_ai(self):
        self.load(document(question().replace('<div class="test_hov">', '<audio></audio><div class="test_hov">', 1)))
        solve = Mock()
        result = self.run_test(solve)
        self.assertEqual(result['counts']['filled'], 0)
        solve.assert_not_called()

    def test_earlier_part_answer_lost_on_switch_blocks_submission(self):
        html = document(self.parts(question(), question(2)))
        html = html.replace('function showPart(n) {', 'function showPart(n) { document.querySelectorAll("input").forEach(e=>e.checked=false);')
        self.load(html)
        result = self.run_test()
        self.assertEqual(result['counts']['submission_attempted'], 0, result)
        self.assertEqual(self.page.evaluate('window.submits'), 0)

    def test_challenge_does_not_fill_or_submit(self):
        self.load(document('<p>请输入验证码</p>'+question()))
        solve = Mock()
        result = self.run_test(solve)
        self.assertEqual(result['counts']['filled'], 0)
        solve.assert_not_called()

    def test_reading_context_and_exact_option_order(self):
        self.load(document(question().replace('<div class="test_hov">', '<div class="col-md-8">A reading passage.</div><div class="test_hov">')))
        solve = Mock(return_value={'answer': [1]})
        self.run_test(solve)
        q = solve.call_args.args[0]
        self.assertEqual(q['context'], 'A reading passage.')
        self.assertEqual(q['options'], ['Hello', 'Goodbye'])
        self.assertTrue(self.page.locator('#rd1_2').is_checked())

    def test_wrong_answer_shape_blocks_native_submit(self):
        self.load(document(question()))
        result = self.run_test(lambda q: {'answer': [99]})
        self.assertEqual(result['counts']['submission_attempted'], 0)
        self.assertEqual(self.page.evaluate('window.submits'), 0)

    def test_confirm_dialog_is_specific_and_clicked_once(self):
        self.load(document(question()).replace('window.submits++;return false', "if(confirm('确定交卷吗？')) window.submits++;return false"))
        result = self.run_test()
        self.assertEqual(result['counts']['submission_attempted'], 1)
        self.assertEqual(self.page.evaluate('window.submits'), 1)


if __name__ == '__main__':
    unittest.main()
