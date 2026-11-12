# Rumo — Marco 3: Coleta de vagas (infraestrutura + Greenhouse) — Plano de implementação

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** O worker coleta periodicamente as vagas das empresas cadastradas (primeira fonte: **Greenhouse**), normaliza por regras, deduplica, marca vagas encerradas, vetoriza só o que é novo ou mudou, e o usuário cadastra empresas e vê a saúde de cada coleta numa tela.

**Architecture:** Cada fonte é um conector atrás de uma interface (`Source`) registrado por nome, de modo que adicionar uma fonte = adicionar um arquivo (spec seção 3). A coleta roda no worker, **uma empresa por vez, isoladas** (falha de uma não afeta as outras; espera crescente por empresa). Normalização por **regras** primeiro; o LLM só preenche campos faltantes (modelo de trabalho, salário no texto), em job separado e limitado. O conteúdo bruto original é guardado para renormalizar sem nova coleta. A vetorização é um job próprio (CPU, local), igual ao dos chunks do perfil.

**Tech Stack:** o dos marcos anteriores; nenhuma dependência nova obrigatória (`httpx` já existe; conversão de HTML com `html.parser` da biblioteca padrão).

**Spec:** `docs/superpowers/specs/2026-11-11-rumo-design.md` (seções 3, 4 `companies`/`jobs`, 5.2, 6, 7, 9; marco 3 da seção 8). **Fora deste plano (marco 3b, plano próprio):** Lever, Ashby, Gupy, Bundesagentur für Arbeit e "importar por link"; lista-semente de empresas (não se inventam tokens: o usuário cadastra); conversão de salário para a moeda de referência e filtros/ranking (marco 4).

## Global Constraints

- Toda tabela tem `user_id`, `created_at`, `updated_at` (mixins de `rumo_core/models.py`); toda rota usa `current_user_id()` de `api/deps.py`; testes usam **usuários descartáveis** (nunca `user_id=1`, nada de deletes sem escopo).
- **Só fontes públicas/permitidas:** API pública do Greenhouse (sem autenticação, só `GET`). Nada de scraping de LinkedIn/Indeed/Seek/Vagas.com (spec seção 2).
- **Boa cidadania:** `User-Agent` genérico (`Rumo/0.1 job-search-tool`, **sem e-mail nem dados pessoais**), timeout de rede, espera entre empresas (`collect_delay_seconds`), teto de tamanho de resposta, retentativa com espera crescente em 429/5xx.
- **Fontes isoladas** (spec 6): falha de um conector/empresa nunca interrompe os outros; painel de saúde por empresa (última coleta, erro, nº de vagas).
- **Conteúdo bruto guardado** (`raw` JSONB) em cada vaga; vetoriza **apenas vagas novas ou alteradas**; `model_version` gravado junto de cada vetor (derivado do embedder, como no marco 2a).
- O LLM **só preenche campos faltantes**, só no worker (Groq), com validação Pydantic e a regra anti-invenção (número de salário precisa existir no texto da vaga).
- Conteúdo de vaga é **dado não confiável**: HTML é convertido em texto puro; nada de HTML da vaga é renderizado no web; texto de vaga que vai ao LLM vai delimitado (`wrap_untrusted`).
- Testes **sem internet**: respostas reais do Greenhouse salvas como fixtures (spec seção 7).
- Frontend: UI em PT-BR, tokens CSS existentes, sem os marcadores de "cara de IA" (spec 9.1–9.2), estados vazio/carregando/erro (9.4), acessibilidade (9.5).

## Review Focus

1. Quadro devolve lista vazia, 404 ou erro 5xx no meio da vida da empresa: as vagas abertas **não** são marcadas como encerradas por engano (guarda: lista vazia quando havia ≥5 abertas é tratada como falha suspeita; só uma coleta bem-sucedida e plausível fecha vagas).
2. Descrição com HTML escapado, entidades, `<script>`/`<style>`, listas e tabelas: vira texto limpo, sem conteúdo de script/estilo e sem quebrar a vetorização.
3. Vaga republicada com outro id, vaga com descrição alterada, vaga encerrada que reaparece: duplicata detectada, re-vetorização só quando o conteúdo mudou, reabertura correta, nada duplicado se o worker reiniciar no meio da coleta.
4. Localizações estranhas ("Remote", "Anywhere", "Berlin or Remote", "New York, NY", "São Paulo, SP, Brasil", vazias, várias separadas por ";"): sem exceção; desconhecido fica `unknown`/nulo, nunca um país errado por palpite.
5. Salários no texto ("$120k–$150k", "€60.000 – 80.000", "R$ 8.000", por hora vs por ano, faixas sem moeda): extração conservadora — na dúvida `salary_known=false`, nunca um valor errado.
6. Resposta gigante ou lenta, JSON inválido, quadro com milhares de vagas: teto de tamanho, timeout, erro registrado na saúde da empresa, demais empresas seguem.
7. Usuário cola uma URL do quadro em vez do token ("https://boards.greenhouse.io/foo", espaços, maiúsculas) ou um token inexistente: token normalizado ou 422 com mensagem clara; nada gravado.
8. Preenchimento por LLM que inventa salário ou modelo de trabalho não sustentado pelo texto: descartado; falhas do LLM não travam nem repetem para sempre.
9. Idioma de vagas curtas/mistas (PT/EN/DE): palpite conservador, `unknown` quando incerto.

