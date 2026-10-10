# Local multi-account server extension

The server Chaoxing page now manages several learning accounts under one
management login. Add an account once, connect to load its courses, select
courses and start a task, then switch accounts to start another task.
The local desktop profile continues to use its original single-account page.

Account records live in the main PostgreSQL `learning_accounts` table.
Username and password use the existing Fernet credential key. All account
operations filter by the authenticated management owner. Public responses
contain a masked login and label, never credentials. Changing a password or
deleting an account requires stopping that account's active task first.

Browsing sessions are cached separately for each owner/account. Workers create
their own sessions and receive credentials from the server account record.
Concurrent admission is atomic; one learning account has at most one active
task. Different learning accounts can run concurrently for the same owner.
Tasks carry account identifiers and masked labels so controls and progress
remain attributable after account switches. Legacy tasks retain their history
and are marked as having no recorded account.

`CHAOXING_MAX_ACTIVE_TASKS` configures the server learning-task capacity:
unset preserves the upstream default of 64; `0` removes the account-task
limit; positive values set a capacity. Paused tasks occupy capacity. The
deployment's container CPU/memory limits still apply. Chapter concurrency is
configured separately for each task and defaults to 1 on this page. It accepts
positive integers without an application maximum, in both the UI and worker.

Account credentials and task history persist. Running/paused worker threads
do not survive a process restart: restart marks interrupted tasks as failed,
and the operator starts a new task. No automatic resume is introduced.

Build the frontend with the locked Node 20 environment (`npm ci`,
`npm run build`), then build `Dockerfile.web-local`. Build the backend using
`Dockerfile.backend-local`; both extend the upstream 1.5.0 images.

Validation covers atomic multi-account admission, independent controls and
sessions, owner isolation, encrypted storage, duplicate protection, account
recreation, trusted server credentials, and frontend account-switch races.
The database integration suite requires `MULTI_ACCOUNT_DB_TEST=1` and must
only run against a dedicated disposable database. It deletes account rows
in that database. Tests use dummy accounts and never contact real courses.

## Server task console

The server `/dashboard` page is a learning-account task console. It reads the
owner-scoped account list and task snapshots every five seconds, with no
overlapping polls. The desktop profile keeps the original service dashboard.

The console shows account totals, running/paused task counts, failed records,
account and status filters, account/course/task search, and every task's
course/chapter progress. A selected task displays video progress and the last
100 log entries. These counters describe program execution; platform completion
has not been independently verified. Legacy task history is explicitly marked
as having no recorded account.

Pause, resume and confirmed stop target the individual task ID. Commands to
different tasks are independent. A snapshot that began before a command is
drained before requesting the post-command snapshot. Failed polling retains
the last data, shows a stale-data message, and disables task controls until
task synchronization succeeds. Account management and task creation remain on
the Chaoxing page; the console links to it and preserves links to other services.

This console is frontend-only and can be deployed by updating the web service
without restarting backend worker threads.

## Passive QQ task query

`integrations/astrbot_plugin_course_tasks` is an independent AstrBot plugin with
an administrator-only `/任务状态 [页码]` command. It uses the existing QQ adapter
and never sends proactive messages. The server optionally exports a single
configured management owner's status every five seconds to a dedicated private
directory. The bot receives only a read-only mount of that sanitized directory;
no database, credentials, tokens or additional network connectivity are shared.
Snapshots exclude raw logs and notification configuration, prioritize active
tasks, and bound detail to 200 records. The bot rejects snapshots older than
30 seconds and replies with five records per page. The existing group silence
guard explicitly permits this exact command; permission checks still apply.
