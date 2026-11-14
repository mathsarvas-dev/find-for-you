# Rumo Marco 3b — Mais fontes de vagas e funil de aderência — Plano de implementação

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Coletar vagas de mais ATS verificados, de páginas de carreira (JSON-LD), de feeds de agregadores e de links colados pela pessoa, sempre por um cliente HTTP seguro e um portão de conformidade, e guardar/processar com descrição, vetor e LLM só as vagas que passam nos filtros duros e no corte vetorial opcional.

**Architecture:** Todo acesso a terceiros passa por `rumo_core/sources/http.py` (`SafeClient`: SSRF com IP fixado, robots, intervalos/teto por host, 403/429 → bloqueio) e, para portais e feeds, por `rumo_core/sources/portals/gate.py` (veredito do `policy.json` + escolha da pessoa em `portal_state`). Conectores novos seguem o protocolo `Source` do Greenhouse; feeds têm protocolo próprio (`FeedSource`) e penduram as vagas em empresas sintéticas. A ingestão ganha uma `IngestPolicy` (origem, `source_rank`, fechamento, filtros) que aplica o funil (`full/stub/gated`) e a deduplicação entre fontes; preenchimento, vetorização, ranking e avaliação passam a olhar só `intake='full'` (e `gated` para listar). Fatia 0 decide, com respostas reais salvas como fixtures, quais fontes ❓ entram.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy 2, Alembic, Postgres 16 + pgvector + `unaccent`, httpx (`MockTransport` nos testes), `urllib.robotparser`, Pydantic v2, pytest (marcadores `db`/`slow`, `asyncio_mode=auto`), Next.js 16 + vitest/RTL.

**Spec:** `docs/superpowers/specs/2026-11-14-rumo-marco-3b-fontes-design.md` (autoridade). Evidência: `docs/superpowers/specs/2026-11-14-rumo-marco-3b-pesquisa.md` (só fatos ✅; ❓/NÃO VERIFICADO só entra após a fatia 0). Também valem `2026-11-11-rumo-design.md` §5.2 e `2026-11-13-rumo-coleta-automatica-design.md`.

## Global Constraints
- Cadeia de migrações: `0007` (4b, `0007_chat_filters.py`) → **`0008` (este plano, `down_revision = "0007"`)** → `0009` (marco 5). O plano `2026-11-14-rumo-marco-5-candidatura.md` cita `0008`/`down_revision="0007"` e **precisa ser renumerado para `0009`/`down_revision="0008"` antes de executar** (não editar aqui; avisar no handoff).
- Uma única migração `0008` com todas as colunas/tabelas do §7 da spec, criada no início da fatia 1 (as fatias 3–4 já precisam de `jobs.origin`, `source_rank`, `job_feeds`, `portal_state`); o comportamento do funil entra na fatia 5.
- `User-Agent: RumoJobSearch/<versão> (uso pessoal)` em toda requisição nova (versão = `importlib.metadata.version("rumo")`, fallback `"0.1.0"`); sem e-mail nem dado pessoal.
- `SafeClient`: só `http`/`https`, portas 80/443, sem userinfo; bloqueia IP não global (privado, loopback, link-local, metadados `169.254.169.254`, CGNAT, ULA, IPv4 mapeado); conecta ao IP verificado (sem segunda resolução); redirecionamento só no mesmo domínio registrável, até 3 saltos; páginas ≤ 5 MB (APIs de ATS usam `fetch_max_bytes`, 20 MB), 20 s, tipos HTML/JSON/XML/texto; robots em cache 24 h.
- **O sistema nunca evita um 403/anti-robô:** 2 respostas 403/429 seguidas do mesmo host → `SourceBlocked`, suspensão e aviso; sem proxy, sem trocar agente, sem captcha, sem repetir.
- `robots.txt`: 404/410 → tudo permitido; qualquer outro erro, 401/403/429/5xx, tempo esgotado ou conteúdo ilegível → nada permitido (cache de 1 h para erros). APIs documentadas (`kind="api"`) seguem a política da API (sem robots).
- `policy.json`: `conditional` vem desligado; `blocked` só admite importar por link; o veredito é apoio à decisão, não parecer jurídico (texto da UI diz isso).
- Chaves só no `.env`: `ADZUNA_APP_ID`, `ADZUNA_APP_KEY`, `JOOBLE_KEY`, `CAREERJET_KEY`; nunca no banco, no repositório ou em log; a API/UI só dizem "configurada/não configurada".
- `source_rank`: ATS = 0, página de carreira e link = 1, agregador/portal = 2. Em colisão (mesmo empregador + mesmo `fingerprint`), o menor rank fica `open`, o outro `duplicate`.
- Funil: `jobs.intake ∈ {full, stub, gated}`; `stub` = `description=''`, `raw={}`, sem vetor, `llm_filled_at` preenchido (nunca vai ao LLM), nunca listado; `gated` mantém vetor, não recebe preenchimento nem avaliação; corte vetorial `None` por padrão; `suggest_floor` mantém ≥ 95% das vagas rotuladas "boas"; `collect_mode='all'` desliga as etapas 1–2.
- Nada marcado ❓/NÃO VERIFICADO é implementado por suposição: só depois de a tarefa de verificação passar (tarefas "condicionais"); falhou → sai do escopo e vai para `docs/superpowers/backlog.md`.
- Testes sem internet (fixtures reais aparadas em `tests/fixtures/<fonte>/`, httpx `MockTransport`, resolvedor DNS injetado). Banco: marcador `db`, banco de dev compartilhado, usuários descartáveis `User(email=f"<tag>-{uuid4().hex}@example.test")` apagados no teardown.
- Logs: só ids e nomes de classe de exceção; nunca URL digitada pela pessoa, token, nome de empresa, texto de vaga ou chave.
- Texto de vaga/página é dado: nunca vira HTML sem escape, instruções dentro dele não valem.
- UI em PT-BR, curta; estado nunca só por cor; `web/AGENTS.md`: ler `node_modules/next/dist/docs/` antes de escrever código Next. Commits em inglês, conventional commits, sem `--no-verify`.

## Review Focus
1. URL digitada com truques (`http://example.com@127.0.0.1/`, espaços, host em maiúsculas, fragmento, IPv6 entre colchetes, `0x7f.1`): rejeitada ou canonicalizada antes de qualquer conexão — testes `test_rejects_userinfo_and_numeric_tricks` e `test_canonical_url` (Task 15).
2. Filtros salvos inválidos (JSON antigo ou corrompido) no worker: o funil se desliga (tudo `full`) e nunca transforma o quadro inteiro em stubs — `test_invalid_saved_filters_disable_funnel` (Task 47).
3. Vários feeds da mesma fonte (Remotive, ~4 req/dia) devidos no mesmo tick: o intervalo/teto é por host, não por feed — só uma requisição sai — `test_two_feeds_same_host_respect_min_interval` (Task 43).
4. Empresa cujo quadro inteiro ficou fora dos filtros: a linha mostra "0 vagas abertas · N fora dos seus filtros", não parece quebrada — `test_company_out_counts_stubs` (Task 52) e `CompanyRow` (Task 54).
5. Vaga de feed sem nome de empregador (ou só sufixo jurídico): vai para a empresa sintética `_desconhecida` e nunca é deduplicada contra outra empresa — `test_unknown_employer_never_cross_deduped` (Task 42).

---

## Fatia 0 — Verificação e fixtures

**Regras comuns às Tasks 1–13** (cada tarefa as inclui):
- Uma requisição real ao endpoint (mais o `robots.txt` do host quando o alvo é uma página e não uma API), com `curl -sS -A "RumoJobSearch/0.1.0 (uso pessoal)" -D headers.txt -o body.<ext> <url>`; sem autenticação, sem contornar nada; 401/403/429/captcha = **falha** (não repetir).
- Fixture aparada em `tests/fixtures/<fonte>/`: no máximo 5 itens, objetos sem alteração, reserializados em UTF-8 indentado; o `README.md` da pasta registra URL, data, tamanho original, itens originais e mantidos (como `tests/fixtures/greenhouse/README.md`). Nunca salvar dado pessoal de candidatos; e-mails/telefones de recrutadores dentro da descrição são trocados por `removido@example.test`.
- Veredito numa linha da tabela de `tests/fixtures/VERIFICATION.md` (criada na Task 1): `fonte | data | URL | status HTTP | formato | chaves do item | paginação | veredito (passou/falhou) | motivo`.
- Falhou → apagar a fixture parcial, acrescentar a fonte à seção `## Marco 3b — fontes não verificadas` de `docs/superpowers/backlog.md` (motivo e evidência) e marcar a tarefa condicional correspondente como "pulada (verificação falhou)". Passou → a tarefa condicional usa exatamente as chaves registradas.
- Commit: `test(sources): verify <fonte> public format` (com ou sem fixture).

### Task 1: Fixtures das fontes já verificadas e registro de verificação

**Files:**
- Create: `tests/fixtures/VERIFICATION.md`, `tests/fixtures/{lever,ashby,smartrecruiters,breezy,remotive,remoteok,arbeitnow}/README.md` + um `.json` aparado cada
- Modify: `docs/superpowers/backlog.md` (cria a seção `## Marco 3b — fontes não verificadas`, com Workable e Gupy já listados: fora de escopo pela spec §1, API oficial exige token)

**Interfaces:**
- Produces: `tests/fixtures/lever/leverdemo.json` (array), `ashby/ashby.json` (`{"jobs": [...]}`; corte para 5 jobs — original ~4,3 M caracteres), `smartrecruiters/smartrecruiters.json` (`{offset, limit, totalFound, content}`), `smartrecruiters/posting_detail.json`, `breezy/breezy.json` (array), `remotive/remote-jobs.json`, `remoteok/api.json` (array com o 1º elemento legal preservado), `arbeitnow/job-board-api.json` (`{"data": [...]}`, com `links`/`meta` se vierem).

- [ ] **Step 1: Capturar** (uma requisição por fonte): `https://api.lever.co/v0/postings/leverdemo?mode=json`, `https://api.ashbyhq.com/posting-api/job-board/ashby?includeCompensation=true`, `https://api.smartrecruiters.com/v1/companies/smartrecruiters/postings?limit=5`, `https://breezy.breezy.hr/json`, `https://remotive.com/api/remote-jobs`, `https://remoteok.com/api`, `https://www.arbeitnow.com/api/job-board-api`. SmartRecruiters: **mais uma** requisição, ao `ref` do 1º item, salva como `posting_detail.json`, para registrar se há URL pública (`postingUrl`/`applyUrl`) e onde fica a descrição.
- [ ] **Step 2: Registrar** em `VERIFICATION.md` as chaves reais de cada item, incluindo as que a pesquisa não detalhou: Lever `categories.*`, `salaryRange.*`, host de `hostedUrl`; Ashby chave e forma da compensação, host de `jobUrl`; SmartRecruiters `location.*`, `department.*`, `company.identifier` e a URL pública do detalhe; Breezy forma de `location`/`salary`, host de `url`; Arbeitnow tipo de `created_at` (epoch ou ISO) e presença de `links.next`.
- [ ] **Step 3: Critério de aprovação** por fonte: HTTP 200, JSON no formato da pesquisa, ≥ 1 item com id e título, sem credencial. Uma que falhe segue a regra comum (backlog + task condicional pulada).
- [ ] **Step 4: Commit** `test(sources): capture fixtures for verified job sources`.

### Task 2: Verificar Recruitee
- Pista (não verificada): `https://{slug}.recruitee.com/api/offers/` (o slug `recruitee` deu 404 na pesquisa). Escolher uma empresa cuja página pública de vagas está em `*.recruitee.com` (busca na web, uma página aberta à mão não conta como requisição do sistema).
- Passa se: 200, JSON com lista de ofertas contendo id, título, URL pública e local, sem credencial. Fixture `tests/fixtures/recruitee/offers.json`.

### Task 3: Verificar Personio
- Pista: `https://{slug}.jobs.personio.de/xml` (a página de suporte deu 429 na pesquisa). Passa se: 200, XML com `position` contendo id, nome, escritório e descrição, sem credencial, e o `robots.txt` do host não proíbe `/xml` para `*`. Rejeitar o arquivo se tiver `<!DOCTYPE`. Fixture `tests/fixtures/personio/positions.xml`.

### Task 4: Verificar Teamtailor
- Pista: `https://{slug}.teamtailor.com/jobs.rss`. Passa se: 200, RSS com `item` contendo título, link e descrição; robots permite o caminho. Fixture `tests/fixtures/teamtailor/jobs.rss`.

### Task 5: Verificar BambooHR
- Pista: `https://{slug}.bamboohr.com/careers/list`. Passa se: 200, JSON com lista de vagas (id, título, local) sem credencial; registrar se a descrição exige outra requisição. Fixture `tests/fixtures/bamboohr/list.json`.

### Task 6: Verificar Comeet
- Pista: a lista pública de posições de uma empresa Comeet (endpoint a localizar na documentação pública do Comeet; se exigir `token` da empresa, **falha**). Passa se: 200 sem credencial, JSON com posições (uid, nome, local, URL). Fixture `tests/fixtures/comeet/positions.json`.

### Task 7: Verificar Pinpoint
- Pista: `https://{slug}.pinpointhq.com/postings.json`. Passa se: 200, JSON com `data` de vagas (id, título, local, URL), sem credencial. Fixture `tests/fixtures/pinpoint/postings.json`.

### Task 8: Verificar JazzHR
- Pista: a página pública `https://{slug}.applytojob.com/apply/jobs`; não há JSON público conhecido. Passa **somente** se houver lista estruturada (JSON público ou JSON-LD `JobPosting` nas páginas de vaga, verificado na página de uma vaga) e o robots permitir. Se só houver HTML de lista, **falha** (o leitor genérico da Task 32 já cobre páginas com JSON-LD). Fixture `tests/fixtures/jazzhr/job.html` (página aparada ao `<head>` + blocos `ld+json`).

### Task 9: Verificar InHire
- Pista: a página de carreiras pública de um cliente InHire (host a registrar) e `docs.inhire.com.br/api/obter-vagas`. Passa se: (a) lista pública JSON sem credencial **ou** (b) JSON-LD `JobPosting` nas páginas de vaga. (a) → conector condicional (Task 29); só (b) → InHire continua pelo leitor genérico (Task 32), registrar o host em `policy.json` (`inhire`, `conditional`) e marcar a Task 29 como pulada. Fixture `tests/fixtures/inhire/<arquivo>`.

