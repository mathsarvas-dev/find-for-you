# Rumo Marco 5 — Candidatura (currículo adaptado, perguntas, PDF) — Plano de implementação

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** De uma vaga em `/jobs`, "Preparar candidatura" leva a um kit pronto — currículo no idioma da vaga e no formato do país (PDF + texto), cobertura "n de N", lacunas que viram perguntas, respostas das perguntas extras, carta opcional e link da vaga — e termina em "Apliquei", sem nenhum envio automático e sem nenhum fato inventado.

**Architecture:** Novo pacote `rumo_core/applications/` (puro, exceto `select.py`): requisitos (LLM), evidências (pgvector), rascunho (fatos copiados por código, texto do LLM com `source_refs`), validador anti-invenção, cobertura, lacunas, perguntas e render (Jinja2 → HTML → WeasyPrint). O LLM só é chamado no worker (`worker/prepare_applications.py`, 1 unidade de trabalho por tick, mesmo portão/teto/pausa do `score_matches`); a API (`api/applications.py`) só lê/grava SQL, chama o Greenhouse (perguntas, como `api/sources.py` já faz) e renderiza PDF sob demanda. Web: entrada no painel de `/jobs`, páginas `/candidaturas` e `/candidaturas/[id]` (trilha Ler a vaga → Currículo → Perguntas → Kit), passo 7 da trilha e item "Candidaturas" na lateral.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy 2 + Alembic, Postgres 16 + pgvector, Pydantic v2, Jinja2 (autoescape), WeasyPrint (Pango/HarfBuzz no Debian da imagem `python:3.12-slim`), pypdf (já é dependência; extrai texto nos testes), pytest (marcadores `db`/`slow`), Next.js 16 + vitest/RTL.

**Spec:** `docs/superpowers/specs/2026-11-14-rumo-marco-5-candidatura-design.md` (autoridade; este plano argumenta a partir dela) e `docs/superpowers/specs/2026-11-11-rumo-design.md` §5.5, §9.

## Global Constraints
- Ordem: executa **depois** do marco 4b (`docs/superpowers/plans/2026-11-14-rumo-marco-4b-filtros-chat.md`), que cria `alembic/versions/0007_chat_filters.py` (`revision = "0007"`) e mexe em `web/app/jobs/page.tsx`; este plano parte da versão mesclada. Migração `0009` com `down_revision = "0008"` (a `0008` é do marco 3b, que executa antes do 5; se o 3b ainda não tiver sido mesclado, confirmar a cadeia com `alembic heads` antes de criar a migração).
- **Tarefa 0 é um portão:** se o build Docker com WeasyPrint ou o teste de fumaça do PDF falhar, PARE e devolva a decisão à pessoa (plano B: Chromium, mais pesado). Nenhuma tarefa de PDF começa antes.
- Todas as tabelas novas com `user_id` (`UserScopedMixin`) e toda consulta escopada por `user_id` (`api.deps.current_user_id`); recurso de outro usuário → 404.
- O LLM só é chamado no worker, via o `GroqProvider`/limitador compartilhado; nunca na API. Timeout de 25 s por chamada (`application_call_timeout`), `application_max_attempts = 3`, `application_daily_limit = 5` (gerações de currículo em 24 h corridas, todos os usuários — mesmo critério do `match_daily_limit`), pausa enquanto qualquer entrevista estiver `generating`/`working` (`INTERVIEW_LLM_STATES` de `worker/match_jobs.py`), pausa de 60 s em `LLMRateLimited`/timeout sem contar tentativa.
- **Fatos copiados, texto escrito:** empresa, cargo, datas, local, instituição, grau, certificados, idiomas e contatos vêm do perfil por código; o LLM escreve só resumo, marcadores e a escolha/ordem de ids de habilidades. Campos extras na saída do LLM são ignorados (`extra="ignore"`).
- Texto da vaga e do perfil são dados não confiáveis: vaga entra no prompt por `wrap_untrusted("job", ...)`, cortada em **6000** caracteres; nada de texto de vaga/LLM/perfil nos logs (só ids e nome da classe da exceção).
- Ao Groq nunca vão e-mail, telefone, links, nome da pessoa nem itens do perfil além das evidências selecionadas (2 por requisito) e da lista de nomes de habilidades.
- Requisitos: cada item ≤ **200** caracteres, até **25** no total (must, depois nice, depois keywords).
- Idioma = `job.language` (`unknown` → `en`); modelo por `job.country`: `US`→`us`; `DE`/`AT`/`CH`→`de`; `AU`→`au`; `BR`→`br`; outro/`None`→`us`.
- Perguntas sensíveis (autorização de trabalho, visto, pretensão salarial, deficiência, gênero, raça, antecedentes, data de nascimento, relocação, dados pessoais/contato) **nunca** são rascunhadas; lista de palavras-chave versionada e conservadora.
- Banco de respostas: reuso só adapta idioma/formatação; números e moedas do rascunho ⊆ os do item do banco.
- Nada binário no banco: PDF gerado sob demanda das linhas aprovadas; PDF e texto só com documento `valid`; `PdfUnavailable` → 503 em PT-BR.
- Jinja2 com `autoescape=True`; texto de vaga/perfil/LLM nunca vira HTML no web (sempre texto); links só http/https.
- Texto da UI e mensagens de erro em PT-BR, curtos, verbo no infinitivo nos botões; commits em inglês (conventional commits).
- `web/AGENTS.md`: ler `node_modules/next/dist/docs/` antes de escrever código Next (rotas dinâmicas e `useParams` mudaram no 16).
- Testes de banco com `@pytest.mark.db`, banco de dev compartilhado, usuários descartáveis (`User(email=f"...-{uuid4().hex}@example.test")`) apagados no teardown (padrão de `tests/test_api_matching.py`); testes de PDF com `@pytest.mark.slow`.

## Review Focus
1. Edição manual que introduz um número/termo novo, ou linha `needs_fix`, nunca chega ao PDF/texto nem conta na cobertura (Task 7, 10, 3) — teste `test_pdf_and_txt_blocked_while_needs_fix` e `test_requirement_cited_only_by_invalid_line_not_covered`.
2. Perfil confirmado de novo (inclusive por "Tenho isso") enquanto a geração está no LLM: o resultado é descartado e a candidatura volta a gerar com a versão nova, nunca grava linhas validadas contra a versão errada (Task 6) — `test_new_profile_version_during_generation_discards_and_requeues`.
3. Vaga em alemão: substantivos com maiúscula não podem reprovar toda tradução legítima, e nome de empresa inventado ainda reprova (Task 4) — `test_german_nouns_pass_but_invented_company_rejected`.
4. Texto hostil (`<script>`, `</job>`, "ignore as regras") na vaga, no perfil ou numa linha editada: aparece literal no HTML/PDF e na tela, não muda o prompt (Task 5, 10, 9) — `test_html_escapes_hostile_text`, `test_prompt_neutralises_job_tags`.
5. Duplo clique em "Preparar candidatura"/"Gerar de novo" e corrida de dois workers: uma candidatura por vaga, uma geração por vez (Task 6, 7) — `test_create_is_idempotent_under_concurrency`, `test_two_workers_do_not_take_same_application`.

---

## Fatia 0 — PDF primeiro

### Task 0: WeasyPrint, Jinja2, bibliotecas do sistema e teste de fumaça (PORTÃO)

**Files:**
- Modify: `pyproject.toml`, `Dockerfile`
- Create: `rumo_core/applications/__init__.py`, `rumo_core/applications/pdf.py`, `tests/applications/test_pdf_smoke.py` (sem `__init__.py`, como `tests/matching/`)

**Interfaces:**
- Produces: `class PdfUnavailable(Exception)`; `html_to_pdf(html: str) -> bytes` em `rumo_core/applications/pdf.py` (importa `weasyprint` dentro da função; `ImportError`/`OSError` de biblioteca nativa ausente e qualquer erro do WeasyPrint → `PdfUnavailable`).
- `pyproject.toml`: dependências `"jinja2>=3.1"`, `"weasyprint>=62"`; `packages` ganha `"rumo_core.applications"`; `[tool.setuptools.package-data]` ganha `"rumo_core.applications" = ["templates/*.html", "static/*.css"]`.
- `Dockerfile`: antes do `pip install`, `apt-get install -y --no-install-recommends libpango-1.0-0 libpangoft2-1.0-0 libharfbuzz0b libharfbuzz-subset0 fonts-dejavu-core && rm -rf /var/lib/apt/lists/*` (conferir os nomes no Debian da imagem base atual; a fonte DejaVu cobre acentos e ß e é embutida no PDF pelo WeasyPrint).