---

## File Structure

```
rumo_core/
├─ models.py                       # + Company, Job
├─ config.py                       # + collect_interval_hours, collect_delay_seconds, fetch limits, llm_fill_batch
└─ sources/
   ├─ __init__.py                  # registro: get_source(name)
   ├─ base.py                      # RawJob, Source (Protocol), SourceError (+ subclasses)
   ├─ greenhouse.py                # GreenhouseSource
   ├─ normalize.py                 # regras: html_to_text, parse_location, detect_work_model, detect_seniority, detect_language, extract_salary, normalize_job
   ├─ ingest.py                    # upsert/dedupe/fechamento (serviço)
   ├─ embed_text.py                # texto da vaga para vetorizar
   └─ fill.py                      # preenchimento de campos faltantes via LLM
worker/collect_jobs.py             # collect_companies, embed_pending_jobs, fill_missing_job_fields
api/sources.py                     # /companies e /jobs
alembic/versions/0004_jobs.py
tests/fixtures/greenhouse/*.json   # respostas reais salvas (aparadas)
web/app/sources/page.tsx + web/components/sources/* + web/lib/sources.ts
tests/sources/ ...
```

---

### Task 1: Tabelas `companies` e `jobs` e migração

**Files:**
- Modify: `rumo_core/models.py`, `rumo_core/config.py`, `.env.example`
- Create: `alembic/versions/0004_jobs.py`
- Test: `tests/test_jobs_models.py` (marcador `db`, usuário descartável)

**Interfaces:**
- Produces (`UserScopedMixin` + `TimestampMixin`):
  - `Company`: `id`, `source: str` (`greenhouse`), `identifier: str` (token do quadro, minúsculo), `name: str`, `country: str | None`, `active: bool = True`, `last_run_at`, `last_success_at`, `next_run_at` (`DateTime(timezone=True) | None`), `last_error: str | None`, `consecutive_failures: int = 0`, `open_jobs: int = 0`; `UniqueConstraint(user_id, source, identifier)`.
  - `Job`: `id`, `company_id` (FK `companies.id`, CASCADE), `external_id: str`, `url: str`, `title: str`, `country: str | None`, `city: str | None`, `location_text: str = ""`, `work_model: str` (`onsite`|`hybrid`|`remote`|`unknown`, padrão `unknown`), `seniority: str` (`intern`|`junior`|`mid`|`senior`|`lead`|`manager`|`director`|`unknown`), `language: str` (`pt`|`en`|`de`|`unknown`), `department: str | None`, `description: Text`, `raw: JSONB`, `content_hash: str`, `fingerprint: str`, `status: str` (`open`|`closed`|`duplicate`, padrão `open`), `first_seen_at`, `last_seen_at`, `closed_at | None`, `posted_at | None`, salário: `salary_min`, `salary_max` (`Numeric | None`), `salary_currency: str | None`, `salary_period: str | None` (`year`|`month`|`hour`), `salary_known: bool = False`, `salary_ref_min`, `salary_ref_max` (`Numeric | None`, preenchidos no marco 4), `llm_filled_at | None`, `fill_attempts: int = 0`, `embedding: Vector(1024) | None`, `model_version: str | None`; `UniqueConstraint(user_id, company_id, external_id)`; índices em `(user_id, status)`, `(user_id, company_id, fingerprint)`, `(user_id, model_version)`.
  - `Settings`: `collect_interval_hours: int = Field(6, gt=0)`, `collect_delay_seconds: float = Field(1.0, ge=0)`, `fetch_timeout_seconds: float = Field(20, gt=0)`, `fetch_max_bytes: int = Field(20_000_000, gt=0)`, `llm_fill_batch: int = Field(5, gt=0)`.

