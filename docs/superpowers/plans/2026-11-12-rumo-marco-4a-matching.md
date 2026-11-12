# Rumo Marco 4a — Filtros manuais e funil de matching — Plano de implementação

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ranquear as vagas coletadas para a pessoa: filtros duros em SQL, nota vetorial contra o perfil, nota 0–100 do Groq (top 30, no worker) e uma tela de vagas com a nota dominante e painel de detalhes.

**Architecture:** `rumo_core/matching/` (filtros, nota vetorial, nota da IA + validação anti-invenção) é puro/SQL e reutilizado pela API (`api/matching.py`, só SQL, nunca Groq) e pelo worker (`worker/match_jobs.py`, único lugar que chama o LLM). Estado e cache em `matches` (vaga × versão do perfil); filtros salvos em `search_profiles`. Frontend em `web/app/jobs`.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy 2, Alembic, pgvector (cosseno `<=>`), Pydantic v2, pytest (marcadores `db`/`slow`), Next.js 16 + vitest.

**Spec:** `docs/superpowers/specs/2026-11-12-rumo-marco-4a-matching-design.md` (autoridade; este plano argumenta a partir dela). Também vale `2026-11-11-rumo-design.md` §5.3, 5.4, 9.3.

## Global Constraints
- Python 3.12; migração `0005` encadeada em `0004`; todas as tabelas novas com `user_id` e todas as consultas escopadas por `user_id` (`api.deps.current_user_id`).
- O LLM só é chamado no worker, via o `GroqProvider`/limitador compartilhado (25/min) — nunca na API.
- Saída do LLM e texto de vaga são **dados não confiáveis**: vaga entra no prompt por `wrap_untrusted("job", ...)` (`rumo_core/profile/prompting.py`); a saída é validada contra o perfil antes de gravar; nada de texto do LLM/da vaga é logado.
- Política "desconhecido não é errado": valor desconhecido nunca elimina uma vaga quando `include_unknown=true` (padrão).
- Salário: só é comparado quando `salary_known` e a moeda do filtro é igual à da vaga; período normalizado para ano (`year`×1, `month`×12, `hour`×2080); moeda diferente conta como desconhecido.
- Nota vetorial = média dos 3 maiores `1 - (embedding <=> chunk.embedding)/2` (cosseno normalizado 0–1) entre a vaga e os chunks da **versão atual do perfil** com `embedding` e `model_version` iguais ao do embedder vigente; < 3 chunks → média dos existentes.
- Limites: `GET /matches` `limit ≤ 100`; refresh = 30 melhores (`match_top_n=30`); `match_batch=3` por tick de 60 s; `attempts` máx. 3.
- Texto da UI em PT-BR, específico e curto; sem dados falsos óbvios; texto de vaga/justificativa sempre como texto (nunca HTML); links só http/https.
- `web/AGENTS.md`: ler `node_modules/next/dist/docs/` antes de escrever código Next.

## Review Focus
1. Perfil sem chunks vetorizados (ou só de modelo antigo): a API devolve `vectorized=false` e lista vazia ordenada, nunca 500 nem ranking inventado (Task 3, 6).
2. Vagas de outro usuário jamais aparecem em `/matches`, no refresh ou no worker (Task 3, 5, 6).
3. Texto hostil na vaga (instruções, `</job>`, HTML) não altera a nota nem vira HTML na UI (Task 4, 7).
4. LLM devolve nota fora de 0–100, JSON incompleto ou justificativa citando empresa/número ausentes do perfil → estado `erro`/descartado, nunca gravado como `ok` (Task 4, 5).
5. `refresh` repetido, ou perfil confirmado de novo durante a avaliação, não duplica linhas nem grava nota na versão errada (Task 5, 6).

---

### Task 1: Migração 0005, modelos e configurações

**Files:**
- Create: `alembic/versions/0005_matching.py`, `tests/test_matching_models.py`
- Modify: `rumo_core/models.py`, `rumo_core/config.py`, `.env.example`