- [ ] **Step 1: Write the failing test** `tests/applications/test_pdf_smoke.py` (`@pytest.mark.slow`): `test_pdf_smoke_accents_and_eszett` — `pdf = html_to_pdf("<p>Ação Straße Müller São Paulo</p>")`; `assert pdf[:4] == b"%PDF"`; texto extraído com `pypdf.PdfReader(io.BytesIO(pdf))` contém `"Ação"` e `"Straße"`. Um fixture `weasy` pula o teste com motivo explícito quando `PdfUnavailable` vem de biblioteca nativa ausente **no host Windows** (`sys.platform == "win32"`); no Linux/Docker nunca pula. `test_pdf_unavailable_wraps_errors` (monkeypatch de `weasyprint.HTML` levantando `OSError`) → `PdfUnavailable`.
- [ ] **Step 2: Run** `pytest tests/applications/test_pdf_smoke.py -v -m slow` → FAIL (módulo inexistente).
- [ ] **Step 3: Implement** `pdf.py`, as dependências e o `Dockerfile`.
- [ ] **Step 4: Verify the build (portão)**: `docker compose build api worker` → sucesso; depois `docker compose run --rm api python -c "import io,pypdf; from rumo_core.applications.pdf import html_to_pdf as h; b=h('<p>Ação Straße</p>'); t=pypdf.PdfReader(io.BytesIO(b)).pages[0].extract_text(); assert b[:4]==b'%PDF' and 'Ação' in t and 'Straße' in t; print('ok', len(b))"` → imprime `ok`. **Se qualquer um falhar: parar o plano e relatar à pessoa (build, mensagem, tamanho da imagem antes/depois).** Anotar o aumento de tamanho da imagem (`docker image ls`) na mensagem do commit.
- [ ] **Step 5: Run** `pytest tests/applications/test_pdf_smoke.py -v -m slow` → PASS (ou SKIP com motivo só no Windows).
- [ ] **Step 6: Commit** `build: add WeasyPrint and Jinja2 with Pango/HarfBuzz and DejaVu fonts`.

---

## Fatia 1 — dados, requisitos, cobertura

### Task 1: Migração 0009, modelos e configurações

**Files:**
- Create: `alembic/versions/0009_applications.py`, `tests/test_applications_models.py`
- Modify: `rumo_core/models.py`, `rumo_core/config.py`, `.env.example`

**Interfaces:**
- Produces (em `rumo_core.models`):
  - `Application(UserScopedMixin, TimestampMixin)` `__tablename__ = "applications"`: `id`, `job_id` FK `jobs.id` `ON DELETE CASCADE`, `profile_version int NULL` (nulo até a 1ª geração), `job_content_hash str(64) NULL`, `state str(16)` default `'draft'`, `locale str(8)`, `template str(8)`, `requirements JSONB NULL`, `coverage JSONB NULL`, `error Text NULL`, `attempts int` default 0, `applied_at timestamptz NULL`, **`tasks JSONB NOT NULL DEFAULT '[]'`** (fila de carta/rascunhos de resposta para o worker; ver Task 14), **`referees Text NULL`** (só modelo `au`, digitado pela pessoa). `UniqueConstraint("user_id","job_id", name="uq_applications_user_job")`, `Index("ix_applications_user_state","user_id","state")`, CHECK `state in ('draft','generating','ready','error','applied')`, CHECK `template in ('us','de','au','br')`.
  - `ApplicationDocument(UserScopedMixin, TimestampMixin)` `application_documents`: `id`, `application_id` FK CASCADE, `kind str(16)` CHECK `('resume','cover_letter')`, `lines JSONB`, `status str(16)` CHECK `('valid','needs_fix')`, `version int`; `UniqueConstraint("application_id","kind","version")`; `Index("ix_application_documents_created", "kind", "created_at")` (teto diário). A versão mais alta de cada `kind` é a vigente; as anteriores ficam (o teto conta gerações por `created_at`).
  - `ApplicationQuestion(UserScopedMixin, TimestampMixin)` `application_questions`: `id`, `application_id` FK CASCADE, `position int`, `source str(8)` CHECK `('api','pasted')`, `label Text`, `kind str(16)` CHECK `('text','choice','bool','file')`, `options JSONB NULL`, `required bool`, `sensitive bool`, `answer Text NULL`, `answer_state str(8)` default `'empty'` CHECK `('empty','draft','edited','final')`, `bank_key str(64) NULL`.
  - `AnswerBankItem.lang: str(8) NOT NULL server_default 'pt'`.
- Produces (settings): `application_daily_limit: int = Field(5, gt=0)`, `application_max_attempts: int = Field(3, gt=0)`, `application_call_timeout: float = Field(25, gt=0)`, `greenhouse_questions_timeout: float = Field(10, gt=0)`.

- [ ] **Step 1: Write failing tests** (`db`, usuário descartável): `test_application_unique_per_user_job`, `test_application_rejects_bad_state_and_template` (`state='x'`, `template='fr'` → `IntegrityError`), `test_application_defaults` (`state=='draft'`, `attempts==0`, `tasks==[]`), `test_documents_and_questions_cascade_with_job` (apagar a vaga apaga candidatura, documentos, perguntas), `test_document_version_unique_per_kind`, `test_answer_bank_lang_defaults_to_pt` (item inserido sem `lang` → `'pt'`), `test_settings_application_defaults` (5, 3, 25, 10).
- [ ] **Step 2: Run** `pytest tests/test_applications_models.py -v` → FAIL.
- [ ] **Step 3: Implement** modelos, settings, `.env.example` (`APPLICATION_DAILY_LIMIT=5` com comentário "gerações de currículo em 24 h, todos os usuários") e a migração `0009` (`revision = "0009"`, `down_revision = "0008"`; `downgrade` remove tabelas e a coluna `lang`). Conferir `alembic downgrade -1 && alembic upgrade head`.
- [ ] **Step 4: Run** `pytest tests/test_applications_models.py tests/test_config.py tests/test_interview_models.py -v` → PASS.
- [ ] **Step 5: Commit** `feat(applications): applications, documents and questions tables (migration 0009)`.

### Task 2: Índice do perfil, extração de requisitos e seleção de evidências

**Files:**
- Create: `rumo_core/applications/profile_index.py`, `rumo_core/applications/requirements.py`, `rumo_core/applications/select.py`, `tests/applications/test_profile_index.py`, `tests/applications/test_requirements.py`, `tests/applications/test_select.py`

**Interfaces:**
- Consumes: `CanonicalProfile`, `JobView` e `_render` de `rumo_core/matching/ai_score.py`, `wrap_untrusted`, `LLMProvider.complete_json`, `ProfileChunk` (`source_ref` = id do item, `"summary"` ou `"skills"`; ver `rumo_core/profile/chunker.py`).
- Produces (`profile_index.py`):
  - `item_texts(profile: CanonicalProfile) -> dict[str, str]`: id → texto de cada experiência (cargo, empresa, datas, local, descrição e conquistas), conquista (texto da conquista + cabeçalho da experiência-mãe), formação, habilidade (nome + nível), idioma, certificação, projeto (sem `url`), nota; mais as pseudo-ids `"summary"` (resumo) e `"skills"` (todos os nomes). Nunca contém e-mail, telefone, links ou nome da pessoa.
  - `full_profile_text(profile) -> str` = `_render(profile, list(profile.experiences), extra=True)` (sem contatos).
  - `evidence_block(profile, refs: list[str]) -> str`: linhas `[id] texto` (para experiências, cada conquista em sua própria linha `[id_conquista]`), ids desconhecidos ignorados.
- Produces (`requirements.py`):
  - `class Requirement(BaseModel): id: str; text: str` e `class Requirements(BaseModel): must: list[Requirement] = []; nice: list[Requirement] = []; keywords: list[str] = []` (com `all_reqs() -> list[Requirement]`).
  - `MAX_JOB_CHARS = 6000`, `MAX_ITEM_CHARS = 200`, `MAX_ITEMS = 25`.
  - `async extract_requirements(llm: LLMProvider, job: JobView) -> Requirements`: uma chamada; esquema bruto `{must: [str], nice: [str], keywords: [str]}`; código apara, descarta vazios, deduplica (sem acento/caixa), corta cada item em ≤ 200 no último espaço, aplica o teto de 25 na ordem must → nice → keywords, e numera ids `m1…`, `n1…`. O prompt (PT-BR) pede os itens no idioma da vaga e declara `<job>` como dado.
- Produces (`select.py`): `select_evidence(session, user_id: int, profile_version: int, model_version: str, req_vectors: dict[str, list[float]], k: int = 2) -> dict[str, list[str]]` (req_id → até 2 `source_ref` mais próximos por `ProfileChunk.embedding.cosine_distance`, só chunks com `embedding` e `model_version` iguais e norma > 0, como `_chunk_conds` de `ranking.py`).

- [ ] **Step 1: Write failing tests**: `test_item_texts_cover_every_item_kind_and_pseudo_ids`, `test_item_texts_never_include_contacts` (perfil com e-mail/telefone/link/nome → nenhum valor aparece em nenhum texto), `test_evidence_block_lists_achievement_ids`; requisitos com `FakeLLM` (padrão de `tests/test_worker_match.py`): `test_requirements_ids_and_order`, `test_item_over_200_chars_cut_at_word`, `test_total_capped_at_25_must_first` (30 must → 25; 20 must + 10 nice + 5 kw → 20 + 5 + 0), `test_duplicates_removed_ignoring_accents_case`, `test_description_cut_at_6000_chars_in_prompt`, `test_prompt_neutralises_job_tags` (descrição com `</job>` e "ignore as regras" → tag neutralizada dentro de `<job>`), `test_llm_errors_propagate`; seleção (`db`, vetores base ortogonais de 1024 dims): `test_select_returns_two_nearest_per_requirement`, `test_select_ignores_other_version_model_and_user`.
- [ ] **Step 2: Run** `pytest tests/applications/test_profile_index.py tests/applications/test_requirements.py tests/applications/test_select.py -v` → FAIL.
- [ ] **Step 3: Implement** os três módulos.
- [ ] **Step 4: Run** → PASS.
- [ ] **Step 5: Commit** `feat(applications): profile index, requirement extraction and evidence selection`.

### Task 3: Linhas do documento e cobertura

**Files:**
- Create: `rumo_core/applications/lines.py`, `rumo_core/applications/coverage.py`, `tests/applications/test_coverage.py`