- [ ] **Step 1: Write the failing tests** — `test_company_job_roundtrip` (insere empresa e vaga para um usuário descartável e lê de volta, conferindo defaults `work_model="unknown"`, `status="open"`, `salary_known=False`, `fill_attempts=0`); `test_company_unique_per_user_source_identifier` (duplicata → `IntegrityError`; outro usuário com o mesmo token é aceito); `test_job_unique_per_company_external_id`; `test_job_vector_dimension` (1024 ok, 3 falha); `test_cascade_delete_company_removes_jobs`.
- [ ] **Step 2: Run** `.venv/Scripts/python -m pytest tests/test_jobs_models.py -v` — Expected: FAIL.
- [ ] **Step 3: Implement** os modelos, a migração `0004` (down_revision `"0003"`; `downgrade` derruba `jobs` e depois `companies`) e as settings (`.env.example` documenta as novas variáveis).
- [ ] **Step 4: Run** o mesmo comando e `-m "not slow" -q` — Expected: PASS.
- [ ] **Step 5: Commit** `feat(jobs): companies and jobs tables, migration 0004`.

---

### Task 2: Interface de fontes e conector do Greenhouse

**Files:**
- Create: `rumo_core/sources/__init__.py`, `base.py`, `greenhouse.py`; `tests/fixtures/greenhouse/*.json`
- Modify: `pyproject.toml` (`packages` inclui `rumo_core.sources`)
- Test: `tests/sources/test_greenhouse.py`

**Interfaces:**
- Produces:
  - `@dataclass(frozen=True) RawJob: external_id: str; title: str; url: str; location_text: str; description_html: str; department: str | None; posted_at: datetime | None; raw: dict`.
  - Erros: `SourceError(Exception)`; `SourceNotFound(SourceError)` (quadro inexistente/404); `SourceUnavailable(SourceError)` (rede, timeout, 5xx, 429 esgotado); `SourceBadResponse(SourceError)` (JSON inválido, formato inesperado, resposta acima do teto).
  - `class Source(Protocol)`: `name: str`; `async def fetch_jobs(self, identifier: str) -> list[RawJob]`; `def normalize_identifier(self, text: str) -> str` (aceita token ou URL do quadro, devolve o token em minúsculo, `ValueError` se inválido).
  - `get_source(name: str) -> Source` (registro por nome; `KeyError` com mensagem para fonte desconhecida) e `register_source`.
  - `GreenhouseSource(client: httpx.AsyncClient, *, max_bytes: int, sleep=asyncio.sleep)`: `GET https://boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true`; cabeçalho `User-Agent: Rumo/0.1 job-search-tool`; 404 → `SourceNotFound`; 429/5xx → até 3 retentativas com espera crescente (respeita `Retry-After` numérico até 60 s) e depois `SourceUnavailable`; timeouts/erros de rede → `SourceUnavailable`; corpo acima de `max_bytes` (lido em streaming) → `SourceBadResponse`; mapeia `id`→`external_id` (str), `title`, `absolute_url`→`url`, `location.name`→`location_text`, `content` (HTML **escapado** pela API: decodificar entidades uma vez para obter o HTML) →`description_html`, `departments[0].name`→`department`, `updated_at`/`first_published`→`posted_at` (ISO 8601 com fuso; ausente → `None`), `raw` = o objeto original da vaga. Itens sem `id` ou `title` são ignorados (não derrubam o lote).
  - `normalize_identifier` aceita `https://boards.greenhouse.io/foo`, `https://job-boards.greenhouse.io/foo/jobs/123`, `foo`, ` Foo `; rejeita vazio, espaços internos e caracteres fora de `[a-z0-9_-]` (após minúsculas).

- [ ] **Step 1: Record fixtures** — com `curl` (somente `GET`, sem autenticação, um pedido por quadro), salvar respostas **reais** de 2 quadros públicos do Greenhouse que respondam hoje (escolher quadros conhecidos, verificar o `200`), **aparadas para 6–8 vagas cada**, em `tests/fixtures/greenhouse/<token>.json`, sem dados pessoais além do que a vaga publica; registrar em `tests/fixtures/greenhouse/README.md` a data da captura e o token. Mais fixtures escritas à mão: `empty.json` (`{"jobs": [], "meta": {"total": 0}}`) e `malformed.json`.
- [ ] **Step 2: Write the failing tests** (cliente `httpx.MockTransport`, sem rede): `test_parses_real_fixture` (nº de vagas, ids como `str`, `url`, `location_text`, `department`, `posted_at` com fuso, HTML decodificado contém tags reais e não `&lt;`); `test_skips_items_without_id_or_title`; `test_404_is_not_found`; `test_5xx_retries_then_unavailable` (sleep injetado, 4 chamadas no total, esperas crescentes); `test_429_retry_after_respected_and_capped`; `test_timeout_is_unavailable`; `test_invalid_json_is_bad_response`; `test_oversized_body_is_bad_response`; `test_user_agent_is_generic_and_sent`; `test_normalize_identifier_variants`; `test_get_source_registry`.
- [ ] **Step 3: Run** `pytest tests/sources/test_greenhouse.py -v` — Expected: FAIL.
- [ ] **Step 4: Implement** conforme as assinaturas.
- [ ] **Step 5: Run** — Expected: PASS. **Commit** `feat(sources): source interface and Greenhouse connector`.

