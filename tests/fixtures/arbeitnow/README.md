# arbeitnow fixtures

Real response from a public, unauthenticated endpoint (one request, User-Agent
`RumoJobSearch/0.1.0 (uso pessoal)`), captured on 2026-11-14.

- URL: https://www.arbeitnow.com/api/job-board-api
- file(s): job-board-api.json
- original size: 2,633,582 bytes (first file)
- original items: 300 jobs (page 1)
- kept: first 5 jobs; `links` and `meta` kept as captured (`meta.per_page`/`to` still say 300)

Trimming: only the list was cut; item objects are unmodified (re-serialised as
indented UTF-8 JSON). E-mail addresses would be replaced by `removido@example.test`
(none were present). No personal data of candidates.