### Task 10: Verificar o formato do Himalayas
- Endpoint documentado: `https://himalayas.app/jobs/api` (confirmar o caminho na página `https://himalayas.app/api` antes; a pesquisa não registrou as chaves). Passa se: 200 sem chave, JSON com lista de vagas e chaves de título, empresa, local, `applicationLink`, data; registrar o nome do parâmetro de cursor/página. Fixture `tests/fixtures/himalayas/jobs.json`.

### Task 11: Verificar o formato da Bundesagentur
- `GET https://rest.arbeitsagentur.de/jobboerse/jobsuche-service/pc/v4/jobs?was=Software&size=5&page=1` com header `X-API-Key: jobboerse-jobsuche` (chave pública do README do bund.dev). Passa se: 200, JSON com a lista de ofertas (`stellenangebote` ou o nome real) com `refnr`, título, empregador e local. Registrar que a descrição exige `/pc/v4/jobdetails/{base64(refnr)}` (não implementado: N+1, vai ao backlog). Fixture `tests/fixtures/bundesagentur/jobs.json`.

### Task 12: Verificar agregadores com chave (Adzuna, Jooble, Careerjet)
- Só roda se a chave estiver no `.env` local da pessoa (`ADZUNA_APP_ID`/`ADZUNA_APP_KEY`, `JOOBLE_KEY`, `CAREERJET_KEY`); a chave nunca entra na fixture, no README nem no commit (apagar `headers.txt`). Sem chave → falha "sem chave para verificar" (backlog), Tasks 39–41 puladas.
- Adzuna: uma busca para `br` (se `br` não existir, registrar o país usado); Jooble: um POST de busca; Careerjet: uma busca com `locale_code` do Brasil, `user_ip` e `user_agent` (exigidos). Passa se: 200, formato e chaves registrados, termos de atribuição registrados. Fixtures `tests/fixtures/{adzuna,jooble,careerjet}/search.json`.

### Task 13: Verificar portais Vagas.com, Catho e InfoJobs
- Vagas.com: uma página de vaga do sitemap (`/vagas/v{id}/{slug}`) → existe JSON-LD `JobPosting`? O sitemap tem vagas atuais (lastmod recente)? Catho: robots permite páginas de vaga e qual é o caminho delas; uma página de vaga → JSON-LD? InfoJobs: só o `robots.txt` (veio ilegível na pesquisa).
- Passa (por portal) se: robots permite o caminho para `*`, a página de vaga tem `JobPosting` válido e o sitemap lista vagas atuais. Atualizar `paths_allowed` do portal em `rumo_core/sources/portals/policy.json` (criado na Task 17; se esta tarefa rodar antes, registrar só no `VERIFICATION.md` e aplicar na Task 17). Falhou → portal continua `conditional` sem caminhos (só link) ou `blocked`, e a Task 46 é pulada. Fixtures `tests/fixtures/portals/{vagas,catho}_job.html` (aparadas ao `<head>` + blocos `ld+json`).

---

## Fatia 1 — Dados, cliente seguro e portão

### Task 14: Migração 0008, modelos e configurações

**Files:**
- Create: `alembic/versions/0008_sources_funnel.py`, `tests/test_sources_funnel_models.py`
- Modify: `rumo_core/models.py`, `rumo_core/config.py`, `tests/test_config.py`, `.env.example`, `pyproject.toml`

**Interfaces:**
- Produces (`rumo_core.models`):
  - `Company.collect_mode: Mapped[str]` `String(8)`, `server_default="funnel"`, `CheckConstraint("collect_mode in ('funnel','all')", name="ck_companies_collect_mode")`; `ck_companies_origin` passa a `origin in ('manual','auto','feed','link')`.
  - `Job.intake` `String(8)` default `'full'` + `CheckConstraint("intake in ('full','stub','gated')", name="ck_jobs_intake")`; `Job.stub_reason: str | None` `String(32)`; `Job.source_rank: int` `SmallInteger` default `0`; `Job.origin` `String(8)` default `'ats'` + `CheckConstraint("origin in ('ats','site','feed','link')", name="ck_jobs_origin")`; índices `ix_jobs_user_intake_status (user_id, intake, status)` e `ix_jobs_user_fingerprint (user_id, fingerprint)`.
  - `JobFeed(UserScopedMixin, TimestampMixin)`, `__tablename__ = "job_feeds"`: `id`, `source String(32)`, `params JSONB default '{}'`, `active bool default true`, `last_run_at`, `next_run_at`, `last_error Text`, `consecutive_failures int default 0`, `jobs_seen int default 0`; índice único funcional `uq_job_feeds_user_source_params` em `(user_id, source, md5(params::text))`.
  - `PortalState(UserScopedMixin, TimestampMixin)`, `__tablename__ = "portal_state"`: `id`, `portal String(64)`, `enabled bool default false`, `blocked_by_site bool default false`, `blocked_at`, `last_check_at`; `UniqueConstraint("user_id", "portal", name="uq_portal_state_user_portal")`.
  - `ck_discovery_candidates_origin_kind` passa a `('catalog','profile','target','feed')`.
  - `UserPreference.adherence_vec_floor: Mapped[float | None]` (`Float`, NULL) — onde fica o corte vetorial (a spec §7 não nomeia a coluna).
- Produces (`Settings`): `fetch_page_max_bytes: int = 5_000_000`, `fetch_host_min_interval_seconds: float = 2.0`, `fetch_host_daily_cap: int = 500`, `site_max_links: int = 200`, `site_max_new_pages: int = 25`, `feed_job_ttl_days: int = 7`, `collect_feeds_max_per_run: int = 2`, `adzuna_app_id: str = ""`, `adzuna_app_key: str = ""`, `jooble_key: str = ""`, `careerjet_key: str = ""`.
- `pyproject.toml`: pacotes `rumo_core.matching`, `rumo_core.sources.feeds`, `rumo_core.sources.portals`; package-data `"rumo_core.sources.portals" = ["policy.json"]`.
- Migração `revision = "0008"`, `down_revision = "0007"`; downgrade apaga `companies` com `origin in ('feed','link')` (cascata leva as vagas) e candidatos `origin_kind='feed'` antes de restaurar os CHECKs, remove tabelas, colunas e índices.

- [ ] **Step 1: Write failing tests** (`db`): `test_job_defaults_full_ats_rank0` (vaga nova → `intake='full'`, `origin='ats'`, `source_rank=0`, `stub_reason is None`); `test_job_intake_and_origin_checks` (`intake='x'` e `origin='rss'` → `IntegrityError`); `test_company_collect_mode_and_origin` (default `'funnel'`; `'tudo'` → erro; `origin='feed'` e `'link'` aceitos); `test_job_feed_unique_per_params` (mesmo `source` + `params` iguais → `IntegrityError`; `params` diferentes ok); `test_portal_state_unique`; `test_discovery_candidate_accepts_feed_origin`; `test_adherence_vec_floor_nullable`. Em `test_config.py`: defaults acima com `Settings(_env_file=None, database_url=...)`.
- [ ] **Step 2: Run** `pytest tests/test_sources_funnel_models.py tests/test_config.py -v` → FAIL.
- [ ] **Step 3: Implement** modelos, settings, `.env.example` (bloco `# Job sources (marco 3b)` com as chaves vazias e o comentário "nunca commitar valores") e a migração; `alembic upgrade head && alembic downgrade -1 && alembic upgrade head` sem erro.
- [ ] **Step 4: Run** `pytest tests/test_sources_funnel_models.py tests/test_config.py tests/test_jobs_models.py tests/test_discovery_models.py -v` → PASS.
- [ ] **Step 5: Commit** `feat(sources): migration 0008 for feeds, portal state and adherence funnel`.

### Task 15: `SafeClient` — URL, SSRF com IP fixado, limites de tamanho/tempo/tipo

**Files:**
- Create: `rumo_core/sources/http.py`, `tests/sources/test_http.py`
- Modify: `rumo_core/sources/base.py`

**Interfaces:**
- Produces (`base.py`): `class SourceBlocked(SourceError)` com atributo `host: str` ("o site recusou o acesso de forma repetida"); `RawJob` ganha campos opcionais no fim: `work_model_hint: str | None = None`, `country_hint: str | None = None`, `salary_hint: SalaryHint | None = None`; `@dataclass(frozen=True) SalaryHint(min: Decimal | None, max: Decimal | None, currency: str, period: str | None)`.
- Produces (`http.py`):
  - `USER_AGENT: str`, `ALLOWED_PORTS = frozenset({80, 443})`, `MAX_REDIRECTS = 3`, `CONTENT_TYPES` (`text/html`, `application/xhtml+xml`, `application/json`, `application/ld+json`, `application/xml`, `text/xml`, `application/rss+xml`, `application/atom+xml`, `text/plain`).
  - `class UnsafeURL(SourceBadResponse)`.
  - `canonical_url(text: str) -> str` — apara, exige esquema `http`/`https`, rejeita userinfo, porta fora de `ALLOWED_PORTS`, host vazio; host em minúsculas (IDN → punycode), remove fragmento; `UnsafeURL` em qualquer falha. Não resolve DNS.
  - `is_public_ip(ip: str) -> bool` (`ipaddress`; `ipv4_mapped` checado; `is_global` e não multicast).
  - `registrable_domain(host: str) -> str` — últimos 2 rótulos, ou 3 quando o par final está em `_MULTI_SUFFIXES = {"com.br","net.br","org.br","gov.br","edu.br","co.uk","org.uk","ac.uk","com.au","net.au","co.jp","com.mx","com.ar","co.in","co.nz","com.co","com.pt","co.za","com.sg"}`; hosts sob `_PRIVATE_SUFFIXES = {"github.io","herokuapp.com","vercel.app","netlify.app","pages.dev"}` usam o rótulo seguinte inteiro. Sem dependência nova (lista pública de sufixos completa vai ao backlog).
  - `async default_resolve(host: str, port: int) -> list[str]` (`socket.getaddrinfo` em `asyncio.to_thread`).
  - `@dataclass(frozen=True) SafeResponse(url: str, status: int, headers: Mapping[str, str], body: bytes, content_type: str)` com `.json()` (`SourceBadResponse` se inválido) e `.text()`.
  - `class SafeClient.__init__(self, transport: httpx.AsyncBaseTransport | None = None, *, resolve=default_resolve, clock=time.monotonic, sleep=asyncio.sleep, today=date.today, page_max_bytes: int = 5_000_000, timeout: float = 20.0, robots: "RobotsCache | None" = None, limits: "HostLimits | None" = None)`; `async get(self, url: str, *, kind: Literal["api", "page"] = "page", max_bytes: int | None = None, headers: Mapping[str, str] | None = None, accept: str | None = None) -> SafeResponse`; `async aclose()`. (Robots e limites por host chegam na Task 16; aqui `get` já recebe os ganchos com implementações nulas.)
  - Conexão fixada: resolve uma vez por salto, **todas** as IPs têm de ser públicas, a requisição vai para a 1ª IP (`url` com o IP, IPv6 entre colchetes), `Host` = nome original, `extensions={"sni_hostname": host}`; `follow_redirects=False` e redirecionamentos tratados à mão (`Location` relativo resolvido, `canonical_url`, mesmo `registrable_domain`, nova resolução + checagem, ≤ 3 saltos).
  - Status: 2xx, 404 e 410 voltam como `SafeResponse`; 5xx, timeout e erro de rede → `SourceUnavailable`; demais 4xx → `SourceBadResponse` (403/429 na Task 16). Corpo lido em stream: `Content-Length` declarado acima do limite ou bytes lidos acima dele → `SourceBadResponse`; `Content-Type` fora de `CONTENT_TYPES` → `SourceBadResponse`. Tempo total por requisição (`asyncio.wait_for`) = `timeout`.

- [ ] **Step 1: Write failing tests** (resolvedor falso `resolve(host, port)` registrando chamadas; `httpx.MockTransport`):
  - `test_rejects_non_http_schemes`: `"file:///etc/passwd"`, `"ftp://example.com/"`, `"javascript:alert(1)"`, `"gopher://x"` → `UnsafeURL`, 0 requisições.
  - `test_rejects_private_and_special_ips`: hosts `127.0.0.1`, `10.0.0.5`, `172.16.0.1`, `192.168.1.1`, `169.254.169.254`, `100.64.0.1`, `0.0.0.0`, `[::1]`, `[fd00::1]`, `[::ffff:127.0.0.1]` → `UnsafeURL`, 0 requisições.
  - `test_rejects_name_resolving_to_private_ip`: `["10.0.0.1"]` → `UnsafeURL`; `["93.184.216.34", "127.0.0.1"]` (misto) → `UnsafeURL`.
  - `test_connects_to_checked_ip_dns_rebinding`: resolvedor devolve `93.184.216.34` na 1ª chamada e `127.0.0.1` depois → exatamente 1 resolução, `request.url.host == "93.184.216.34"`, `request.headers["host"] == "jobs.example.com"`, `request.extensions["sni_hostname"] == "jobs.example.com"`.
  - `test_redirect_to_private_ip_rejected`: 302 `Location: http://169.254.169.254/latest/meta-data` → `UnsafeURL`.
  - `test_redirect_other_domain_rejected_same_domain_followed`: `jobs.example.com` → `careers.example.com` seguido; → `evil.test` rejeitado; 4 saltos → `UnsafeURL`.
  - `test_rejects_userinfo_and_numeric_tricks` (Review Focus 1): `"http://example.com@127.0.0.1/"`, `"http://user:pw@example.com/"`, `"http://0x7f.1/"`, `"http://2130706433/"`, `"http://example.com:8080/"` → `UnsafeURL`, 0 requisições.
  - `test_canonical_url` (Review Focus 1): `"  HTTPS://Jobs.Example.COM/Careers?x=1#top "` → `"https://jobs.example.com/Careers?x=1"`; `"https://exämple.com/"` → host punycode.
  - `test_registrable_domain`: `"jobs.acme.com.br"→"acme.com.br"`, `"a.b.co.uk"→"b.co.uk"`, `"acme.com"→"acme.com"`, `"x.github.io"→"x.github.io"`.
  - `test_size_cap_page_and_api`: `Content-Length: 6000000` em `kind="page"` → `SourceBadResponse`; stream de 6 MB sem cabeçalho → `SourceBadResponse`; `kind="api", max_bytes=20_000_000` aceita 6 MB.
  - `test_rejects_unexpected_content_type`: `image/png`, `application/octet-stream` → `SourceBadResponse`; `text/html; charset=utf-8` ok.
  - `test_timeout_is_unavailable`: transporte que espera além de `timeout=0.05` → `SourceUnavailable`.
  - `test_status_mapping`: 404 → `SafeResponse(status=404)`; 500 → `SourceUnavailable`; 400 → `SourceBadResponse`.
  - `test_user_agent`: cabeçalho começa com `"RumoJobSearch/"`, termina com `" (uso pessoal)"`, sem `"@"`.