---

### Task 3: Normalização por regras

**Files:**
- Create: `rumo_core/sources/normalize.py`
- Test: `tests/sources/test_normalize.py` (casos de tabela; usa trechos das fixtures reais para `html_to_text` e `parse_location`)

**Interfaces:**
- Produces (funções puras, sem rede nem banco):
  - `html_to_text(html: str) -> str`: via `html.parser`; ignora `script`/`style`; blocos (`p`, `br`, `li`, `h1-6`, `div`, `tr`) viram quebras; `li` ganha `- `; entidades decodificadas; espaços normalizados; **sem** URLs de tracking inventadas.
  - `parse_location(text: str) -> tuple[str | None, str | None]` (país ISO-3166 alpha-2 como `"BR"`, `"US"`, `"DE"`…, cidade): tabela interna de aliases de países (inclui nomes em PT/EN/DE: Brazil/Brasil/Brasilien, Germany/Deutschland/Alemanha, United States/USA/EUA, Australia…) e UF/estados dos EUA; formatos "Cidade, País", "Cidade, UF, País", "Cidade, ST" (EUA), "Remote - USA"; várias localizações ("A; B", "A or B") → usa a **primeira** se inequívoca, senão `(None, None)`; nunca palpita.
  - `detect_work_model(location_text: str, title: str, description: str) -> str` (`remote`/`hybrid`/`onsite`/`unknown`): palavras-chave (remote/remoto/home office/híbrido/hybrid/on-site/presencial/vor Ort/Remote-first…), prioridade título+localização sobre descrição; descrição só decide com frase explícita ("this is a fully remote role"); conflito → `unknown`.
  - `detect_seniority(title: str) -> str`: estagiário/intern, jr/junior, pleno/mid, sr/senior/sênior, staff/principal/lead, manager/gerente, director/diretor/head of; sem indício → `unknown`.
  - `detect_language(text: str) -> str` (`pt`/`en`/`de`/`unknown`): heurística de stopwords sobre os primeiros ~1500 caracteres; empate ou texto < 200 caracteres sem evidência clara → `unknown`.
  - `extract_salary(text: str) -> SalaryInfo | None` (`SalaryInfo(min: Decimal | None, max: Decimal | None, currency: str, period: str | None)`): reconhece moedas `$ US$ USD € EUR £ GBP R$ BRL A$ AUD C$ CAD CHF`, faixas com `-`/`–`/`to`/`bis`/`a`, sufixos `k`, separadores de milhar/decimal PT/EN/DE, períodos (`/year`, `per hour`, `ao ano`, `pro Jahr`, `por mês`, `/h`…); **ignora** números que não sejam claramente salário (anos, "401k", "10 years", percentuais, quantias de equity, "$1B funding"); exige moeda explícita ou contexto de remuneração ("salary", "compensation", "Gehalt", "salário", "pay range"); ambíguo → `None`.
  - `has_pay_context(text: str) -> bool`: verdadeiro se o texto menciona remuneração ("salary", "compensation", "pay range", "Gehalt", "salário", "remuneração"…) — usado também pela Task 6 para decidir se vale chamar o LLM.
  - `@dataclass NormalizedJob`: todos os campos de `Job` derivados (`description`, `country`, `city`, `work_model`, `seniority`, `language`, `salary_*`, `salary_known`, `content_hash`, `fingerprint`); `normalize_job(raw: RawJob) -> NormalizedJob`: `content_hash` = sha256 de (título, localização, texto da descrição, departamento); `fingerprint` = sha256 de (título normalizado em minúsculas sem pontuação, `location_text` normalizado) — usado para duplicatas dentro da empresa.