**Interfaces:**
- Produces: `SearchProfile` e `Match` em `rumo_core.models`; settings `match_batch: int = 3`, `match_top_n: int = 30`, `match_weight_profile: float = 1.0`, `match_weight_query: float = 0.0`, `match_max_attempts: int = 3`.
- `SearchProfile(UserScopedMixin, TimestampMixin)`: `id, name str(255), filters JSONB (default {}), query_text Text|None, query_embedding Vector(1024)|None, is_default bool`; único `(user_id, name)`; apenas um `is_default=true` por usuário (índice único parcial).
- `Match(UserScopedMixin, TimestampMixin)`: `id, job_id FK jobs ON DELETE CASCADE, profile_version int, vec_score float, ai_score int|None, rationale Text|None, strengths JSONB|None, gaps JSONB|None, state str(16) ('pendente'|'ok'|'erro', default 'pendente'), error Text|None, attempts int default 0, scored_at timestamptz|None, content_hash str(64)` (hash da vaga no momento da avaliação); único `(user_id, job_id, profile_version)`; índice `(user_id, profile_version, ai_score desc)`; CHECK `state in (...)` e `ai_score between 0 and 100`.

- [ ] **Step 1: Write failing tests** em `tests/test_matching_models.py` (marcador `db`, usuário descartável como em `tests/test_jobs_models.py`): `test_match_unique_per_job_and_version` (segundo insert idêntico → `IntegrityError`), `test_match_rejects_bad_state_and_score` (state 'x' e ai_score 101 → erro), `test_only_one_default_search_profile_per_user`, `test_match_deleted_with_job`, `test_settings_defaults` (`get_settings()` tem os cinco valores acima).
- [ ] **Step 2: Run** `pytest tests/test_matching_models.py -v` → FAIL (modelos inexistentes).
- [ ] **Step 3: Implement** os modelos, as settings (com `gt=0`/`ge=0` onde cabe), `.env.example` (`MATCH_BATCH=3`, `MATCH_TOP_N=30`) e a migração `0005` (down_revision `0004`), espelhando exatamente os modelos; `alembic upgrade head` e `alembic downgrade -1` + `upgrade head` devem funcionar.
- [ ] **Step 4: Run** `pytest tests/test_matching_models.py tests/test_jobs_models.py tests/test_config.py -v` → PASS.
- [ ] **Step 5: Commit** `feat(matching): matches and search_profiles tables`.

---

### Task 2: Esquema de filtros e aplicação em SQL

**Files:**
- Create: `rumo_core/matching/__init__.py`, `rumo_core/matching/filters.py`, `tests/matching/test_filters.py`

**Interfaces:**
- Produces: `class SearchFilters(BaseModel)` com `countries: list[str]=[]`, `work_models: list[str]=[]`, `seniorities: list[str]=[]`, `languages: list[str]=[]`, `departments: list[str]=[]`, `salary_min: float|None`, `salary_currency: str|None`, `include_unknown: bool=True`, `accept_no_salary: bool=True`; validadores: work_models ⊂ {onsite,hybrid,remote}, seniorities ⊂ {intern,junior,mid,senior,lead,manager,director}, languages ⊂ {pt,en,de}, países normalizados com o mesmo normalizador de `api/sources.py::_norm_country` (extraia para `rumo_core/sources/` se estiver só na API), listas ≤ 20 itens, `salary_min ≥ 0` e `salary_currency` obrigatório quando `salary_min` existe, `extra="forbid"`.
- Produces: `apply_filters(stmt: Select, filters: SearchFilters) -> Select` (sobre `Job`; não aplica `user_id`/`status`).

