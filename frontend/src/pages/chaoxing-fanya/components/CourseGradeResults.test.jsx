import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, expect, test } from 'vitest'
import CourseGradeResults from './CourseGradeResults'
import { managedTaskPayload } from '../managedTaskPayload'

afterEach(cleanup)

test('completed task can still have a verified grade below its selected target', () => {
  render(<CourseGradeResults progress={{ grade_results: [{ course_name: 'Example', state: 'verified',
    score: 45, passing_score: 60, meets_target: false, visits_score: 2.83, visits_weight: 20, full_mark_count: 120 }] }} />)
  expect(screen.getByText('尚未达到参考线')).toBeInTheDocument()
  expect(screen.getByText(/平台总分 45/)).toBeInTheDocument()
  expect(screen.getByText(/章节次数分 2.83\/20.*本课满分门槛 120 次/)).toBeInTheDocument()
})

test('unknown platform grade does not become a guessed zero or a pass', () => {
  render(<CourseGradeResults progress={{ grade_results: [{ course_name: 'Example', state: 'unverified' }] }} />)
  expect(screen.getByText(/平台成绩未核验，不能据任务点完成判定及格/)).toBeInTheDocument()
  expect(screen.queryByText(/平台总分/)).not.toBeInTheDocument()
})

test('browser visit count stays separate from verified platform score', () => {
  render(<CourseGradeResults progress={{ visit_review: { state: 'waiting_grade', visited_pages: 7,
    visits_score: 2.83, visits_weight: 20 } }} />)
  expect(screen.getByRole('status')).toHaveTextContent(/等待平台统计更新：\s*已访问 7 次，平台次数分 2.83\/20/)
})

test('managed and batch payloads enable new verification but never expand assessment-only scopes', () => {
  const config = { tikuProvider: ['LocalCache', 'AI'], correctOptions: '对', wrongOptions: '错' }
  expect(managedTaskPayload(config, ['a'], {})).toMatchObject({ verify_grades: true,
    review_chapter_visits: true, passing_score: 60 })
  expect(managedTaskPayload({ ...config, learnChapters: false }, ['a'], {})).toMatchObject({
    review_chapter_visits: false, learn_chapters: false })
  expect(managedTaskPayload({ ...config, reviewChapterVisits: false, passingScore: 70 }, ['a'], { a: 'p2' }))
    .toMatchObject({ review_chapter_visits: false, passing_score: 70, chapter_limits: { a: 'p2' } })
})