- [ ] **Step 1: Write the failing tests** — tabelas parametrizadas para cada função (≥ 6 casos por função, incluindo os do Review Focus 2, 4, 5 e 9): `html_to_text` (script/style removidos, `<li>`, entidades `&amp; &nbsp;`, HTML malformado sem exceção, entrada vazia); `parse_location` ("Berlin, Germany"→("DE","Berlin"), "São Paulo, SP, Brasil", "New York, NY", "Remote - US"→("US",None), "Anywhere"→(None,None), "Berlin or Remote"→primeira inequívoca ou nulos, "", "Springfield" ambíguo→nulos); `detect_work_model` (título "(Remote)", "Hybrid – 3 days", conflito → unknown); `detect_seniority` ("Sr. Backend Engineer", "Engenheiro de Software Pleno", "Head of Data", "Software Engineer"→unknown); `detect_language` (parágrafos PT/EN/DE, texto curto→unknown); `extract_salary` (`$120k–$150k`, `€60.000 – 80.000 pro Jahr`, `R$ 8.000 por mês`, `$45/hour`, `401k match`→None, `10 years of experience`→None, `$2B in funding`→None, sem moeda→None); `normalize_job` (campos completos e `content_hash` estável; muda ao mudar a descrição; `fingerprint` igual para o mesmo título/local com ids diferentes); teste com **as fixtures reais**: todas as vagas normalizam sem exceção.
- [ ] **Step 2: Run** `pytest tests/sources/test_normalize.py -v` — Expected: FAIL.
- [ ] **Step 3: Implement** conforme as assinaturas (regex compiladas no módulo; sem dependência nova).
- [ ] **Step 4: Run** — Expected: PASS.
- [ ] **Step 5: Commit** `feat(sources): rule-based job normalization`.

---

### Task 4: Ingestão (upsert, duplicatas, encerramento) e job de coleta do worker

**Files:**
- Create: `rumo_core/sources/ingest.py`, `worker/collect_jobs.py` (parte `collect_companies`)
- Modify: `worker/main.py` (registrar o job; `reset`/startup se necessário)
- Test: `tests/sources/test_ingest.py`, `tests/test_worker_collect.py` (`db`; fontes e relógio falsos)

**Interfaces:**
- Consumes: `Source`/`RawJob`/erros (Task 2), `normalize_job` (Task 3), modelos (Task 1).
- Produces:
  - `@dataclass IngestResult: created: int; updated: int; unchanged: int; closed: int; reopened: int; duplicates: int`.
  - `ingest_jobs(session, user_id: int, company: Company, raws: list[RawJob], *, now: datetime) -> IngestResult` (uma transação; o chamador faz commit): para cada vaga normalizada, `external_id` novo → cria `open` (`first_seen_at=now`); existente com `content_hash` igual → só `last_seen_at`; existente com hash diferente → atualiza campos e **zera** `embedding`/`model_version`/`llm_filled_at` (re-vetoriza e re-preenche) e `status` `open`; existente `closed` que reaparece → `open`, `closed_at=None` (`reopened`); `fingerprint` igual a outra vaga **aberta** da mesma empresa com `external_id` diferente → a nova entra como `duplicate` (não vetorizada); vagas abertas da empresa **ausentes** da lista → `closed` (`closed_at=now`) **exceto** se a lista veio vazia e a empresa tinha ≥ 5 vagas abertas — nesse caso `raise SuspiciousEmptyResult` (nada é fechado; a coleta registra falha). Atualiza `company.open_jobs`. `raw` e `description` sempre guardados.
  - `async def collect_companies(session_factory, *, source_getter=get_source, user_id: int | None = None, clock=..., sleep=asyncio.sleep, interval_hours: int, delay_seconds: float) -> int`: seleciona empresas `active` com `next_run_at IS NULL OR next_run_at <= now` (mais atrasadas primeiro; `FOR UPDATE SKIP LOCKED`), processa **uma por vez** com espera `delay_seconds` entre elas; por empresa: busca → `ingest_jobs` → sucesso: `last_success_at`, `last_run_at`, `last_error=None`, `consecutive_failures=0`, `next_run_at = now + interval_hours`; falha (`SourceError`/`SuspiciousEmptyResult`/exceção inesperada): grava `last_run_at`, `last_error` (mensagem curta PT-BR, sem corpo da resposta), `consecutive_failures += 1`, `next_run_at = now + min(15 min × 2^(falhas-1), interval)`; **cada empresa em sua própria transação**; `CancelledError` não deixa empresa presa; retorna quantas empresas processou.