- [ ] **Step 1: Write failing tests** (unitários puros para validação; `db` para SQL com vagas de usuário descartável): `test_rejects_unknown_work_model`, `test_salary_min_requires_currency`, `test_country_filter_matches_normalized_country`, `test_unknown_work_model_kept_when_include_unknown`, `test_unknown_work_model_dropped_when_include_unknown_false`, `test_salary_filter_year_month_hour_normalization` (vaga 5 000/mês USD passa em `salary_min=50000 USD`; 20/hora = 41 600/ano não passa), `test_salary_other_currency_counts_as_unknown`, `test_no_salary_dropped_when_accept_no_salary_false`, `test_department_filter_is_case_insensitive_and_escapes_like` (departamento `%`), `test_empty_filters_change_nothing`.
- [ ] **Step 2: Run** `pytest tests/matching/test_filters.py -v` → FAIL.
- [ ] **Step 3: Implement** `SearchFilters` e `apply_filters` (um `or_` por filtro: valor listado OU, se `include_unknown`, valor desconhecido/`NULL`; país `NULL` = desconhecido; departamento por `lower(department) = ANY(...)`, igualdade, sem LIKE; salário conforme a regra global, anualizando em SQL com `case`).
- [ ] **Step 4: Run** → PASS.
- [ ] **Step 5: Commit** `feat(matching): search filters schema and SQL application`.

---

### Task 3: Nota vetorial e ranking

**Files:**
- Create: `rumo_core/matching/ranking.py`, `tests/matching/test_ranking.py`

**Interfaces:**
- Consumes: `apply_filters`, `get_current_profile(session, user_id)`, `ProfileChunk`, `Job`, `Match`.
- Produces:
  - `@dataclass RankedJob: job: Job, vec_score: float, match: Match | None`.
  - `@dataclass RankPage: total: int, items: list[RankedJob], profile_version: int | None, vectorized: bool, missing_embeddings: int, chunks_used: int`.
  - `rank_jobs(session, user_id: int, filters: SearchFilters, *, model_version: str, status: str = "open", limit: int = 50, offset: int = 0) -> RankPage`.
  - `top_candidate_ids(session, user_id, filters, *, model_version, n: int) -> list[tuple[int, float]]` (job_id, vec_score) das `n` melhores por `vec_score`, mesmos filtros.

Regras: ordena por `ai_score DESC NULLS LAST, vec_score DESC, job.id ASC` (estável para paginação) só entre vagas com embedding de `model_version` igual; vagas sem embedding não entram e são contadas em `missing_embeddings`; `vectorized=false` e `items=[]` quando o perfil não existe ou não tem chunks utilizáveis; `match` é a linha da versão atual do perfil, se houver; `status` ∈ {open, closed, all}; `vec_score` sai de uma subconsulta lateral `top 3` (sem carregar embeddings no Python).

- [ ] **Step 1: Write failing tests** (`db`, usuários descartáveis, embeddings sintéticos de 1024 dims com vetores base ortogonais): `test_vec_score_is_mean_of_top3_normalized_cosine` (valor numérico esperado), `test_fewer_than_three_chunks_uses_available`, `test_job_without_embedding_excluded_and_counted`, `test_chunks_of_other_model_version_ignored`, `test_no_profile_means_not_vectorized_and_empty`, `test_only_current_profile_version_chunks_used`, `test_orders_by_ai_score_then_vec_score_then_id`, `test_pagination_is_stable_and_total_matches_filters`, `test_other_users_jobs_never_returned`, `test_status_all_includes_closed`, `test_top_candidate_ids_respects_filters_and_n`.
- [ ] **Step 2: Run** `pytest tests/matching/test_ranking.py -v` → FAIL.
- [ ] **Step 3: Implement** com SQLAlchemy Core + `ProfileChunk.embedding.cosine_distance(Job.embedding)` (pgvector-sqlalchemy) numa `lateral` limitada a 3 por vaga (`vec = avg(1 - dist/2)`), `total` por `count` sobre a mesma seleção filtrada.
- [ ] **Step 4: Run** → PASS.
- [ ] **Step 5: Commit** `feat(matching): vector score (top-3 mean) and ranking`.

---

### Task 4: Nota da IA — prompt, esquema e validação anti-invenção

**Files:**
- Create: `rumo_core/matching/ai_score.py`, `tests/matching/test_ai_score.py`