- [ ] **Step 2: Run** `pytest tests/sources/test_http.py -v` → FAIL.
- [ ] **Step 3: Implement** `base.py` e `http.py` conforme acima.
- [ ] **Step 4: Run** `pytest tests/sources/test_http.py tests/sources/test_greenhouse.py tests/sources/test_normalize.py -v` → PASS.
- [ ] **Step 5: Commit** `feat(sources): safe HTTP client with pinned DNS and SSRF guards`.

### Task 16: Robots, intervalo/teto por host, `Retry-After` e bloqueio por 403/429

**Files:**
- Create: `rumo_core/sources/robots.py`, `tests/sources/test_robots.py`
- Modify: `rumo_core/sources/http.py`, `tests/sources/test_http.py`

**Interfaces:**
- Produces (`robots.py`):
  - `ROBOTS_AGENT = "RumoJobSearch"`, `ROBOTS_TTL = 86400.0`, `ROBOTS_ERROR_TTL = 3600.0`, `ROBOTS_MAX_BYTES = 500_000`.
  - `@dataclass(frozen=True) RobotsRules(allow_all: bool, deny_all: bool, parser: RobotFileParser | None, crawl_delay: float | None, sitemaps: tuple[str, ...])` com `allowed(url: str) -> bool`.
  - `parse_robots(status: int, body: bytes | None) -> RobotsRules` — 404/410 → `allow_all`; `None`/outro status/bytes não decodificáveis como UTF-8 ou com NUL → `deny_all`; senão `RobotFileParser.parse`, `crawl_delay(ROBOTS_AGENT)` e `site_maps()`.
  - `class RobotsCache(clock=time.monotonic)`: `get(origin: str) -> RobotsRules | None` (respeitando TTL), `put(origin, rules, *, error: bool)`.
  - `class RobotsDisallowed(SourceError)`.
- Produces (`http.py`):
  - `class HostCapReached(SourceUnavailable)`; `BLOCK_AFTER = 2`; `MAX_WAIT_SECONDS = 10.0`.
  - `class HostLimits(clock, today)`: por host — `next_allowed` (monotônico), contagem do dia, 403/429 consecutivos.
  - `SafeClient.get(..., min_interval: float | None = None, daily_cap: int | None = None)`: `kind="page"` → busca/usa robots do `scheme://host` (pelo mesmo caminho seguro, `kind="api"`, `max_bytes=ROBOTS_MAX_BYTES`), negado → `RobotsDisallowed` sem requisitar a página; intervalo efetivo = `max(min_interval or settings.fetch_host_min_interval_seconds, crawl_delay or 0)`; espera ≤ `MAX_WAIT_SECONDS` com `sleep`, acima disso → `SourceUnavailable` sem requisição; teto diário (`daily_cap or settings.fetch_host_daily_cap`) estourado → `HostCapReached` sem requisição; 403/429 → conta e, com `Retry-After` (segundos ou data HTTP), empurra `next_allowed`; 1ª ocorrência → `SourceUnavailable`, `BLOCK_AFTER`ª seguida → `SourceBlocked(host)`; qualquer 2xx zera a contagem. `SafeClient.sitemaps(origin) -> tuple[str, ...]` (do robots em cache).

- [ ] **Step 1: Write failing tests** (`test_robots.py`, puros; `test_http.py`, com relógio/sleep falsos):
  - `test_parse_robots_cases`: 404 → permite tudo; 500, 403 e `None` → nega tudo; corpo gzip binário → nega tudo; `"User-agent: *\nDisallow: /private"` → `/private/x` negado, `/jobs` permitido; `"User-agent: RumoJobSearch\nDisallow: /"` → tudo negado; `Crawl-delay: 5` → `crawl_delay == 5.0`; `Sitemap: https://x.test/s.xml` → em `sitemaps`.
  - `test_page_disallowed_by_robots_never_requested`: robots nega `/vagas/pesquisas` → `RobotsDisallowed`; o transporte só viu `/robots.txt`.
  - `test_robots_cached_24h`: duas páginas do mesmo host → 1 busca de robots; relógio +86401 s → nova busca; erro de robots refaz após 3601 s.
  - `test_api_kind_skips_robots`: `kind="api"` → nenhuma requisição a `/robots.txt`.
  - `test_crawl_delay_and_min_interval`: `Crawl-delay: 5`, `min_interval=2` → duas páginas, `sleeps == [5.0]`; `min_interval=30` → 2ª chamada levanta `SourceUnavailable` sem requisição.
  - `test_daily_cap`: `daily_cap=3` → 4ª `HostCapReached`, 3 requisições; virar o dia (`today` falso) zera.
  - `test_retry_after_respected`: 429 com `Retry-After: 30` → `SourceUnavailable`; chamada seguinte antes de 30 s → `SourceUnavailable` sem requisição.
  - `test_repeated_403_429_blocks`: `[403, 403]` → 2ª `SourceBlocked` com `.host == "jobs.example.com"`; `[429, 429]` idem; `[403, 200, 403]` → nunca bloqueia.
- [ ] **Step 2: Run** `pytest tests/sources/test_robots.py tests/sources/test_http.py -v` → FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** `pytest tests/sources/test_robots.py tests/sources/test_http.py -v` → PASS.
- [ ] **Step 5: Commit** `feat(sources): robots, per-host limits and block on repeated 403/429`.

### Task 17: Portão de conformidade (`policy.json`, `gate.check`, estado por usuário)

**Files:**
- Create: `rumo_core/sources/portals/__init__.py`, `rumo_core/sources/portals/policy.json`, `rumo_core/sources/portals/gate.py`, `rumo_core/sources/portals/state.py`, `tests/sources/test_gate.py`, `tests/sources/test_portal_state.py`

**Interfaces:**
- `policy.json`: lista de objetos `{id, name, kind: "portal"|"feed", status, checked_at: "YYYY-MM-DD", evidence: {urls: [...], summary}, hosts: [...], paths_allowed: [...], min_interval_seconds, daily_cap}` (`hosts`/`kind`/`name` acrescentados aos campos da spec para mapear URL → portal). `paths_allowed` vazio = nenhum caminho automático. Entradas (`checked_at` `2026-11-14`, evidência copiada da pesquisa):
  - `blocked`: `indeed` (hosts `indeed.com`, `indeed.com.br`), `linkedin` (`linkedin.com`), `jobbol` (`jobbol.com.br`; 403), `weworkremotely` (`weworkremotely.com`, `kind=feed`; 403), `gupy_portal` (`portal.gupy.io`, `gupy.io`; API interna não documentada), `infojobs_br` (`infojobs.com.br`; robots ilegível — reavaliar na Task 13).
  - `conditional`: `vagas` (`vagas.com.br`, `paths_allowed ["/vagas/v"]`, 10 s, 200/dia), `catho` (`catho.com.br`, `[]` até a Task 13), `inhire` (`inhire.com.br` + hosts da Task 9, `[]`), `bundesagentur` (`rest.arbeitsagentur.de`, `kind=feed`, documentação comunitária, termos não verificados; 3600 s, 24/dia).
  - `allowed` (`kind=feed`): `remotive` (`remotive.com`, 21600 s, 4/dia), `remoteok` (`remoteok.com`, 3600 s, 24/dia), `himalayas` (`himalayas.app`, 3600 s, 24/dia), `arbeitnow` (`arbeitnow.com`, 3600 s, 24/dia), `adzuna` (`api.adzuna.com`), `jooble` (`jooble.org`), `careerjet` (`careerjet.com`, `search.api.careerjet.net`) — 3600 s, 50/dia para os três.
- Produces (`gate.py`):
  - `@dataclass(frozen=True) PortalPolicy(id, name, kind, status, checked_at: date, evidence_urls: tuple[str, ...], evidence_summary: str, hosts: tuple[str, ...], paths_allowed: tuple[str, ...], min_interval_seconds: float, daily_cap: int)`.
  - `load_policy(path: Path | str | None = None) -> dict[str, PortalPolicy]` (cache por caminho; arquivo inválido, status fora do conjunto ou id repetido → `ValueError`).
  - `portal_for_url(url: str, policy: dict[str, PortalPolicy] | None = None) -> PortalPolicy | None` (host igual a um de `hosts` ou terminando em `"." + host`).
  - `@dataclass(frozen=True) PortalUserState(enabled: bool = False, blocked_by_site: bool = False)`.
  - `@dataclass(frozen=True) Verdict(allowed: bool, reason: Literal["allowed","blocked","conditional_off","blocked_by_site","path_not_allowed","unknown_portal"], message: str)`.
  - `check(portal: str, url: str | None = None, *, state: PortalUserState | None = None, manual: bool = False, policy=None) -> Verdict` — `manual=True` → sempre `allowed` (importar por link); `blocked` → falso; `blocked_by_site` → falso; `conditional` sem `enabled` → falso; `kind="portal"` com `url` cujo caminho não começa por nenhum `paths_allowed` → falso; feeds ignoram `paths_allowed`.
  - Mensagens (exatas): `blocked` → `"Este portal proíbe coleta automática. Use “Importar por link”."`; `conditional_off` → `"Portal desligado: os termos de uso não foram totalmente verificados. Ligue na aba Portais se aceitar o risco."`; `blocked_by_site` → `"O site recusou nosso acesso; a coleta foi suspensa."`; `path_not_allowed` → `"Este caminho não está liberado para coleta automática neste portal."`; `unknown_portal` → `"Portal desconhecido."`.
- Produces (`state.py`): `load_states(session, user_id) -> dict[str, PortalUserState]`; `set_enabled(session, user_id, portal: str, enabled: bool) -> PortalState` (upsert, não comita); `mark_blocked_by_site(session, user_id, portal: str, now: datetime) -> None` (upsert `blocked_by_site=True`, `blocked_at=now`, não comita).

- [ ] **Step 1: Write failing tests**:
  - `test_policy_file_is_valid`: `load_policy()` carrega; `indeed`, `linkedin`, `jobbol`, `weworkremotely` são `blocked`; `vagas`, `catho` são `conditional`; `remotive.daily_cap == 4`; nenhum `allowed` com `kind="portal"`.
  - `test_load_policy_rejects_bad_file` (tmp_path): status `"maybe"` → `ValueError`; id repetido → `ValueError`.
  - `test_portal_for_url`: `https://www.vagas.com.br/vagas/v123/x` → `vagas`; `https://br.indeed.com/viewjob?jk=1` → `indeed`; `https://notvagas.com.br/` → `None`.
  - `test_check_matrix`: `blocked` → `allowed False, reason "blocked"`; com `manual=True` → `True`; `conditional` sem estado → `"conditional_off"`; com `enabled=True` e caminho `/vagas/v1/x` → `True`; `/vagas/pesquisas` → `"path_not_allowed"`; `catho` ligado com `paths_allowed=[]` → `"path_not_allowed"`; `allowed` + `blocked_by_site=True` → `"blocked_by_site"`; `"nope"` → `"unknown_portal"`. Mensagens iguais às da Interfaces.
  - `test_portal_state_roundtrip` (`db`): `set_enabled` duas vezes não duplica; `mark_blocked_by_site` marca e preserva `enabled`; `load_states` só vê o próprio usuário.
- [ ] **Step 2: Run** `pytest tests/sources/test_gate.py tests/sources/test_portal_state.py -v` → FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** mesmo comando → PASS.
- [ ] **Step 5: Commit** `feat(sources): compliance gate with versioned portal policy`.

---

## Fatia 2 — Conectores de ATS

**Padrão comum (Tasks 18–30):** cada conector em `rumo_core/sources/<nome>.py`, classe `<Nome>Source` com `name`, `__init__(self, client: SafeClient, *, max_bytes: int)`, `normalize_identifier(text) -> str` (slug/id; aceita a URL hospedada no host **registrado na fixture**; `ValueError` em PT-BR caso contrário), `async fetch_jobs(identifier) -> list[RawJob]` (via `client.get(..., kind="api", max_bytes=max_bytes)`; 404 → `SourceNotFound`; JSON fora do formato → `SourceBadResponse`; itens sem id/título/URL descartados; id repetido → o último vence, como no Greenhouse) e `aclose()`. Registro em `rumo_core/sources/__init__.py` com fábrica que lê `get_settings()` na 1ª construção e usa **um `SafeClient` compartilhado** (`shared_client()` no mesmo módulo, fechado por `aclose_sources()`). Só são mapeadas chaves presentes na fixture. Testes em `tests/sources/test_<nome>.py`, sempre com a fixture real e `MockTransport`, cobrindo: `test_parses_real_fixture`, `test_missing_fields_tolerated`, `test_duplicate_ids_last_wins`, `test_hostile_html_kept_as_data` (descrição com `<script>` e `</p><img onerror>` vira texto sem script em `normalize_job`), `test_not_found`, `test_bad_shape`, `test_normalize_identifier`.

### Task 18: Infra comum dos conectores + Lever

**Files:**
- Create: `rumo_core/sources/lever.py`, `tests/sources/test_lever.py`
- Modify: `rumo_core/sources/__init__.py`, `rumo_core/sources/base.py`, `rumo_core/sources/normalize.py`, `tests/sources/test_normalize.py`

**Interfaces:**
- Consumes: `SafeClient` (Task 15–16), fixture `tests/fixtures/lever/leverdemo.json`.
- Produces (`base.py`): `ATS_SOURCES: frozenset[str]` (cresce a cada conector: `{"greenhouse","lever"}` aqui) e `source_rank_for(source: str) -> int` (ATS → 0, `"site"`/`"link"` → 1, demais → 2).
- Produces (`__init__.py`): `shared_client() -> SafeClient`; `aclose_sources()` também fecha o cliente compartilhado.
- Produces (`normalize.py`): `normalize_job` usa `raw.work_model_hint` quando ∈ `{"remote","hybrid","onsite"}`, `raw.country_hint` quando casa `^[A-Z]{2}$` (cidade continua por `parse_location`) e `raw.salary_hint` em vez de `extract_salary` quando presente (`salary_known=True`). `content_hash`/`fingerprint` inalterados.
- Produces: `LeverSource`: `API = "https://api.lever.co/v0/postings/{site}?mode=json&skip={skip}&limit={limit}"`, UE `api.eu.lever.co` quando o identificador é `"eu:<site>"`; `page_size=100`, `max_pages=20`; para quando uma página vem com menos de `page_size`; atingir `max_pages` → `SourceBadResponse` (nada gravado, evita fechar vagas por lista truncada). Mapeamento: `external_id=id`, `title=text`, `url=hostedUrl`, `location_text=categories.location`, `department=categories.department or categories.team`, `description_html = description + listas (lists[].text + <ul>content</ul>) + additional`, `posted_at=createdAt` (epoch ms), `work_model_hint` de `workplaceType`, `country_hint=country`, `salary_hint` de `salaryRange` só se a fixture mostrar `min/max/currency/interval`.