- [ ] **Step 1: Write the failing tests** — ingest: `test_new_jobs_created_open`, `test_unchanged_job_only_touches_last_seen`, `test_changed_description_resets_embedding_and_fill`, `test_missing_jobs_closed`, `test_closed_job_reappears_reopened`, `test_same_fingerprint_new_id_is_duplicate`, `test_empty_result_with_many_open_raises_and_closes_nothing`, `test_empty_result_with_few_open_closes_them`, `test_open_jobs_counter_updated`, `test_ingest_idempotent_twice`; coleta: `test_success_updates_health_and_schedules_next`, `test_failure_records_error_and_exponential_backoff_capped`, `test_not_found_marks_error_message_pt_br`, `test_one_company_failure_does_not_stop_others`, `test_inactive_and_not_due_companies_skipped`, `test_user_scope_filter`, `test_skip_locked_company` (segunda sessão segura o lock), `test_cancelled_midway_leaves_no_half_state`, `test_error_message_has_no_response_body`.
- [ ] **Step 2: Run** `pytest tests/sources/test_ingest.py tests/test_worker_collect.py -v` — Expected: FAIL.
- [ ] **Step 3: Implement**; registrar `collect_companies` a cada 60 s em `worker/main.py` (a elegibilidade por `next_run_at` governa a frequência real).
- [ ] **Step 4: Run** `pytest -m "not slow" -q` — Expected: PASS.
- [ ] **Step 5: Commit** `feat(jobs): ingestion with dedupe/closing and collection job`.

---

### Task 5: Vetorização das vagas

**Files:**
- Create: `rumo_core/sources/embed_text.py`
- Modify: `worker/collect_jobs.py` (+ `embed_pending_jobs`), `worker/main.py`
- Test: `tests/sources/test_embed_text.py`, `tests/test_worker_embed_jobs.py` (`db`; embedder falso)

**Interfaces:**
- Consumes: `Embedder` (`dimensions`, `model_version`, `embed`), padrão de `embed_pending_chunks` em `worker/jobs.py` (lote, `asyncio.to_thread` sob `Lock`, commit por lote, backoff exponencial 5→300 s, relógio injetável, `user_id` opcional).
- Produces:
  - `job_embedding_text(job, company_name: str) -> str`: título, empresa, localização/modelo, departamento e descrição (truncada em ~6000 caracteres), em ordem fixa; determinístico.
  - `async def embed_pending_jobs(session_factory, embedder: Embedder, batch_size: int, *, user_id: int | None = None, clock=...) -> int`: seleciona vagas `status='open'` com `embedding IS NULL OR model_version IS DISTINCT FROM embedder.model_version` (`FOR UPDATE SKIP LOCKED`), em lotes; **vagas `closed`/`duplicate` não são vetorizadas**; grava `embedding` e `model_version`; falha do embedder → log uma vez (sem texto da vaga) e backoff; retorna quantas vetorizou. Job registrado a cada 5 s.

- [ ] **Step 1: Write the failing tests** — texto determinístico e truncado; `test_embeds_open_jobs_only`; `test_changed_job_reembedded_unchanged_not` (conta chamadas do embedder falso); `test_old_model_version_reembedded`; `test_batching_and_commit_per_batch`; `test_failure_backoff_and_no_text_in_logs`; `test_model_not_loaded_when_nothing_pending`; `test_lock_skip`; `test_user_scope`.
- [ ] **Step 2: Run** `pytest tests/sources/test_embed_text.py tests/test_worker_embed_jobs.py -v` — Expected: FAIL.
- [ ] **Step 3: Implement**; reaproveitar helpers de `worker/jobs.py` quando natural, sem refatorar o código do marco 2.
- [ ] **Step 4: Run** `pytest -m "not slow" -q` — Expected: PASS.
- [ ] **Step 5: Commit** `feat(jobs): job embedding worker job`.

---

### Task 6: Preenchimento de campos faltantes via LLM

**Files:**
- Create: `rumo_core/sources/fill.py`
- Modify: `worker/collect_jobs.py` (+ `fill_missing_job_fields`), `worker/main.py`
- Test: `tests/sources/test_fill.py`, `tests/test_worker_fill_jobs.py` (`db`; LLM falso)

