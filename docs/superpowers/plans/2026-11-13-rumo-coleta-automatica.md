# Rumo — Coleta automática de empresas — Plano de implementação

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** O sistema propõe, valida e adiciona empresas Greenhouse sozinho (catálogo + empresas do perfil + empresas-alvo), respeitando teto, pausas da pessoa e a entrevista.

**Architecture:** `rumo_core/discovery/` (nomes→tokens, catálogo, candidatos, validação) é puro/SQL; `worker/discover_companies.py` valida poucos candidatos por tick e cria `Company(origin='auto')` com coleta imediata; `api/discovery.py` expõe preferências e contadores; um painel em `/sources` mostra e controla.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy 2, Alembic, httpx (conector existente), pytest (`db`/`slow`), Next.js 16 + vitest.

**Spec:** `docs/superpowers/specs/2026-11-13-rumo-coleta-automatica-design.md` (autoridade). Também `2026-11-11-rumo-design.md` §5.2.

## Global Constraints
- Migração `0006` encadeada em `0005`; todas as tabelas novas com `user_id`; toda consulta escopada por `user_id` (`api.deps.current_user_id`).
- A validação usa só o conector existente (`get_source("greenhouse")`); host fixo, sem seguir redirects; nenhum LLM; só o **token** sai para a rede.
- Token válido = `[a-z0-9_-]{2,64}`; candidato válido = HTTP 200 com ≥ 1 vaga aberta; 404 ou 0 vagas → `invalid` (0 vagas reavaliável após 30 dias); 429/5xx/timeout não consomem a tentativa definitiva (máx. 3, backoff exponencial em `retry_after`).
- `discovery_batch=5` por tick de 60 s, `discovery_max_seconds=20`, `discovery_max_auto_companies=100`, pausa mínima entre requisições = `collect_delay_seconds`; job registrado depois de `collect_companies`; nunca rodar enquanto `discovery_enabled=false`.
- Empresa pausada (`active=false`) ou já existente (qualquer origem) nunca é recriada/reativada pela descoberta (`known`).
- Logs sem nomes de empresas do perfil e sem texto de vagas.
- UI em PT-BR, texto específico e curto; texto de fonte sempre como texto; `web/AGENTS.md`: ler `node_modules/next/dist/docs/` antes de escrever código Next.

## Review Focus
1. Descoberta nunca reativa/recria empresa pausada ou existente (Task 3, 4).
2. Teto de empresas automáticas e `discovery_enabled=false` realmente param o job, mesmo com candidatos pendentes (Task 4).
3. Falha transitória (429/5xx) não marca `invalid` nem queima as 3 tentativas; 404 marca `invalid` imediatamente (Task 4).
4. Nome de empresa hostil/estranho (unicode, `../`, espaços, 5000 caracteres, vazio) vira zero tokens ou tokens seguros; nada além de `[a-z0-9_-]` chega ao conector (Task 2).
5. Isolamento entre usuários em candidatos, preferências e API (Task 3, 4, 5).

---

### Task 1: Migração 0006, modelos e configurações

**Files:**
- Create: `alembic/versions/0006_discovery.py`, `tests/test_discovery_models.py`
- Modify: `rumo_core/models.py`, `rumo_core/config.py`, `.env.example`

**Interfaces:**
- Produces: `Company.origin: str` ('manual'|'auto', default 'manual', CHECK); `DiscoveryCandidate(UserScopedMixin, TimestampMixin)` com `id, source str(32), token str(64), name_hint str|None, origin_kind str(16) ('catalog'|'profile'|'target'), state str(16) ('pending'|'valid'|'invalid'|'known'|'rejected'), attempts int, checked_at, retry_after (timestamptz|None), last_error text|None, company_id FK companies ON DELETE SET NULL|None`; único `(user_id, source, token)`; índice `(user_id, state, retry_after)`; CHECKs nos enums; `UserPreference` com PK `user_id` (FK users), `discovery_enabled bool default true`, `target_companies JSONB default []`, `updated_at`.
- Settings: `discovery_batch: int = 5`, `discovery_max_seconds: float = 20`, `discovery_max_auto_companies: int = 100`, `discovery_max_attempts: int = 3` (todas `gt=0`).