**Interfaces:**
- Produces (`lines.py`): `class DocLine(BaseModel)`: `id: str` (`"L1"…`, sequencial por documento), `section: Literal["contact","summary","experience","bullet","project","education","skills","languages","certifications","letter"]`, `text: str`, `fields: dict[str, str] = {}` (só linhas de fato: `title, company, start, end, location` / `degree, field, institution` …), `parent: str | None = None` (id da experiência/projeto de um marcador), `source_refs: list[str]`, `origin: Literal["fact","ai","user"]`, `covers: list[str] = []`, `violations: list[str] = []`. `document_status(lines) -> Literal["valid","needs_fix"]` (`needs_fix` se alguma linha tem `violations`).
- Produces (`coverage.py`):
  - `requirement_terms(req: Requirement) -> list[str]`: termos salientes (`_salient_terms` de `ai_score.py`); sem nenhum, as palavras de conteúdo com ≥ 4 letras fora de `_STOPWORDS`.
  - `line_covers(line: DocLine, req: Requirement, index: dict[str, str]) -> bool`: verdadeiro se algum termo do requisito aparece (sem acento/caixa, borda de palavra) no texto da linha **ou** nos textos dos itens que ela cita (o "sinônimo do perfil").
  - `compute_coverage(reqs: Requirements, lines: list[DocLine], index, decisions: dict[str, str] | None = None) -> dict`: `{"must": {"covered": n, "total": N}, "nice": {"covered": n, "total": N}, "items": [{"id","kind": "must"|"nice","text","covered": bool,"line_ids": [...],"decision": None|"have"|"lack"}]}`. Só contam linhas sem `violations` cujo `covers` cita o requisito e passam em `line_covers`; `decisions` (de uma cobertura anterior) é preservado por id.

- [ ] **Step 1: Write failing tests**: `test_counts_must_and_nice_separately` (3 must, 2 cobertos; 2 nice, 1 coberto → `{"covered":2,"total":3}` e `{"covered":1,"total":2}`), `test_requirement_cited_only_by_invalid_line_not_covered`, `test_declared_cover_without_term_not_counted`, `test_term_found_in_cited_item_counts`, `test_accent_and_case_ignored`, `test_decisions_preserved_by_id`, `test_document_status_needs_fix_when_any_violation`.
- [ ] **Step 2: Run** `pytest tests/applications/test_coverage.py -v` → FAIL.
- [ ] **Step 3: Implement** reaproveitando `_fold`, `_salient_terms`, `_STOPWORDS` de `ai_score.py` sem alterá-los.
- [ ] **Step 4: Run** → PASS.
- [ ] **Step 5: Commit** `feat(applications): document lines and requirement coverage`.

---

## Fatia 2 — validador, rascunho, worker, API e as primeiras telas

### Task 4: Validador anti-invenção

**Files:**
- Create: `rumo_core/applications/validator.py`, `tests/applications/test_validator.py`

**Interfaces:**
- Consumes: `_fold`, `_technical`, `_salient_terms_at`, `_known`, `_words`, `_job_skill_words`, `_STOPWORDS`, `_CONTACT`, `_BARE_DOMAIN`, `_SENTENCE_SPLIT` de `ai_score.py`; `numbers_supported`, `lexicon_supported` de `rumo_core/interview/suggestions.py`; `item_texts`, `full_profile_text`; `DocLine`.
- Produces:
  - `@dataclass(frozen=True) class ValidationContext` + `build_context(profile: CanonicalProfile, job: JobView | None, locale: str) -> ValidationContext` (índice, texto completo dobrado com `_fold(x).casefold()` — `casefold` faz `ß`≡`ss` —, contatos do perfil: e-mail, links, url de projetos, dígitos do telefone; palavras de habilidade da vaga; título e empresa exatos da vaga).
  - `validate_line(line: DocLine, ctx) -> list[str]` (motivos em PT-BR, vazio = válida; linhas `origin="fact"` → sempre `[]`).
  - `validate_lines(lines, ctx) -> dict[str, list[str]]` (só ids com motivo).
  - Constantes de motivo: `R_SOURCE = "Sem origem no seu perfil"`, `R_NUMBER = "Número, data ou valor que não está no item citado: {token}"`, `R_TERM = "Termo que não está no seu perfil: {token}"`, `R_CONTACT = "E-mail, link ou telefone que não é do seu perfil"`, `R_CLAIM = "Afirma anos de experiência ou nível que o seu perfil não traz"`, `R_EMPTY = "Linha vazia"`, `R_LONG = "Linha longa demais (máx. 600 caracteres)"`.
  - `LEVEL_GROUPS: tuple[tuple[str, ...], ...]` (equivalências pt/en/de dobradas: `("senior","sênior")`, `("pleno","mid-level")`, `("junior","júnior")`, `("especialista","expert","experte")`, `("fluente","fluent","fliessend","fließend")`, `("nativo","native","muttersprache","muttersprachlich")`, `("avancado","advanced","fortgeschritten")`, `("lider","lead","leitend")`, `("principal","staff")`), `YEARS_CLAIM` (número ou número por extenso + `anos|years|jahre`).
- Regras (spec §2, na ordem): (1) `source_refs` vazio ou id fora de `item_texts` da versão usada → `R_SOURCE`; marcador com `parent` inexistente também. (2) `numbers_supported`/`lexicon_supported` contra os textos **dos itens citados** → `R_NUMBER`. (3) termos salientes por frase (`_salient_terms_at`; a 1ª palavra só se técnica) precisam estar no perfil completo; da vaga só valem o título e o nome da empresa **exatos** (removidos antes da checagem, como `_strip_exact`); e nenhuma palavra de habilidade da vaga (`_job_skill_words`) ausente do perfil → `R_TERM`. Em `locale == "de"` um substantivo isolado com maiúscula não é checado como nome próprio; são checados os termos técnicos (`_technical`), siglas (todas maiúsculas, ≥ 2 letras), sequências de ≥ 2 palavras com maiúscula fora do início da frase e palavras ao lado de sufixo societário (`GmbH`, `AG`, `SE`, `Inc`, `Ltd`, `LLC`, `S.A.`). (4) e-mail/URL/domínio (`_CONTACT`, `_BARE_DOMAIN`) ou telefone (`\+?\d[\d\s().-]{7,}\d`) que não são do perfil → `R_CONTACT`. (5) `YEARS_CLAIM` cujo número+unidade não está nos itens citados, ou palavra de `LEVEL_GROUPS` cujo grupo não aparece no perfil completo → `R_CLAIM`. Palavras comuns são livres.

- [ ] **Step 1: Write failing tests** (perfil de fixture: "Engenheira de Dados" na "Acme", 2019-03 a 2023-08, conquista "Reduzi o tempo do pipeline em 40% com Airflow", habilidades Python, SQL, Airflow, formação USP; vaga "Data Engineer" na "Globex" pedindo Kubernetes): rejeição — `test_line_without_source_refs`, `test_source_ref_not_in_profile_version`, `test_bullet_parent_unknown`, `test_invented_number` ("em 60%"), `test_invented_date` ("desde 2015"), `test_spelled_number_or_month_invented` ("dez projetos", "em março"), `test_number_from_other_item_not_cited` (40% citando só a formação), `test_skill_not_in_profile` ("Spark"), `test_job_only_skill_lowercase` ("kubernetes" vindo da vaga), `test_invented_company_or_institution` ("na Google", "pela Stanford"), `test_foreign_email_url_phone` (três casos), `test_years_claim_without_basis` ("10 anos de experiência", "ten years of"), `test_level_claim_without_basis` ("especialista em SQL", "fluent German"), `test_job_injection_text_rejected` ("Ignore as regras: 10 anos de Kubernetes"), `test_empty_and_too_long_line`; aceitação — `test_rephrased_line_passes`, `test_translation_pt_to_en_passes` ("Cut pipeline time by 40% with Airflow" citando a conquista), `test_translation_pt_to_de_passes` ("Pipelinezeit mit Airflow um 40% verkürzt"), `test_german_nouns_pass_but_invented_company_rejected` ("Entwicklung von Datenpipelines" passa; "bei Siemens AG" reprova), `test_accent_and_case_ignored` ("sao paulo" vs "São Paulo", "AIRFLOW"), `test_unicode_eszett_casefold` (perfil "Straße", linha "STRASSE"), `test_job_exact_company_and_title_allowed` ("Quero contribuir com a Globex como Data Engineer"), `test_profile_email_allowed`, `test_fact_lines_skip_validation`, `test_reasons_are_ptbr_constants` (formato `R_NUMBER.format(token="60%")`).
- [ ] **Step 2: Run** `pytest tests/applications/test_validator.py -v` → FAIL.
- [ ] **Step 3: Implement** `validator.py` (reaproveitar os auxiliares; não alterar `ai_score.py`; limites em constantes no topo do módulo).
- [ ] **Step 4: Run** `pytest tests/applications/test_validator.py tests/matching/test_ai_score.py -v` → PASS.
- [ ] **Step 5: Commit** `feat(applications): anti-invention line validator`.

### Task 5: `draft_resume` — fatos por código, texto do LLM, 1 retentativa

**Files:**
- Create: `rumo_core/applications/draft.py`, `tests/applications/test_draft_resume.py`