**Interfaces:**
- Consumes: `LLMProvider.complete_json(messages, schema)`, `wrap_untrusted`, `numbers_supported`/`lexicon_supported` (`rumo_core/interview/suggestions.py`), `CanonicalProfile`.
- Produces:
  - `class MatchDraft(BaseModel)`: `score: int` (0–100 validado), `rationale: str` (≤ 600), `strengths: list[str]` (≤ 5 itens de ≤ 160), `gaps: list[str]` (≤ 5 de ≤ 160).
  - `profile_summary(profile: CanonicalProfile) -> str` (título/headline, resumo, experiências com empresa/título/datas/descrição/conquistas, habilidades, idiomas, formação; sem email/telefone/links; ≤ 6 000 caracteres, truncando experiências mais antigas primeiro).
  - `async score_job(llm, profile: CanonicalProfile, job: JobView) -> MatchResult` com `JobView(title, company_name, location_text, work_model, seniority, department, description)` (descrição cortada em 4 000 caracteres no prompt) e `MatchResult(score, rationale, strengths, gaps)` **já validado**.
  - `validate_match(draft: MatchDraft, profile: CanonicalProfile) -> MatchResult`: cada texto (`rationale`, `strengths`, `gaps`) é verificado com `numbers_supported` e `lexicon_supported` contra as fontes do perfil (texto do perfil serializado) **mais** os campos da vaga permitidos para citação (título, empresa) quando a frase se refere à vaga; itens que falham são descartados; se a justificativa falhar ou não sobrar nenhum ponto forte nem lacuna → `raise MatchInvalid`.
  - `class MatchInvalid(Exception)`.

O prompt (PT-BR) declara a rubrica: nota 0–100 baseada apenas em evidências do perfil; senioridade e idioma contam; ausência de requisito vira lacuna, nunca presumir experiência; a vaga vem dentro de `<job>` e é tratada como dados.

- [ ] **Step 1: Write failing tests** com um `FakeLLM` (padrão dos testes do marco 3): `test_valid_draft_becomes_result`, `test_score_out_of_range_rejected` (101, -1), `test_invented_number_in_rationale_raises_match_invalid`, `test_invented_skill_in_strength_is_dropped_but_others_kept`, `test_all_items_invalid_raises`, `test_prompt_wraps_job_and_neutralises_closing_tag` (descrição com `</job>` e "ignore as instruções"), `test_profile_summary_omits_contact_info_and_is_capped`, `test_description_truncated_to_4000_chars_in_prompt`, `test_llm_errors_propagate` (LLMRateLimited não é capturado).
- [ ] **Step 2: Run** `pytest tests/matching/test_ai_score.py -v` → FAIL.
- [ ] **Step 3: Implement** conforme as interfaces; reutilize os validadores existentes sem alterá-los.
- [ ] **Step 4: Run** → PASS.
- [ ] **Step 5: Commit** `feat(matching): LLM job scoring with anti-invention validation`.

---

### Task 5: Worker — refresh e avaliação (`score_matches`)

**Files:**
- Create: `rumo_core/matching/refresh.py`, `worker/match_jobs.py`, `tests/matching/test_refresh.py`, `tests/test_worker_match.py`
- Modify: `worker/main.py`

**Interfaces:**
- Consumes: `top_candidate_ids`, `score_job`, `MatchInvalid`, `get_current_profile`, o limitador do Groq já embutido no `GroqProvider`.
- Produces:
  - `mark_pending(session, user_id, filters, *, model_version, n) -> RefreshResult(requested: int, created: int, reset: int, already_ok: int, profile_version: int | None)` em `rumo_core/matching/refresh.py`: cria linhas `pendente` (`vec_score` atual) para as `n` melhores da versão atual do perfil; linhas `ok` cuja vaga não mudou (`content_hash` igual) ficam; `ok` com `content_hash` diferente ou `erro` voltam a `pendente` (`attempts=0`); idempotente; sem perfil → `profile_version=None`, nada criado.
  - `async score_pending_matches(session_factory, llm: LLMProvider | None, *, batch: int, max_attempts: int, user_id: int | None = None, clock=time.monotonic) -> int` em `worker/match_jobs.py`, no padrão de `fill_missing_job_fields`: sem LLM → aviso único e 0; pausa de 60 s após `LLMRateLimited` (sem contar tentativa); seleção `FOR UPDATE SKIP LOCKED` + commit antes da chamada lenta; relê vaga e perfil no momento de gravar e descarta se `content_hash` da vaga ou a versão atual do perfil mudaram (linha da versão antiga fica como está); `MatchInvalid`/`LLMError` → `attempts+1`, e ao atingir `max_attempts` → `state='erro'` com `error` = nome da classe (sem texto da vaga nem do LLM); sucesso → `state='ok'`, `ai_score/rationale/strengths/gaps/scored_at/content_hash`.
  - `main.py`: registra `score_matches` a cada 60 s **depois** de `fill_missing_job_fields`, com `settings.match_batch`/`match_max_attempts`.