- [ ] **Step 1: Write failing tests** (`db`, usuário descartável): `test_company_origin_defaults_manual_and_checks_values`, `test_candidate_unique_per_user_source_token`, `test_candidate_state_check`, `test_candidate_company_set_null_on_company_delete`, `test_user_preference_defaults`, `test_settings_defaults`.
- [ ] **Step 2: Run** `pytest tests/test_discovery_models.py -v` → FAIL.
- [ ] **Step 3: Implement** modelos, settings, `.env.example` (`DISCOVERY_BATCH`, `DISCOVERY_MAX_AUTO_COMPANIES`) e a migração espelhando exatamente os modelos; `alembic upgrade head`, `downgrade -1`, `upgrade head` e `alembic check` sem diferenças.
- [ ] **Step 4: Run** `pytest tests/test_discovery_models.py tests/test_jobs_models.py tests/test_config.py -v` → PASS.
- [ ] **Step 5: Commit** `feat(discovery): candidates, preferences and company origin`.

---

### Task 2: Nomes → tokens e catálogo

**Files:**
- Create: `rumo_core/discovery/__init__.py`, `rumo_core/discovery/names.py`, `rumo_core/discovery/catalog.py`, `rumo_core/discovery/catalog/greenhouse.json`, `tests/discovery/test_names.py`, `tests/discovery/test_catalog.py`

**Interfaces:**
- Produces: `token_variants(name: str) -> list[str]` (puro: NFKD sem acentos, minúsculas, remove sufixos jurídicos `inc llc ltd gmbh ag sa ltda corp co plc`, no máximo 3 variantes `acmecorp`/`acme`/`acme-corp`, só `[a-z0-9_-]`, comprimento 2–64, sem duplicatas, nome > 200 caracteres → `[]`); `load_catalog() -> list[CatalogEntry]` com `CatalogEntry(token: str, name: str)` lendo `catalog/greenhouse.json` (lista de `{token, name}`), validando token com a mesma regra e descartando duplicatas.
- O catálogo contém ~150 entradas de empresas amplamente conhecidas por ter quadros públicos Greenhouse (incluir `gitlab` e `anthropic`, já usados nos fixtures); o implementador inclui apenas tokens de que tem confiança razoável — tokens errados são descartados em execução pela validação.

- [ ] **Step 1: Write failing tests**: `test_variants_strip_legal_suffixes_and_accents` ("Acme Corp." → contém `acmecorp`, `acme`; "Café Münch GmbH" → `cafemunch`), `test_variants_only_safe_chars`, `test_variants_hostile_inputs_yield_nothing_unsafe` ("../etc", "a b\u0000c", unicode, "", 5000 caracteres, só sufixo "Inc"), `test_variants_max_three_unique`, `test_catalog_loads_and_has_known_entries`, `test_catalog_rejects_invalid_tokens_and_duplicates` (arquivo temporário), `test_catalog_size_reasonable` (≥ 100).
- [ ] **Step 2: Run** `pytest tests/discovery -v` → FAIL.
- [ ] **Step 3: Implement** conforme as interfaces.
- [ ] **Step 4: Run** → PASS.
- [ ] **Step 5: Commit** `feat(discovery): name to token variants and curated catalog`.

---

### Task 3: Candidatos (catálogo, perfil, empresas-alvo)

**Files:**
- Create: `rumo_core/discovery/candidates.py`, `tests/discovery/test_candidates.py`

**Interfaces:**
- Consumes: `load_catalog`, `token_variants`, `get_current_profile`, `CanonicalProfile`, `Company`, `DiscoveryCandidate`, `UserPreference`.
- Produces: `refresh_candidates(session, user_id: int, *, source: str = "greenhouse") -> RefreshStats(created: int, known: int)`: idempotente; insere candidatos `catalog` (todas as entradas), `profile` (empresas das `experiences` da versão atual do perfil → variantes), `target` (de `UserPreference.target_companies`); um candidato por token (a primeira origem vence); marca `known` (com `company_id`) quando já existe `Company` de qualquer origem/estado com o mesmo `(source, identifier)`; nunca altera candidatos `valid`/`invalid`/`rejected`; um candidato `known` cuja empresa não existe mais volta a `pending`.
- `normalize` do token de `Company.identifier` segue `normalize_identifier` do conector (minúsculas).

