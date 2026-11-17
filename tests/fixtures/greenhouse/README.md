# Greenhouse fixtures

Real responses from the public Greenhouse Job Board API
(`GET https://boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true`,
unauthenticated, one request per board), captured on 2026-11-12.

| file | token | original size | original jobs | kept |
|------|-------|---------------|---------------|------|
| gitlab.json | gitlab | 4,032,838 bytes | 259 | first 7 jobs having departments and content |
| anthropic.json | anthropic | 10,238,831 bytes | 672 | first 7 jobs having departments and content |

Trimming: only the `jobs` list was cut; job objects are unmodified (re-serialised
as indented UTF-8 JSON). `meta.total` was left as captured (original totals), so it
no longer equals `len(jobs)`.

`empty.json` and `malformed.json` are hand-written.

`job_questions.json`: the job detail `GET https://boards-api.greenhouse.io/v1/boards/anthropic/jobs/5456619008?questions=true`
(unauthenticated), captured on 2026-11-16 for the application questions (marco 5). Its 21
`questions`, the `compliance` blocks and the rest of the response are unmodified; only the
`content` field (the job description, 12,638 characters) was removed to keep the file small.