- [ ] **Step 1: Write failing tests**: os do padrão comum + `test_pagination_skip_limit` (páginas de 100, 100, 3 → 203 vagas, 3 requisições com `skip=0,100,200`); `test_page_cap_raises` (20 páginas cheias → `SourceBadResponse`); `test_eu_identifier` (`"eu:acme"` → host `api.eu.lever.co`); em `test_normalize.py`: `test_hints_override_rules` (`work_model_hint="remote"` com local `"Berlin"` → `remote`; `country_hint="DE"` → `DE`; `salary_hint` → `salary_known` e valores) e `test_invalid_hints_ignored` (`"anywhere"`, `"Germany"`).
- [ ] **Step 2: Run** `pytest tests/sources/test_lever.py tests/sources/test_normalize.py -v` → FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** `pytest tests/sources -v` → PASS.
- [ ] **Step 5: Commit** `feat(sources): Lever connector and source hints in normalization`.

### Task 19: Ashby

**Files:** Create `rumo_core/sources/ashby.py`, `tests/sources/test_ashby.py`; Modify `rumo_core/sources/__init__.py`, `rumo_core/sources/base.py` (`ATS_SOURCES` += `ashby`).

**Interfaces:**
- Produces: `AshbySource`, `API = "https://api.ashbyhq.com/posting-api/job-board/{board}?includeCompensation=true"`, sem paginação, `max_bytes = settings.fetch_max_bytes` (quadros grandes). Mapeamento: chave de topo `jobs`; `isListed is False` → descartado; `external_id=id`, `title`, `url=jobUrl`, `location_text=location`, `department=department or team`, `description_html=descriptionHtml`, `posted_at=publishedAt`, `work_model_hint` de `workplaceType` (ou `"remote"` se `isRemote is True`); compensação → `salary_hint` só com a forma registrada na Task 1.

- [ ] **Step 1: Write failing tests**: padrão comum + `test_unlisted_jobs_skipped`, `test_is_remote_sets_hint`.
- [ ] **Step 2: Run** `pytest tests/sources/test_ashby.py -v` → FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** → PASS.
- [ ] **Step 5: Commit** `feat(sources): Ashby connector`.

### Task 20: SmartRecruiters

**Pré-condição:** a Task 1 registrou a URL pública de uma vaga (campo do detalhe). Se não houver URL pública confiável, pular a tarefa e registrar no backlog.

**Files:** Create `rumo_core/sources/smartrecruiters.py`, `tests/sources/test_smartrecruiters.py`; Modify `__init__.py`, `base.py` (`ATS_SOURCES` += `smartrecruiters`).

**Interfaces:**
- Produces: `SmartRecruitersSource`, `API = "https://api.smartrecruiters.com/v1/companies/{id}/postings?limit={limit}&offset={offset}"`, `limit=100`, `max_pages=20` (estouro → `SourceBadResponse`), para quando `offset + len(content) >= totalFound` ou página vazia. Mapeamento: `external_id=id`, `title=name`, `url` = padrão de URL pública registrado na Task 1, `location_text` a partir de `location.*` (chaves da fixture), `department=department.label` (se existir), `posted_at=releasedDate`. **Sem descrição** (exige `GET ref` por vaga — N+1; vai ao backlog): `description_html=""`.

- [ ] **Step 1: Write failing tests**: padrão comum + `test_pagination_offset_total_found` (`totalFound=205` → 3 requisições, 205 vagas), `test_description_empty_by_design`.
- [ ] **Step 2: Run** → FAIL. **Step 3: Implement.** **Step 4: Run** → PASS.
- [ ] **Step 5: Commit** `feat(sources): SmartRecruiters connector`.

### Task 21: Breezy

**Files:** Create `rumo_core/sources/breezy.py`, `tests/sources/test_breezy.py`; Modify `__init__.py`, `base.py` (`ATS_SOURCES` += `breezy`).

**Interfaces:**
- Produces: `BreezySource`, `API = "https://{company}.breezy.hr/json"` (identificador `[a-z0-9-]{1,63}`; aceita `https://{company}.breezy.hr/...`), array JSON. Mapeamento: `external_id=id` (ou `friendly_id`), `title=name`, `url`, `location_text` da forma de `location` registrada, `department`, `posted_at=published_date`; sem descrição na lista: `description_html = "<p>Salary: {salary}</p>"` quando `salary` for texto, senão `""` (descrição por vaga vai ao backlog).

- [ ] **Step 1: Write failing tests**: padrão comum + `test_salary_text_feeds_salary_extraction` (`"$100,000 - $120,000"` → `normalize_job(...).salary_known`).
- [ ] **Step 2: Run** → FAIL. **Step 3: Implement.** **Step 4: Run** → PASS.
- [ ] **Step 5: Commit** `feat(sources): Breezy connector`.

### Tasks 22–29: Conectores condicionais (só se a verificação passou)

Cada uma segue o **padrão comum**, usa exatamente o endpoint, o formato e as chaves registrados em `tests/fixtures/VERIFICATION.md`, acrescenta o nome a `ATS_SOURCES` e registra a fonte. Antes de começar: abrir `VERIFICATION.md`; veredito "falhou" → marcar a tarefa como pulada e seguir. Commit `feat(sources): <Nome> connector`.

- **Task 22: Recruitee** (verificação Task 2) — `rumo_core/sources/recruitee.py`, `tests/sources/test_recruitee.py`.
- **Task 23: Personio** (Task 3) — XML: parse com `xml.etree.ElementTree` depois de rejeitar `<!DOCTYPE`/`<!ENTITY` (`SourceBadResponse`); teste `test_rejects_doctype_entities`.
- **Task 24: Teamtailor** (Task 4) — RSS, mesma regra de XML e teste.
- **Task 25: BambooHR** (Task 5) — sem descrição se a lista não trouxer (backlog).
- **Task 26: Comeet** (Task 6).
- **Task 27: Pinpoint** (Task 7).
- **Task 28: JazzHR** (Task 8) — se a verificação só achou JSON-LD em páginas, esta tarefa é pulada (o leitor genérico cobre).
- **Task 29: InHire** (Task 9, caso (a)) — senão pulada.

### Task 30: Registro final e normalização de URLs hospedadas

**Files:** Modify `rumo_core/sources/__init__.py`; Create `tests/sources/test_registry.py`.

**Interfaces:**
- Produces: `ats_from_url(url: str) -> tuple[str, str] | None` — tenta `normalize_identifier` de cada fonte de `ATS_SOURCES` **só** quando o host da URL pertence ao domínio do ATS (mapa `ATS_HOSTS: dict[str, tuple[str, ...]]` montado a partir das fixtures: `greenhouse.io`, `lever.co`, `ashbyhq.com`, `smartrecruiters.com`, `breezy.hr` + os condicionais aprovados).
- [ ] **Step 1: Write failing tests**: `test_every_ats_source_registered` (`get_source(n)` para cada `n in ATS_SOURCES`), `test_ats_from_url` (URL hospedada de cada fixture → `(fonte, id)`; `https://example.com/careers` → `None`; `https://evil.test/?x=greenhouse.io` → `None`), `test_source_rank_for` (`greenhouse`→0, `site`→1, `link`→1, `remotive`→2).
- [ ] **Step 2: Run** `pytest tests/sources/test_registry.py -v` → FAIL. **Step 3: Implement.** **Step 4: Run** → PASS.
- [ ] **Step 5: Commit** `feat(sources): ATS registry and hosted URL detection`.

---

## Fatia 3 — JSON-LD, páginas de carreira e importar por link

### Task 31: `jsonld.extract_job_postings`

**Files:** Create `rumo_core/sources/jsonld.py`, `tests/sources/test_jsonld.py`, `tests/fixtures/jsonld/*.html` (escritas à mão, mais as páginas reais da Task 13 se houver).

**Interfaces:**
- Produces:
  - `MAX_BLOCKS = 50`, `MAX_BLOCK_CHARS = 1_000_000`.
  - `extract_job_postings(html: str, *, base_url: str | None = None) -> list[RawJob]` — varre todo `<script type="application/ld+json">` (tipo sem caixa, com `; charset` tolerado), `json.loads(strict=False)` por bloco (bloco inválido ou grande demais é pulado), desembrulha listas e `@graph`, aceita `@type == "JobPosting"` ou lista que o contenha; ignora o resto. Por posting: `title` obrigatório; `url` = `posting.url` se `http(s)` válido por `canonical_url`, senão `base_url`, senão descartado; `external_id` = `identifier.value` / `identifier` (texto) / URL canônica, cortado a 255 (acima disso, `sha256` hex); `description_html = description or ""`; `location_text` de `jobLocation` (objeto ou lista; `address.addressLocality`, `addressRegion`, `addressCountry` texto ou `{name}`; vários locais unidos por `"; "`); `jobLocationType == "TELECOMMUTE"` → `work_model_hint="remote"` e, sem `jobLocation`, `location_text = "Remote - " + países de applicantLocationRequirements`; `country_hint` = `addressCountry` de 2 letras quando único; `baseSalary` (`MonetaryAmount`: `currency`, `value.minValue/maxValue/value`, `unitText` `YEAR/MONTH/HOUR` → `year/month/hour`; `WEEK`/`DAY`/ausente → sem salário) → `salary_hint`; `datePosted` (data ou data-hora) → `posted_at` com fuso UTC; `department=None`; `raw` = o dicionário do posting.
  - `employer_name(raw: RawJob) -> str | None` (de `raw.raw["hiringOrganization"]`, texto ou `{name}`, aparado a 255).
- [ ] **Step 1: Write failing tests**: `test_graph_wrapper`, `test_multiple_blocks_and_list`, `test_mixed_types_only_jobposting` (`Organization` + `JobPosting` → 1), `test_type_as_list`, `test_invalid_block_skipped_other_parsed`, `test_telecommute_with_applicant_country` (→ hint `remote`, `location_text == "Remote - Brazil"`), `test_salary_range_year` (→ `SalaryHint(Decimal("100000"), Decimal("120000"), "USD", "year")`), `test_salary_week_ignored`, `test_date_only_posted_at_is_aware`, `test_description_html_with_script_is_data` (`normalize_job` não contém `alert(`), `test_javascript_url_falls_back_to_base_url`, `test_missing_title_skipped`, `test_no_jobposting_returns_empty`, `test_employer_name`.
- [ ] **Step 2: Run** `pytest tests/sources/test_jsonld.py -v` → FAIL. **Step 3: Implement** (parser com `html.parser.HTMLParser` coletando só o texto dos scripts `ld+json`). **Step 4: Run** → PASS.
- [ ] **Step 5: Commit** `feat(sources): JobPosting JSON-LD extraction`.

### Task 32: Leitor de páginas de carreira (`site`)

**Files:** Create `rumo_core/sources/careers.py`, `tests/sources/test_careers.py`; Modify `rumo_core/sources/__init__.py` (registra `"site"`).

**Interfaces:**
- Consumes: `SafeClient.get(kind="page")`, `SafeClient.sitemaps`, `extract_job_postings`, `ats_from_url`, `canonical_url`, `registrable_domain`.
- Produces:
  - `class EmbeddedAtsFound(SourceError)` com `source: str`, `identifier: str`; `class NoStructuredData(SourceError)`.
  - `MSG_NO_STRUCTURED = "Esta página não tem dados estruturados de vagas. Use “Importar por link” para vagas específicas."`
  - `detect_embedded_ats(html: str, page_url: str) -> tuple[str, str] | None` — atributos `src`/`href`/`data-src` de `iframe`, `script`, `link`, `a`, em ordem, via `ats_from_url`.
  - `job_links(html: str, page_url: str, *, limit: int) -> list[str]` — `a[href]` canonicalizados, mesmo `registrable_domain`, diferentes da própria página, caminho contendo um de `("job", "jobs", "vaga", "vagas", "career", "careers", "carreira", "position", "opening", "stellen", "oportunidade")`, sem duplicados, ordem de aparição, até `limit`.
  - `async sitemap_links(client: SafeClient, page_url: str, *, limit: int) -> list[str]` — sitemaps do robots ou `/sitemap.xml`; XML com `<!DOCTYPE`/`<!ENTITY` rejeitado; índice de sitemaps seguido 1 nível (até 5 arquivos); só URLs sob o prefixo de caminho da página de carreira; até `limit`.
  - `@dataclass(frozen=True) SiteFetch(raws: list[RawJob], listed_ids: frozenset[str])`.
  - `@dataclass(frozen=True) ProbeResult(kind: Literal["ats", "jsonld", "none"], source: str | None, identifier: str | None, raws: list[RawJob])`.
  - `class SiteSource`: `name = "site"`; `__init__(client, *, max_links: int, max_new_pages: int)`; `normalize_identifier(text) -> str` (= `canonical_url`, ≤ 255, senão `ValueError("URL longa demais.")`); `async fetch_listing(url: str, *, known_ids: frozenset[str]) -> SiteFetch` — (1) JSON-LD na própria página → `SiteFetch(raws, ids)` sem seguir links; (2) ATS embutido → `EmbeddedAtsFound`; (3) `job_links` (ou `sitemap_links` se vazio) até `max_links` = `listed_ids`; busca só os ainda não conhecidos, até `max_new_pages`, 1º `JobPosting` de cada página (`base_url` = link); erro de uma página de vaga (`UnsafeURL`, `RobotsDisallowed`, `SourceBadResponse`, `SourceUnavailable`) pula só aquela; (4) sem nenhuma vaga nova nem conhecida → `NoStructuredData(MSG_NO_STRUCTURED)`. `SourceBlocked` sempre propaga. `async fetch_jobs(identifier)` = `fetch_listing(identifier, known_ids=frozenset()).raws`. `async probe(url, *, extra_paths: tuple[str, ...] = ()) -> ProbeResult` — página dada + `extra_paths` (≤ 5) até achar ATS (`"ats"`) ou JSON-LD em até 5 páginas de vaga (`"jsonld"`), senão `"none"`.