- [ ] **Step 1: Write failing tests** (`db`): `test_catalog_candidates_created_once`, `test_profile_experience_companies_become_candidates`, `test_target_companies_become_candidates`, `test_existing_company_marks_known_incl_paused`, `test_refresh_idempotent_and_keeps_states`, `test_scoped_by_user`, `test_no_profile_ok`, `test_hostile_names_create_only_safe_tokens`.
- [ ] **Step 2: Run** `pytest tests/discovery/test_candidates.py -v` → FAIL.
- [ ] **Step 3: Implement** com `insert ... on conflict do nothing` e uma única consulta de empresas existentes por usuário.
- [ ] **Step 4: Run** → PASS.
- [ ] **Step 5: Commit** `feat(discovery): candidate generation`.

---

### Task 4: Validação e job `discover_companies`

**Files:**
- Create: `rumo_core/discovery/validate.py`, `worker/discover_companies.py`, `tests/discovery/test_validate.py`, `tests/test_worker_discover.py`
- Modify: `worker/main.py` (registro depois de `collect_companies`; fábrica `build_discover_job`)

**Interfaces:**
- Consumes: `get_source`, `RawJob`, erros `SourceNotFound/SourceUnavailable/SourceBadResponse`, `refresh_candidates`, `Company`, `UserPreference`, settings da Task 1.
- Produces: `async validate_candidate(source: Source, token: str) -> Outcome` onde `Outcome(kind: Literal["valid","invalid","transient"], open_jobs: int, name: str | None)`; `async discover_companies(session_factory, *, batch, max_seconds, max_auto, delay, max_attempts, user_id=None, source_getter=get_source, clock=time.monotonic, sleep=asyncio.sleep) -> int` (candidatos processados).
- Regras do job: por usuário com `discovery_enabled` (linha ausente = habilitado); chama `refresh_candidates` no máximo uma vez por hora por usuário (estado em memória, `_reset_state()` para testes); seleciona `pending` com `retry_after IS NULL OR <= now()` por `FOR UPDATE SKIP LOCKED`, commit antes da rede, pausa `delay` entre requisições, para ao estourar `max_seconds` ou o teto de empresas automáticas ativas; `valid` com ≥ 1 vaga → cria `Company(origin='auto', active=True, next_run_at=now())` (nome devolvido ou token), `state='valid'`, `company_id`; 0 vagas → `invalid` com `retry_after=now()+30d`; 404 → `invalid`; transiente → `attempts+1`, `retry_after` exponencial, e ao chegar em `max_attempts` → `invalid` com `last_error` = nome da classe; criação protegida contra corrida (se a empresa passou a existir → `known`); nunca cria empresa para usuário com teto atingido; nunca toca empresa existente.

- [ ] **Step 1: Write failing tests** (`db`, conector falso): `test_valid_creates_auto_company_with_immediate_run`, `test_404_marks_invalid`, `test_zero_jobs_invalid_retry_30d`, `test_transient_does_not_burn_attempt_definitively` (retry_after avança, estado segue `pending` até 3), `test_cap_stops_creation`, `test_disabled_user_skipped`, `test_paused_or_existing_company_never_recreated`, `test_respects_batch_and_max_seconds`, `test_two_users_isolated`, `test_job_registered_after_collect`, `test_no_llm_or_other_hosts_used` (só o conector injetado), `test_validate_candidate_outcomes` (200 com vagas, 200 vazio, 404, 429, 500, JSON ruim).
- [ ] **Step 2: Run** `pytest tests/discovery/test_validate.py tests/test_worker_discover.py -v` → FAIL.
- [ ] **Step 3: Implement** no padrão de `collect_companies` (commit antes da rede, relógio/sleep injetáveis, `_reset_state()`); registrar em `worker/main.py` a cada 60 s com as settings.
- [ ] **Step 4: Run** os dois arquivos + `tests/test_worker.py tests/test_worker_collect.py` → PASS.
- [ ] **Step 5: Commit** `feat(worker): automatic company discovery job`.

