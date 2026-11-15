# Himalayas fixtures

Real response from the public Himalayas browse feed
(`GET https://himalayas.app/jobs/api`, unauthenticated, no key, one request),
captured on 2026-11-14.

| file | original size | original jobs | kept |
|------|---------------|---------------|------|
| jobs.json | 169,851 bytes | 20 (`totalCount` 120,513) | first 5 jobs |

Trimming: only the `jobs` list was cut; job objects are unmodified (re-serialised
as indented UTF-8 JSON), except one recruiter phone number inside a description,
replaced by `removido@example.test`. `limit`, `totalCount` and `nextCursor` were
left as captured.