- [ ] **Step 1: Write failing tests** (MockTransport + resolvedor público fixo; HTML escrito à mão):
  - `test_jsonld_on_listing_page_no_link_following`.
  - `test_detects_embedded_greenhouse_iframe` (`iframe src="https://boards.greenhouse.io/embed/job_board?for=acme"` → `EmbeddedAtsFound(source="greenhouse", identifier="acme")`) e `test_detects_lever_link` (URL hospedada da fixture da Task 1).
  - `test_follows_same_domain_job_links_only` (3 links de vaga + 1 externo + `mailto:` → 3 páginas buscadas).
  - `test_link_cap_and_new_page_budget` (250 links → `len(listed_ids) == 200`, 25 páginas buscadas; com `known_ids` contendo os 25 primeiros → busca os 25 seguintes).
  - `test_sitemap_used_when_no_links` (robots com `Sitemap:`; só URLs sob `/careers/`), `test_sitemap_with_doctype_rejected`.
  - `test_no_jsonld_raises_no_structured_data` (mensagem == `MSG_NO_STRUCTURED`).
  - `test_job_page_resolving_private_is_skipped` (subdomínio do mesmo domínio resolvendo `10.0.0.1` é pulado; as demais vagas seguem) e `test_careers_url_private_raises_unsafe`.
  - `test_blocked_propagates` (403, 403 numa página de vaga → `SourceBlocked`).
  - `test_probe_kinds`.
- [ ] **Step 2: Run** `pytest tests/sources/test_careers.py -v` → FAIL. **Step 3: Implement.** **Step 4: Run** → PASS.
- [ ] **Step 5: Commit** `feat(sources): careers page reader with embedded ATS detection`.

### Task 33: `IngestPolicy` e deduplicação entre fontes

**Files:** Modify `rumo_core/sources/ingest.py`, `rumo_core/discovery/names.py`, `tests/sources/test_ingest.py`, `tests/discovery/test_names.py`.

**Interfaces:**
- Produces (`names.py`): `employer_key(name: str | None) -> str | None` — `"".join(core)` de `token_variants` (sem sufixo jurídico), `None` se vazio ou se o nome só tiver sufixos.
- Produces (`ingest.py`):
  - `@dataclass(frozen=True) IngestPolicy(origin: str = "ats", source_rank: int = 0, close_missing: bool = True, listed_ids: frozenset[str] = frozenset(), filters: SearchFilters | None = None, keywords: tuple[str, ...] = ())` (`filters`/`keywords` só têm efeito a partir da Task 47).
  - `ingest_jobs(session, user_id, company, raws, *, now, policy: IngestPolicy = IngestPolicy()) -> IngestResult` — vagas criadas/alteradas recebem `origin`/`source_rank` da política; `close_missing=False` → nenhuma vaga fecha por ausência e a guarda de lista vazia não se aplica; ausente de `raws` mas em `listed_ids` → continua como está, `last_seen_at = now`.
  - `IngestResult` ganha `outranked: int = 0`, `stubs: int = 0`, `promoted: int = 0`.
  - Deduplicação entre fontes (no fim de `_ingest`, antes de recontar `open_jobs`): chave = (`employer_key(company.name)`, `fingerprint`); `employer_key` `None` → nunca deduplica entre empresas. `_outranked(session, user_id, company, rank, fps) -> set[str]` = fingerprints com vaga `open` (não `stub`) de **outra** empresa com a mesma chave e `source_rank` **menor**; os grupos dessas fingerprints ficam todos `duplicate` (contam em `outranked`). `_demote_higher(session, user_id, company, rank, fps) -> int` = vagas `open` de outras empresas com a mesma chave/fingerprint e rank **maior** viram `duplicate` (e `open_jobs` delas é recontado). Rank igual nunca deduplica. Usa o índice `ix_jobs_user_fingerprint`.
- [ ] **Step 1: Write failing tests** (`db`): `test_employer_key` (`"Acme Ltda."` e `"ACME"` → `"acme"`; `"Ltda"` → `None`; `""` → `None`); `test_policy_sets_origin_and_rank`; `test_close_missing_false_keeps_absent_open`; `test_listed_ids_keep_jobs_open`; `test_ats_outranks_feed_same_employer` (feed `"ACME"` rank 2 primeiro → `open`; ATS `"Acme Inc"` rank 0 com mesmo título/local → ATS `open`, feed `duplicate`; nova ingestão do feed → continua `duplicate`); `test_feed_promoted_when_ats_job_closes`; `test_same_fingerprint_other_employer_not_deduped`; `test_equal_rank_not_deduped`.
- [ ] **Step 2: Run** `pytest tests/sources/test_ingest.py tests/discovery/test_names.py -v` → FAIL. **Step 3: Implement.** **Step 4: Run** `pytest tests/sources tests/test_worker_collect.py -v` → PASS (ingestão antiga intacta).
- [ ] **Step 5: Commit** `feat(sources): ingest policy and cross-source dedupe by source rank`.

### Task 34: Coleta de empresas `site` no worker

**Files:** Modify `worker/collect_jobs.py`, `tests/test_worker_collect.py`.

**Interfaces:**
- Consumes: `SiteSource.fetch_listing`, `EmbeddedAtsFound`, `NoStructuredData`, `SourceBlocked`, `RobotsDisallowed`, `portal_for_url`, `check`, `load_states`, `mark_blocked_by_site`, `IngestPolicy`, `source_rank_for`.
- Produces:
  - `MSG_BLOCKED = "O site recusou nosso acesso repetidamente; a coleta desta fonte foi suspensa."`, `MSG_ROBOTS = "O robots.txt do site não permite a coleta desta página."`, `MSG_NO_STRUCTURED` (reexportado), `MSG_CONVERTED = "Página de carreira usa {ats}; a empresa passou a ser coletada direto pela API."`.
  - `_collect_one` passa a montar `IngestPolicy(origin="site" if source == "site" else "ats", source_rank=source_rank_for(source))`; para `site`: checa o portão se a URL for de um portal (não permitido → falha com `verdict.message`, sem requisição), lê os `external_id` conhecidos da empresa e chama `fetch_listing`; `listed_ids` vão para a política.
  - `EmbeddedAtsFound` → `_convert_company(session_factory, company_id, source, identifier, now)`: se não existe empresa do usuário com `(source, identifier)`, a própria linha muda `source`/`identifier`, `next_run_at=now`, `last_error=MSG_CONVERTED` formatado; se existe, a empresa `site` fica `active=False` com a mesma mensagem.
  - `SourceBlocked` → empresa `active=False`, `last_error=MSG_BLOCKED`; se o host for de um portal, `mark_blocked_by_site`. `NoStructuredData` → `active=False`, `last_error=MSG_NO_STRUCTURED`. `RobotsDisallowed` → falha com `MSG_ROBOTS` e backoff normal.
- [ ] **Step 1: Write failing tests** (`db`, fonte falsa): `test_site_company_ingests_listing_with_listed_ids`, `test_site_converted_to_embedded_ats`, `test_site_conversion_when_ats_company_exists_pauses_site`, `test_blocked_site_pauses_company_with_message`, `test_no_structured_data_pauses_with_message`, `test_portal_site_not_collected_when_gate_closed` (empresa `site` em `https://www.catho.com.br/...` sem `portal_state` → 0 chamadas à fonte, `last_error` = mensagem de `conditional_off`), `test_ats_company_gets_rank0_origin_ats`.
- [ ] **Step 2: Run** `pytest tests/test_worker_collect.py -v` → FAIL. **Step 3: Implement.** **Step 4: Run** → PASS.
- [ ] **Step 5: Commit** `feat(worker): collect careers pages and convert embedded ATS`.

### Task 35: Importar por link (núcleo + `POST /jobs/import-link`)

**Files:** Create `rumo_core/sources/link_import.py`, `tests/sources/test_link_import.py`, `tests/test_api_import_link.py`; Modify `api/sources.py`.

**Interfaces:**
- Produces (`link_import.py`):
  - `class NeedsPastedText(Exception)` com `reason: Literal["no_structured_data", "robots", "refused", "unreachable"]`.
  - `MAX_TITLE = 300`, `MAX_DESCRIPTION = 50_000`, `LINK_COMPANY_FALLBACK = "_sem_empresa"`.
  - `async read_link(client: SafeClient, url: str) -> RawJob` — `kind="page"` (respeita robots: importar por link nunca ignora o `robots.txt`; quando ele proíbe, a pessoa cola o texto), 1º `JobPosting` da página (`base_url=url`); sem JSON-LD → `NeedsPastedText("no_structured_data")`; `RobotsDisallowed` → `"robots"`; `SourceBlocked`/403 → `"refused"`; `SourceUnavailable` → `"unreachable"`. `UnsafeURL` propaga.
  - `pasted_raw(url: str, title: str, description: str) -> RawJob` — `external_id` = `sha256(canonical_url(url))` hex; descrição como texto escapado em `<p>` (nunca HTML da pessoa).
  - `save_imported_job(session, user_id: int, url: str, raw: RawJob, *, now: datetime) -> tuple[Job, bool]` — empresa sintética `Company(source="link", identifier=employer_key(nome) or LINK_COMPANY_FALLBACK, name=nome or "Vagas importadas por link", origin="link", active=False)` (upsert pela unique); vaga upsert por `external_id`: `origin="link"`, `source_rank=1`, `intake="full"` (escolha humana, sem funil); nunca fecha outras vagas; dedupe entre fontes da Task 33 vale. Retorna `(job, created)`. Não comita.
- Produces (`api/sources.py`): `class ImportLinkIn(BaseModel, extra="forbid")`: `url: str` (≤ 2048), `title: str | None` (≤ 300), `description: str | None` (≤ 50 000); título e descrição juntos ou nenhum (422 `"Informe o título e a descrição juntos."`). `POST /jobs/import-link` → 201 `{"job_id": int, "created": true}` / 200 `{"job_id", "created": false}`; `NeedsPastedText` → 422 `{"detail": {"code": "needs_text", "reason": <reason>, "message": "Não consegui ler esta vaga automaticamente. Cole o título e a descrição."}}`; `UnsafeURL` → 422 `"Endereço não permitido."`; `SourceUnavailable` → 503; tempo > 25 s → 504. Limite de 20 importações por hora por usuário (por processo) → 429 `"Muitas importações em pouco tempo. Tente de novo mais tarde."`. Nunca loga a URL. Dependência `safe_client()` sobrescrevível nos testes.
- [ ] **Step 1: Write failing tests**: núcleo (`db` onde grava) — `test_read_link_jsonld`, `test_read_link_without_jsonld_needs_text`, `test_read_link_robots_needs_text`, `test_read_link_private_ip_unsafe`, `test_pasted_text_is_escaped` (`"<script>x</script>"` → `normalize_job` sem tag e sem `x()`), `test_save_imported_job_idempotent` (2ª vez → `created False`, 1 vaga), `test_imported_job_full_rank1_origin_link`, `test_link_company_from_hiring_org`; API — `test_import_link_201_then_200`, `test_import_link_needs_text_422_code`, `test_import_link_with_pasted_text_skips_fetch` (0 requisições), `test_import_link_title_without_description_422`, `test_import_link_user_scoped`, `test_import_link_rate_limited` (21ª → 429), `test_import_link_works_for_blocked_portal` (URL `linkedin.com` com JSON-LD mockado → 201: o portão não barra ação manual).
- [ ] **Step 2: Run** `pytest tests/sources/test_link_import.py tests/test_api_import_link.py -v` → FAIL. **Step 3: Implement.** **Step 4: Run** → PASS.
- [ ] **Step 5: Commit** `feat(sources): import a job by link with pasted-text fallback`.

---

## Fatia 4 — Feeds, empregadores sintéticos e descoberta ampliada

### Task 36: Protocolo de feeds + Remotive, RemoteOK e Arbeitnow

**Files:** Create `rumo_core/sources/feeds/{__init__,base,remotive,remoteok,arbeitnow}.py`, `tests/sources/feeds/test_{remotive,remoteok,arbeitnow,base}.py`.

**Interfaces:**
- Produces (`feeds/base.py`):
  - `@dataclass(frozen=True) FeedParams(keywords: tuple[str, ...] = (), country: str | None = None)` + `FeedParams.from_dict(d: dict) -> FeedParams` (≤ 5 palavras de 1–60 caracteres, aparadas, sem duplicadas sem caixa; `country` via `norm_country`; chaves extras → `ValueError`) e `.to_dict()` (ordem estável, para o `md5(params)` único).
  - `@dataclass(frozen=True) FeedItem(raw: RawJob, employer_name: str | None, employer_url: str | None)`.
  - `@dataclass(frozen=True) Attribution(label: str, url: str)`.
  - `class FeedSource(Protocol)`: `name: str`, `needs_key: bool`, `attribution: Attribution`, `key_configured(settings) -> bool`, `async fetch(params: FeedParams, *, known_ids: frozenset[str] = frozenset()) -> list[FeedItem]`, `async aclose()`.
  - `register_feed(name, factory)`, `get_feed(name) -> FeedSource` (cache como `get_source`), `feed_names() -> list[str]`.
- Produces: `RemotiveFeed` (`https://remotive.com/api/remote-jobs`, sem parâmetros — os da API não foram verificados; `jobs[]`: `id`, `url`, `title`, `company_name`, `category`→`department`, `candidate_required_location`→`location_text`, `publication_date`, `description`, `salary` (texto → `<p>Salary: …</p>`); `work_model_hint="remote"`; atribuição `Attribution("Vagas via Remotive", "https://remotive.com")`); `RemoteOKFeed` (`https://remoteok.com/api`; ignora elementos sem `id`/`position` — o 1º é o aviso legal; `id`, `position`, `company`, `location`, `description`, `url` (link de volta exigido), `date`/`epoch`; `salary_min/max` **não** mapeados — moeda não verificada; hint `remote`; `Attribution("Vagas via Remote OK", "https://remoteok.com")`); `ArbeitnowFeed` (`https://www.arbeitnow.com/api/job-board-api`, só a 1ª página; `data[]`: `slug`→id, `company_name`, `title`, `description`, `remote` (`True` → hint `remote`), `url`, `location`, `created_at` no tipo registrado; `Attribution("Vagas via Arbeitnow", "https://www.arbeitnow.com")`). Todos `kind="api"`, `min_interval`/`daily_cap` do `policy.json`, `needs_key=False`.
- [ ] **Step 1: Write failing tests**: por feed `test_parses_real_fixture` (itens, empregador, hint, URL de volta), `test_bad_shape`, `test_items_without_id_or_title_skipped`; `remoteok`: `test_legal_notice_element_skipped`; `base`: `test_feed_params_validation` (6 palavras → erro; `"  dados "` → `"dados"`; `{"x":1}` → erro; país `"br"` → `"BR"`), `test_feed_params_stable_dict`.
- [ ] **Step 2: Run** `pytest tests/sources/feeds -v` → FAIL. **Step 3: Implement.** **Step 4: Run** → PASS.
- [ ] **Step 5: Commit** `feat(sources): aggregator feed protocol with Remotive, Remote OK and Arbeitnow`.