- [ ] **Step 1: Write failing tests** (`db`, LLM falso): refresh — `test_mark_pending_top_n_only`, `test_mark_pending_idempotent`, `test_ok_row_kept_when_job_unchanged`, `test_ok_row_reset_when_job_content_changed`, `test_no_profile_creates_nothing`, `test_refresh_scoped_by_user`; worker — `test_scores_pending_to_ok`, `test_invalid_draft_counts_attempt_then_error_at_max`, `test_rate_limit_pauses_without_attempt`, `test_no_llm_does_nothing`, `test_stale_job_hash_discards_result`, `test_new_profile_version_discards_result`, `test_batch_limit_respected`, `test_two_workers_do_not_take_same_row` (SKIP LOCKED), `test_job_registered_after_fill` (ordem no registry de `main`), `test_other_users_rows_untouched_when_user_id_given`.
- [ ] **Step 2: Run** `pytest tests/matching/test_refresh.py tests/test_worker_match.py -v` → FAIL.
- [ ] **Step 3: Implement** conforme as interfaces; módulo de estado `_reset_state()` para testes como em `collect_jobs`.
- [ ] **Step 4: Run** os dois arquivos + `tests/test_worker.py` → PASS.
- [ ] **Step 5: Commit** `feat(worker): score_matches job and refresh`.

---

### Task 6: API `/search-profile` e `/matches`

**Files:**
- Create: `api/matching.py`, `tests/test_api_matching.py`
- Modify: `api/main.py` (include_router; CORS já permite GET/POST/PATCH — adicionar PUT)

**Interfaces:**
- Consumes: `SearchFilters`, `rank_jobs`, `mark_pending`, `SearchProfile`, `Match`, `current_user_id`, o `model_version` do embedder (constante exposta por `rumo_core/embeddings/bge_m3.py`; a API não carrega o modelo — use a constante/atributo de classe, sem instanciar torch).
- Produces:
  - `GET /search-profile` → `{filters: SearchFilters}` (padrão vazio, sem criar linha); `PUT /search-profile` (corpo `SearchFilters`, 422 em inválido) cria/atualiza a linha `is_default` (upsert atômico) e devolve o mesmo formato.
  - `GET /matches?status=open&limit=50&offset=0` (usa os filtros salvos do perfil padrão; `limit` 1–100) → `{total, profile_version, vectorized, missing_embeddings, chunks_used, items: [{job: JobItem-sem-descrição, vec_score, ai_score|null, state|null, unknown_fields: ["salary"|"work_model"|"country"|"seniority"|"language"]}]}`.
  - `GET /matches/{job_id}` → vaga completa (como `GET /jobs/{id}`) + `{vec_score, ai_score, state, rationale, strengths, gaps, error}`; 404 se a vaga não for do usuário.
  - `POST /matches/refresh` → `RefreshResult` como JSON; 429 se houve chamada do mesmo usuário há < 10 s (estado em memória por processo, injetável para teste); 409 `{detail: "Confirme seu perfil antes de avaliar vagas."}` sem perfil; 409 com mensagem própria quando o perfil ainda não tem vetores.

