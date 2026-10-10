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
configured separately for each task and defaults to 1 on this page.

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
