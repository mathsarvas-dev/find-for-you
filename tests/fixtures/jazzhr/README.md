# jazzhr fixtures

Real response from a public, unauthenticated endpoint (one request, User-Agent
`RumoJobSearch/0.1.0 (uso pessoal)`), captured on 2026-11-14.

- URL: https://ptg.applytojob.com/apply/jobs (list, 132,459 bytes, 1 request) and https://ptg.applytojob.com/apply/jobs/details/2R4OS0zTqb (one job page, 138,687 bytes, 1 request); robots.txt of the host also fetched
- file(s): job.html
- original size: 138,687 bytes
- original items: 1 job page (list page not stored)
- kept: job page cut to `<head>` plus the two `ld+json` blocks (Organization and JobPosting)

Trimming: item objects are unmodified (re-serialised as indented UTF-8 JSON).
E-mail addresses inside the data are replaced by `removido@example.test`. No personal
data of candidates.

Robots (https://ptg.applytojob.com/robots.txt, 200): `User-agent: *` disallows only /cb (bots SemrushBot and dotbot are disallowed entirely). Company: ptg board (organisation name Courser), found by web search. The list page links jobs as `/apply/jobs/details/<id>`; its only ld+json is an Organization.
