"""Opt-in recording experiments in offline fixtures; never contact WE Learn."""
import json
import time
import unittest

from app.services.course.english.forms import answer_page, read_form
from welearn_et_controls import fixture


def courseware(body):
    return '<style>et-recorder,et-follow-me,et-talk{display:block;min-height:28px}</style><et-item><p>Recording exercise with independent submission.</p>' + body + '''
      <button onclick="window.clicked=true;this.parentElement.setAttribute('data-submitted','true')">Submit</button>
      </et-item><script>
      window.changes=[];
      window.item={isSubmitted:false,isBrowseMode:false,
        interactionIds:[...document.querySelectorAll('et-recorder,et-follow-me,et-talk')].map(e=>e.id),
        handleStatusChange:s=>window.changes.push(JSON.parse(JSON.stringify(s)))};
      window.angular={element:e=>({controller:name=>name==='etItem'?window.item:e.nativeKey,
        injector:()=>({get:()=>({$apply:fn=>fn(),$$phase:false})})})};
      document.querySelectorAll('et-recorder').forEach(e=>e.nativeKey={
        recordingCount:0,isRecording:false,reset:false,isMobile:false,
        scriptFlag:e.hasAttribute('script'),script:e.getAttribute('script')||''});
      </script>'''


class RecordingBypassTest(unittest.TestCase):
    def answer(self, page, **options):
        return answer_page(page, lambda q: {'answer': None}, submit=True,
                           allow_reading=True, recording_bypass=True, **options)

    def test_default_and_save_only_do_not_change_recording_state(self):
        for options in ({'submit': True}, {'submit': False, 'recording_bypass': True}):
            with self.subTest(options=options), fixture(courseware('<et-recorder id="voice"></et-recorder>')) as page:
                result = answer_page(page, lambda q: self.fail('No paid AI'), allow_reading=True, **options)
                self.assertEqual(result['status'], 'needs_manual')
                self.assertEqual(page.evaluate('window.changes'), [])
                self.assertIsNone(page.evaluate('window.clicked'))

    def test_pure_ordinary_and_scored_recordings_use_native_submit(self):
        with fixture(courseware('<et-recorder id="open"></et-recorder><et-recorder id="read" script="Read precisely"></et-recorder>')) as page:
            result = self.answer(page)
            self.assertEqual(result['counts']['recordings_bypassed'], 2)
            self.assertEqual(result['counts']['submitted'], 1)
            changes = page.evaluate('window.changes')
            self.assertEqual([s['interaction']['result'] for s in changes], ['neutral', '0'])
            self.assertTrue(all(s['isDirty'] and not s['isScored'] for s in changes))
            self.assertEqual(page.evaluate("document.querySelector('#open').nativeKey.recordingCount"), 0)

    def test_follow_me_and_talk_use_restore_compatible_json(self):
        html = courseware('''<et-follow-me id="follow"><p class="sentence">One</p><p class="sentence">Two</p></et-follow-me>
          <et-talk id="talk"><flow record="1" role="A">Hello</flow><flow record="1" role="B">Hi</flow></et-talk>''') + '''<script>
          document.querySelector('#follow').nativeKey={recordingCount:0,isRecording:false,sentenceCount:2};
          document.querySelector('#talk').nativeKey={recordingCount:0,isTalking:false,rolesArray:['A','B'],chosenRole:NaN,
            flowSteps:[{recorder:true,role:0,filename:''},{recorder:true,role:1,filename:''}]};
          </script>'''
        with fixture(html) as page:
            self.assertEqual(read_form(page.main_frame)['recordings'], 2)
            result = self.answer(page)
            self.assertEqual(result['counts']['recordings_bypassed'], 2)
            follow, talk = [s['interaction'] for s in page.evaluate('window.changes')]
            self.assertEqual(json.loads(follow['result']), [[0, 0], [0, 0]])
            self.assertEqual(len(follow['learner_response'].split('[,]')), 2)
            self.assertEqual(json.loads(talk['result']), {'attempts': [1, 0], 'allScores': [0, 0], 'htmls': ['', '']})
            self.assertTrue(talk['learner_response'].startswith('ROLE_0[,]'))

    def test_unknown_id_rejects_whole_plan_without_partial_changes(self):
        with fixture(courseware('<et-recorder id="one"></et-recorder><et-recorder id="two"></et-recorder>') +
                     '<script>window.item.interactionIds=["one"];</script>') as page:
            result = self.answer(page)
            self.assertEqual(result['status'], 'needs_manual')
            self.assertEqual(page.evaluate('window.changes'), [])
            self.assertIsNone(page.evaluate('window.clicked'))

    def test_unknown_text_control_blocks_recording_bypass(self):
        with fixture(courseware('<et-recorder id="voice"></et-recorder><et-matching>Unsupported</et-matching>')) as page:
            result = self.answer(page)
            self.assertGreater(result['counts']['unsupported'], 0)
            self.assertEqual(page.evaluate('window.changes'), [])

    def test_missing_text_answer_blocks_recording_bypass(self):
        with fixture(courseware('<et-recorder id="voice"></et-recorder><et-blank><input type="text"></et-blank>')) as page:
            result = self.answer(page)
            self.assertEqual(result['counts']['missing'], 1)
            self.assertEqual(page.evaluate('window.changes'), [])

    def test_complete_text_is_verified_before_mixed_submission(self):
        html = courseware('<p>Say <et-blank><input type="text"></et-blank></p><et-recorder id="voice"></et-recorder>')
        with fixture(html) as page:
            result = answer_page(page, lambda q: {'answer': ['hello'], 'source': 'native'},
                                 submit=True, allow_reading=True, recording_bypass=True)
            self.assertEqual(result['counts']['filled'], 1)
            self.assertEqual(result['counts']['recordings_bypassed'], 1)
            self.assertEqual(result['counts']['submitted'], 1)
            self.assertEqual(page.locator('input').input_value(), 'hello')

    def test_existing_recording_is_preserved_when_other_recording_is_pending(self):
        html = courseware('<et-recorder id="done"></et-recorder><et-recorder id="pending"></et-recorder>') + '''<script>
          document.querySelector('#done').nativeKey.recordingCount=2;
          </script>'''
        with fixture(html) as page:
            result = self.answer(page)
            self.assertEqual(result['counts']['recordings_bypassed'], 1)
            self.assertEqual([s['id'] for s in page.evaluate('window.changes')], ['pending'])
            self.assertEqual(page.evaluate("document.querySelector('#done').nativeKey.recordingCount"), 2)

    def test_expired_budget_does_not_change_recording_or_click_submit(self):
        with fixture(courseware('<et-recorder id="voice"></et-recorder>')) as page:
            result = self.answer(page, deadline=time.monotonic() - 1)
            self.assertEqual(result['status'], 'needs_manual')
            self.assertEqual(page.evaluate('window.changes'), [])
            self.assertIsNone(page.evaluate('window.clicked'))

    def test_existing_talk_recording_uses_native_submission_without_bypass(self):
        html = courseware('<et-talk id="talk"></et-talk>') + '''<script>
          document.querySelector('#talk').nativeKey={recordingCount:1,isTalking:false};
          </script>'''
        with fixture(html) as page:
            self.assertEqual(read_form(page.main_frame)['recordings'], 0)
            result = self.answer(page)
            self.assertEqual(result['counts']['submitted'], 1)
            self.assertEqual(page.evaluate('window.changes'), [])


if __name__ == '__main__':
    unittest.main(verbosity=2)
