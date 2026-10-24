import { readFileSync } from 'node:fs'
import { afterEach, expect, test } from 'vitest'

const script = readFileSync(process.env.ENGLISH_FORM_SCRIPT || new URL('../../../backend/app/services/course/english/question_form.js', import.meta.url), 'utf8')
const nativeScript = readFileSync(process.env.ENGLISH_NATIVE_SCRIPT || new URL('../../../backend/app/services/course/english/native_answers.js', import.meta.url), 'utf8')
const originalRects = HTMLElement.prototype.getClientRects
HTMLElement.prototype.getClientRects = function () { return this.hidden ? [] : [{}] }
const extract = () => window.eval(`(${script})`)()
const native = q => window.eval(`(${nativeScript})`)(q).candidates
afterEach(() => { document.body.innerHTML = '' })

// Synthetic DOM fixtures; no real platform data, accounts or network.
test('mixed radio/checkbox/blank/dropdown forms preserve DOM and option order', () => {
  document.body.innerHTML = `
    <nav><input type="search"></nav>
    <div class="passage">A short reading passage.</div>
    <div class="question"><h3 class="stem">Pick a word</h3><label><input type="radio" name="a">A. cat</label><label><input type="radio" name="a">B. dog</label></div>
    <div class="question"><h3 class="stem">Pick two</h3><label><input type="checkbox" name="b">A. red</label><label><input type="checkbox" name="b">B. blue</label></div>
    <div class="question"><h3 class="stem">Fill two blanks</h3><input type="text"><input type="text"></div>
    <div class="question"><h3 class="stem">Choose a tense</h3><select><option value="">Select</option><option value="p">past</option><option value="f">future</option></select></div>
    <div class="question"><h3 class="stem">Translate</h3><textarea></textarea></div>`
  const result = extract()
  expect(result.unsupported).toBe(0)
  expect(result.questions.map(q => q.type)).toEqual(['single','multiple','completion','single','shortanswer'])
  expect(result.questions[0].options).toEqual(['cat','dog'])
  expect(result.questions[2].count).toBe(2)
  expect(result.questions[3].values).toEqual(['p','f'])
  expect(result.questions[0].context).toContain('A short reading passage.')
  document.querySelector('input[type=text]').value = 'filled'
  expect(extract()).toEqual(result)
})

test('unrecognized controls, missing listening text and verification stay unsupported', () => {
  document.body.innerHTML = '<div class="question"><h3>Listen</h3><audio></audio><input type="text"></div><input type="password">'
  const result = extract()
  expect(result.questions).toHaveLength(0)
  expect(result.unsupported).toBe(2)
  document.body.innerHTML = '<div>请输入验证码</div><div class="question"><input type="text"></div>'
  expect(extract().blocked).toBe(true)
})

test('separate radio groups in one unknown container do not become a guessed question', () => {
  document.body.innerHTML = '<div class="question"><h3>Unrecognized combined widget</h3><label><input type="radio" name="one">A</label><label><input type="radio" name="two">B</label></div>'
  expect(extract().questions).toHaveLength(0)
  expect(extract().unsupported).toBe(2)
})

test('courseware solutions are bound to their question and explicit option values after reordering', () => {
  document.body.innerHTML = `
    <div data-controltype="choice" data-solution="choice-red"><h3 class="stem">Pick red</h3>
      <label><input type="radio" name="one" value="choice-blue">A. blue</label>
      <label><input type="radio" name="one" value="choice-red">B. red</label></div>
    <div data-controltype="filling"><h3 class="stem">Fill two</h3>
      <input type="text" data-solution="one"><input type="text" data-solution="two"></div>
    <div data-solution="unrelated">Not a question</div>`
  const form = extract()
  expect(form.unsupported).toBe(0)
  expect(native(form.questions[0])).toEqual([[1]])
  expect(native(form.questions[1])).toEqual([['one', 'two']])
  document.querySelector('input[type=text]').value = 'previous student answer'
  expect(native(extract().questions[1])).toEqual([['one', 'two']])
})