### Tasks 37–41: Feeds condicionais (só se a verificação passou)

Mesmo padrão da Task 36 (módulo em `rumo_core/sources/feeds/`, teste com a fixture registrada, `register_feed`), exatamente com o formato de `VERIFICATION.md`. Verificação falhou → pulada. Commit `feat(sources): <Nome> feed`.
- **Task 37: Himalayas** (Task 10) — 1ª página apenas; `applicationLink` intacto como `url`; `Attribution("Vagas via Himalayas", "https://himalayas.app")`.
- **Task 38: Bundesagentur** (Task 11) — `X-API-Key: jobboerse-jobsuche`; `was` = 1ª palavra-chave, `size=50`, `page=1`; só Alemanha (`country_hint="DE"`); sem descrição (detalhe N+1 no backlog); portal `conditional` (só roda ligado).
- **Task 39: Adzuna**, **Task 40: Jooble**, **Task 41: Careerjet** (Task 12) — `needs_key=True`, `key_configured` lê `adzuna_app_id`+`adzuna_app_key` / `jooble_key` / `careerjet_key`; a chave nunca aparece em exceção, log ou `repr` (teste `test_key_never_in_errors`: erro 500 mockado → `str(exc)` sem a chave); Careerjet envia `user_ip` e `user_agent` (o do sistema).

### Task 42: Empregadores sintéticos e ingestão de feed

**Files:** Create `rumo_core/sources/feed_ingest.py`, `tests/sources/test_feed_ingest.py`; Modify `rumo_core/discovery/__init__.py`.

**Interfaces:**
- Consumes: `FeedItem`, `employer_key`, `token_variants`, `ingest_jobs`/`IngestPolicy`, `DiscoveryCandidate`, `UserPreference.discovery_enabled`.
- Produces:
  - `UNKNOWN_EMPLOYER = "_desconhecida"`.
  - `rumo_core.discovery.DISCOVERY_SOURCES: tuple[str, ...]` (criado aqui em `rumo_core/discovery/__init__.py`) = `("greenhouse", "lever", "ashby", "smartrecruiters", "breezy")` + condicionais aprovados nas Tasks 22–29.
  - `synthetic_company(session, user_id: int, feed: str, employer_name: str | None) -> Company` — upsert por `(user_id, source=feed, identifier=employer_key(nome) or UNKNOWN_EMPLOYER)`, `origin="feed"`, `active=False`, `name` = nome aparado a 255 ou `"Empresa não informada"`.
  - `ingest_feed_items(session, user_id: int, feed: str, items: list[FeedItem], *, now: datetime, filters: SearchFilters | None, keywords: tuple[str, ...]) -> FeedIngestResult` (`companies`, `created`, `stubs`, `candidates`) — agrupa por empresa sintética, `ingest_jobs(..., policy=IngestPolicy(origin="feed", source_rank=2, close_missing=False, filters=filters, keywords=keywords))`; empresas `_desconhecida` nunca deduplicam entre empresas (já garantido por `employer_key=None`). Não comita.
  - `seed_feed_candidates(session, user_id: int, employers: Iterable[tuple[str, str | None]]) -> int` — só com descoberta ligada; para cada nome com `employer_key`, um `DiscoveryCandidate(origin_kind="feed", source=<ats>, token=<variante>, name_hint=nome)` por fonte de `DISCOVERY_SOURCES` (`on_conflict_do_nothing`); com `employer_url` `http(s)`, mais um `source="site"`, `token=registrable_domain(host)` (≤ 64). Retorna inseridos.
  - `close_stale_feed_jobs(session, user_id: int, feed: str, *, now: datetime, ttl: timedelta) -> int` — vagas `open`/`duplicate` de empresas `origin="feed"` desse `source` com `last_seen_at < now - ttl` → `closed`.
- [ ] **Step 1: Write failing tests** (`db`): `test_items_grouped_into_synthetic_companies` (2 empregadores → 2 empresas `active=False`, `origin="feed"`), `test_unknown_employer_never_cross_deduped` (Review Focus 5: item sem empresa e item `"Ltda"` → `_desconhecida`; uma vaga ATS de outra empresa com o mesmo título/local continua `open` e a do feed também), `test_feed_jobs_rank2_origin_feed`, `test_feed_absence_never_closes`, `test_close_stale_feed_jobs_after_ttl` (8 dias → fecha; 6 → não), `test_seed_feed_candidates_per_ats_and_site` (nome `"Acme Ltda"` com URL `https://www.acme.com.br/` → candidatos `acme` por ATS + `site:acme.com.br`; descoberta desligada → 0), `test_seed_is_idempotent`.
- [ ] **Step 2: Run** `pytest tests/sources/test_feed_ingest.py -v` → FAIL. **Step 3: Implement.** **Step 4: Run** → PASS.
- [ ] **Step 5: Commit** `feat(sources): synthetic employers and discovery candidates from feeds`.

### Task 43: Worker `collect_feeds`

**Files:** Create `worker/collect_feeds.py`, `tests/test_worker_collect_feeds.py`; Modify `worker/main.py`, `tests/test_worker.py`.

**Interfaces:**
- Consumes: `get_feed`, `load_policy`, `check`, `load_states`, `mark_blocked_by_site`, `ingest_feed_items`, `seed_feed_candidates`, `close_stale_feed_jobs`, `funnel_filters` (Task 47; até lá `None`).
- Produces:
  - `async collect_feeds(session_factory, *, feed_getter=get_feed, user_id: int | None = None, clock=_utcnow, sleep=asyncio.sleep, interval_hours: int, delay_seconds: float, ttl_days: int, max_feeds: int | None = None, max_seconds: float | None = None, settings=None) -> int` — mesmo padrão de `collect_companies` (claim `FOR UPDATE SKIP LOCKED` + lease de 30 min commitado, rede sem transação, gravação numa transação via `asyncio.to_thread`).
  - Antes da rede: portão do `source` (não permitido → `last_error = verdict.message`, `next_run_at = now + interval`, sem requisição); `needs_key` sem chave → `last_error = "Chave da API não configurada no .env."`, idem.
  - Sucesso: `ingest_feed_items` + `seed_feed_candidates` + `close_stale_feed_jobs`; `jobs_seen = len(items)`, `last_error=None`, `consecutive_failures=0`, `next_run_at = now + max(interval_hours h, policy.min_interval_seconds s)`. Falha: backoff de `collect_jobs._backoff`; `SourceBlocked` → `active=False`, `last_error=MSG_BLOCKED`, `mark_blocked_by_site`.
  - `build_collect_feeds_job(settings, session_factory)` em `worker/main.py`; registrado como `"collect_feeds"` a cada 60 s **logo depois** de `collect_companies` (mesma fila, mesma justiça com a entrevista), `max_feeds=settings.collect_feeds_max_per_run`, `max_seconds=settings.collect_max_seconds`.
- [ ] **Step 1: Write failing tests** (`db`, feeds falsos): `test_due_feed_collected_and_rescheduled` (com `interval_hours=1`: `remotive` → `next_run_at = now + 21600 s` (política), `remoteok` → `now + 1 h`), `test_conditional_feed_skipped_until_enabled` (`bundesagentur` sem estado → 0 chamadas, `last_error` com a mensagem `conditional_off`; ligado → chamado), `test_keyed_feed_without_key_skipped`, `test_blocked_feed_paused_and_portal_marked`, `test_feed_failure_backoff`, `test_two_feeds_same_host_respect_min_interval` (Review Focus 3: dois feeds `remotive` devidos com o `SafeClient` real sobre `MockTransport` → 1 requisição; o 2º falha com `SourceUnavailable` e entra em backoff), `test_registry_order` (em `test_worker.py`: `"collect_feeds"` logo após `"collect_companies"`).
- [ ] **Step 2: Run** `pytest tests/test_worker_collect_feeds.py tests/test_worker.py -v` → FAIL. **Step 3: Implement.** **Step 4: Run** → PASS.
- [ ] **Step 5: Commit** `feat(worker): collect aggregator feeds behind the compliance gate`.

### Task 44: Descoberta ampliada (catálogo por ATS, filtros duros na validação, origem `feed`)

**Files:** Modify `rumo_core/discovery/{__init__,catalog,candidates,validate}.py`, `worker/discover_companies.py`, `pyproject.toml` (package-data já cobre `catalog/*.json`); Create `rumo_core/discovery/catalog/{lever,ashby,smartrecruiters,breezy}.json` (`[]`); Modify `tests/discovery/test_{catalog,candidates,validate}.py`, `tests/test_worker_discover.py`, `docs/superpowers/backlog.md`.

**Interfaces:**
- Produces:
  - Consome `DISCOVERY_SOURCES` (Task 42).
  - `load_catalog(source: str = "greenhouse", path=None) -> list[CatalogEntry]`; `CatalogEntry` ganha `country: str | None = None` (catálogo "por país": entrada opcional `country` ISO-2). Arquivos novos começam vazios — só entram tokens confirmados por resposta real (popular = backlog).
  - `refresh_candidates(session, user_id, *, source)` usa `load_catalog(source)`; candidatos `feed` nunca são apagados pela reconciliação.
  - `validate_candidate(source, token, *, accept: Callable[[RawJob], bool] | None = None, timeout=...) -> Outcome` — `open_jobs` = vagas aceitas; havia vagas mas nenhuma aceita → `Outcome("invalid", 0, name, None)` (recheck em 30 dias, como quadro vazio). `Outcome` ganha `resolved: tuple[str, str] | None = None`.
  - Worker: `_maybe_refresh` chama `refresh_candidates` para cada fonte de `DISCOVERY_SOURCES`; `_ORIGIN_PRIORITY`: `target` 0, `profile` 1, `feed` 2, `catalog` 3; `accept` construído dos filtros do usuário (`decide_intake(...) == "full"`, Task 47; até lá aceita tudo); `_record_valid` cria a empresa com `outcome.resolved` quando houver.
- [ ] **Step 1: Write failing tests**: `test_load_catalog_per_source` (arquivo vazio → `[]`; entrada com `country`), `test_refresh_per_source_tokens` (perfil `"Acme"` → candidatos `acme` em cada fonte), `test_feed_candidates_survive_refresh`, `test_validate_requires_accepted_job` (2 vagas, `accept` recusa ambas → `invalid` sem erro; aceita 1 → `valid`, `open_jobs == 1`), `test_feed_origin_priority_between_profile_and_catalog`, `test_valid_lever_candidate_creates_lever_company`.
- [ ] **Step 2: Run** `pytest tests/discovery tests/test_worker_discover.py -v` → FAIL. **Step 3: Implement** (+ item no backlog: "popular `catalog/<ats>.json` com tokens confirmados"). **Step 4: Run** → PASS.
- [ ] **Step 5: Commit** `feat(discovery): per-ATS catalogs, hard-filter validation and feed candidates`.

### Task 45: Candidatos de página de carreira (`site`) vindos do empregador

**Files:** Modify `rumo_core/discovery/validate.py`, `worker/discover_companies.py`, `tests/discovery/test_validate.py`, `tests/test_worker_discover.py`.

**Interfaces:**
- Produces: `CAREER_PATHS = ("/careers", "/jobs", "/carreiras", "/vagas", "/trabalhe-conosco")`; `async validate_site_candidate(site: SiteSource, domain: str, *, accept=None, timeout=...) -> Outcome` — `site.probe(f"https://{domain}/", extra_paths=CAREER_PATHS)`: `"ats"` → `Outcome("valid", n, None, resolved=(fonte, id))` depois de validar esse ATS com `validate_candidate` (mesmo `accept`); `"jsonld"` com ≥ 1 vaga aceita → `valid`, `resolved=("site", url_da_pagina)`; `"none"` → `invalid`. Domínio de portal (`portal_for_url`) → `invalid` sem rede. O worker escolhe `validate_site_candidate` quando `candidate.source == "site"`.
- [ ] **Step 1: Write failing tests**: `test_site_candidate_resolves_embedded_ats`, `test_site_candidate_jsonld_creates_site_company`, `test_site_candidate_none_invalid`, `test_portal_domain_never_probed`.
- [ ] **Step 2: Run** → FAIL. **Step 3: Implement.** **Step 4: Run** `pytest tests/discovery tests/test_worker_discover.py -v` → PASS.
- [ ] **Step 5: Commit** `feat(discovery): find careers pages of feed employers`.

### Task 46: Coletor de portal por sitemap (só se a Task 13 aprovou o portal)

Condicional: só para portais com veredito "passou" (Vagas.com e/ou Catho). Senão, pulada.

**Files:** Create `rumo_core/sources/feeds/portal_sitemap.py`, `tests/sources/feeds/test_portal_sitemap.py`.

**Interfaces:**
- Produces: `PortalSitemapFeed(portal_id: str, client: SafeClient, *, pages_per_run: int = 5)` registrado como feed `portal_id` (`kind="portal"`; `needs_key=False`); `fetch(params, *, known_ids)` lê o sitemap do portal (`sitemap_links`, só `paths_allowed`), descarta `known_ids`, busca até `pages_per_run` páginas novas (robots, `min_interval_seconds`, `daily_cap` do `policy.json`), 1º `JobPosting` de cada uma → `FeedItem(employer_name=employer_name(raw))`. Nunca toca páginas de busca. O `collect_feeds` passa `known_ids` (external_ids já gravados para esse `source`) e agenda portais a cada 15 min.
- [ ] **Step 1: Write failing tests**: `test_only_allowed_paths_fetched` (`/vagas/pesquisas` no sitemap → ignorado), `test_known_urls_not_refetched`, `test_page_budget_per_run`, `test_portal_feed_requires_gate_enabled` (via `collect_feeds`).
- [ ] **Step 2: Run** → FAIL. **Step 3: Implement.** **Step 4: Run** → PASS.
- [ ] **Step 5: Commit** `feat(sources): sitemap collector for conditional portals`.

---

## Fatia 5 — Funil de aderência

