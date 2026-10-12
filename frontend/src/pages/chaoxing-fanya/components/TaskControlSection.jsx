import { CARD, DONE_STATUSES, toNum, normalizeCourseText } from '../utils'


export default function TaskControlSection({
  taskId, taskStatus, loading, isRunning, statusText,
  startTask, controlTask, controlLoading = {}, showStartButton = true,
}) {


  const progress = taskStatus?.progress || {}
  const scope = taskStatus?.task_scope
  const showChapters = scope?.learn_chapters !== false
  const homeworkEnabled = scope?.course_homework === true
  const examsEnabled = scope?.course_exams === true
  const showAssessments = homeworkEnabled || examsEnabled ||
    ['submitted', 'saved', 'skipped', 'failed'].some(key => toNum(progress[`assessments_${key}`]) > 0)
  const assessmentTitle = homeworkEnabled && !examsEnabled ? '课程作业处理结果'
    : examsEnabled && !homeworkEnabled ? '课程考试处理结果' : '作业／考试处理结果'
  const currentTask = taskStatus?.current_task === 'course-assessment' ? '作业／考试'
    : taskStatus?.current_task === 'finished' ? '已结束' : taskStatus?.current_task || '--'
  const currentCourseName = normalizeCourseText(progress.current_course)
  const videoProgress = progress.video_progress && typeof progress.video_progress === 'object' ? progress.video_progress : null
  const videoCurrent = Math.max(0, toNum(videoProgress?.current))
  const videoDuration = Math.max(0, toNum(videoProgress?.duration))
  const videoPercent = videoDuration > 0 ? Math.min(100, Math.max(0, (videoCurrent / videoDuration) * 100)) : 0
  const coursePercent = toNum(progress.total) > 0 ? Math.min(100, Math.max(0, (toNum(progress.completed) / Math.max(toNum(progress.total), 1)) * 100)) : 0
  const chapterPercent = toNum(progress.total_chapters) > 0 ? Math.min(100, Math.max(0, (toNum(progress.completed_chapters) / Math.max(toNum(progress.total_chapters), 1)) * 100)) : 0


  return (
    <section className={CARD}>


      <div className="mb-4 flex flex-wrap gap-2">


        {showStartButton && <button


          type="button"


          onClick={() => {


            void startTask()


          }}


          disabled={loading || isRunning}


          className="min-h-[44px] cursor-pointer rounded-xl bg-primary px-6 py-3 text-white disabled:cursor-not-allowed disabled:bg-text-muted"


        >


          {loading ? '启动中...' : '开始刷课'}


        </button>}


        <button


          type="button"


          onClick={() => {


            void controlTask('pause')


          }}


          disabled={statusText !== 'running' || Boolean(controlLoading?.pause)}


          className="min-h-[44px] cursor-pointer rounded-xl border border-amber-300 px-4 py-2 disabled:cursor-not-allowed disabled:border-border"


        >


          暂停


        </button>


        <button


          type="button"


          onClick={() => {


            void controlTask('resume')


          }}


          disabled={statusText !== 'paused' || Boolean(controlLoading?.resume)}


          className="min-h-[44px] cursor-pointer rounded-xl border border-success px-4 py-2 disabled:cursor-not-allowed disabled:border-border"


        >


          继续


        </button>


        <button


          type="button"


          onClick={() => {
            if (window.confirm('确定要停止当前刷课任务吗？')) {
              void controlTask('stop')
            }
          }}


          disabled={!taskId || statusText === 'cancelling' || DONE_STATUSES.has(statusText) || Boolean(controlLoading?.stop)}


          className="min-h-[44px] cursor-pointer rounded-xl border border-danger/30 px-4 py-2 disabled:cursor-not-allowed disabled:border-border"


        >


          停止


        </button>


      </div>


      <div className="grid gap-3 text-sm md:grid-cols-4">


        <div className="rounded-xl border border-border bg-surface p-3">


          <p className="text-text-muted">任务状态</p>


          <p className="font-semibold">{taskStatus?.status || 'idle'}</p>


        </div>


        <div className="rounded-xl border border-border bg-surface p-3">


          <p className="text-text-muted">当前任务</p>


          <p className="font-semibold">{currentTask}</p>


        </div>


        <div className="rounded-xl border border-border bg-surface p-3">


          <p className="text-text-muted">课程进度</p>


          <p className="font-semibold">


            {toNum(progress.completed)}/{toNum(progress.total)}（失败 {toNum(progress.failed)}）


          </p>


        </div>


        {showChapters && <div className="rounded-xl border border-border bg-surface p-3">
          <p className="text-text-muted">章节进度</p>
          <p className="font-semibold">{toNum(progress.completed_chapters)}/{toNum(progress.total_chapters)}</p>
        </div>}
        <div className="rounded-xl border border-border bg-surface p-3">
          <p className="text-text-muted">课程作业</p>
          <p className={`font-semibold ${homeworkEnabled ? 'text-primary' : 'text-text-muted'}`}>{homeworkEnabled ? '已启用' : '未启用'}</p>
        </div>
        <div className="rounded-xl border border-border bg-surface p-3">
          <p className="text-text-muted">课程考试</p>
          <p className={`font-semibold ${examsEnabled ? 'text-primary' : 'text-text-muted'}`}>{examsEnabled ? '已启用' : '未启用'}</p>
        </div>


      </div>


      <div className="mt-4 grid gap-3 md:grid-cols-2">
        <div className="rounded-xl border border-border bg-surface p-4">
          <p className="text-sm text-text-muted">当前课程</p>
          <p className="mt-1 font-semibold text-text">{currentCourseName || '--'}</p>
          {taskStatus?.message && <p className="mt-3 break-words text-sm text-text/80">{taskStatus.message}</p>}
          {showChapters && <>
          <p className="mt-3 text-sm text-text-muted">当前章节</p>
          <p className="mt-1 font-semibold text-text">{normalizeCourseText(progress.current_chapter) || '--'}</p>
          <p className="mt-3 text-sm text-text-muted">当前视频</p>
          <p className="mt-1 font-semibold text-text">{videoProgress?.name || '--'}</p>
          <div className="mt-3 h-2 overflow-hidden rounded-full bg-surface-hover">
            <div className="h-full rounded-full bg-primary transition-all duration-200" style={{ width: `${videoPercent}%` }} />
          </div>
          <p className="mt-2 text-xs text-text/70">播放进度：{videoCurrent.toFixed(1)} / {videoDuration.toFixed(1)} 秒（{Math.round(videoPercent)}%）</p>
          </>}
        </div>
        <div className="rounded-xl border border-border bg-surface p-4">
          <p className="text-sm text-text-muted">任务推进</p>
          <p className="mt-1 text-sm text-text/80">课程：{toNum(progress.completed)}/{toNum(progress.total)}（{Math.round(coursePercent)}%）</p>
          <div className="mt-2 h-2 overflow-hidden rounded-full bg-surface-hover">
            <div className="h-full rounded-full bg-success transition-all duration-200" style={{ width: `${coursePercent}%` }} />
          </div>
          {showChapters && <>
          <p className="mt-3 text-sm text-text/80">章节：{toNum(progress.completed_chapters)}/{toNum(progress.total_chapters)}（{Math.round(chapterPercent)}%）</p>
          <div className="mt-2 h-2 overflow-hidden rounded-full bg-surface-hover">
            <div className="h-full rounded-full bg-violet-500 transition-all duration-200" style={{ width: `${chapterPercent}%` }} />
          </div>
          </>}
          {showAssessments && <div className="mt-4 rounded-xl border border-border p-3" aria-label={assessmentTitle}>
            <p className="text-sm font-medium">{assessmentTitle}</p>
            {homeworkEnabled && examsEnabled && <p className="mt-1 text-xs text-text-muted">以下为课程作业和课程考试的合计结果</p>}
            <div className="mt-3 grid grid-cols-2 gap-3 text-sm sm:grid-cols-4">
              {[
                ['已提交', 'submitted'], ['已保存', 'saved'], ['已跳过', 'skipped'], ['未完成', 'failed'],
              ].map(([label, key]) => <div key={key}>
                <p className="text-xs text-text-muted">{label}</p>
                <p className="mt-1 font-semibold">{toNum(progress[`assessments_${key}`])}</p>
              </div>)}
            </div>
            <p className="mt-3 text-xs text-text-muted">已保存不代表已交卷，已跳过不代表平台已完成；具体原因见任务日志。</p>
          </div>}
        </div>
      </div>
    </section>
  )


}