**Interfaces:**
- Consumes: `LLMProvider`, `wrap_untrusted` (`rumo_core/profile/prompting.py`), `numbers_supported` (`rumo_core/interview/suggestions.py`), padrão de pausa por rate limit/erros de `worker/interview_jobs.py`.
- Produces:
  - `FillResult(BaseModel)`: `work_model: Literal["remote","hybrid","onsite","unknown"] = "unknown"`, `salary_min: Decimal | None`, `salary_max: Decimal | None`, `salary_currency: str | None`, `salary_period: Literal["year","month","hour"] | None`.
  - `async def fill_fields(llm, job_view) -> FillResult` (prompt de sistema fixo PT-BR + cauda EN com o json schema e a palavra "json": o texto da vaga é DADO em `<vaga>`; responda `unknown`/`null` se o texto não disser; nunca inferir) e `validate_fill(result, description: str, current) -> FillResult`: descarta `work_model` que contradiga um valor já determinado por regras (só preenche quando era `unknown`); descarta salário se `salary_known` já era verdadeiro, se algum número **não** estiver no texto (`numbers_supported`), se min > max ou moeda/período ausentes; **nunca sobrescreve** campo já preenchido por regras.
  - `async def fill_missing_job_fields(session_factory, llm | None, *, batch: int, user_id=None, clock=...) -> int`: seleciona até `batch` vagas `open` com `llm_filled_at IS NULL AND fill_attempts < 3` e **algum campo faltando** (`work_model='unknown'` ou `salary_known=false` com o texto contendo indício de remuneração — a mesma regex de "contexto de remuneração" da Task 3); uma chamada por vaga; sucesso (mesmo sem nada a preencher) → `llm_filled_at=now`; `LLMInvalidResponse`/`LLMError` → `fill_attempts += 1` (após 3, para de tentar); `LLMRateLimited` → pausa de 60 s do job (relógio injetável, um aviso por pausa), sem contar tentativa; `llm is None` (sem chave) → não faz nada e avisa uma vez; nunca registra texto da vaga nos logs. Registrado a cada 20 s.

- [ ] **Step 1: Write the failing tests** — `test_fills_unknown_work_model_only`; `test_does_not_overwrite_rule_values`; `test_salary_with_number_not_in_text_rejected`; `test_salary_min_gt_max_rejected`; `test_missing_currency_rejected`; `test_prompt_wraps_posting_and_has_json_schema` (texto hostil `</vaga>` neutralizado, ausente do sistema); `test_selection_skips_complete_jobs`; `test_attempts_capped_at_3`; `test_rate_limited_pauses_without_counting`; `test_no_key_noop_warn_once`; `test_unexpected_error_isolated`; `test_user_scope`; `test_no_posting_text_in_logs`.
- [ ] **Step 2: Run** `pytest tests/sources/test_fill.py tests/test_worker_fill_jobs.py -v` — Expected: FAIL.
- [ ] **Step 3: Implement** conforme as assinaturas.
- [ ] **Step 4: Run** `pytest -m "not slow" -q` — Expected: PASS.
- [ ] **Step 5: Commit** `feat(jobs): LLM fill of missing job fields`.

---

### Task 7: Rotas da API (empresas e vagas)

**Files:**
- Create: `api/sources.py`
- Modify: `api/main.py` (router no topo, sem `noqa`)
- Test: `tests/test_api_sources.py` (`db`, `TestClient`, usuários descartáveis, fonte falsa injetada por override)

**Interfaces:**
- Consumes: `get_source`, `Source.normalize_identifier`, modelos.
- Produces (escopadas por `current_user_id`; dependência `source_getter` sobrescrevível em testes):
  - `POST /companies` body `{source: "greenhouse", identifier: str, name?: str, country?: str}` → `201 CompanyOut`; normaliza o identificador (`ValueError` → 422 PT-BR); **valida buscando o quadro** (timeout curto): `SourceNotFound` → 422 "Quadro não encontrado no Greenhouse"; `SourceUnavailable` → 503 com mensagem; duplicata → 409; `name` padrão = identificador; define `next_run_at=now` (coleta na próxima rodada).
  - `GET /companies` → lista com saúde: `{id, source, identifier, name, country, active, last_run_at, last_success_at, next_run_at, last_error, consecutive_failures, open_jobs}`.
  - `PATCH /companies/{id}` body `{active?: bool, name?: str, country?: str}`; `POST /companies/{id}/collect-now` → seta `next_run_at=now` e `200`; 404 para empresa de outro usuário.
  - `GET /jobs?status=open&company_id=&limit=50&offset=0` → `{total, items: [{id, company_id, company_name, title, url, location_text, country, city, work_model, seniority, language, department, salary_known, status, first_seen_at, posted_at}]}` (ordenado por `first_seen_at` desc; `limit` ≤ 200; **sem** `description`/`raw` na lista); `GET /jobs/{id}` → inclui `description` (texto limpo) e salário.
  - Fonte desconhecida no corpo → 422; `OperationalError` → 503 conforme o handler existente.

