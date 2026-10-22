import { readFileSync } from 'node:fs'
import { afterEach, expect, test } from 'vitest'

const script = readFileSync(process.env.ENGLISH_FORM_SCRIPT || new URL('../../../backend/app/services/course/english/question_form.js', import.meta.url), 'utf8')
const originalRects = HTMLElement.prototype.getClientRects
HTMLElement.prototype.getClientRects = function () { return this.hidden ? [] : [{}] }
const extract = () => window.eval(`(${script})`)()
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

// Restore the global DOM shim when this module is unloaded by the runner.
import { afterAll } from 'vitest'
afterAll(() => { HTMLElement.prototype.getClientRects = originalRects })