### Task 47: `funnel.hard_filter` com paridade Python ↔ SQL e intake na ingestão

**Files:** Create `rumo_core/sources/funnel.py`, `tests/sources/test_funnel.py`, `tests/sources/test_funnel_parity.py`; Modify `rumo_core/sources/ingest.py`, `worker/collect_jobs.py`, `worker/collect_feeds.py`, `worker/discover_companies.py`, `tests/sources/test_ingest.py`.

**Interfaces:**
- Consumes: `SearchFilters`, `apply_filters` (referência da paridade), `NormalizedJob`, `fold_term`.
- Produces (`funnel.py`):
  - `@dataclass(frozen=True) FilterView(title: str, country: str | None, work_model: str, seniority: str, language: str, department: str | None, salary_known: bool, salary_min: Decimal | None, salary_max: Decimal | None, salary_currency: str | None, salary_period: str | None)`; `view_of(raw: RawJob, n: NormalizedJob) -> FilterView`; `view_of_job(job: Job) -> FilterView`.
  - `fold_like_unaccent(text: str) -> str` — NFKD sem marcas + mapa `{"ß":"ss","ø":"o","Ø":"O","ł":"l","Ł":"L","đ":"d","Đ":"D","æ":"ae","Æ":"AE","œ":"oe","Œ":"OE","þ":"th","ð":"d"}`, depois `.lower()` (espelha `lower(unaccent(x))`).
  - `hard_filter(view: FilterView, f: SearchFilters) -> tuple[bool, str | None]` — mesma semântica e ordem de `apply_filters`; motivos (≤ 32): `"country"`, `"work_model"`, `"seniority"`, `"language"`, `"department"`, `"exclude_country"`, `"exclude_work_model"`, `"exclude_seniority"`, `"exclude_language"`, `"exclude_department"`, `"title_exclude"`, `"salary"`. Salário: comparável = `salary_known` e mesma moeda e `salary_min` não nulo; anual = `(salary_max se não nulo senão salary_min) × {month:12, hour:2080}.get(period, 1)`; desconhecido/sem salário seguem `include_unknown`/`accept_no_salary` como no SQL.
  - `decide_intake(view: FilterView, filters: SearchFilters | None, *, keywords: tuple[str, ...] = (), text: str = "") -> tuple[Literal["full", "stub"], str | None]` — `filters is None` → `("full", None)`; falha → `("stub", motivo)`; `keywords` não vazias e nenhuma contida em `fold_like_unaccent(title + " " + text)` → `("stub", "feed_keywords")`.
  - `funnel_filters(session, user_id: int) -> SearchFilters | None` — filtros do `SearchProfile` padrão; sem linha → `SearchFilters()`; JSON inválido → `None` (funil desligado, aviso no log só com o `user_id`).
- Produces (`ingest.py`, com `policy.filters`): vaga nova/alterada → `decide_intake`; `stub` grava `description=""`, `raw={}`, `embedding=None`, `llm_filled_at=now`, `stub_reason`; stub com conteúdo igual que agora passa → aplica tudo (`_apply`), `intake="full"`, `stub_reason=None`, zera vetor/preenchimento (**promoção**); vaga `full`/`gated` com conteúdo igual nunca é rebaixada; stubs ficam fora dos grupos de `fingerprint` e da dedupe entre fontes, mantêm `status` de presença (`open`/`closed`) e não contam em `open_jobs`. Contadores `stubs`/`promoted`.
- Workers: `collect_jobs`/`collect_feeds` passam `filters = funnel_filters(...)` quando `company.collect_mode == "funnel"` (empresa) — feeds sempre `funnel`; `collect_mode == "all"` → `filters=None`. `discover_companies` monta `accept` com `decide_intake(...)[0] == "full"`.
- [ ] **Step 1: Write failing tests**:
  - `test_funnel_parity.py` (`db`): ~30 vagas cobrindo cada campo, `unknown`/NULL, departamento com caixa/espaços, títulos `"Engenheiro (PLANTÃO)"`, `"Straße Planner"`, `"Øresund Lead"`, `"Bonus 100% remoto"`, `"a_b dev"`, salários `year`/`month`/`hour`, moeda diferente, `salary_max` nulo, igualdade na fronteira; tabela de ~20 `SearchFilters` (cada inclusão com `include_unknown` True/False, cada exclusão, `title_exclude` `["plantao"]`, `["strasse"]`, `["oresund"]`, `["100%"]`, `["a_b"]`, `salary_min=100000 USD` com `accept_no_salary` False, combinações). `test_python_matches_sql[case]`: `set(ids de apply_filters)` == `{j.id for j if hard_filter(view_of_job(j), f)[0]}`.
  - `test_funnel.py`: `test_reason_codes` (um caso por motivo), `test_keywords_filter`, `test_decide_intake_none_filters_full`, `test_funnel_filters_invalid_returns_none` (`db`).
  - `test_ingest.py` (`db`): `test_job_failing_filters_becomes_stub` (descrição `""`, `raw == {}`, sem vetor, `llm_filled_at` preenchido, `stub_reason == "work_model"`, `open_jobs == 0`), `test_recollect_promotes_stub_after_filter_change`, `test_full_job_not_demoted_when_unchanged`, `test_changed_content_reevaluated_to_stub`, `test_stub_excluded_from_fingerprint_groups`.
  - `tests/test_worker_collect.py`: `test_collect_mode_all_keeps_everything_full`, `test_invalid_saved_filters_disable_funnel` (Review Focus 2: `SearchProfile.filters = {"countries": 5}` → todas `full`).
- [ ] **Step 2: Run** `pytest tests/sources/test_funnel.py tests/sources/test_funnel_parity.py tests/sources/test_ingest.py tests/test_worker_collect.py -v` → FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** mesmo comando + `pytest tests/matching/test_filters.py -v` → PASS.
- [ ] **Step 5: Commit** `feat(sources): hard-filter funnel at intake with SQL parity`.

### Task 48: Stubs e `gated` fora de vetorização, preenchimento, ranking, avaliação e listas

**Files:** Modify `worker/collect_jobs.py` (`pending_jobs_stmt`, `_select_fill_candidates`, `_load_fill_view`, `_write_fill`), `worker/match_jobs.py` (`_select_candidates`, `_load`, `_write`), `rumo_core/matching/ranking.py`, `rumo_core/matching/refresh.py`, `api/sources.py` (`list_jobs`, `job_detail`), `api/overview.py`; tests `tests/test_worker_embed_jobs.py`, `tests/test_worker_fill_jobs.py`, `tests/test_worker_match.py`, `tests/matching/test_ranking.py`, `tests/matching/test_refresh.py`, `tests/test_api_sources.py`, `tests/test_api_overview.py`.

**Interfaces:**
- Produces (`ranking.py`): `VISIBLE_INTAKES = ("full", "gated")`; `_base_jobs(..., intakes: Collection[str] = VISIBLE_INTAKES)` acrescenta `Job.intake.in_(intakes)`; `rank_jobs(..., intakes=VISIBLE_INTAKES)`, `top_candidate_ids(..., intakes=VISIBLE_INTAKES, only_ids: Collection[int] | None = None)`, `job_vec_score` inalterado (sem filtro de intake).
- `mark_pending(...)` chama `top_candidate_ids(..., intakes=("full",))`.
- `pending_jobs_stmt`: `Job.intake != "stub"`. Preenchimento: `Job.intake == "full"` e, quando o usuário tem `adherence_vec_floor` (outer join `UserPreference`), só vagas já vetorizadas (`Job.embedding.is_not(None)`) — assim o corte da Task 49 roda antes do LLM; `_load_fill_view`/`_write_fill` rechecam `intake == "full"`. Avaliação: `_select_candidates` filtra `Job.intake == "full"`; `_load`/`_write` rechecam.
- API: `list_jobs` exclui `stub` (inclusive `status=all`); `job_detail` de stub → 404; `JobItem` ganha `origin: str`, `source: str` (fonte da empresa) e `attribution: {label, url} | None` (do feed). Overview: `JobsOverview.open` conta só `full`/`gated`; `JobsOverview.outside_filters: int` = stubs `open`.
- [ ] **Step 1: Write failing tests** (`db`): `test_stub_not_embedded`, `test_stub_and_gated_not_filled`, `test_fill_waits_for_embedding_when_floor_set`, `test_stub_and_gated_not_scored` (linha `pendente` de vaga `gated` é ignorada), `test_mark_pending_only_full`, `test_rank_excludes_stub_includes_gated`, `test_jobs_list_hides_stubs`, `test_stub_detail_404`, `test_job_item_attribution_for_feed_job`, `test_overview_outside_filters`.
- [ ] **Step 2: Run** os arquivos de teste acima → FAIL. **Step 3: Implement.** **Step 4: Run** → PASS (incluindo `tests/matching/test_quality.py`).
- [ ] **Step 5: Commit** `feat(matching): keep stub and gated jobs away from the LLM`.

### Task 49: Corte vetorial (`apply_vec_gate`) e `suggest_floor`

**Files:** Create `rumo_core/matching/gate.py`, `tests/matching/test_gate.py`; Modify `rumo_core/matching/quality.py`, `tests/matching/test_quality.py`, `worker/collect_jobs.py` (`embed_pending_jobs`), `tests/test_worker_embed_jobs.py`.

**Interfaces:**
- Produces (`gate.py`): `@dataclass(frozen=True) GateResult(gated: int, restored: int)`; `apply_vec_gate(session, user_id: int, *, model_version: str, floor: float | None, job_ids: Collection[int] | None = None) -> GateResult` — vagas `open` `full`/`gated` de empresas `collect_mode='funnel'`, com vetor; `vec_score` por `top_candidate_ids(SearchFilters(), intakes=("full","gated"), n=2**31-1, only_ids=job_ids, query_embedding=usable_query_embedding(...))`; `full` com score `< floor` → `gated`; `gated` com score `>= floor` (ou `floor is None` → todas) → `full` (e `llm_filled_at=None`, `fill_attempts=0` para voltar ao preenchimento). Sem perfil utilizável → `GateResult(0, 0)`. Não comita.
- `embed_pending_jobs`: depois de cada lote comitado, para cada usuário do lote com `adherence_vec_floor` não nulo, `apply_vec_gate(job_ids=ids do lote)` e commit.
- Produces (`quality.py`): `FLOOR_KEEP = 0.95`; `floor_from_scores(scores: Sequence[float], keep: float = FLOOR_KEEP) -> float | None` — `None` se `len(scores) < MIN_EACH`; ordena asc., `k = floor((1 - keep) * n + 1e-9)`, valor `sorted[k]` arredondado **para baixo** a 3 casas; `suggest_floor(session, user_id: int, *, model_version: str) -> float | None` — scores das vagas rotuladas `good` (via `top_candidate_ids(..., intakes=("full","gated"), only_ids=boas, status="all")`) → `floor_from_scores`. (Assinatura ganha `model_version`, como todo o `quality.py`.)
- [ ] **Step 1: Write failing tests**: `test_floor_from_scores` (`[0.50, 0.51, …, 0.69]` (20) → `0.51` e ≥ 95% ficam; 9 valores → `None`; 40 valores → índice 2; `0.6789` → `0.678`); `test_suggest_floor_needs_ten_good_labels` (`db`); `test_gate_moves_below_floor_to_gated_and_back` (`db`, vetores `e(i)` do 4b: floor alto → `gated`; `None` → `full` com `llm_filled_at` limpo); `test_gate_skips_collect_mode_all`; `test_gated_keeps_vector`; `test_embed_job_applies_gate_when_floor_set`.
- [ ] **Step 2: Run** `pytest tests/matching/test_gate.py tests/matching/test_quality.py tests/test_worker_embed_jobs.py -v` → FAIL. **Step 3: Implement.** **Step 4: Run** → PASS.
- [ ] **Step 5: Commit** `feat(matching): optional vector floor gate and label-based suggestion`.

### Task 50: Reavaliação quando os filtros mudam

**Files:** Modify `rumo_core/sources/funnel.py`, `api/matching.py` (`save_filters`), `tests/sources/test_funnel.py`, `tests/test_api_matching.py`.

**Interfaces:**
- Produces: `mark_for_reevaluation(session, user_id: int) -> tuple[int, int]` — `UPDATE companies SET next_run_at = now()` onde `user_id`, `active`, `collect_mode='funnel'` e existe vaga `stub` `open` da empresa; `UPDATE job_feeds SET next_run_at = now()` onde `user_id`, `active` e existe stub `open` em empresa `origin='feed'` com `source = job_feeds.source`. Retorna `(empresas, feeds)`. Não comita.
- `save_filters`: lê os filtros guardados antes do upsert; se `filters.model_dump(mode="json")` mudou, chama `mark_for_reevaluation` na mesma transação.
- [ ] **Step 1: Write failing tests** (`db`): `test_filter_change_marks_companies_with_stubs` (com stub → `next_run_at <= now`; sem stub → intocada; `collect_mode='all'` → intocada), `test_filter_change_marks_feeds_with_stubs`, `test_same_filters_do_not_mark`, `test_put_search_profile_triggers_reevaluation` (API).
- [ ] **Step 2: Run** → FAIL. **Step 3: Implement.** **Step 4: Run** `pytest tests/sources/test_funnel.py tests/test_api_matching.py tests/test_api_chat_filters.py -v` → PASS.
- [ ] **Step 5: Commit** `feat(sources): recollect stub sources when search filters change`.

---

## Fatia 6 — API e web

### Task 51: API de feeds, portais e funil

**Files:** Create `api/feeds.py`, `api/portals.py`, `api/funnel.py`, `tests/test_api_feeds.py`, `tests/test_api_portals.py`, `tests/test_api_funnel.py`; Modify `api/main.py`.

