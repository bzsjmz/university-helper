"""Critical Thinking control regressions; all browser traffic is local fixtures."""
import unittest

from app.services.course.english.forms import answer_page, read_form, verify_question
from app.services.course.english.native_answers import NativeAnswers
from welearn_et_controls import fixture, SAVE, ANGULAR, native_only


def matching_html(attributes='g', options=('skills for living',)):
    choices = ''.join(f'''<li option="{value}" onclick="document.querySelector('et-blank .blank').textContent=this.getAttribute('option');this.closest('.optionsPicker').hidden=true">{value}</li>''' for value in options)
    return f'''<et-item><p>Choose a phrase <et-blank noinput {attributes}><span class="blank" tabindex="101"
      onclick="document.querySelector('.optionsPicker').hidden=false" style="display:inline-block;width:150px;height:25px"></span>
      <span class="key" hidden>Skills for living</span></et-blank></p>{SAVE}</et-item>
      <div class="optionsPicker" hidden>{choices}</div>'''


class CriticalControlsTest(unittest.TestCase):
    def test_inline_circle_choices_use_bound_keys_and_real_clicks(self):
        html = '''<et-item><div>Choose a reason <et-choice circle id="q"><div class="wrapper">
          <span ng-click="choice.select(0)" onclick="this.parentElement.querySelectorAll('span').forEach(e=>e.classList.remove('chosen'));this.classList.add('chosen')">good jobs</span>/
          <span ng-click="choice.select(1)" onclick="this.parentElement.querySelectorAll('span').forEach(e=>e.classList.remove('chosen'));this.classList.add('chosen')">good grades</span>
          </div></et-choice></div>''' + SAVE + '</et-item>' + ANGULAR + '''<script>
          document.querySelector('#q').nativeKey={hasKey:true,key:[1],optionLength:2,multiple:false};
          </script>'''
        with fixture(html) as page:
            snapshot = read_form(page.main_frame)
            self.assertEqual(snapshot['unsupported'], 0)
            self.assertEqual(len(snapshot['questions']), 1)
            self.assertEqual(snapshot['questions'][0]['options'], ['good jobs', 'good grades'])
            result = answer_page(page, native_only, native=NativeAnswers())
            self.assertEqual(result['counts']['native_answers'], 1)
            self.assertEqual(result['counts']['filled'], 1)
            self.assertEqual(result['counts']['saved'], 1)
            self.assertEqual(page.locator('span.chosen').inner_text(), 'good grades')

    def test_malformed_inline_choice_events_remain_unsupported(self):
        with fixture('''<et-item><div>Unknown <et-choice circle><div class="wrapper">
          <span ng-click="unrelated(0)">one</span><span ng-click="choice.select(0)">two</span>
          </div></et-choice></div>''' + SAVE + '</et-item>') as page:
            result = answer_page(page, native_only, native=NativeAnswers())
            self.assertGreater(result['counts']['unsupported'], 0)
            self.assertIsNone(page.evaluate('window.saved'))

    def test_declared_case_insensitive_matching_accepts_unique_lowercase_option(self):
        for flag in ('g', 'i'):
            with self.subTest(flag=flag), fixture(matching_html(flag)) as page:
                result = answer_page(page, native_only, native=NativeAnswers())
                self.assertEqual(result['counts']['filled'], 1)
                self.assertEqual(result['counts']['saved'], 1)
                self.assertEqual(page.locator('et-blank .blank').inner_text(), 'skills for living')
                question = read_form(page.main_frame)['questions'][0]
                self.assertTrue(verify_question(page.main_frame, question, ['Skills for living']))

    def test_unflagged_matching_stays_case_sensitive(self):
        with fixture(matching_html('')) as page:
            result = answer_page(page, native_only, native=NativeAnswers())
            self.assertEqual(result['counts']['filled'], 0)
            self.assertIsNone(page.evaluate('window.saved'))

    def test_ambiguous_case_fold_does_not_choose_an_option(self):
        with fixture(matching_html('g', ('skills for living', 'SKILLS FOR LIVING'))) as page:
            result = answer_page(page, native_only, native=NativeAnswers())
            self.assertEqual(result['counts']['filled'], 0)
            self.assertIsNone(page.evaluate('window.saved'))

    def test_case_fold_does_not_apply_to_normal_text_input(self):
        with fixture('<et-item><p>Type <et-blank g><input type="text" value="skills for living"></et-blank></p></et-item>') as page:
            question = read_form(page.main_frame)['questions'][0]
            self.assertFalse(verify_question(page.main_frame, question, ['Skills for living']))


if __name__ == '__main__':
    unittest.main(verbosity=2)
