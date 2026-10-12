import { cleanup, render, screen, within } from '@testing-library/react'
import { afterEach, describe, expect, test, vi } from 'vitest'

import TaskControlSection from './TaskControlSection'


const renderControls = (controlLoading = {}) => render(
  <TaskControlSection
    taskId="task-A"
    taskStatus={{ status: 'running' }}
    loading={false}
    isRunning
    statusText="running"
    startTask={vi.fn()}
    controlTask={vi.fn()}
    controlLoading={controlLoading}
  />
)


describe('TaskControlSection control loading', () => {
  afterEach(() => {
    cleanup()
  })


  test('disables only the in-flight action while preserving existing status gates', () => {
    renderControls({ pause: true })


    expect(screen.getByRole('button', { name: '暂停' })).toBeDisabled()
    expect(screen.getByRole('button', { name: '继续' })).toBeDisabled()
    expect(screen.getByRole('button', { name: '停止' })).toBeEnabled()
  })
})


describe('TaskControlSection task scopes and assessment results', () => {
  afterEach(cleanup)
  const show = taskStatus => render(<TaskControlSection taskId="task-A" taskStatus={taskStatus}
    statusText={taskStatus.status} startTask={vi.fn()} controlTask={vi.fn()} />)

  test('assessment-only tasks show enabled scopes and aggregate results without empty chapter/video bars', () => {
    show({ status: 'failed', current_task: 'finished', message: '需要人工核对',
      task_scope: { learn_chapters: false, course_homework: true, course_exams: true },
      progress: { current_course: '安全教育', total: 1, failed: 1,
        assessments_submitted: 2, assessments_saved: 3, assessments_skipped: 4, assessments_failed: 1 } })
    expect(screen.getByText('课程作业')).toBeInTheDocument()
    expect(screen.getByText('课程考试')).toBeInTheDocument()
    expect(screen.getAllByText('已启用')).toHaveLength(2)
    expect(screen.queryByText('章节进度')).not.toBeInTheDocument()
    expect(screen.queryByText('当前视频')).not.toBeInTheDocument()
    expect(screen.getByText('已结束')).toBeInTheDocument()
    expect(screen.getByText('需要人工核对')).toBeInTheDocument()
    const summary = within(screen.getByLabelText('作业／考试处理结果'))
    expect(summary.getByText('以下为课程作业和课程考试的合计结果')).toBeInTheDocument()
    for (const [label, value] of [['已提交', '2'], ['已保存', '3'], ['已跳过', '4'], ['未完成', '1']]) {
      expect(summary.getByText(label).parentElement).toHaveTextContent(value)
    }
    expect(summary.queryByText(/%/)).not.toBeInTheDocument()
  })

  test('exam-only tasks have an exam result title and zero results before first callback', () => {
    show({ status: 'running', current_task: 'course-assessment',
      task_scope: { learn_chapters: false, course_homework: false, course_exams: true }, progress: {} })
    expect(screen.getByText('未启用')).toBeInTheDocument()
    expect(within(screen.getByLabelText('课程考试处理结果')).getAllByText('0')).toHaveLength(4)
    expect(screen.queryByText(/合计结果/)).not.toBeInTheDocument()
  })

  test('older tasks retain chapter and video displays and do not infer enabled assessments', () => {
    show({ status: 'running', progress: { current_chapter: '导论', completed_chapters: 1, total_chapters: 4 } })
    expect(screen.getByText('章节进度')).toBeInTheDocument()
    expect(screen.getByText('当前视频')).toBeInTheDocument()
    expect(screen.getByText('导论')).toBeInTheDocument()
    expect(screen.getAllByText('未启用')).toHaveLength(2)
    expect(screen.queryByLabelText('作业／考试处理结果')).not.toBeInTheDocument()
  })
})