- [ ] **Step 1: Write the failing tests** — `test_add_company_normalizes_url_and_validates`, `test_add_company_not_found_422_nothing_stored`, `test_add_company_unavailable_503_nothing_stored`, `test_add_company_duplicate_409`, `test_unknown_source_422`, `test_list_companies_health_fields`, `test_patch_toggle_active`, `test_collect_now_sets_next_run`, `test_cross_user_404`, `test_jobs_list_pagination_and_filters`, `test_jobs_list_excludes_description_and_raw`, `test_job_detail_has_clean_text`, `test_limit_cap`, `test_import_api_sources_fresh_subprocess`.
- [ ] **Step 2: Run** `pytest tests/test_api_sources.py -v` — Expected: FAIL.
- [ ] **Step 3: Implement** o router com modelos de resposta Pydantic; sem regra de negócio nas rotas além da validação.
- [ ] **Step 4: Run** `pytest -m "not slow" -q` — Expected: PASS.
- [ ] **Step 5: Commit** `feat(api): companies and jobs routes`.

---

### Task 8: Tela "Fontes" no frontend

**Files:**
- Create: `web/lib/sources.ts`, `web/app/sources/page.tsx`, `web/components/sources/{AddCompanyForm,CompanyRow,JobsList}.tsx` e testes ao lado
- Modify: `web/app/page.tsx` (link para "Fontes"), `web/app/globals.css` (tokens existentes)

**Interfaces:**
- Consumes: rotas da Task 7; helpers `request`/`ApiError` de `web/lib/profile.ts`.
- Produces: `listCompanies()`, `addCompany({source, identifier, name?, country?})`, `patchCompany(id, patch)`, `collectNow(id)`, `listJobs({status, companyId, limit, offset})`, `getJob(id)` com tipos; componentes `AddCompanyForm` (campo único "Token ou URL do quadro Greenhouse", ajuda curta de onde achar o token, estados enviando/erro com a mensagem do servidor, limpa ao sucesso, botão desabilitado após o primeiro clique), `CompanyRow` (nome, token, país, ativa/pausada, "última coleta há…", "N vagas abertas", erro legível quando houver — com o número de falhas — e ações "Coletar agora" e "Pausar/Ativar"), `JobsList` (somente leitura, paginação "Mais vagas", colunas título/empresa/local/modelo/data; texto puro, **nunca** `dangerouslySetInnerHTML`; link "Abrir vaga" com `rel="noopener noreferrer"` e `target="_blank"`).
- Comportamento: carrega empresas e as primeiras vagas; polling a cada 10 s **apenas** enquanto alguma empresa tem `next_run_at` vencido/`last_run_at` nulo (coleta pendente), com limpeza de timers e parada ao desmontar; estados: vazio (explica como cadastrar a primeira empresa), carregando, erro de rede com "Tentar de novo"; datas relativas calculadas no cliente sem quebrar a pré-renderização (mesmo cuidado do onboarding com `Date.now()`).

- [ ] **Step 1: Write the failing tests** — `lib/sources.ts` (mapeamento de erros 422/409/503, query string de `listJobs`); `AddCompanyForm` (envia a URL colada, mostra erro do servidor, desabilita após clique, limpa no sucesso); `CompanyRow` (erro e contagem de falhas visíveis em texto, "Coletar agora" chama a API uma vez, alterna ativa); `JobsList` (renderiza título hostil `<img onerror>` como texto, link seguro, "Mais vagas"); página (estado vazio, polling para quando não há coleta pendente e timers limpos ao desmontar, erro de rede com retentativa).
- [ ] **Step 2: Run** `cd web && npm test` — Expected: FAIL.
- [ ] **Step 3: Implement** seguindo spec 9.1–9.5 (tokens, rótulos associados, foco visível, mensagens específicas, `aria-live` discreto, sem decoração genérica).
- [ ] **Step 4: Run** `cd web && npm test && npm run lint && npm run build` — Expected: PASS. Verificar visualmente em 3 larguras (375, 768, desktop) e claro/escuro com o navegador de preview, usando um mock local descartável da API (dizer qual foi usado).
- [ ] **Step 5: Commit** `feat(web): sources screen`.

---

## Verificação ponta a ponta (fim do marco)

Com `docker compose up -d --build` (o serviço `migrate` aplica a `0004`): abrir `/sources`, cadastrar um quadro público real do Greenhouse (o usuário escolhe o token), esperar a coleta (≤ 1 min), conferir vagas listadas, contagem na empresa e, depois, `embedding` preenchido nas vagas abertas (`GET /jobs` não expõe o vetor; conferir por SQL ou pelo contador de vetorização). Rodar de novo "Coletar agora": nada duplicado, `unchanged` na maioria. Usa a internet pública e o modelo local; feito pelo usuário ou com a aprovação dele.
