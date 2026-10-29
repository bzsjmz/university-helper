"""Native ET widget regressions in an offline Chromium sandbox."""
from contextlib import contextmanager
import time
import unittest

from playwright.sync_api import sync_playwright

from app.services.course.english.forms import answer_page, read_form
from app.services.course.english.native_answers import NativeAnswers
from app.services.course.english.welearn_page import wait_content


ANGULAR = '''<script>window.angular={element:e=>({controller:name=>e.nativeKey})};</script>'''
SAVE = '''<button onclick="window.saved=(window.saved||0)+1;document.querySelector('.result').textContent='保存成功'">保存</button><div class="result"></div>'''


def native_only(question):
    return {'answer': question.get('native_answer'), 'source': 'native'}


@contextmanager
def fixture(html):
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True, chromium_sandbox=True)
        try:
            context = browser.new_context(service_workers='block')
            context.route('**/*', lambda route: route.fulfill(content_type='text/html; charset=utf-8', body=html))
            page = context.new_page()
            page.set_default_timeout(3000)
            page.goto('https://centercourseware.sflep.com/fixture.html')
            yield page
        finally:
            browser.close()


class WelearnControlsTest(unittest.TestCase):
    def test_reading_with_hidden_player_controls_waits_for_native_commit(self):
        with fixture('''<et-item notscored><p>A reading page with enough text and a native delayed save.</p>
          <select hidden><option>audio speed</option></select></et-item>
          <script>setTimeout(()=>window.nativeCommit=true,3000)</script>''') as page:
            started = time.monotonic()
            frames = wait_content(page, started + 15)
            self.assertGreaterEqual(time.monotonic() - started, 5)
            result = answer_page(page, lambda q: self.fail('Reading cannot request AI'), frames=frames, allow_reading=True)
            self.assertEqual(result['status'], 'visited')
            self.assertTrue(page.evaluate('window.nativeCommit'))

    def test_unknown_loaded_content_also_stays_five_seconds(self):
        with fixture('<et-item><p>An unsupported matching task is still loaded content.</p><et-matching>unknown</et-matching></et-item>') as page:
            started = time.monotonic()
            frames = wait_content(page, started + 15)
            self.assertGreaterEqual(time.monotonic() - started, 5)
            result = answer_page(page, lambda q: self.fail('Unknown control cannot request AI'), frames=frames, allow_reading=True)
            self.assertEqual(result['status'], 'needs_manual')
            self.assertEqual(result['counts']['unsupported'], 1)

    def test_true_false_spans_use_native_key_and_real_clicks(self):
        with fixture('''<et-item><et-tof id="q"><div class="wrapper"><span class="controls">
          <span ng-click="tof.chose('t')" onclick="this.classList.add('chosen');window.clicked='t'">T</span>
          <span ng-click="tof.chose('f')" onclick="this.classList.add('chosen');window.clicked='f'">F</span>
          </span><div ng-transclude>A test statement.</div></div></et-tof>''' + SAVE + '</et-item>' + ANGULAR + '''
          <script>document.querySelector('#q').nativeKey={hasKey:true,key:['f'],labels:{t:'T',f:'F'},value:['t']};</script>''') as page:
            result = answer_page(page, native_only, native=NativeAnswers())
            self.assertEqual(result['counts']['unsupported'], 0)
            self.assertEqual(result['counts']['native_answers'], 1)
            self.assertEqual(result['counts']['saved'], 1)
            self.assertEqual(page.evaluate('window.clicked'), 'f')

    def test_et_choice_reads_authored_key_after_attribute_removed(self):
        with fixture('''<et-item><et-choice id="q"><div class="wrapper"><span>Choose blue</span><ol>
          <li onclick="this.classList.add('chosen')">red</li><li onclick="this.classList.add('chosen')">blue</li>
          </ol></div></et-choice>''' + SAVE + '</et-item>' + ANGULAR + '''
          <script>document.querySelector('#q').nativeKey={hasKey:true,key:[1],optionLength:2,multiple:false,value:[0]};</script>''') as page:
            result = answer_page(page, native_only, native=NativeAnswers())
            self.assertEqual(result['counts']['native_answers'], 1)
            self.assertEqual(result['counts']['saved'], 1)
            self.assertEqual(page.locator('li.chosen').inner_text(), 'blue')

    def test_bound_blank_alternatives_allow_authored_punctuation(self):
        with fixture('''<et-item><p>Complete the phrase <et-blank id="q"><input type="text"><span class="key" hidden>on/off; switch, now</span></et-blank></p>'''
                     + SAVE + '</et-item>' + ANGULAR + '''<script>
          document.querySelector('#q').nativeKey={hasKey:true,key:['on/off; switch, now','another accepted form'],value:'previous student response'};
          </script>''') as page:
            result = answer_page(page, native_only, native=NativeAnswers())
            self.assertEqual(result['counts']['native_answers'], 1)
            self.assertEqual(result['counts']['saved'], 1)
            self.assertEqual(page.locator('input').input_value(), 'on/off; switch, now')

    def test_generic_solution_punctuation_is_still_rejected(self):
        with fixture('''<et-item><p>Unknown variants <et-blank><input type="text" data-solution="one/two"><span class="key" hidden>one/two</span></et-blank></p>'''
                     + SAVE + '</et-item>') as page:
            result = answer_page(page, native_only, native=NativeAnswers())
            self.assertEqual(result['counts']['missing'], 1)
            self.assertEqual(result['counts']['filled'], 0)
            self.assertIsNone(page.evaluate('window.saved'))

    def test_native_placeholder_cannot_become_a_bound_answer(self):
        with fixture('''<et-item><p>Open question <et-blank id="q"><input type="text"></et-blank></p>'''
                     + SAVE + '</et-item>' + ANGULAR + '''<script>
          document.querySelector('#q').nativeKey={hasKey:true,key:['Answers may vary.'],value:'learner response'};
          </script>''') as page:
            result = answer_page(page, native_only, native=NativeAnswers())
            self.assertEqual(result['counts']['missing'], 1)
            self.assertEqual(result['counts']['filled'], 0)

    def test_point_matching_uses_only_the_visible_native_picker(self):
        with fixture('''<et-item><p>Match the description <et-blank noinput><span class="wrapper">
          <span class="blank" tabindex="101" onclick="document.querySelector('.optionsPicker').hidden=false" style="display:inline-block;width:80px;height:25px"></span>
          <span class="key" hidden>brave</span></span></et-blank></p>''' + SAVE + '''</et-item>
          <div class="optionsPicker" hidden><ul><li option="calm" onclick="window.wrong=true">calm</li>
          <li option="brave" onclick="document.querySelector('et-blank .blank').textContent='brave';this.closest('.optionsPicker').hidden=true;window.nativePick=true">brave</li></ul></div>''') as page:
            started = time.monotonic()
            frames = wait_content(page, started + 15)
            self.assertTrue(frames)
            self.assertLess(time.monotonic() - started, 10)
            result = answer_page(page, native_only, native=NativeAnswers(), frames=frames)
            self.assertEqual(result['counts']['unsupported'], 0)
            self.assertEqual(result['counts']['filled'], 1)
            self.assertEqual(result['counts']['saved'], 1)
            self.assertTrue(page.evaluate('window.nativePick'))
            self.assertIsNone(page.evaluate('window.wrong'))

    def test_ambiguous_picker_never_selects_or_submits(self):
        with fixture('''<et-item><p>Match <et-blank noinput><span class="blank" tabindex="101" onclick="document.querySelector('.optionsPicker').hidden=false" style="display:inline-block;width:80px;height:25px"></span><span class="key" hidden>same</span></et-blank></p>'''
                     + SAVE + '''</et-item><div class="optionsPicker" hidden><li option="same">same</li><li option="same">same</li></div>''') as page:
            result = answer_page(page, native_only, native=NativeAnswers())
            self.assertEqual(result['status'], 'needs_manual')
            self.assertEqual(result['counts']['saved'], 0)

    def test_mixed_text_and_recordings_do_not_attempt_submission(self):
        with fixture('''<et-item><p>Fill <et-blank><input type="text"><span class="key" hidden>word</span></et-blank></p>
          <et-recorder>Record one</et-recorder><et-recorder>Record two</et-recorder>
          <button onclick="window.submitted=true">Submit</button></et-item>''') as page:
            result = answer_page(page, native_only, native=NativeAnswers(), submit=True, allow_reading=True)
            self.assertEqual(result['counts']['filled'], 1)
            self.assertEqual(result['counts']['submitted'], 0)
            self.assertEqual(result['counts']['submission_rejected'] if 'submission_rejected' in result['counts'] else 0, 0)
            self.assertIn('2 项本人录音', result['message'])
            self.assertIsNone(page.evaluate('window.submitted'))

    def test_restored_recording_does_not_block_supported_text_submission(self):
        with fixture('''<et-item><p>Fill <et-blank><input type="text"><span class="key" hidden>word</span></et-blank></p>
          <et-recorder id="voice">Existing voice</et-recorder>
          <button onclick="this.parentElement.setAttribute('data-submitted','true');window.submitted=true">Submit</button></et-item>'''
                     + ANGULAR + '''<script>document.querySelector('#voice').nativeKey={recordingCount:1,isRecording:false,reset:false};</script>''') as page:
            result = answer_page(page, native_only, native=NativeAnswers(), submit=True, allow_reading=True)
            self.assertEqual(result['counts']['submitted'], 1)
            self.assertTrue(page.evaluate('window.submitted'))

    def test_optional_recording_does_not_block_text_submission(self):
        with fixture('''<et-item><p>Fill <et-blank><input type="text"><span class="key" hidden>word</span></et-blank></p>
          <et-recorder noprogress>Optional voice</et-recorder>
          <button onclick="this.parentElement.setAttribute('data-submitted','true')">Submit</button></et-item>''') as page:
            result = answer_page(page, native_only, native=NativeAnswers(), submit=True, allow_reading=True)
            self.assertEqual(result['counts']['submitted'], 1)


if __name__ == '__main__':
    unittest.main(verbosity=2)