test('empty solution markers identify only the associated correct option, never an ordinal letter', () => {
  document.body.innerHTML = `<div class="question"><h3 class="stem">Pick one</h3>
    <label><input type="radio" name="one" value="x">A. first</label>
    <label data-solution><input type="radio" name="one" value="y">B. second</label></div>`
  let q = extract().questions[0]
  expect(native(q)).toEqual([[1]])
  document.querySelector('.question').setAttribute('data-solution', 'A')
  q = extract().questions[0]
  expect(native(q)).toEqual([]) // Unmapped A is not interpreted as position zero.
})

test('multi-blank answers need explicit bindings and unknown custom widgets block the page', () => {
  document.body.innerHTML = `<div class="question"><h3 class="stem">Fill two</h3>
    <input type="text" data-solution="one"><input type="text"></div>`
  expect(native(extract().questions[0])).toEqual([])
  document.body.insertAdjacentHTML('beforeend', '<et-choice>Custom choices with no native controls</et-choice>')
  expect(extract().unsupported).toBeGreaterThan(0)
})

test('alternative answer strings and previous student input are not treated as a native solution', () => {
  document.body.innerHTML = '<div class="question"><h3 class="stem">Fill</h3><input type="text" value="old student response" answer="untrusted"></div>'
  expect(native(extract().questions[0])).toEqual([])
  const input = document.querySelector('input')
  for (const solution of ['one/two', '["one","two"]', '(Open.)', 'Answers may vary.', 'one|two']) {
    input.setAttribute('data-solution', solution)
    expect(native(extract().questions[0])).toEqual([])
  }
})

test('stale control markers cannot bind hidden fields and completed widgets are not mistaken for unknown controls', () => {
  document.body.innerHTML = `<input type="hidden" data-uh-control="0" data-solution="wrong">
    <div data-controltype="filling"><h3 class="stem">Completed</h3><input type="text" disabled></div>
    <div data-controltype="filling"><h3 class="stem">Current</h3><input type="text" data-solution="correct"></div>`
  const form = extract()
  expect(form.unsupported).toBe(0)
  expect(form.questions).toHaveLength(1)
  expect(native(form.questions[0])).toEqual([['correct'], ['correct']])
  expect(document.querySelector('input[type=hidden]').hasAttribute('data-uh-control')).toBe(false)
})

// Restore the global DOM shim when this module is unloaded by the runner.
import { afterAll } from 'vitest'
afterAll(() => { HTMLElement.prototype.getClientRects = originalRects })

test('WE listening dialogue blanks use their bound hidden result without leaking it to the question', () => {
  document.body.innerHTML = `<video></video><div class="transcript" hidden>Not currently available</div>
    <p>Dad: Wow, 1 <span data-controltype="filling"><input data-itemtype="input"><span hidden data-itemtype="result">all packed</span></span> already?</p>
    <p>Daughter: Seat belt, 2 <span data-controltype="filling"><input data-itemtype="input"><span hidden data-itemtype="result">I'll be</span></span> safe.</p>`
  expect(document.getElementsByTagName('video')).toHaveLength(1)
  expect(document.querySelector('.transcript').hidden).toBe(true)
  const form = extract()
  expect(form.unsupported).toBe(0)
  expect(form.questions).toHaveLength(2)
  expect(form.questions[0].title).toContain('already?')
  expect(form.questions[0].title).not.toContain('all packed')
  expect(form.questions[0].requires_native).toBe(true)
  expect(native(form.questions[0])).toEqual([['all packed']])
  expect(native(form.questions[1])).toEqual([["I'll be"]])
  document.querySelector('[data-itemtype=result]').textContent = '(Answers may vary.)'
  expect(native(extract().questions[0])).toEqual([])
})
