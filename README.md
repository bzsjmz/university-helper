**Language:** English | [简体中文](./README.zh-CN.md)

<p align="center">
  <img src="frontend/public/favicon.svg" width="72" alt="University Helper logo" />
</p>

<h1 align="center">University Helper</h1>

**Personal desktop edition:** Windows first, with macOS and Linux builds retained. See the [fork desktop guide](./docs/DESKTOP.md) for the current workbench, installation and release status. No Docker, Python or PostgreSQL installation is required; use this fork’s `desktop-v…` releases.

<p align="center">
  <a href="../../actions/workflows/test.yml"><img alt="CI" src="https://img.shields.io/github/actions/workflow/status/sweetcornna/university-helper/test.yml?branch=main&label=ci&style=flat-square" /></a>
  <a href="../../actions/workflows/codeql.yml"><img alt="CodeQL" src="https://img.shields.io/github/actions/workflow/status/sweetcornna/university-helper/codeql.yml?branch=main&label=codeql&style=flat-square" /></a>
  <a href="../../releases/latest"><img alt="Release" src="https://img.shields.io/github/v/release/sweetcornna/university-helper?style=flat-square&label=release" /></a>
  <a href="./LICENSE"><img alt="License" src="https://img.shields.io/github/license/sweetcornna/university-helper?style=flat-square" /></a>
  <img alt="python" src="https://img.shields.io/badge/python-3.11-3776AB?logo=python&logoColor=white&style=flat-square" />
  <img alt="react" src="https://img.shields.io/badge/react-18-61DAFB?logo=react&logoColor=black&style=flat-square" />
  <img alt="fastapi" src="https://img.shields.io/badge/fastapi-0.115-009688?logo=fastapi&logoColor=white&style=flat-square" />
</p>

<p align="center">
  <strong>Upstream demo (not this fork’s deployment):</strong>
  <a href="https://shuake.cornna.xyz">shuake.cornna.xyz</a>
</p>

<p align="center">
  <img src="docs/university-helper-promo.gif" alt="University Helper product film: while you sleep, it signs in, watches lectures and answers quizzes on Chaoxing and Zhihuishu, finishing by dawn" width="640" />
  <br />
  <sub><b>"You sleep, it studies."</b> A 60-second product film, rendered frame by frame with Remotion.</sub>
</p>

## Learning platforms and integration status

This fork primarily maintains **Chaoxing / Fanya**, retains the inherited Chaoxing sign-in and Zhihuishu integrations, and provides **experimental WE Learn and U-campus AI 5.0 support**. It uses FastAPI and React. The status below describes committed source; check the running version and validate the selected course separately.

| Learning platform | Status | Scope and limits |
| --- | --- | --- |
| Chaoxing / Fanya | Actively maintained; selected live courses verified | Multiple accounts, chapter ranges, supported media and exercises, ordinary course assignments/exams, history and restarts. Assignments/exams are separate opt-in scopes; closed, expired and verification-protected items remain manual. |
| Chaoxing sign-in | Inherited support | Existing UI and APIs are retained; this fork has not verified every live sign-in workflow. |
| Zhihuishu | Inherited support | Password/QR login and course tasks are retained; this fork has not completed a full live-course regression. |
| WE Learn | **Experimental** | Login, course/lesson ranges, supported ET text exercises and internal tabs, native submission and completion reads. Selected live lessons have confirmed completion; loading, reading completion and special interactions still need work. Personal recordings remain manual. |
| U-campus AI 5.0 | **Experimental** | Current portal login, bookshelf and supported course entry points. Live-material execution compatibility still needs acceptance testing; full platform coverage is not claimed. Legacy U-campus was removed. |

WE Learn has a [dedicated workbench](docs/welearn-workbench.md) with encrypted saved logins, concurrent accounts, course/lesson select-all, and durable task history. Expired platform sessions still require reauthentication. The recording experiment is opt-in and produces no audio; scored recordings use zero-score placeholders.

WE Learn prefers courseware answers and cache hits. Each new AI question, including the first, waits a random **20–40 seconds** before the AI call; this uses the existing answer budget. New Chaoxing tasks default to `LocalCache → AI`; GO and Yanxi are not enabled by default. An answer hit, button click or local task ending does not prove platform completion.

**Deploy this fork using the [Agent deployment handbook](./docs/AGENT_DEPLOYMENT.md)** (Chinese). It covers builds from a pinned commit, private configuration, persistent state, optional English browser sandboxing, idle upgrades, backups, verification and rollback. The upstream demo and prebuilt images do not establish the version deployed from this fork.

