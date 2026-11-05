import { normalizeCourseText, toNum } from '../utils'

const REVIEW_STATES = {
  waiting_browser: '等待章节复习浏览器', reviewing: '正在正常复习章节', waiting_grade: '等待平台统计更新',
}

export default function CourseGradeResults({ progress = {}, compact = false }) {
  const rows = Array.isArray(progress.grade_results) ? progress.grade_results : []
  const review = progress.visit_review
  if (!rows.length && !review) return null
  return <section aria-label="平台成绩核验" className="mt-4 space-y-3 rounded-xl border border-border p-3 text-sm">
    <h3 className="font-semibold">平台成绩核验</h3>
    <p className="text-xs text-text-muted">任务点进度与平台成绩分别统计；参考及格线不代替教师的考核要求。</p>
    {review && <p role="status">{REVIEW_STATES[review.state] || '正在核对章节次数'}：
      已访问 {toNum(review.visited_pages)} 次，平台次数分 {toNum(review.visits_score)}/{toNum(review.visits_weight)}。
      {review.current_chapter && normalizeCourseText(review.current_chapter)}
    </p>}
    {(compact ? rows.slice(-1) : rows).map((row, index) => <div key={index} className="space-y-1">
      <p className="font-medium">{normalizeCourseText(row.course_name)}</p>
      {row.state === 'verified' ? <>
        <p>平台总分 {row.score} · 参考及格线 {row.passing_score} ·
          <span className={row.meets_target ? 'text-success' : 'text-amber-600'}>
            {row.meets_target ? '已达到参考线' : '尚未达到参考线'}
          </span>
        </p>
        <p>章节次数分 {row.visits_score}/{row.visits_weight}
          {row.full_mark_count != null && ` · 本课满分门槛 ${row.full_mark_count} 次`}
        </p>
      </> : <p className="text-amber-600">平台成绩未核验，不能据任务点完成判定及格。</p>}
      {row.message && <p className="text-xs text-text-muted">{row.message}</p>}
    </div>)}
    {compact && rows.length > 1 && <p className="text-xs text-text-muted">已记录 {rows.length} 门课程，详情中可查看全部。</p>}
  </section>
}
