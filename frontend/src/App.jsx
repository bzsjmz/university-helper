import { lazy, Suspense } from 'react'
import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom'
import {
  AppLayout,
  AuthExpiredListener,
  ErrorBoundary,
  PrivateRoute,
  RouteFallback,
  RuntimeProfileProvider,
  ThemeProvider,
  ToastProvider,
  useRuntimeProfile,
} from './components'
import { isAuthenticated } from './utils/auth'

const Login = lazy(() => import('./pages/Login'))
const Register = lazy(() => import('./pages/Register'))
const Dashboard = lazy(() => import('./pages/Dashboard'))
const ChaoxingSignin = lazy(() => import('./pages/ChaoxingSignin'))
const ChaoxingFanya = lazy(() => import('./pages/ChaoxingFanya'))
const Zhihuishu = lazy(() => import('./pages/Zhihuishu'))
const EnglishLearning = lazy(() => import('./pages/EnglishLearning'))
const NotFound = lazy(() => import('./pages/NotFound'))

function RuntimeRoutes() {
  const { isLocal, loading } = useRuntimeProfile()

  if (loading) return <RouteFallback />

  const home = isLocal || isAuthenticated() ? '/dashboard' : '/login'

  return (
    <>
      {!isLocal && <AuthExpiredListener />}
      <Suspense fallback={<RouteFallback />}>
        <Routes>
          <Route path="/login" element={isLocal ? <Navigate to="/dashboard" replace /> : <Login />} />
          <Route
            path="/register"
            element={isLocal ? <Navigate to="/dashboard" replace /> : <Register />}
          />
          <Route
            element={
              <PrivateRoute>
                <AppLayout />
              </PrivateRoute>
            }
          >
            <Route path="/dashboard" element={<Dashboard />} />
            <Route path="/chaoxing-signin" element={<ChaoxingSignin />} />
            <Route path="/chaoxing-fanya" element={<ChaoxingFanya />} />
            <Route path="/zhihuishu-panel" element={<Zhihuishu />} />
            <Route path="/english-learning" element={<EnglishLearning />} />
          </Route>
          <Route path="/" element={<Navigate to={home} replace />} />
          <Route path="*" element={<NotFound />} />
        </Routes>
      </Suspense>
    </>
  )
}

function App() {
  return (
    <ErrorBoundary>
      <ThemeProvider>
        <BrowserRouter>
          <RuntimeProfileProvider>
            <ToastProvider>
              <RuntimeRoutes />
            </ToastProvider>
          </RuntimeProfileProvider>
        </BrowserRouter>
      </ThemeProvider>
    </ErrorBoundary>
  )
}

export default App