Course assignment/exam scopes are opt-in and can run without chapters. They cover eligible unsubmitted items in selected courses, regardless of chapter limits. Only ordinary assignment papers and per-question exams are handled; verification/proctoring and unsupported layouts remain manual. Incomplete answers are saved without random fallback. Exam timers may continue while paused. Mock tests do not establish compatibility with every live course.

## How this fork differs from upstream

[bzsjmz/university-helper](https://github.com/bzsjmz/university-helper) maintains server extensions based on [sweetcornna/university-helper](https://github.com/sweetcornna/university-helper) **v1.5.0** (`6c10c064`). These are source changes, not a claim that every deployment runs them. Later upstream versions are not synchronized automatically.

| Area | Changes in this fork |
| --- | --- |
| Multiple accounts | Encrypted saved Chaoxing accounts per management user, independent tasks and duplicate-account protection; display-name editing, account deletion, per-account course selection, chapter-range previews and batch starts. |
| Course assignments and exams | Separate opt-in scopes for chapters, assignments, and exams, including courses with no chapters. Only chapters are enabled by default. Uses the selected answer sources and save/submit mode, with separate result counts. |
| Task controls and history | Aggregated console, details, pause/stop and terminal-record deletion; encrypted restart configuration preserves the original scope and reads platform progress. A successful restart replaces its old record. Persisted history remains visible after the two-hour memory-cache expiry. After a first failure, managed tasks restart once after one hour with their original settings; another failure requires manual action. Existing unscheduled failures are not enrolled. |
| Failures and threads | Failed chapters yield to later chapters and stop after at most three attempts; repeated video HTTP 403 responses use bounded recovery. Answer budgets, cooperative cancellation and worker cleanup reduce prolonged waits. |
| AI and answer costs | Server-side DeepSeek defaults and primary/fallback keys; concurrent identical-query coalescing, question-type/option-order-aware caching and no repeated paid generation after a blank or malformed HTTP 200 response. Private attempt/token-usage telemetry excludes questions, answers and keys. |
| Providers and concurrency | New-task defaults of 1.5x speed, two chapter workers and LocalCache→AI; GO and Yanxi are not enabled by default. The application task-count cap can be disabled and chapter concurrency has no application maximum; container resource limits remain a deployment concern. |
| Passive QQ status | A separate AstrBot plugin answers administrator `/任务状态` commands from owner-scoped sanitized snapshots, rejects stale data and does not push notifications or share database/authentication credentials. |

Restarting creates new workers; it does not preserve execution threads across process restarts. Skipped failed chapters are not platform-confirmed completions. AI fallback may still incur charges on cache misses. Machine-specific credentials, runtime data, private operations configuration and backups are excluded from this repository. The inherited deployment and desktop instructions below refer to upstream capabilities; check actual fork releases for available prebuilt images and installers.

## Quick start

### Desktop app

Use the [fork desktop guide](./docs/DESKTOP.md) for personal computers. Windows 10/11 x64 comes first, with macOS (Apple Silicon / Intel) and Linux x64 builds retained.
The app includes the current multi-account workbench, backend, SQLite and the English trial browser. No Docker, PostgreSQL, Python installation or management registration is required.

Look for `desktop-v…` versions in [this fork’s Releases](https://github.com/bzsjmz/university-helper/releases). Drafts and failed builds do not establish installer availability.
Windows users should prefer the installer ending in `x86_64-pc-windows-msvc.exe`; the guide lists every platform and checksum instructions.

Add your learning accounts, read courses and confirm the selected scope before starting. The desktop shares the current task controls, batch preview, assessments, persistent history and restart implementation.
Desktop Settings accepts your own AI endpoint, model and primary/fallback keys. The default remains `LocalCache → AI`; maintainer credentials and private question banks are never included. AI calls may incur charges.

This preview has no fork updater signing key and uses manual installer updates. Stop tasks before quitting or updating; sleep and process restarts do not preserve execution threads.
Windows installers have no commercial code signature and macOS builds are not notarized. See the guide for platform dependencies, local data, backups and log paths.

### Upstream release installation (reference)

> To deploy this fork, use the [Agent deployment handbook](./docs/AGENT_DEPLOYMENT.md) and build app/web from the approved commit. The inherited installer below downloads upstream artifacts and may omit this fork’s extensions.

On a clean server, install Docker and run the deploy script. It writes `.env`
with random secrets, pulls the prebuilt images from GHCR, starts Postgres, the
backend and the web container, and waits for `/health`. You don't need a git
checkout: when the script runs on its own, it first downloads the matching
source release into `./university-helper` (set `UH_INSTALL_DIR` to change that).

```bash
# Linux / macOS / WSL2, local access at http://localhost:8080
curl -fsSL https://github.com/sweetcornna/university-helper/releases/latest/download/deploy_server.sh | bash -s -- -y --admin-email you@example.com

# Plain http on a public IP
curl -fsSL https://github.com/sweetcornna/university-helper/releases/latest/download/deploy_server.sh | bash -s -- -y --host 203.0.113.10 --admin-email you@example.com

# A domain (ENV=production, https CORS, plus a host-nginx template for TLS)
curl -fsSL https://github.com/sweetcornna/university-helper/releases/latest/download/deploy_server.sh | bash -s -- -y --domain your.domain --admin-email you@example.com
```

On Windows with Docker Desktop:

```powershell
Invoke-WebRequest -UseBasicParsing https://github.com/sweetcornna/university-helper/releases/latest/download/deploy_server.ps1 -OutFile deploy_server.ps1
powershell -ExecutionPolicy Bypass -File deploy_server.ps1 -Yes -AdminEmail you@example.com
```

From a checkout, the same scripts work directly:

```bash
git clone https://github.com/sweetcornna/university-helper.git
cd university-helper

# Linux / macOS / WSL2
bash scripts/deploy_server.sh --tag 1.4.7 -y                 # local: http://localhost:8080
bash scripts/deploy_server.sh --tag 1.4.7 --host 203.0.113.10 -y
bash scripts/deploy_server.sh --tag 1.4.7 --domain your.domain -y

# Windows (PowerShell + Docker Desktop)
pwsh scripts/deploy_server.ps1 -Tag 1.4.7 -Port 8080 -Yes
```

Useful options (PowerShell names in parentheses):

- `--tag` (`-Tag`): release to install. `1.4.7` and `v1.4.7` both work; the
  default is the latest release.
- `--port` (`-Port`): host port for the web container, 8080 by default.
- `--admin-email` (`-AdminEmail`): administrator emails, comma separated.
- `--allowed-hosts` (`-AllowedHosts`): extra hostnames or IPs people will open
  the site with, such as a LAN IP or a second domain.
- `--build` (`-Build`): build the images from source instead of pulling them.

Running the script again is safe. It keeps the secrets in `.env` and the
database, and applies the options you pass. To update, run it again
with the new version from the install directory:
`bash scripts/deploy_server.sh --tag <new version> -y`.
If the installer downloaded that directory, it also brings the compose file
and scripts up to that version first.

The server never updates itself. When GitHub has a newer release, administrators
see a notice in the web app with the new version, its release notes and the
update command to copy. Administrators are the emails in `ADMIN_EMAILS` (what
`--admin-email` sets). If that is empty, the first account registered on the
server is the administrator. The server checks every 6 hours. On a server
without internet access, set `UPDATE_CHECK_ENABLED=false` in `.env` and re-run
the deploy script.

### Local development

```bash
bash scripts/setup.sh        # creates .env, installs python/node deps
make start                   # docker-compose stack (app + postgres)
make test                    # pytest + vitest
```

On Windows, run these inside WSL2 with Docker Desktop's WSL integration turned on.

## For deployment agents

Read the [Agent deployment handbook](./docs/AGENT_DEPLOYMENT.md) before acting. Check the target host’s rules, source commit, running images, all active/scheduled tasks and data ownership. Fresh installation and upgrades have different steps; wait for idle unless interrupting current tasks is authorized. The handbook contains no maintainer credentials or private runtime configuration.

## Highlights

- Multi-tenant: each user gets their own Postgres database (`tenant_<username>`), so no two users' data share a database.
- JWT-authenticated REST API on FastAPI 0.115, Pydantic v2 and psycopg2 connection pools.
- Chaoxing sign-in and Fanya course automation.
- Zhihuishu QR or password login, plus course task orchestration.
- React 18 + Vite 5 + Tailwind SPA with route-level code splitting, lazy-loaded heavy widgets, a global error boundary and authenticated routes.
- Hardened docker-compose: non-root runtime, multi-stage build, security headers, nginx rate limiting and encrypted backups.

## Repository layout

```text
backend/        FastAPI application, services, schemas, alembic migrations, tests
frontend/       React + Vite frontend (Tailwind, lazy routes, error boundary)
database/       Postgres init SQL (auto-run by docker-entrypoint) + tenant_template/ schema
nginx/          Reverse-proxy configuration (rate limits, CSP, HSTS)
scripts/        setup / test / backup / hotfix-publish helpers
.github/        CI workflows, CodeQL, Dependabot, CODEOWNERS, issue templates
```

## Tech stack

- Backend: Python 3.11, FastAPI 0.115, Pydantic v2, psycopg2, PyJWT, bcrypt, Fernet credential encryption
- Frontend: React 18, Vite 5, React Router 6, Tailwind, lucide-react, leaflet
- Desktop: Tauri 2 shell around a PyInstaller build of the backend
- Data: PostgreSQL 15 (`max_connections=300`, `work_mem=8MB`)
- Migrations: Alembic
- Deployment: Docker + Compose in production, with host nginx in front of the app
- CI: GitHub Actions runs blocking checks for the backend (Ruff, Bandit,
  pytest), the frontend (ESLint, Vitest, build), the desktop Rust tests and the
  Docker image build. CodeQL runs separately. Trivy and `npm audit` are
  report-only; `mypy` is not currently run by CI.

## Operating systems and access

The desktop app runs natively on Windows, macOS and Linux. The server edition
is a web app: Linux is the production target, and it runs anywhere Docker
runs, using the multi-arch images described below. Any device, Android
included, can use a server through the browser or the installed PWA.

| Platform | Status | Recommended path |
|---|---|---|
| Linux | Full local dev + production deployment | Docker Engine + Compose, Python 3.11, Node 20 |
| macOS | Local dev + deploy client | Docker Desktop or Colima, Python 3.11, Node 20 |
| Windows | Server (Docker Desktop) + deploy client | `scripts/deploy_server.ps1` (PowerShell) or WSL2 + `deploy_server.sh` |
| Android | End-user client | Install the PWA from Chrome/Edge; there is no native APK |

See [Platform Support](./docs/PLATFORMS.md) for platform-specific steps.

## Development details

### Backend only

```bash
cd backend
install -m 600 .env.example .env  # then edit SECRET_KEY / CORS_ORIGINS / DB creds
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

Required env vars: `MAIN_DB_*`, `SECRET_KEY` (at least 16 characters),
`CORS_ORIGINS`, and `CREDENTIAL_ENCRYPTION_KEY` (a Fernet key, required in
production and optional in dev).

### Frontend only

```bash
cd frontend
npm install
npm run dev          # http://localhost:3000 (proxies /api to :8000)
```

### Database

Postgres 15 or newer. The docker entrypoint runs the schema files in
[`database/`](./database) automatically. Alembic migrations live in
[`backend/alembic/versions/`](./backend/alembic/versions) and have separate
`main_db` and `tenant_db` heads. From `backend/`, upgrade the shared main
database with:

```bash
alembic upgrade main_db@head
```

From the repository root, upgrade every existing tenant database with:

```bash
python scripts/migrate_tenants.py
```

## Main API areas

`POST /api/v1/auth/register` · `POST /api/v1/auth/login` · `GET /api/v1/auth/shuake-token`
`POST /api/v1/chaoxing/{login,sign}` · `GET /api/v1/chaoxing/courses`
`POST /api/v1/course/start` · `GET /api/v1/course/status/{task_id}`
`POST /api/v1/course/zhihuishu/{qr-login,password-login,tasks/course}`
`GET /api/v1/system/update` (server edition, administrators only) · `GET /health`

## Answer banks (题库)

When the worker answers Chaoxing quizzes, it looks each question up in a
configurable answer bank (`tiku`). The answer is checked against the question
type and cached, so a quiz of N questions does O(1) cache reads. Choose the
source in `tiku_config.provider`; the Fanya page labels this field **题库来源**.

| Provider | `provider` value | Token | Notes |
|----------|------------------|-------|-------|
| 言溪题库 | `TikuYanxi` | required | General-purpose bank. |
| GO 题库 | `TikuGo` | optional | Free search source (网课小工具, `q.icodef.com`), throttled. |
| Like 题库 | `TikuLike` | required | Backup bank (datam.site). |
| 题库适配器 | `TikuAdapter` | none | Points at a self-hosted [tikuAdapter](https://github.com/DokiDoki1103/tikuAdapter) (`url`). |
| AI 智能答题 | `AI` | none | OpenAI-compatible LLM (`endpoint`/`key`/`model`). |
| 硅基流动 | `SiliconFlow` | required | SiliconFlow LLM. |
| 本地缓存 | `LocalCache` | none | Uses cached answers only and never calls an external API. |

### Fallback chain (多题库回退)

`provider` also takes an ordered, comma-separated list. The worker tries each
provider in turn and moves to the next one when a provider has no answer or
returns one that doesn't match the question type. Providers that can't start,
such as a bank without a token or an LLM without a key, are dropped from the
chain, so the chain keeps working as long as one provider does.

```jsonc
// Fork default: local answers first, then configured server AI on a miss
{ "provider": "LocalCache,AI" }
```

> The shared cache and an installed offline bank are checked first. `LocalCache` supports an offline-only mode and remains the first entry in the default chain; AI fallback may incur charges. The table lists optional adapters, not a recommendation to enable or purchase GO, Yanxi or other services.

## Deployment details

Use the [Agent deployment handbook](./docs/AGENT_DEPLOYMENT.md) for this fork’s installation, upgrades and rollback. Build app and web from a pinned commit and record source and runtime image versions separately. English trials additionally need a browser runtime and a verified sandbox; the ordinary backend image does not include them.

The [older deployment guide](./docs/DEPLOYMENT.md) describes upstream infrastructure and remains historical reference. Do not apply its fixed hosts, hotfix defaults or image addresses to this fork. Back up databases, the Fernet key, private configuration and state together. Before replacing app, check active/paused tasks, scheduled retries and English workers, then update only the authorized services.

## Security

- The backend container runs as non-root (UID/GID 10001) with a read-only root filesystem and dropped capabilities.
- Postgres has a capped `max_connections`, slow-query logging and WAL compression.
- nginx sets `Content-Security-Policy`, HSTS and `frame-ancestors` deny, and limits `/api/v1/auth/login` to 5 requests per minute.
- Third-party credentials are stored encrypted with Fernet (`CREDENTIAL_ENCRYPTION_KEY`).
- Report vulnerabilities as described in [`.github/SECURITY.md`](./.github/SECURITY.md).

## Compliance

Use this project only within the rules of your school, the platforms involved
and local law. Think through the risks before you turn on automation against
third-party services.

## Acknowledgements

The Chaoxing and Zhihuishu automation is based on protocol research from the
open-source projects below, and in places adapts it. Thanks to their authors.

**Chaoxing 学习通: sign-in**
- [cxOrz/chaoxing-signin](https://github.com/cxOrz/chaoxing-signin): protocol reference for normal, photo, gesture, location and QR sign-in.

**Chaoxing 学习通: course automation (刷课)**
- [Samueli924/chaoxing](https://github.com/Samueli924/chaoxing): unattended task-point completion for Chaoxing, Erya and Fanya. Our Chaoxing course module follows its overall approach.

**Zhihuishu 智慧树 / Zhidao 知到: course automation (刷课)**
- [luoyily/zhihuishu-tool](https://github.com/luoyily/zhihuishu-tool): reference for the Zhihuishu and Zhidao APIs and tooling.

**Question banks, font de-obfuscation and OCR (used directly)**
- [SocialSisterYi/xuexiaoyi-to-xuexitong-tampermonkey-proxy](https://github.com/SocialSisterYi/xuexiaoyi-to-xuexitong-tampermonkey-proxy): Chaoxing encrypted-font de-obfuscation and answer proxy (see `backend/app/services/course/chaoxing/cxsecret_font.py`, `answer_cache.py`).
- [DokiDoki1103/tikuAdapter](https://github.com/DokiDoki1103/tikuAdapter): pluggable question-bank adapter (see `answer_providers/adapter.py`).
- [sml2h3/ddddocr](https://github.com/sml2h3/ddddocr): captcha OCR (see `captcha.py`).

Each of these projects has its own license; please follow its terms. If your
project is listed here and you want the attribution changed or removed, open an
issue.

## License

[MIT](./LICENSE)

### English platform trials

WE Learn and U-campus AI 5.0 remain experimental. The committed implementation uses isolated browsers for supported lessons in the selected range. WE Learn supports declared ET internal tabs, native submission confirmation and platform completion reads; this does not establish compatibility with every course. Recordings, unknown controls, loading failures and unconfirmed completion can still require manual handling.
Each new WE Learn AI question waits 20–40 seconds before the call; courseware and cache hits skip the wait. The ordinary backend image does not include Playwright/Chromium. See the [Agent handbook](docs/AGENT_DEPLOYMENT.md) for deployment requirements and the [integration notes](docs/english-learning.md) for details and licensing.