**Interfaces:**
- `api/feeds.py`: `GET /feeds` → `FeedsOut{feeds: list[FeedOut], sources: list[FeedSourceOut]}`; `FeedOut{id, source, params: {keywords, country}, active, last_run_at, next_run_at, last_error, jobs_seen, attribution: {label, url}}`; `FeedSourceOut{source, label, needs_key, key_configured: bool, gate_status, gate_allowed: bool, attribution}` (só fontes registradas; nunca o valor da chave). `POST /feeds` `FeedIn{source: str, params: dict}` → 201 `FeedOut`, `next_run_at=now`; erros: fonte desconhecida 422 `"Fonte desconhecida."`; `FeedParams.from_dict` inválido 422; sem chave 422 `"Chave da API não configurada no .env."`; portão `blocked` 409 com `verdict.message`; `conditional` desligado 409 com `verdict.message`; repetido 409 `"Esse feed já existe."`; > 20 feeds 422 `"Limite de 20 feeds atingido."`. `DELETE /feeds/{id}` → 204 (as vagas ficam); de outro usuário → 404.
- `api/portals.py`: `GET /portals` → `list[PortalOut{id, name, kind, status, checked_at, evidence_urls, evidence_summary, enabled, blocked_by_site, blocked_at, can_toggle}]` (`can_toggle = status == "conditional"`); `PUT /portals/{id}` `{enabled: bool}` → 404 desconhecido; 409 `"Só portais condicionais podem ser ligados ou desligados."`; ligar não limpa `blocked_by_site`.
- `api/funnel.py`: `GET /funnel` → `FunnelOut{full, stub, gated, floor: float | None, suggested_floor: float | None, good_labels: int}` (vagas `open`); `PUT /funnel` `{adherence_vec_floor: float | None}` (`0 ≤ x ≤ 1`, finito) → upsert em `UserPreference` + `apply_vec_gate` (em threadpool) + commit → `FunnelOut`.
- [ ] **Step 1: Write failing tests** (`db`, `TestClient` com `current_user_id` sobrescrito como nos testes atuais): feeds — `test_create_list_delete_feed`, `test_feed_errors` (cada mensagem acima), `test_feed_sources_hide_key_values` (com `adzuna_app_key="segredo"` no settings falso → `"segredo"` não aparece no corpo), `test_feeds_user_scoped`; portais — `test_list_portals_from_policy` (`indeed` `blocked`, `can_toggle False`; `vagas` `conditional`, `can_toggle True`), `test_toggle_conditional_only`, `test_toggle_keeps_blocked_by_site`; funil — `test_funnel_counts`, `test_put_floor_validates_range` (`1.5`, `NaN` → 422), `test_put_floor_null_restores_gated`.
- [ ] **Step 2: Run** `pytest tests/test_api_feeds.py tests/test_api_portals.py tests/test_api_funnel.py -v` → FAIL. **Step 3: Implement.** **Step 4: Run** → PASS.
- [ ] **Step 5: Commit** `feat(api): feeds, portals and adherence funnel routes`.

### Task 52: API de empresas (novas fontes, URL de carreira, `collect_mode`) e `/overview`

**Files:** Modify `api/sources.py`, `api/overview.py`, `tests/test_api_sources.py`, `tests/test_api_overview.py`.

**Interfaces:**
- `CompanyIn.source: Literal[*ATS_SOURCES, "site"] | None = None` (sem `source`: `ats_from_url(identifier)` → ATS; outra URL `http(s)` → `site`; senão 422 `"Informe a fonte ou cole a URL da página de carreiras."`), `identifier ≤ 500`, `collect_mode: Literal["funnel", "all"] = "funnel"`. `site`: portal pelo portão automático não permitido → 409 `verdict.message`; `SiteSource.probe` → `"ats"` cria a empresa do ATS detectado; `"jsonld"` cria `site`; `"none"` → 422 `MSG_NO_STRUCTURED`; `UnsafeURL` → 422 `"Endereço não permitido."`. Mensagem de quadro inexistente passa a citar a fonte (`"Quadro não encontrado no {Fonte}."`).
- `CompanyPatch.collect_mode` (mudar para `funnel`/`all` agenda `next_run_at=now`). `CompanyOut` ganha `collect_mode`, `stub_jobs: int` (stubs `open` da empresa). `GET /companies` sem `origin` exclui `feed`/`link`; `origin: Literal["manual","auto","feed","link"]`.
- Overview: `FeedsOverview{active: int}` em `OverviewOut.feeds`; `JobsOverview.outside_filters` (Task 48).
- [ ] **Step 1: Write failing tests** (`db`): `test_add_lever_company`, `test_add_by_careers_url_detects_ats`, `test_add_site_company_with_jsonld`, `test_add_site_without_jsonld_422`, `test_add_portal_url_gate_409`, `test_add_private_url_422`, `test_patch_collect_mode_schedules`, `test_company_out_counts_stubs` (Review Focus 4: 0 abertas, 3 stubs → `open_jobs == 0`, `stub_jobs == 3`), `test_list_companies_hides_synthetic`, `test_overview_feeds_active`.
- [ ] **Step 2: Run** `pytest tests/test_api_sources.py tests/test_api_overview.py -v` → FAIL. **Step 3: Implement.** **Step 4: Run** → PASS.
- [ ] **Step 5: Commit** `feat(api): companies from any verified source or careers URL`.

### Task 53: Web — clientes

**Files:** Create `web/lib/{feeds,portals,funnel,importLink}.ts` + `.test.ts`; Modify `web/lib/sources.ts`, `web/lib/overview.ts` e seus testes.

**Interfaces:**
- `lib/sources.ts`: `CompanySource = "greenhouse" | "lever" | "ashby" | "smartrecruiters" | "breezy" | "site" | string`; `Company` ganha `collect_mode: "funnel" | "all"`, `stub_jobs: number`; `CompanyOrigin` ganha `"feed" | "link"`; `NewCompany.source?: CompanySource`, `collect_mode?`; `JobItem` ganha `origin`, `source`, `attribution: {label: string; url: string} | null`; `sourceLabel(source: string): string` (`"Greenhouse"`, `"Lever"`, `"Ashby"`, `"SmartRecruiters"`, `"Breezy"`, `"Página de carreira"`).
- `lib/feeds.ts`: tipos `Feed`, `FeedSourceInfo`, `FeedsState`; `listFeeds()`, `addFeed({source, params})`, `deleteFeed(id)`; `parseKeywords(text: string): {ok: true; keywords: string[]} | {ok: false; error: string}` (separa por vírgula/linha, ≤ 5, cada ≤ 60; erros `"Use no máximo 5 palavras-chave."`, `"Cada palavra-chave pode ter até 60 caracteres."`).
- `lib/portals.ts`: `Portal`; `listPortals()`, `setPortalEnabled(id, enabled)`; `portalStatusLabel(status)` → `"Permitido"`, `"Condicional"`, `"Bloqueado"`.
- `lib/funnel.ts`: `FunnelState`; `getFunnel()`, `putFunnel(floor: number | null)`; `outsideFiltersText(n)` → `"Nenhuma vaga fora dos seus filtros"`, `"1 vaga fora dos seus filtros"`, `"N vagas fora dos seus filtros"` (número em pt-BR).
- `lib/importLink.ts`: `importLink({url, title?, description?}): Promise<{job_id: number; created: boolean}>`; `class NeedsTextError extends ApiError` (detail `code === "needs_text"`, com `reason`).
- `lib/overview.ts`: `feeds: {active: number}`, `jobs.outside_filters: number`, ambos com padrão `0` em `normalizeOverview` para corpos antigos.
- [ ] **Step 1: Write failing tests** (vitest, `fetch` falso): um teste por função exportada, incluindo `normalizeOverview` com corpo sem `feeds` → `{active: 0}`, `importLink` 422 `needs_text` → `NeedsTextError`, `parseKeywords` limites.
- [ ] **Step 2: Run** `cd web && npx vitest run lib` → FAIL. **Step 3: Implement.** **Step 4: Run** → PASS.
- [ ] **Step 5: Commit** `feat(web): clients for feeds, portals, funnel and link import`.

### Task 54: Web — abas de `/sources`, aba Empresas e resumo do funil

**Files:** Create `web/components/sources/{SourcesTabs,FunnelSummary}.tsx` + testes; Modify `web/app/sources/page.tsx`, `web/app/sources/page.test.tsx`, `web/components/sources/{AddCompanyForm,CompanyRow,sourcesSteps}.tsx/.ts` e testes.

**Interfaces:**
- `SourcesTabs({tab, onChange})`: `role="tablist"` com 4 `role="tab"` — **Empresas · Feeds · Portais · Importar por link** — `aria-selected`, `aria-controls`, setas ←/→/Home/End; a aba vive em `?tab=empresas|feeds|portais|link` (padrão `empresas`).
- `FunnelSummary()`: `outsideFiltersText(stub)`; se `suggested_floor` existe e `floor` é `null`: `"Sugestão de corte por similaridade: 0,51 (mantém 95% das vagas que você marcou como boas)."` + botão `"Ativar corte"`; com corte ativo: `"Corte ativo: 0,51"` + `"Desligar corte"`; aviso fixo `"O corte economiza avaliações, mas pode esconder vagas boas; vagas cortadas podem voltar se você desligar."`.
- `AddCompanyForm`: campo único aceita token/URL; seletor `Fonte` (`Detectar pela URL` padrão + `sourceLabel` de cada fonte); 422/409 mostram a mensagem da API.
- `CompanyRow`: selo da fonte; `"N vagas abertas · M fora dos seus filtros"` quando `stub_jobs > 0`; interruptor `"Coletar todas as vagas desta empresa"` (`collect_mode='all'`) com texto de ajuda `"Ignora seus filtros só para esta empresa."`.
- `sourcesSteps`: detalhe do passo "Adicionar" sem empresas → `"Cole um quadro, uma página de carreiras ou crie um feed"`.
- [ ] **Step 1: Write failing tests** (RTL): `test_tabs_keyboard_and_query` (seta → próxima aba, URL atualiza), `test_funnel_summary_suggestion_and_activate`, `test_company_row_outside_filters_and_mode_toggle`, `test_add_company_source_select_sends_source`, estado nunca só por cor (selos com texto).
- [ ] **Step 2: Run** `cd web && npx vitest run components/sources app/sources` → FAIL. **Step 3: Implement** (ler `node_modules/next/dist/docs/` sobre `useSearchParams`/client components antes). **Step 4: Run** → PASS.
- [ ] **Step 5: Commit** `feat(web): sources tabs, companies tab and adherence summary`.

### Task 55: Web — abas Feeds e Portais

**Files:** Create `web/components/sources/{FeedsPanel,PortalsPanel}.tsx` + testes; Modify `web/app/sources/page.tsx`.

**Interfaces:**
- `FeedsPanel`: lista de feeds (fonte, palavras-chave, país, `jobs_seen`, última coleta relativa, erro em texto) com crédito obrigatório `"Vagas via <label>"` linkando `attribution.url` (`rel="noopener noreferrer"`, `target="_blank"`); formulário (fonte, palavras-chave, país); fontes com chave não configurada aparecem desabilitadas com `"Chave não configurada no .env"`; fonte condicional desligada aparece com `"Ligue na aba Portais"`; excluir com confirmação `"Excluir este feed? As vagas já coletadas continuam."`.
- `PortalsPanel`: um cartão por portal com status em texto (`portalStatusLabel`) + ícone, data `"Verificado em 14/11/2026"`, resumo da evidência e links; `blocked`: `"Só importar por link."`; `conditional`: interruptor com aviso antes de ligar (diálogo: `"Os termos deste portal não foram totalmente verificados. Ligar pode violar regras do site. A coleta respeita o robots.txt e para se o site recusar o acesso. Isto é apoio à decisão, não parecer jurídico."` + `"Ligar mesmo assim"` / `"Cancelar"`); `blocked_by_site`: `"O site recusou nosso acesso em <data>; coleta suspensa."`.
- [ ] **Step 1: Write failing tests**: `test_feed_credit_link_rendered`, `test_keyed_source_disabled_without_key`, `test_delete_feed_confirms`, `test_portal_toggle_requires_confirmation`, `test_blocked_portal_has_no_toggle`, `test_blocked_by_site_message`, texto de evidência renderizado como texto (string com `<b>` aparece literal).
- [ ] **Step 2: Run** `cd web && npx vitest run components/sources` → FAIL. **Step 3: Implement.** **Step 4: Run** → PASS.
- [ ] **Step 5: Commit** `feat(web): feeds and portals tabs with gate warnings`.

### Task 56: Web — Importar por link, trilha e verificação visual

**Files:** Create `web/components/sources/ImportLinkForm.tsx` + teste; Modify `web/app/sources/page.tsx`, `web/components/stepper/computeSteps.ts`, `web/components/shell/Sidebar.tsx` e testes (`computeSteps.test.ts`, `Sidebar.test.tsx`), `web/components/sources/JobsList.tsx` (crédito do feed).

**Interfaces:**
- `ImportLinkForm`: campo URL + `"Importar vaga"`; `NeedsTextError` revela título e descrição (`"Não consegui ler esta vaga automaticamente. Cole o título e a descrição."`) e reenvia com eles; sucesso → `"Vaga importada."` / `"Essa vaga já estava importada."` com link para `/jobs`; texto colado nunca vira HTML.
- `computeSteps` (passo `sources`): `done = companies.active > 0 || feeds.active > 0`; detalhe `"{N empresas} · {M feeds} · {K vagas abertas}"` (omitindo zeros), sem fontes `"Nenhuma fonte ativa"`; CTA `"Adicionar fontes"`. `pendingFor("/sources")` usa a mesma regra (`"nenhuma fonte ativa"`).
- `JobsList`: vaga de feed mostra `"via <label>"` com link para `attribution.url`.
- [ ] **Step 1: Write failing tests**: `test_import_link_flow_needs_text_then_success`, `test_import_link_duplicate_message`, `test_sources_step_done_with_feed_only`, `test_sidebar_pending_with_feed_only`, `test_jobs_list_feed_credit`.
- [ ] **Step 2: Run** `cd web && npx vitest run` → FAIL. **Step 3: Implement.** **Step 4: Run** `cd web && npx vitest run && npm run lint && npm run build` → PASS.
- [ ] **Step 5: Verificação visual**: subir API, worker e web (skill `run`); em `/sources`, cada aba em 375, 768 e desktop, claro e escuro: sem rolagem horizontal, abas utilizáveis por teclado, avisos do portão legíveis, estados com texto além da cor; importar um link de fixture servida localmente. Corrigir o que aparecer antes do commit.
- [ ] **Step 6: Commit** `feat(web): import by link and journey step counting feeds`.

---

## Handoff
- Antes de executar o marco 5: renumerar `docs/superpowers/plans/2026-11-14-rumo-marco-5-candidatura.md` para a migração `0009` (`down_revision = "0008"`).
- Itens que este plano manda ao backlog (`docs/superpowers/backlog.md`, seção do 3b): fontes que falharem na fatia 0; Workable e Gupy; descrição por vaga em SmartRecruiters/Breezy/BambooHR/Bundesagentur (N+1); recoleta de páginas de vaga já conhecidas em empresas `site`; lista pública de sufixos completa em `registrable_domain`; popular `catalog/<ats>.json`; desbloquear manualmente um portal `blocked_by_site`.
