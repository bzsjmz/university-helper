# Imported local question bank

Chaoxing answer queries first check `/state/imported-question-bank.sqlite`.
`CHAOXING_LOCAL_BANK_FILE` can override the file on the server. Missing, corrupt,
unsupported or unmatched files fall through to the existing cache/provider chain.
This adds no external provider and does not change the configured provider order.

The bank is separate from generated answer caches. A student submission or AI
response never writes into it. Choices require the same question type, title and
complete option text set. Their answers are remapped to the current option labels;
partial option sets and fuzzy title matches are not accepted. Years, numbers,
negations and whitespace in quoted code are preserved.

Prepare source-attributed UTF-8 JSONL outside Git, one object per line:

```json
{"type":"single","title":"Which value is even?","options":["1","2","3"],"answers":["2"],"source":"https://example.org/pinned-source"}
```

Types are `single`, `multiple`, `judgement`, `completion`, and `shortanswer`.
Use answer texts, not option letters. Judgements must be canonical true/false
values. Preserve blank order in completion answers. In the configured backend
environment, build a staging file with:

```sh
python -m app.services.course.chaoxing.local_question_bank input.jsonl staging.sqlite --report import-report.json
```

The builder rejects abstentions, missing options, invalid labels/answers, image
questions and unsupported records. Duplicate questions retain their source URLs;
conflicting answers for the same complete question remove every variant. Validate
source rights, course context, freshness and answer quality before installation.
Structural validation does not establish correctness. Unverified public records
can contain wrong answers even when their structure is valid.

Back up state, inspect the report and install the validated file atomically with
the application user's ownership and mode `0600`. Each lookup opens the indexed
database read-only, so subsequent data replacements do not require a restart.
Keep raw data, licenses, provenance, reports and databases outside Git.
