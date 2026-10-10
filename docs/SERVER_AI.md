# Server-side AI defaults

The AI answer provider can use deployment defaults when the web task supplies only `provider: AI`:

- `CHAOXING_AI_ENDPOINT`: full chat-completions URL.
- `CHAOXING_AI_MODEL`: model name.
- `CHAOXING_AI_KEY_FILE`: read-only UTF-8 secret file containing the API key.
- `CHAOXING_AI_TIMEOUT_SECONDS`: request timeout, default 30 seconds.

Task admission still validates the effective endpoint. The server key is available only for the exact server-configured endpoint; a different endpoint requires an explicit caller key. The server key is read at provider initialization and never inserted into the task configuration. New tasks see a key updated in place; existing tasks keep their initialized key.

Select AI in the page, or add AI after another answer source in its fallback chain. The page's answer-bank Token field is separate from the server AI key. A blank or unreadable secret file prevents AI initialization; configuration alone does not verify external API access.

`Dockerfile.backend-local` layers the provider and task-admission changes onto the existing 1.5.0 backend image. Secret files must stay outside the source/build context and be mounted at runtime.