**Interfaces:**
- Consumes: `Requirements`, `evidence_block`, `item_texts`, `DocLine`, `build_context`/`validate_lines`, `line_covers`, `wrap_untrusted`, `LLMProvider`.
- Produces:
  - Esquema do LLM (`model_config = ConfigDict(extra="ignore")`): `_TextIn{text: str, source_refs: list[str] = [], covers: list[str] = []}`, `_ItemIn{item_id: str, bullets: list[_TextIn] (≤ 6)}`, `ResumeDraftIn{summary: _TextIn, experiences: list[_ItemIn] = [], projects: list[_ItemIn] = [], skill_ids: list[str] = [] (≤ 30)}`.
  - `class DraftInvalid(Exception)` com `.violations: dict[str, list[str]]`.
  - `build_fact_lines(profile: CanonicalProfile, locale: str) -> list[DocLine]`: contato (nome, e-mail, telefone, local, links), cada experiência (cabeçalho com `fields` e `source_refs=[id]`; `end` vazio → `{"pt":"atual","en":"Present","de":"heute"}[locale]`), formação, idiomas, certificações — tudo `origin="fact"`, ordem do perfil.
  - `assemble_resume(profile, draft: ResumeDraftIn, locale: str) -> list[DocLine]`: resumo (`origin="ai"`), marcadores logo após o cabeçalho da experiência/projeto (`parent=item_id`), linha `skills` montada por código a partir dos `skill_ids` existentes (ids desconhecidos descartados; sem nenhum, todas as habilidades na ordem do perfil); ids `L1…` sequenciais.
  - `async draft_resume(llm, profile, job: JobView, reqs: Requirements, evidence: dict[str, list[str]], *, locale: str) -> list[DocLine]`: 1ª chamada; valida; com violações, 2ª chamada com as mensagens originais + `{"role":"user","content": <lista "L3: motivo">}`; ainda inválido → `DraftInvalid`; `covers` com id de requisito desconhecido ou que não passa em `line_covers` é retirado silenciosamente. O prompt leva só: título/empresa da vaga, requisitos (em `<job>`), o bloco de evidências (união dos refs selecionados) e a lista `[id] nome` das habilidades; nunca contatos/nome.

- [ ] **Step 1: Write failing tests** (`FakeLLM` com respostas em fila e registro de mensagens): `test_facts_copied_from_profile` (empresa, cargo, datas, instituição iguais ao perfil), `test_llm_attempt_to_change_company_or_dates_ignored` (resposta com `"company":"Google","start":"2010"` extras → linhas de fato inalteradas, nenhum "Google"), `test_unknown_skill_ids_dropped_and_names_from_profile`, `test_retry_sends_violations_then_succeeds` (2 chamadas; a 2ª mensagem contém `R_NUMBER`), `test_second_failure_raises_draft_invalid` (2 chamadas, `DraftInvalid.violations` não vazio), `test_prompt_has_no_contacts_or_name`, `test_prompt_only_selected_evidence` (experiência não selecionada não aparece no prompt, mas seu cabeçalho de fato aparece nas linhas), `test_unchecked_covers_dropped`, `test_end_empty_rendered_as_locale_word` (`de` → "heute").
- [ ] **Step 2: Run** `pytest tests/applications/test_draft_resume.py -v` → FAIL.
- [ ] **Step 3: Implement** `draft.py`.
- [ ] **Step 4: Run** → PASS.
- [ ] **Step 5: Commit** `feat(applications): resume draft with facts copied from the profile`.

### Task 6: Worker `prepare_applications` (geração)

**Files:**
- Create: `worker/prepare_applications.py`, `rumo_core/applications/quota.py`, `tests/test_worker_prepare_applications.py`
- Modify: `worker/main.py`, `tests/test_worker.py` (ordem do registry)

**Interfaces:**
- Consumes: `extract_requirements`, `select_evidence`, `draft_resume`, `DraftInvalid`, `compute_coverage`, `item_texts`, `document_status`, `get_current_profile`, `INTERVIEW_LLM_STATES`, `EMBED_LOCK` de `worker/jobs.py`, `Embedder`.
- Produces:
  - `async prepare_applications(session_factory, llm: LLMProvider | None, embedder: Embedder, *, max_attempts: int, daily_limit: int | None, call_timeout: float = 25.0, user_id: int | None = None, clock=time.monotonic) -> int` (unidades processadas: 0 ou 1), `_reset_state()`; e `generated_last_24h(session) -> int` em `rumo_core/applications/quota.py` (conta `ApplicationDocument` `kind='resume'` com `created_at > now() - 24 h`, todos os usuários; usado pelo worker e pela API, que não importa `worker.*`).
  - Mensagens (`error`): `MSG_DRAFT_INVALID = "A IA escreveu algo que não está no seu perfil. Gere de novo ou complete o perfil."`, `MSG_LLM_FAIL = "Não foi possível gerar agora. Tente de novo mais tarde."`, `MSG_NO_PROFILE = "Confirme seu perfil antes de preparar candidaturas."`.
  - `main.py`: `build_prepare_applications_job(settings, session_factory, llm, embedder)` e registro `"prepare_applications"` a cada 5 s **logo depois de** `interview_turns` (pedido da pessoa, que acompanha por polling).
- Comportamento: sem LLM → aviso único, 0; pausa ativa → 0; entrevista precisando do LLM → 0; teto atingido → não gera (log no máx. 1×/h). Seleciona **1** candidatura `generating` com `attempts < max_attempts` (mais antiga por `updated_at`) com `FOR UPDATE SKIP LOCKED` e faz commit antes da rede. Carrega vaga + empresa + perfil atual. Requisitos: reutiliza `app.requirements` se `app.job_content_hash == job.content_hash`, senão `extract_requirements`. Vetores dos requisitos (must+nice) com `asyncio.to_thread(embedder.embed, ...)` sob `EMBED_LOCK`; `select_evidence` com `embedder.model_version`; sem nenhum chunk utilizável → evidência = todas as experiências. Cada chamada ao LLM sob `asyncio.wait_for(..., call_timeout)`. Gravação numa transação nova com lock da linha: descarta (fica `generating`, sem tentativa) se a candidatura não está mais `generating` ou se a versão atual do perfil mudou; senão grava `requirements`, `coverage` (preservando `decision`s), `profile_version`, `job_content_hash`, novo `ApplicationDocument(kind="resume", version=max+1, status=document_status(lines))`, `state="ready"`, `attempts=0`, `error=None`. `DraftInvalid` → `state="error"`, `MSG_DRAFT_INVALID`, nenhum documento. `LLMError`/inesperado → `attempts+1`; no máximo → `error`, `MSG_LLM_FAIL`. `LLMRateLimited`/`TimeoutError` → pausa 60 s, sem tentativa. Logs: só id da candidatura e nome da classe.

- [ ] **Step 1: Write failing tests** (`db`, LLM e embedder falsos — embedder com `model_version = MODEL_VERSION` e vetores fixos): `test_generating_to_ready_with_document_and_coverage`, `test_requirements_reused_when_job_unchanged` (2ª geração: 1 chamada a menos), `test_requirements_reextracted_when_job_hash_changed`, `test_draft_invalid_sets_error_without_document`, `test_llm_error_counts_attempt_then_error_at_max`, `test_rate_limit_pauses_without_attempt`, `test_timeout_pauses_without_attempt`, `test_daily_limit_blocks_generation` (5 documentos `resume` nas últimas 24 h → nada processado), `test_interview_gate_pauses`, `test_no_llm_does_nothing`, `test_two_workers_do_not_take_same_application` (dois ticks concorrentes, 1 chamada de requisitos), `test_new_profile_version_during_generation_discards_and_requeues` (hook do FakeLLM confirma versão nova → candidatura segue `generating`, sem documento; próximo tick usa a versão nova), `test_network_failure_of_embedder_falls_back_to_all_experiences`, `test_logs_have_no_job_or_llm_text` (`caplog` sem o segredo da descrição), `test_registered_after_interview_turns` (em `tests/test_worker.py`).
- [ ] **Step 2: Run** `pytest tests/test_worker_prepare_applications.py tests/test_worker.py -v` → FAIL.
- [ ] **Step 3: Implement** no padrão de `worker/match_jobs.py` (`_select`, `_load`, `_write`, `_bump_attempts`, estado de processo com `_reset_state`).
- [ ] **Step 4: Run** → PASS.
- [ ] **Step 5: Commit** `feat(worker): prepare_applications job (resume generation)`.

### Task 7: API — criar, listar, detalhar, gerar, trocar modelo e editar linhas

**Files:**
- Create: `api/applications.py`, `tests/test_api_applications.py`
- Modify: `api/main.py` (router)