- [ ] **Step 1: Write failing tests** (TestClient + `db`, no padrão de `tests/test_api_sources.py`, usuários descartáveis): `test_put_then_get_search_profile_roundtrip`, `test_put_rejects_invalid_enum_and_extra_fields`, `test_get_matches_applies_saved_filters`, `test_get_matches_not_vectorized_without_chunks`, `test_get_matches_limit_bounds_422`, `test_matches_never_leak_other_user`, `test_match_detail_includes_rationale_for_ok`, `test_match_detail_404_other_user`, `test_refresh_creates_pending_rows`, `test_refresh_rate_limited_429`, `test_refresh_without_profile_409`, `test_unknown_fields_marked`, `test_cors_allows_put`.
- [ ] **Step 2: Run** `pytest tests/test_api_matching.py -v` → FAIL.
- [ ] **Step 3: Implement** o router e registre em `api/main.py`; `unknown_fields` calculado em Python a partir dos campos `unknown`/`NULL` da vaga e de `salary_known`.
- [ ] **Step 4: Run** `pytest tests/test_api_matching.py tests/test_api_sources.py tests/test_api_cors.py -v` → PASS; depois `pytest -m "not slow"` completo.
- [ ] **Step 5: Commit** `feat(api): search profile and matches routes`.

---

### Task 7: Tela "Vagas" (frontend)

**Files:**
- Create: `web/lib/matching.ts`, `web/lib/matching.test.ts`, `web/app/jobs/layout.tsx`, `web/app/jobs/page.tsx`, `web/components/jobs/{FilterBar,MatchList,MatchRow,MatchPanel,ScoreBar}.tsx` com testes `*.test.tsx`
- Modify: `web/app/page.tsx` (card "Vagas" para `/jobs`), `web/app/globals.css` se preciso

**Interfaces:**
- Consumes: `request`/`ApiError` de `web/lib/profile.ts`, `safeJobUrl`, `workModelLabel`, `relativeTime` de `web/lib/sources.ts`; rotas da Task 6.
- Produces em `web/lib/matching.ts`: tipos `SearchFilters`, `MatchItem`, `MatchPage`, `MatchDetail`, `RefreshResult`; `getSearchProfile()`, `putSearchProfile(f)`, `listMatches(q)`, `getMatch(id)`, `refreshMatches()`; `hasPending(items)`; `scoreLabel(item)` (nota da IA, ou vetorial com legenda "pré-seleção").

Comportamento (da spec §6): lista densa (título, empresa, país, modelo, salário, nota); **nota dominante** em número tabular com barra fina (`ScoreBar`), sem selo colorido gritante; filtros manuais (departamento, país, modelo, senioridade, idioma, salário com moeda, "aceitar sem salário", "incluir desconhecidos") com debounce 300 ms que salva o perfil de busca e recarrega; botão "Avaliar melhores vagas" com polling de 10 s só enquanto houver `pendente`; painel lateral (não modal) com justificativa, pontos fortes, lacunas, link da vaga e `chunks_used`; atalhos `j`/`k` (navegar, ignorados dentro de inputs), `s`/`p` reservados sem efeito; guarda de resposta antiga; estados específicos (sem perfil confirmado, perfil vetorizando, sem vagas, sem resultado para os filtros, erro de rede com "Tentar de novo"); texto sempre como texto, `javascript:` nunca vira link; sem `Date.now()` em render (pré-render seguro).

- [ ] **Step 1: Write failing tests** (vitest/RTL, fetch mockado): `matching.test.ts` (query string, erro com `detail`), `MatchRow` (título hostil `<img onerror>` aparece literal; marca "salário desconhecido"), `ScoreBar` (nota da IA vs. vetorial com legenda), `FilterBar` (debounce 300 ms salva uma vez; validação de moeda com salário), `MatchPanel` (justificativa como texto, link `javascript:` não renderiza `<a>`), página (polling só com pendentes; para ao desmontar; resposta antiga ignorada; estados vazios; `j/k` troca a seleção e não age dentro de input).
- [ ] **Step 2: Run** `npm test` em `web/` → FAIL.
- [ ] **Step 3: Implement** os módulos e a página; ler `node_modules/next/dist/docs/` antes.
- [ ] **Step 4: Verificação** `npm test`, `npm run lint`, `npm run build` (a rota `/jobs` pré-renderiza); conferência visual em 375, 768 e desktop, claro e escuro, usando um mock descartável da API (não a API real, sem POST nela).
- [ ] **Step 5: Commit** `feat(web): jobs screen with scores and filters`.