---

### Task 5: API `/discovery`

**Files:**
- Create: `api/discovery.py`, `tests/test_api_discovery.py`
- Modify: `api/main.py` (router; CORS já permite PUT), `api/sources.py` (`CompanyOut` ganha `origin`; `GET /companies` aceita `origin=manual|auto` opcional)

**Interfaces:**
- Produces: `GET /discovery` → `{enabled: bool, target_companies: list[str], auto_companies: int, max_auto: int, pending: int, found: int}` (linha de preferências ausente = habilitado, lista vazia; `found` = candidatos `valid`); `PUT /discovery` (corpo `{enabled, target_companies}`; ≤ 50 nomes de 1–80 caracteres, sem controle/NUL, aparados e sem duplicatas; 422 caso contrário; upsert atômico) devolve o mesmo formato; `GET /discovery/candidates?state=&limit=&offset=` (limit 1–100) → `{total, items:[{token, name_hint, origin_kind, state, attempts, last_error}]}`.

- [ ] **Step 1: Write failing tests**: `test_get_defaults`, `test_put_roundtrip_and_trims`, `test_put_rejects_bad_names_and_too_many`, `test_counts_reflect_candidates_and_auto_companies`, `test_candidates_list_filters_state_and_paginates`, `test_scoped_by_user`, `test_companies_expose_origin_and_filter`.
- [ ] **Step 2: Run** `pytest tests/test_api_discovery.py -v` → FAIL.
- [ ] **Step 3: Implement** o router (padrão de `api/matching.py`, mesmos erros PT-BR e upsert atômico).
- [ ] **Step 4: Run** `pytest tests/test_api_discovery.py tests/test_api_sources.py tests/test_api_cors.py -v` → PASS; depois `pytest -m "not slow"` completo.
- [ ] **Step 5: Commit** `feat(api): discovery preferences and counters`.

---

### Task 6: Painel "Descoberta automática" em `/sources`

**Files:**
- Create: `web/lib/discovery.ts`, `web/lib/discovery.test.ts`, `web/components/sources/DiscoveryPanel.tsx` (+ teste)
- Modify: `web/lib/sources.ts` (`Company.origin`), `web/components/sources/CompanyRow.tsx` (selo "automática"), `web/app/sources/page.tsx` (monta o painel e o filtro por origem)

**Interfaces:**
- Consumes: `request`/`ApiError` de `web/lib/profile.ts`, rotas da Task 5.
- Produces: `getDiscovery()`, `putDiscovery(body)`, `listCandidates(q)`; `DiscoveryPanel` com interruptor "Descobrir empresas automaticamente", contadores "N empresas encontradas · M em validação", campo "Empresas que você quer acompanhar" (uma por linha, ≤ 50), botão "Salvar" com estado e erro do servidor, e link "Ver candidatos" que expande a lista (token, estado). Polling de 10 s só enquanto `pending > 0`, limpo ao desmontar; guarda de resposta antiga; texto sempre como texto; sem `Date.now()` em render.

- [ ] **Step 1: Write failing tests** (vitest/RTL, fetch mockado): cliente (query string, erro com `detail`), painel (interruptor salva, contadores, validação de 50 nomes/80 caracteres, polling só com pendentes e limpo ao desmontar, resposta antiga ignorada, nome hostil `<img onerror>` literal), `CompanyRow` (selo "automática" só para `origin==='auto'`).
- [ ] **Step 2: Run** `npm test` em `web/` → FAIL.
- [ ] **Step 3: Implement**; ler `node_modules/next/dist/docs/` antes.
- [ ] **Step 4: Verificação** `npm test`, `npm run lint`, `npm run build`; conferência visual em 375, 768 e desktop, claro e escuro, com mock descartável da API (não a real).
- [ ] **Step 5: Commit** `feat(web): automatic discovery panel`.