**Interfaces:**
- Consumes: `Application`, `ApplicationDocument`, `Job`, `Company`, `usable_profile`/`MODEL_VERSION` (como `api/matching.py`), `get_current_profile`, `generated_last_24h`, `build_context`, `validate_line`, `compute_coverage`, `item_texts`, `document_status`, `JobView`.
- Produces:
  - `template_for(country: str | None) -> str`, `locale_for(language: str) -> str` (em `rumo_core/applications/lines.py`, usados também pela Task 10).
  - `POST /applications {job_id: int}` → `ApplicationOut` (vaga de outro usuário/inexistente → 404 "Vaga não encontrada."; idempotente com `insert … on_conflict_do_nothing` em `uq_applications_user_job` + select; nova nasce `draft` com `locale_for`/`template_for`).
  - `GET /applications` → `{items: [{id, job_id, job_title, company_name, state, updated_at, applied_at, coverage: {must, nice} | null}]}` por `updated_at desc`, sem descrição de vaga.
  - `GET /applications/{id}` → `ApplicationDetail{id, state, error, locale, template, referees, profile_version, current_profile_version, warnings: list[Literal["profile_changed","job_changed","job_closed"]], job: {id, title, company_name, url, status}, requirements, coverage, resume: DocumentOut | null, cover_letter: DocumentOut | null, questions: list[QuestionOut], tasks: list, can_generate: {allowed: bool, reason: str | null}, applied_at}`; `DocumentOut{version, status, lines: list[DocLine]}` (versão mais alta do `kind`). `QuestionOut` é definido na Task 13 (aqui lista vazia).
  - `POST /applications/{id}/generate` → 202 `ApplicationOut`: 409 `MSG_GENERATING = "A candidatura já está sendo gerada."`; 409 `MSG_APPLIED_LOCKED = "Desfaça o \"Apliquei\" antes de gerar de novo."`; 409 sem perfil/sem vetores (mensagens de `api/matching.py`); 429 `MSG_DAILY_LIMIT = "Limite diário de {n} candidaturas geradas atingido. Tente de novo amanhã."` com `Retry-After: 3600`; senão `state="generating"`, `attempts=0`, `error=None` (`UPDATE … WHERE state <> 'generating'` para ser atômico).
  - `PATCH /applications/{id} {template?: "us"|"de"|"au"|"br", referees?: str ≤ 1000}` → `ApplicationOut` (`referees` só aceito com `template == "au"`, senão 422; texto da pessoa, sem validador).
  - `PUT /applications/{id}/lines/{line_id} {text: str ≤ 600}` (só documento vigente; `kind` via query `?kind=resume|cover_letter`, padrão `resume`): linha `fact` → 409 `MSG_FACT_LINE = "Este dado vem do seu perfil. Para mudar, edite o perfil."`; senão grava `text`, `origin="user"`, `violations = validate_line(...)` contra a versão do perfil da candidatura, recalcula `status` e `coverage`; devolve `DocumentOut`.
  - `can_generate.reason` usa as mesmas mensagens dos 409/429.

- [ ] **Step 1: Write failing tests** (`db`, TestClient, `current_user_id` sobrescrito, usuários descartáveis): `test_create_is_idempotent` (dois POST → mesmo id, 1 linha), `test_create_is_idempotent_under_concurrency` (duas threads), `test_create_job_of_other_user_404`, `test_initial_locale_and_template` (`language="unknown"`, `country="AT"` → `en`/`de`; `country=None` → `us`), `test_list_and_detail_scoped_by_user` (detalhe de outro usuário → 404), `test_generate_409_when_generating_and_when_applied`, `test_generate_429_at_daily_limit`, `test_generate_409_without_vectorized_profile`, `test_detail_warns_profile_changed_job_changed_job_closed`, `test_edit_ai_line_valid_keeps_valid`, `test_edit_line_with_invented_number_needs_fix` (`status=="needs_fix"`, `violations` com `R_NUMBER`), `test_fixing_line_restores_valid_and_coverage`, `test_edit_fact_line_409`, `test_patch_template_and_referees_rules`, `test_detail_has_no_job_description`.
- [ ] **Step 2: Run** `pytest tests/test_api_applications.py -v` → FAIL.
- [ ] **Step 3: Implement** o router.
- [ ] **Step 4: Run** `pytest tests/test_api_applications.py tests/test_api_cors.py -v` → PASS.
- [ ] **Step 5: Commit** `feat(api): applications core routes (create, generate, edit lines)`.

### Task 8: Web — cliente, entrada em `/jobs` e casca de `/candidaturas/[id]`

**Files:**
- Create: `web/lib/applications.ts`, `web/lib/applications.test.ts`, `web/app/candidaturas/layout.tsx`, `web/app/candidaturas/[id]/page.tsx`, `web/app/candidaturas/[id]/page.test.tsx`, `web/components/applications/applicationSteps.ts`, `web/components/applications/applicationSteps.test.ts`, `web/components/applications/RequirementsStep.tsx`, `web/components/applications/RequirementsStep.test.tsx`
- Modify: `web/components/jobs/MatchPanel.tsx` (+ teste), `web/app/jobs/page.tsx` (+ teste), `web/app/globals.css`

**Interfaces:**
- Consumes: `request`/`ApiError` de `web/lib/profile.ts`, `PageHeader`, `Stepper`, `useRefreshOverview`.
- Produces (`lib/applications.ts`): tipos `ApplicationState`, `DocLine`, `DocumentOut`, `Coverage`, `ApplicationDetail`, `ApplicationListItem` (espelham a Task 7); `createApplication(jobId)`, `listApplications()`, `getApplication(id)`, `generateApplication(id)`, `patchApplication(id, {template?, referees?})`, `editLine(id, lineId, text, kind?)`; `prepareApplication(jobId): Promise<number>` = cria e, se `state` for `draft`, chama `generate`; devolve o id.
- Produces: `applicationSteps(d: ApplicationDetail | null, view: "read"|"resume"|"questions"|"kit"): Step[]` — ids `read`, `resume`, `questions`, `kit`, títulos "Ler a vaga", "Currículo", "Perguntas", "Kit"; `read` concluído quando há `requirements`; `resume` concluído com documento `valid`; `kit` concluído com `applied`.
- `MatchPanel` ganha `onPrepare?: () => void` e `preparing?: boolean` → botão primário "Preparar candidatura" no estado `ready`; o texto de atalhos passa a citar <kbd>p</kbd>. `jobs/page.tsx`: tecla `p` (mesmas guardas do `j/k`) com vaga selecionada → `prepareApplication` → `router.push(/candidaturas/{id})`; erro vira mensagem no painel (429/409 com o `detail` do servidor).
- Página `/candidaturas/[id]` (cliente, `useParams`): `PageHeader` com título "Candidatura · {cargo}", stepper horizontal, polling de 3 s enquanto `state === "generating"` ou `tasks` com item `queued` (parar ao desmontar); estados: carregando, erro de rede ("Tentar de novo"), `error` com o motivo e "Gerar de novo" (desabilitado com `can_generate.reason`), avisos de `warnings` ("O perfil mudou desde esta versão" + "Gerar de novo"; "A vaga mudou/foi encerrada"). Passo "Ler a vaga": requisitos must/nice/keywords como texto, cobertura "n de N". Layout `metadata.title = "Candidaturas · Rumo"`.

- [ ] **Step 1: Write failing tests**: `lib` — `prepareApplication` chama generate só em `draft`; erros viram `ApiError` com `detail`. `applicationSteps` — estados por fase. `MatchPanel` — botão "Preparar candidatura" chama `onPrepare`; `jobs/page` — `p` com vaga selecionada navega; `p` digitando num campo não faz nada; erro 429 aparece em PT-BR. Página — polling para quando `ready`; `error` mostra motivo e "Gerar de novo"; aviso de perfil mudado; requisito hostil `<img onerror>` aparece literal.
- [ ] **Step 2: Run** `npm test` (em `web/`) → FAIL.
- [ ] **Step 3: Implement** (ler `node_modules/next/dist/docs/` sobre rotas dinâmicas antes).
- [ ] **Step 4: Run** `npm test && npm run lint` → PASS.
- [ ] **Step 5: Commit** `feat(web): prepare application from jobs and application page shell`.

### Task 9: Web — passo "Currículo" (cobertura, linhas com origem, edição)

**Files:**
- Create: `web/components/applications/ResumeStep.tsx`, `web/components/applications/ResumeStep.test.tsx`, `web/components/applications/LineEditor.tsx`, `web/components/applications/LineEditor.test.tsx`
- Modify: `web/app/candidaturas/[id]/page.tsx`, `web/app/globals.css`

**Interfaces:**
- Consumes: `editLine`, `ApplicationDetail`, `DocLine`.
- Produces: `ResumeStep({detail, onChanged})`: cobertura "{n} de {N} obrigatórios · {n} de {N} desejáveis" com a nota "contagem aproximada"; linhas agrupadas por seção; cada linha com chip da origem ("Do perfil: {cabeçalho do item}", resolvido pelas linhas de fato do mesmo documento); linhas `fact` sem edição e com a dica "edite no perfil"; `LineEditor` (textarea, Salvar/Cancelar, Escape cancela) → resposta `needs_fix` mostra os motivos sob a linha (`role="alert"`) e um aviso no topo "Corrija as linhas marcadas para liberar o PDF"; requisitos não cobertos listados como "Não encontrei no seu perfil" (botões na Task 16).

- [ ] **Step 1: Write failing tests**: edição válida atualiza a linha; edição inválida mostra o motivo e o aviso de bloqueio; linha de fato não tem botão Editar; chip de origem mostra o cabeçalho do item; motivo/linha hostil aparece como texto.
- [ ] **Step 2: Run** `npm test` → FAIL.
- [ ] **Step 3: Implement**.
- [ ] **Step 4: Run** `npm test && npm run lint` → PASS.
- [ ] **Step 5: Commit** `feat(web): resume review step with source chips and validated edits`.

---

## Fatia 3 — render, modelos, PDF e texto

### Task 10: Render HTML por modelo, texto puro e rotas de download

**Files:**
- Create: `rumo_core/applications/render.py`, `rumo_core/applications/templates/{us,de,au,br}.html`, `rumo_core/applications/templates/letter.html`, `rumo_core/applications/static/{base,us,de,au,br}.css`, `tests/applications/test_render.py`
- Modify: `api/applications.py`, `tests/test_api_applications.py`

