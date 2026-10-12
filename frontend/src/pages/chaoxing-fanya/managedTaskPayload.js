export function managedTaskPayload(config, courseIds, chapterLimits) {
  return {
    learn_chapters: config.learnChapters ?? true,
    course_homework: config.courseHomework ?? false, course_exams: config.courseExams ?? false,
    course_ids: [...courseIds], chapter_limits: { ...chapterLimits },
    speed: config.speed, concurrency: config.concurrency, unopened_strategy: config.unopenedStrategy,
    tiku_config: {
      provider: config.tikuProvider.join(','), token: config.tikuToken,
      coverage_threshold: config.coverageThreshold, submit_mode: config.submitMode,
      judge_mapping: { correct: config.correctOptions.split(','), wrong: config.wrongOptions.split(',') },
    },
    notify_config: config.notifyService && config.notifyUrl ? { service: config.notifyService, url: config.notifyUrl } : {},
  }
}
