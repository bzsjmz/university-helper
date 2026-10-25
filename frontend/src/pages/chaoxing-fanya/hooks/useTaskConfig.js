import { useState } from 'react'


export default function useTaskConfig() {


  const [learnChapters, setLearnChapters] = useState(true)
  const [courseHomework, setCourseHomework] = useState(false)
  const [courseExams, setCourseExams] = useState(false)

  const [speed, setSpeed] = useState(1.5)


  const [concurrency, setConcurrency] = useState(2)


  const [unopenedStrategy, setUnopenedStrategy] = useState('retry')


  // The backend consults persistent local answers before external queries.
  // Query the anonymous answer bank before spending an AI request.
  const [tikuProvider, setTikuProvider] = useState(['LocalCache', 'TikuGo', 'AI'])


  const [tikuToken, setTikuToken] = useState('')


  const [coverageThreshold, setCoverageThreshold] = useState(0.9)


  const [correctOptions, setCorrectOptions] = useState('对,正确,是')


  const [wrongOptions, setWrongOptions] = useState('错,错误,否')


  const [submitMode, setSubmitMode] = useState('submit')


  const [notifyService, setNotifyService] = useState('')


  const [notifyUrl, setNotifyUrl] = useState('')


  return {
    learnChapters, setLearnChapters,
    courseHomework, setCourseHomework,
    courseExams, setCourseExams,
    speed, setSpeed,
    concurrency, setConcurrency,
    unopenedStrategy, setUnopenedStrategy,
    tikuProvider, setTikuProvider,
    tikuToken, setTikuToken,
    coverageThreshold, setCoverageThreshold,
    correctOptions, setCorrectOptions,
    wrongOptions, setWrongOptions,
    submitMode, setSubmitMode,
    notifyService, setNotifyService,
    notifyUrl, setNotifyUrl,
  }


}