**Interfaces:**
- Consumes: `DocLine`, `html_to_pdf`, `PdfUnavailable`.
- Produces:
  - `HEADINGS: dict[str, dict[str, str]]` por locale (`pt`: Resumo, Experiência, Projetos, Formação, Habilidades, Idiomas, Certificações; `en`: Summary, Experience, Projects, Education, Skills, Languages, Certifications, Referees; `de`: Profil, Berufserfahrung, Projekte, Ausbildung, Kenntnisse, Sprachen, Zertifikate, Persönliche Daten).
  - `SECTION_ORDER: dict[str, tuple[str, ...]]`: `us` = contact, summary, experience, projects, education, skills, languages, certifications; `de` = contact, summary, experience, education, skills, languages, certifications, projects (experiência e formação em `<table class="cv-table">` com datas na 1ª coluna); `au` = contact, summary, skills, experience, education, certifications, projects, languages, referees (só quando `referees` não vazio); `br` = contact, summary, experience, education, skills, languages, certifications, projects.
  - `render_html(lines: list[DocLine], *, template: str, locale: str, referees: str | None = None) -> str` (Jinja2 `Environment(loader=PackageLoader("rumo_core.applications","templates"), autoescape=True)`, CSS do modelo embutido em `<style>`, `<html lang>` = locale, sem `<img>`).
  - `render_letter_html(lines, *, template, locale, job_title: str, company_name: str, sender_lines: list[DocLine], today: date) -> str` (saudação/fecho/assunto por código: `de` = "Bewerbung als {title}", "Sehr geehrte Damen und Herren,", "Mit freundlichen Grüßen"; `en` = "Application for {title}", "Dear Hiring Team,", "Sincerely,"; `pt` = "Candidatura para {title}", "Prezada equipe,", "Atenciosamente,"; título `Anschreiben` no modelo `de`).
  - `render_text(lines, *, locale, referees=None) -> str` e `render_letter_text(...)` (mesmas linhas, cabeçalhos em maiúsculas, marcadores com "- ").
  - Rotas: `GET /applications/{id}/resume.pdf|resume.txt` e `GET /applications/{id}/cover-letter.pdf|cover-letter.txt`: sem documento → 404; documento `needs_fix` → 409 `MSG_PDF_BLOCKED = "Corrija as linhas marcadas antes de baixar."`; `PdfUnavailable` → 503 `MSG_PDF_UNAVAILABLE = "Não foi possível gerar o PDF agora. Use a versão em texto."`; `Content-Disposition: attachment; filename="curriculo-{id}.pdf"` (nome ASCII fixo, nunca texto da vaga); txt `text/plain; charset=utf-8`.

- [ ] **Step 1: Write failing tests**: `test_us_section_order`, `test_de_is_tabular_with_dates_column`, `test_au_referees_only_when_typed`, `test_br_headings_in_portuguese`, `test_headings_follow_locale_not_template` (`de` + `en` → "Experience" em tabela), `test_no_img_or_photo_in_any_template`, `test_html_escapes_hostile_text` (`<script>` → `&lt;script&gt;`), `test_text_version_has_same_lines`, `test_letter_salutation_and_anschreiben_by_locale`; `slow`: `test_pdf_each_template_starts_with_pdf_and_keeps_accents` (4 modelos, texto extraído com "São Paulo" e "Straße"); API (`db`): `test_pdf_and_txt_blocked_while_needs_fix`, `test_pdf_503_when_unavailable_txt_still_works` (monkeypatch `html_to_pdf` → `PdfUnavailable`), `test_download_other_user_404`, `test_filename_never_contains_job_text`.
- [ ] **Step 2: Run** `pytest tests/applications/test_render.py tests/test_api_applications.py -v` → FAIL.
- [ ] **Step 3: Implement** render, modelos e CSS (`@page` A4 para `de`/`br`, Letter para `us`; fonte `"DejaVu Sans", sans-serif`; margens e tamanhos visando 1–2 páginas `us`, 2–3 `au`).
- [ ] **Step 4: Run** `pytest tests/applications/test_render.py tests/test_api_applications.py -v` e `pytest -m slow tests/applications` → PASS. Abrir um PDF de cada modelo gerado no container e conferir visualmente (acentos, ß, tabela `de`).
- [ ] **Step 5: Commit** `feat(applications): per-country HTML templates, plain text and PDF downloads`.

### Task 11: Web — modelo, referees e downloads na revisão

**Files:**
- Create: `web/components/applications/TemplatePicker.tsx`, `web/components/applications/TemplatePicker.test.tsx`
- Modify: `web/components/applications/ResumeStep.tsx` (+ teste), `web/lib/applications.ts` (+ teste)

**Interfaces:**
- Produces: `resumeUrl(id, "pdf"|"txt")`, `letterUrl(id, "pdf"|"txt")` em `lib/applications.ts` (base da API como em `request`); `TemplatePicker({value, onChange})` com rótulos "EUA (1–2 páginas)", "Alemanha (Lebenslauf)", "Austrália (2–3 páginas)", "Brasil (livre)"; campo "Referees" só com `au`, salvo por `patchApplication`. "Baixar PDF" e "Baixar texto" desabilitados com o motivo quando o documento está `needs_fix`; 503 do PDF mostra a mensagem do servidor e mantém o texto disponível.

- [ ] **Step 1: Write failing tests**: trocar modelo chama `patchApplication`; referees só aparece com `au`; downloads desabilitados com motivo em `needs_fix`; erro 503 mostra a mensagem.
- [ ] **Step 2: Run** `npm test` → FAIL.
- [ ] **Step 3: Implement**.
- [ ] **Step 4: Run** `npm test && npm run lint` → PASS.
- [ ] **Step 5: Commit** `feat(web): template picker, referees and resume downloads`.

---

## Fatia 4 — lacunas, perguntas, respostas e carta

### Task 12: Lacunas — "Tenho isso" / "Não tenho"

**Files:**
- Create: `rumo_core/applications/gaps.py`, `tests/applications/test_gaps.py`
- Modify: `api/applications.py`, `tests/test_api_applications.py`

**Interfaces:**
- Consumes: `confirm_profile(session, user_id, profile, narrative)`, `get_current_profile`, `CanonicalProfile`, `Skill`, `Note`, `generated_last_24h`.
- Produces:
  - `classify_gap_answer(text: str) -> Literal["skill","note"]`: `skill` quando o texto aparado tem ≤ 40 caracteres, ≤ 4 palavras e nenhum `.`/`!`/`?` final nem vírgula; senão `note`.
  - `add_gap_to_profile(profile: CanonicalProfile, requirement_text: str, answer: str) -> CanonicalProfile`: `Skill(name=answer)` (não duplica nome igual sem acento/caixa) ou `Note(topic=requirement_text[:120], text=answer)`; ids vazios (preenchidos por `confirm_profile`).
  - `POST /applications/{id}/gaps/{req_id}/resolve {action: "have"|"lack", text?: str (1..200, obrigatório em have)}` → `{coverage, profile_version: int | null, regenerated: bool}`: req inexistente → 404; requisito coberto → 409; `lack` só grava `decision="lack"`; `have` cria nova versão do perfil (com a narrativa atual), grava `decision="have"` e, se não estiver `generating`/`applied` e abaixo do teto, põe a candidatura em `generating` (`regenerated=true`); caso contrário `regenerated=false` e a tela mostra o aviso de perfil mudado.

- [ ] **Step 1: Write failing tests**: `test_short_tool_name_is_skill`, `test_sentence_is_note`, `test_skill_not_duplicated`; API (`db`): `test_have_creates_new_profile_version_and_regenerates`, `test_have_reembeds_only_changed_chunk` (chunks da versão nova: só o `skills` — ou a nota nova — com `embedding` nulo; os demais reaproveitados), `test_lack_does_not_change_profile`, `test_have_at_daily_limit_updates_profile_without_regenerating`, `test_resolve_covered_requirement_409`, `test_resolve_other_user_404`.
- [ ] **Step 2: Run** `pytest tests/applications/test_gaps.py tests/test_api_applications.py -v` → FAIL.
- [ ] **Step 3: Implement**.
- [ ] **Step 4: Run** → PASS.
- [ ] **Step 5: Commit** `feat(applications): resolve gaps into the profile or as known gaps`.

### Task 13: Perguntas — Greenhouse, texto colado, sensíveis

**Files:**
- Create: `rumo_core/applications/questions.py`, `tests/applications/test_questions.py`, `tests/fixtures/greenhouse/job_questions.json`
- Modify: `rumo_core/sources/greenhouse.py`, `rumo_core/sources/base.py`, `tests/sources/test_greenhouse.py`, `tests/fixtures/greenhouse/README.md`, `api/applications.py`, `tests/test_api_applications.py`

**Interfaces:**
- Produces (`sources`): `@dataclass RawQuestion(label: str, field_type: str, values: list[str], required: bool, section: Literal["questions","compliance","demographic","location"])` em `base.py`; `QUESTIONS_URL = "https://boards-api.greenhouse.io/v1/boards/{token}/jobs/{job_id}?questions=true"`; `GreenhouseSource.fetch_job_questions(identifier: str, external_id: str) -> list[RawQuestion]` (mesmos retries, teto de bytes e exceções de `fetch_jobs`; `external_id` só dígitos, senão `ValueError`).
- Produces (`questions.py`):
  - `SENSITIVE_KEYWORDS_VERSION = 1`; `SENSITIVE_KEYWORDS: dict[str, tuple[str, ...]]` (dobrados, casados no início de palavra; itens ≤ 4 letras como palavra inteira): `work_authorization` (authorized to work, eligible to work, right to work, work permit, sponsorship, sponsor, autorizacao de trabalho, patrocinio, arbeitserlaubnis), `visa` (visa, visto, visum, aufenthalt), `salary` (salary, salario, pretensao, remuneracao, compensation, expected pay, gehalt, ctc), `disability` (disab, deficien, behinderung), `gender` (gender, genero, sexo, geschlecht, pronoun, pronome), `race` (race, raca, ethnic, etnia, hispanic, latino), `criminal` (criminal, antecedente, convict, felony, vorstrafe, fuhrungszeugnis), `birth` (date of birth, birth, nascimento, idade, age, geburt), `relocation` (relocat, mudanca, realoca, umzug, willing to move), `veteran` (veteran, military, militar), `orientation` (sexual orientation, orientacao sexual), `personal` (first name, last name, full name, nome, name, e-mail, email, phone, telefone, telefon, address, endereco, adresse, linkedin).
  - `sensitive_category(label: str, options: list[str] | None = None) -> str | None` (opção "decline to self-identify"/"prefer not to say"/"prefiro não dizer" → `"demographic"`); `is_sensitive(...) -> bool`.
  - `SENSITIVE_BANK_KEYS = {"salary": "salary_expectation", "work_authorization": "work_authorization", "visa": "work_authorization", "relocation": "relocation"}`.
  - `@dataclass ParsedQuestion(label, kind, options, required, sensitive)`; `map_greenhouse(raw: list[RawQuestion]) -> list[ParsedQuestion]` (`input_text`/`textarea` → `text`, `input_file` → `file`, `multi_value_single_select` com valores só sim/não → `bool`, outros selects → `choice`; seções `compliance`/`demographic` sempre sensíveis; `input_hidden` descartado).
  - `parse_pasted(text: str) -> list[str]`: separa por linha em branco, apara, junta quebras simples com espaço, descarta vazios, cada pergunta ≤ 1000 caracteres, no máximo 30.
- Produces (API): `QuestionOut{id, position, source, label, kind, options, required, sensitive, answer, answer_state, bank_key, bank_suggestion: {key, value} | null, task: {state, error} | null}` (sugestão só para sensíveis, pelo `SENSITIVE_BANK_KEYS`, só do banco do próprio usuário); `POST /applications/{id}/questions/fetch` (async; empresa não-Greenhouse → 409 `MSG_NOT_GREENHOUSE = "Esta vaga não informa as perguntas. Cole-as aqui."`; já há perguntas `api` → devolve as existentes sem chamar a rede; falha/timeout `greenhouse_questions_timeout` → 503 `MSG_QUESTIONS_UNAVAILABLE = "Não foi possível buscar as perguntas agora. Cole-as aqui."`; usa `source_getter` de `api/sources.py`); `POST /applications/{id}/questions {text}` (colar; acrescenta `source="pasted"`, `kind="text"`, `required=false`); `PUT /applications/{id}/questions/{qid} {answer?: str ≤ 5000, final?: bool, save_to_bank?: str (chave ≤ 64, `[a-z0-9_]+`)}` (`answer` → `answer_state="edited"`; `final` → `"final"`; `save_to_bank` faz upsert em `answer_bank` com `lang = app.locale`).

- [ ] **Step 1: Capture the fixture**: uma chamada real a `QUESTIONS_URL` para um job de `tests/fixtures/greenhouse/gitlab.json` (ou `anthropic.json`); salvar sem alterar os objetos em `job_questions.json`, sem `content` gigante se preciso (documentar o corte), e registrar data/token/job no `README.md`.
- [ ] **Step 2: Write failing tests**: Greenhouse — `test_fetch_job_questions_parses_fixture` (contagem e um rótulo conhecido da fixture), `test_fetch_job_questions_404_and_bad_json`, `test_external_id_must_be_digits`; `questions.py` — `test_parse_pasted_splits_on_blank_lines`, `test_parse_pasted_caps_and_trims`, `test_sensitive_list` (parametrizado com ≥ 1 rótulo pt/en/de por categoria → sensível; "Why do you want to join us?", "Describe a project you are proud of", "Wie haben Sie von uns erfahren?" → não), `test_age_word_boundary` ("manage a team" não é sensível), `test_decline_option_is_sensitive`, `test_compliance_and_demographic_always_sensitive`, `test_map_kinds`; API (`db`) — `test_fetch_non_greenhouse_409`, `test_fetch_failure_503_falls_back_to_paste`, `test_fetch_is_idempotent`, `test_paste_appends_positions`, `test_save_to_bank_uses_application_locale`, `test_bank_suggestion_only_for_sensitive_and_only_own_bank` (item de outro usuário com mesma chave invisível).
- [ ] **Step 3: Run** `pytest tests/applications/test_questions.py tests/sources/test_greenhouse.py tests/test_api_applications.py -v` → FAIL.
- [ ] **Step 4: Implement**.
- [ ] **Step 5: Run** → PASS.
- [ ] **Step 6: Commit** `feat(applications): Greenhouse and pasted questions with sensitive classification`.

### Task 14: Rascunho de respostas (banco e abertas) pela fila do worker

**Files:**
- Modify: `rumo_core/applications/questions.py`, `worker/prepare_applications.py`, `api/applications.py`, `tests/applications/test_questions.py`, `tests/test_worker_prepare_applications.py`, `tests/test_api_applications.py`

**Interfaces:**
- Consumes: `build_context`/`validate_line`, `evidence_block`, `select_evidence`, `AnswerBankItem`.
- Produces:
  - `class AnswerDraftIn(BaseModel, extra="ignore"): bank_key: str | None = None; text: str; source_refs: list[str] = []`.
  - `class AnswerInvalid(Exception)`; `MAX_BANK_ITEMS = 50`.
  - `bank_reuse_ok(answer: str, bank_value: str) -> bool`: números (`numbers_supported`) e moedas (`R$`, `US$`, `A$`, `$`, `€`, `£`, `BRL`, `USD`, `EUR`, `AUD`, `GBP`, `CHF`) do rascunho ⊆ os do valor do banco.
  - `async draft_answer(llm, question_label: str, job: JobView, profile: CanonicalProfile, evidence_refs: list[str], bank: list[tuple[str, str, str]], *, locale: str) -> tuple[str, str | None]` (texto, bank_key): até 50 itens `(key, lang, value)` mais recentes e **não sensíveis** (`sensitive_category(key)` nulo); com `bank_key` válido do usuário → `bank_reuse_ok` obrigatório; sem `bank_key` → o texto passa por `validate_line` como linha `ai` com os `source_refs` declarados; falha → 1 retentativa com os motivos; depois `AnswerInvalid`.
  - Fila: `Application.tasks` com itens `{"kind": "answer", "qid": int, "state": "queued"|"failed", "error": str | null}`. `POST /applications/{id}/questions/{qid}/draft` → 202: pergunta sensível → 409 `MSG_SENSITIVE = "Esta pergunta é pessoal, legal ou de remuneração: responda você."`; `kind="file"` → 409 `"Envie o arquivo você mesmo no site da vaga."`; candidatura sem currículo `ready`/`applied` → 409 `MSG_NOT_READY = "Gere o currículo antes."`; item já `queued` → 202 sem duplicar.
  - Worker: quando não há geração pendente, processa **1** tarefa `queued` (mais antiga) — mesmo portão, pausa, timeout e `SKIP LOCKED` na candidatura; sucesso grava `answer`, `answer_state="draft"`, `bank_key` (só se a resposta ainda está `empty`/`draft`; edição da pessoa nunca é sobrescrita) e remove a tarefa; `AnswerInvalid` → tarefa `failed` com `"Não consegui rascunhar sem inventar. Escreva você."`; `LLMError` → `failed` com `MSG_LLM_FAIL`. Tarefas não contam no teto diário.

- [ ] **Step 1: Write failing tests**: `test_bank_reuse_keeps_number_and_currency` (banco "R$ 8.000": "R$ 8.000 por mês" → `True`; "US$ 8,000" → `False`), `test_bank_number_converted_rejected` ("R$ 8.000" → "R$ 1.600"), `test_open_answer_validated_like_lines` (invenção → retry → `AnswerInvalid`), `test_sensitive_bank_items_not_sent_to_llm`, `test_bank_capped_at_50_most_recent`; worker — `test_answer_task_drafted_and_removed`, `test_answer_task_failed_message`, `test_user_edit_not_overwritten`, `test_generation_has_priority_over_tasks`; API — `test_draft_sensitive_question_409_never_queued`, `test_draft_requires_ready_resume`, `test_draft_not_duplicated`, `test_other_user_bank_invisible_to_draft` (FakeLLM recebe só itens do dono).
- [ ] **Step 2: Run** `pytest tests/applications/test_questions.py tests/test_worker_prepare_applications.py tests/test_api_applications.py -v` → FAIL.
- [ ] **Step 3: Implement**.
- [ ] **Step 4: Run** → PASS.
- [ ] **Step 5: Commit** `feat(applications): answer drafts from the answer bank or the profile`.

### Task 15: Carta (opcional) — `draft_letter`

**Files:**
- Modify: `rumo_core/applications/draft.py`, `worker/prepare_applications.py`, `api/applications.py`, `tests/applications/test_draft_resume.py` (ou novo `tests/applications/test_draft_letter.py`), `tests/test_worker_prepare_applications.py`, `tests/test_api_applications.py`

**Interfaces:**
- Produces: `LetterDraftIn{paragraphs: list[_TextIn] (1..5)}`; `async draft_letter(llm, profile, job: JobView, reqs: Requirements, evidence: dict[str, list[str]], *, locale: str) -> list[DocLine]` (`section="letter"`, `origin="ai"`; mesma validação e retentativa; `DraftInvalid`); empresa e cargo do prompt vêm da vaga e são liberados pelo validador só na forma exata. Tarefa `{"kind": "cover_letter", "state": ...}`; `POST /applications/{id}/cover-letter` → 202 (409 `MSG_NOT_READY`; não duplica). Sucesso grava `ApplicationDocument(kind="cover_letter", version=max+1)`; falha → tarefa `failed` com `MSG_DRAFT_INVALID`/`MSG_LLM_FAIL`. As rotas `cover-letter.pdf|txt` da Task 10 passam a funcionar com o documento.

- [ ] **Step 1: Write failing tests**: `test_letter_lines_validated_and_retry`, `test_letter_company_from_job_allowed_other_company_rejected`, `test_letter_task_creates_document`, `test_letter_request_requires_ready`, `test_letter_pdf_after_generation` (`slow` para o PDF; `db` para a rota txt).
- [ ] **Step 2: Run** os arquivos acima → FAIL.
- [ ] **Step 3: Implement**.
- [ ] **Step 4: Run** → PASS.
- [ ] **Step 5: Commit** `feat(applications): optional cover letter under the same validation`.

### Task 16: Web — lacunas, passo "Perguntas" e botão "Gerar carta"

**Files:**
- Create: `web/components/applications/GapItem.tsx`, `web/components/applications/GapItem.test.tsx`, `web/components/applications/QuestionsStep.tsx`, `web/components/applications/QuestionsStep.test.tsx`
- Modify: `web/components/applications/ResumeStep.tsx` (+ teste), `web/lib/applications.ts` (+ teste), `web/app/candidaturas/[id]/page.tsx`, `web/app/globals.css`

**Interfaces:**
- Produces (`lib`): `resolveGap(id, reqId, action, text?)`, `fetchQuestions(id)`, `pasteQuestions(id, text)`, `updateQuestion(id, qid, {answer?, final?, save_to_bank?})`, `draftQuestion(id, qid)`, `requestCoverLetter(id)`; tipos `QuestionOut`, `TaskItem`.
- `GapItem({item, onResolve})`: "Não encontrei no seu perfil: {texto}" + "Tenho isso" (abre campo curto ≤ 200, "Adicionar ao perfil") e "Não tenho" (vira o aviso "Lacuna conhecida", sem desfazer); após "Tenho isso", mostra "Perfil atualizado (versão N). Gerando de novo…" ou, com `regenerated=false`, "Perfil atualizado. Gere de novo quando puder.".
- `QuestionsStep`: ao abrir sem perguntas, chama `fetchQuestions` uma vez se a fonte permitir; 409/503 → área "Cole as perguntas (uma por parágrafo)"; lista com rótulo, obrigatória, tipo; sensível → selo "Responda você" sem botão de rascunho e com "Sugestão do seu banco: …" quando houver; "Rascunhar" (estado `queued` → "Rascunhando…", `failed` → motivo), textarea de resposta, "Marcar como final", "Salvar no banco" (chave sugerida a partir do rótulo, editável). "Gerar carta" no passo Currículo (desabilitado até `ready`, mostra estado da tarefa).

- [ ] **Step 1: Write failing tests**: "Tenho isso" envia texto e mostra versão; "Não tenho" marca aviso sem chamar perfil; fallback para colar no 409/503; sensível sem botão "Rascunhar" e com sugestão do banco; rascunho `failed` mostra motivo; "Marcar como final" envia `final: true`; pergunta hostil aparece como texto; "Gerar carta" desabilitado antes de `ready`.
- [ ] **Step 2: Run** `npm test` → FAIL.
- [ ] **Step 3: Implement**.
- [ ] **Step 4: Run** `npm test && npm run lint` → PASS.
- [ ] **Step 5: Commit** `feat(web): gaps, extra questions and cover letter request`.

---

## Fatia 5 — kit, "Apliquei", trilha e lateral

### Task 17: `apply`/`unapply` e `/overview.applications`

**Files:**
- Modify: `api/applications.py`, `api/overview.py`, `tests/test_api_applications.py`, `tests/test_api_overview.py`

**Interfaces:**
- Produces: `POST /applications/{id}/apply` → `ApplicationOut` (só de `ready`; senão 409 `MSG_NOT_READY`; `state="applied"`, `applied_at=now()`; repetido em `applied` → 200 idempotente); `POST /applications/{id}/unapply` → `state="ready"`, `applied_at=None` (de outro estado → 409 `"Esta candidatura não está marcada como aplicada."`). Nenhuma chamada externa.
- `ApplicationsOverview(BaseModel): total: int = 0; ready: int = 0; applied: int = 0`; `OverviewOut.applications`; contagem escopada por `user_id` (`ready` = estado `ready`).

- [ ] **Step 1: Write failing tests**: `test_apply_from_ready_sets_applied_at`, `test_apply_requires_ready`, `test_unapply_reverts`, `test_apply_other_user_404`; overview: `CONTRACT_KEYS` passa a incluir `"applications"`, `test_applications_counts_scoped_by_user` (`{total: 3, ready: 1, applied: 1}`), `test_overview_empty_user_defaults` com `applications == {"total":0,"ready":0,"applied":0}`.
- [ ] **Step 2: Run** `pytest tests/test_api_applications.py tests/test_api_overview.py -v` → FAIL.
- [ ] **Step 3: Implement**.
- [ ] **Step 4: Run** → PASS; depois `pytest -m "not slow"`.
- [ ] **Step 5: Commit** `feat(api): apply/unapply and applications in the overview`.

### Task 18: Web — Kit, "Apliquei", lista `/candidaturas`, passo 7 da trilha e item da lateral

**Files:**
- Create: `web/components/applications/KitStep.tsx`, `web/components/applications/KitStep.test.tsx`, `web/app/candidaturas/page.tsx`, `web/app/candidaturas/page.test.tsx`
- Modify: `web/lib/overview.ts` (+ teste), `web/lib/applications.ts` (+ teste), `web/components/stepper/computeSteps.ts` (+ teste), `web/components/shell/Sidebar.tsx` (+ teste), `web/components/shell/icons.tsx`, `web/components/shell/AppShell.test.tsx` (`titleForPath`), `web/components/home/*` (se algum teste usa o overview completo), `web/app/candidaturas/[id]/page.tsx`, `web/app/globals.css`

**Interfaces:**
- Produces (`lib`): `applyApplication(id)`, `unapplyApplication(id)`; `ApplicationsOverview = {total, ready, applied}` em `Overview`, `emptyOverview()` e `normalizeOverview` (seção ausente → zeros).
- `computeSteps`: passo `apply` vira `kind: "required"`; `done` quando `applications.applied ≥ 1`; detalhe: `"{n} aplicada(s)"` / `"{n} kit(s) pronto(s)"` / `"Nenhuma candidatura ainda"`; CTA: sem `ready` → `{label: "Preparar candidatura", href: "/jobs"}`, com `ready` → `{label: "Abrir candidaturas", href: "/candidaturas"}`; `reviewHref: "/candidaturas"`. Como é o último obrigatório, só vira atual depois de "Avaliar melhores vagas" concluído (≥ 1 vaga avaliada).
- `Sidebar.NAV_ITEMS`: `{ href: "/candidaturas", label: "Candidaturas", Icon: ApplicationsIcon }` depois de "Vagas"; `pendingFor` sem regra nova (null).
- `KitStep`: "Baixar PDF", "Copiar texto" (texto do `resume.txt`, `navigator.clipboard` com fallback de seleção), carta (se houver), respostas finais copiáveis uma a uma, "Abrir a vaga" (link http/https, nova aba, `rel="noopener noreferrer"`), aviso "Você envia a candidatura no site da empresa; o Rumo não envia nada." e **"Apliquei"** (→ `applied`, troca por "Aplicada em {data}" + "Desfazer"); chama `useRefreshOverview` depois de apply/unapply/gerar.
- `/candidaturas`: lista simples (cargo, empresa, estado em texto, cobertura "n de N", atualizada há…), vazio com "Ver vagas" → `/jobs`, erro com "Tentar de novo".

- [ ] **Step 1: Write failing tests**: `computeSteps` — atualizar `test_all_done_apply_blocked` para `test_all_done_apply_current_with_prepare_cta` (scored > 0, sem candidaturas → `apply` atual, CTA `/jobs`), novo `test_ready_application_cta_opens_list`, `test_applied_marks_apply_done`, `test_apply_upcoming_until_evaluated`, e o teste "obrigatórios prontos e entrevista pendente" passa a esperar `["apply"]` como atual; `overview.ts` — seção `applications` ausente vira zeros; `Sidebar` — item "Candidaturas" ativo em `/candidaturas/12`; `titleForPath("/candidaturas/3") === "Candidaturas"`; `KitStep` — "Apliquei" chama `applyApplication` e mostra "Desfazer"; link `javascript:` não vira `<a>`; lista — vazio e erro.
- [ ] **Step 2: Run** `npm test` → FAIL.
- [ ] **Step 3: Implement**.
- [ ] **Step 4: Run** `npm test && npm run lint && npm run build` → PASS.
- [ ] **Step 5: Verify visually** (subir `docker compose up` + `npm run dev`): fluxo completo com uma vaga real (Preparar → Currículo → editar linha inválida → PDF bloqueado → corrigir → Perguntas coladas → Kit → Apliquei → trilha passo 7 concluído), em 375 px, 768 px e desktop, claro e escuro, sem rolagem horizontal; baixar e abrir um PDF de cada modelo (`us`, `de`, `au`, `br`) conferindo acentos/ß e o formato.
- [ ] **Step 6: Run** `pytest -m "not slow"` e `pytest -m slow tests/applications` (no container) → PASS.
- [ ] **Step 7: Commit** `feat(web): application kit, applied flag, list page, journey step and sidebar item`.
