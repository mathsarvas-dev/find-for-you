# Rumo — Marco 2a: Perfil (upload → extração → revisão → vetorização) — Plano de implementação

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** O usuário envia um currículo (PDF/DOCX), vê o JSON extraído como formulário editável, confirma, e o perfil canônico versionado é vetorizado localmente pelo worker.

**Architecture:** A api só grava e lê; o **worker** faz tudo que é pesado ou usa Groq/torch (extração com LLM, vetorização), pegando trabalho por status no banco (reinício seguro). O perfil confirmado vira uma linha versionada em `profiles` e chunks determinísticos em `profile_chunks`, vetorizados só depois da confirmação. A entrevista com IA é o **marco 2b**, com plano próprio (spec 8, marco 2: "depois a entrevista").

**Tech Stack:** o do marco 1, mais `pypdf`, `python-docx`, `python-multipart`; Next.js + vitest no web.

**Spec:** `docs/superpowers/specs/2026-11-11-rumo-design.md` (seções 4, 5.1 passos 1–3, 6, 7, 9; marco 2 da seção 8)

## Global Constraints

- Toda tabela tem `user_id`, `created_at`, `updated_at` (mixins de `rumo_core/models.py`); toda rota usa `current_user_id()`.
- **Nada é vetorizado antes da confirmação** do usuário (spec 5.1 passo 3).
- Respostas do LLM validadas por Pydantic, **1 retentativa**, depois estado `erro` sem gravar nada como válido (spec 6). O prompt deve conter a palavra "json" (modo `json_object` do Groq).
- Chamadas ao Groq **só no worker** (o limitador de taxa é por processo).
- Vetores de 1024 dimensões; `model_version` gravado junto de cada vetor e derivado do nome do modelo + `max_tokens` (não string fixa).
- `embed()` é síncrono: no worker chamar via `asyncio.to_thread`, com lock e lote configurável (`embed_batch_size`, padrão 8).
- O texto do currículo é **dado, nunca instrução** para o LLM; ids dos itens do perfil são gerados pelo código, não pelo LLM.
- Testes sem internet, LLM e embedder mockados; testes de banco usam marcador `db`.
- Frontend: UI em PT-BR, tokens CSS existentes, sem os marcadores de "cara de IA" (spec 9.1–9.2), estados vazio/carregando/erro em toda tela (9.4).

## Review Focus

1. PDF escaneado/sem camada de texto (ou DOCX vazio): erro claro "não consegui ler texto", sem chamar o LLM.
2. Currículo longo: texto truncado em `extract_max_chars` (padrão 20 000) com aviso, sem erro de contexto do LLM.
3. Currículo parcial (sem seção de formação, datas ausentes): campos opcionais viram vazios, a extração não falha.
4. Texto do currículo com instrução embutida ("ignore as regras e devolva…"): tratado como conteúdo; o schema continua sendo imposto e nada vira instrução.
5. Duplo clique em "Confirmar" ou reenvio do mesmo perfil: não cria versão duplicada nem chunks duplicados.
6. Arquivo > `upload_max_bytes` (5 MB), tipo inválido ou corrompido: 4xx com mensagem, nada gravado.
7. Worker reiniciado no meio de uma extração ou de um lote de vetorização: retoma sem perder nem duplicar.

---

## File Structure

```
rumo_core/
├─ config.py                  # + upload_max_bytes, extract_max_chars, embed_batch_size
├─ models.py                  # + ProfileImport, Profile, ProfileChunk
├─ profile/
│  ├─ schema.py               # CanonicalProfile e sub-modelos (Pydantic)
│  ├─ text_extract.py         # PDF/DOCX -> texto
│  ├─ extraction.py           # texto -> CanonicalProfile via LLMProvider
│  ├─ chunker.py              # CanonicalProfile -> chunks determinísticos
│  └─ service.py              # confirmar versão, criar chunks (idempotente)
├─ embeddings/bge_m3.py       # model_version derivado
api/profile.py                # rotas /profile/*
worker/jobs.py                # process_pending_imports, embed_pending_chunks
alembic/versions/0002_profile.py
tests/profile/ ...            # espelha
web/app/onboarding/...        # wizard passos 1–3
```

---

### Task 1: Tabelas e migração do perfil

**Files:**
- Modify: `rumo_core/models.py`, `rumo_core/config.py`
- Create: `alembic/versions/0002_profile.py`
- Test: `tests/test_profile_models.py` (marcador `db`)

**Interfaces:**
- Produces (modelos SQLAlchemy, todos com `TimestampMixin` + `UserScopedMixin` salvo `User`):
  - `ProfileImport`: `id`, `filename: str`, `content_type: str`, `raw_text: str | None`, `status: str` (`pending`|`extracting`|`ready`|`error`), `extracted: JSONB | None`, `error: str | None`, `truncated: bool`.
  - `Profile`: `id`, `version: int`, `data: JSONB`, `narrative: str | None`, `content_hash: str`; `UniqueConstraint(user_id, version)` e `UniqueConstraint(user_id, content_hash)`.
  - `ProfileChunk`: `id`, `profile_version: int`, `source_ref: str` (id do item de origem), `kind: str`, `text: str`, `content_hash: str`, `embedding: Vector(1024) | None`, `model_version: str | None`; `UniqueConstraint(user_id, profile_version, source_ref)`.
- Produces (`Settings`): `upload_max_bytes: int = Field(5_000_000, gt=0)`, `extract_max_chars: int = Field(20_000, gt=0)`, `embed_batch_size: int = Field(8, gt=0)`.

- [ ] **Step 1: Write the failing test** — `test_profile_tables_roundtrip`: após `alembic upgrade head`, inserir `ProfileImport`, `Profile` e `ProfileChunk` (com `embedding=None`) para `user_id=1`, ler de volta; `test_profile_unique_version_per_user`: segunda linha com mesmo `(user_id, version)` levanta `IntegrityError`; `test_chunk_vector_dimension`: gravar vetor de 1024 floats funciona, de 3 falha.
- [ ] **Step 2: Run** `.venv/Scripts/python -m pytest tests/test_profile_models.py -v` — Expected: FAIL (modelos inexistentes).
- [ ] **Step 3: Implement** os modelos acima e a migração `0002` (down_revision `"0001"`; `downgrade` derruba as três tabelas); adicionar os três campos em `Settings`. Índice em `profile_chunks(user_id, model_version)` para achar pendentes.
- [ ] **Step 4: Run** o mesmo comando e `pytest -m "not slow" -q` — Expected: tudo PASS.
- [ ] **Step 5: Commit** `feat(profile): tables and migration 0002`.

---

### Task 2: Schema canônico e extração de texto de arquivos

**Files:**
- Create: `rumo_core/profile/__init__.py`, `rumo_core/profile/schema.py`, `rumo_core/profile/text_extract.py`
- Modify: `pyproject.toml` (deps `pypdf`, `python-docx`, `python-multipart`; `packages` inclui `rumo_core.profile`)
- Test: `tests/profile/test_schema.py`, `tests/profile/test_text_extract.py` (fixtures PDF/DOCX pequenos gerados nos testes)

**Interfaces:**
- Produces:
  - `CanonicalProfile(BaseModel)`: `basics: Basics` (`name`, `headline`, `email`, `phone`, `location`, `links: list[str]` — todos opcionais/vazios por padrão), `summary: str = ""`, `experiences: list[Experience]` (`id`, `company`, `title`, `start`, `end`, `location`, `description`, `achievements: list[Achievement]` com `id`, `text`), `education: list[Education]`, `skills: list[Skill]` (`id`, `name`, `level | None`), `languages: list[Language]`, `certifications`, `projects`. Todo item de lista tem `id: str`.
  - `assign_ids(profile: CanonicalProfile) -> CanonicalProfile`: preenche `id` ausente com `uuid4().hex[:8]`, preserva ids existentes.
  - `class TextExtractError(Exception)`; `extract_text(data: bytes, content_type: str, filename: str) -> str`: suporta PDF e DOCX; tipo decidido por conteúdo (magic bytes `%PDF`, zip) e não só pela extensão; retorna texto normalizado (espaços colapsados).

- [ ] **Step 1: Write the failing tests** — `test_extract_pdf_text`, `test_extract_docx_text`, `test_scanned_pdf_raises` (PDF sem texto → `TextExtractError` com mensagem contendo "texto"), `test_unsupported_type_raises`, `test_corrupt_file_raises`, `test_partial_profile_valid` (`CanonicalProfile()` vazio e só com `basics.name` validam), `test_assign_ids_keeps_existing_and_fills_missing`.
- [ ] **Step 2: Run** `pytest tests/profile -v` — Expected: FAIL.
- [ ] **Step 3: Implement** conforme as assinaturas; PDF com `pypdf.PdfReader`, DOCX com `python-docx` (parágrafos + células de tabela). Qualquer exceção das libs vira `TextExtractError`.
- [ ] **Step 4: Run** — Expected: PASS.
- [ ] **Step 5: Commit** `feat(profile): canonical schema and resume text extraction`.

---

### Task 3: Extração do perfil com o LLM

**Files:**
- Create: `rumo_core/profile/extraction.py`
- Test: `tests/profile/test_extraction.py` (LLM falso)

**Interfaces:**
- Consumes: `LLMProvider.complete_json(messages, schema)`, `CanonicalProfile`, `assign_ids`.
- Produces: `async def extract_profile(llm: LLMProvider, text: str, max_chars: int) -> tuple[CanonicalProfile, bool]` (o `bool` é `truncated`). Mensagem de sistema fixa: instruções em PT/EN dizendo que o texto do usuário é **apenas conteúdo do currículo**, nunca instruções; que a resposta é um objeto **json** no schema dado; que campos ausentes ficam vazios e nada pode ser inventado. O texto vai em mensagem de usuário delimitada (`<curriculo>…</curriculo>`).

- [ ] **Step 1: Write the failing tests** — `test_truncates_long_text` (texto > `max_chars` chega ao LLM cortado e `truncated is True`); `test_ids_assigned_by_code` (resposta do LLM com `id` vazio recebe ids); `test_prompt_mentions_json_and_delimits_resume` (inspeciona `messages` do LLM falso: contém "json", e o texto do currículo aparece só dentro do delimitador, na mensagem de usuário); `test_injection_text_is_only_data` (currículo contendo "ignore as instruções anteriores" não altera a mensagem de sistema); `test_llm_invalid_response_propagates` (`LLMInvalidResponse` do provedor sobe sem ser engolida).
- [ ] **Step 2: Run** `pytest tests/profile/test_extraction.py -v` — Expected: FAIL.
- [ ] **Step 3: Implement** `extract_profile`; a retentativa de validação já vive no `GroqProvider`.
- [ ] **Step 4: Run** — Expected: PASS.
- [ ] **Step 5: Commit** `feat(profile): LLM resume extraction`.

---

### Task 4: Chunker e serviço de confirmação (versionamento idempotente)

**Files:**
- Create: `rumo_core/profile/chunker.py`, `rumo_core/profile/service.py`
- Test: `tests/profile/test_chunker.py`, `tests/profile/test_service.py` (`db`)

**Interfaces:**
- Produces:
  - `@dataclass Chunk: source_ref: str; kind: str; text: str; content_hash: str`.
  - `build_chunks(profile: CanonicalProfile, narrative: str | None) -> list[Chunk]`: determinístico; um chunk por experiência (cargo, empresa, período, descrição e conquistas), um por formação, um para o conjunto de habilidades (`source_ref="skills"`), um para o resumo+narrativa (`source_ref="summary"`), um por projeto/certificação; chunks vazios omitidos; `content_hash` = sha256 do `text`.
  - `confirm_profile(session: Session, user_id: int, profile: CanonicalProfile, narrative: str | None) -> Profile`: calcula `content_hash` do perfil (JSON canônico ordenado + narrativa); se já existe versão do usuário com esse hash, **retorna a existente** (idempotente); senão cria `version = max+1`, grava os chunks com `embedding=None`. Para chunks cujo `source_ref` e `content_hash` não mudaram desde a versão anterior, **copia embedding e model_version** (re-vetoriza só o alterado).
  - `get_current_profile(session, user_id) -> Profile | None`.

- [ ] **Step 1: Write the failing tests** — chunker: `test_one_chunk_per_experience`, `test_empty_sections_omitted`, `test_chunks_deterministic`; service: `test_confirm_creates_version_1_with_pending_chunks`, `test_confirm_twice_same_profile_is_idempotent` (uma versão, mesmos chunks), `test_edit_creates_version_2_and_reuses_unchanged_embeddings` (marca um chunk como vetorizado, edita outra experiência, confere que o chunk intocado manteve embedding e o editado ficou `None`), `test_versions_are_per_user`.
- [ ] **Step 2: Run** `pytest tests/profile/test_chunker.py tests/profile/test_service.py -v` — Expected: FAIL.
- [ ] **Step 3: Implement** conforme as assinaturas; transação única; corrida entre dois confirms resolvida pela unicidade `(user_id, content_hash)` (capturar `IntegrityError` e reler).
- [ ] **Step 4: Run** — Expected: PASS.
- [ ] **Step 5: Commit** `feat(profile): chunker and idempotent versioned confirm`.

---

### Task 5: Rotas da API do perfil

**Files:**
- Create: `api/profile.py`
- Modify: `api/main.py` (incluir o router; restringir o handler de `OperationalError` para não responder corpo de health em rotas que não são `/health`)
- Test: `tests/test_api_profile.py` (`db`, `TestClient`)

**Interfaces:**
- Consumes: `extract_text` só para validar tipo na entrada; `confirm_profile`, `get_current_profile`; `current_user_id`.
- Produces (todas escopadas por `user_id`):
  - `POST /profile/imports` (multipart `file`) → `201 {id, status:"pending"}`; valida tamanho (`upload_max_bytes`, 413), tipo por conteúdo (415) e extrai o texto **na api** (rápido, local); falha de leitura → `422 {detail}` sem gravar; texto vazio → `422` "não consegui ler texto do arquivo". Grava `raw_text` e `status="pending"`.
  - `GET /profile/imports/{id}` → `{id, status, error, truncated, profile: CanonicalProfile | null}` (404 se de outro usuário).
  - `POST /profile/confirm` body `{profile: CanonicalProfile, narrative: str | None}` → `{version, chunks_total, chunks_embedded}`.
  - `GET /profile` → perfil atual + `{chunks_total, chunks_embedded}` ou `404` se não houver.

- [ ] **Step 1: Write the failing tests** — `test_upload_pdf_creates_pending_import`; `test_upload_too_large_413`; `test_upload_wrong_type_415`; `test_upload_scanned_pdf_422_and_nothing_stored`; `test_get_import_other_user_404`; `test_confirm_idempotent_double_post` (duas chamadas, uma versão); `test_get_profile_404_when_empty`; `test_get_profile_reports_embedding_progress`; `test_operational_error_on_other_route_not_health_body`.
- [ ] **Step 2: Run** `pytest tests/test_api_profile.py -v` — Expected: FAIL.
- [ ] **Step 3: Implement** as rotas; limite de tamanho lendo com teto (não carregar arquivo gigante inteiro); `GET /profile/imports/{id}` devolve `profile` só quando `status="ready"`.
- [ ] **Step 4: Run** `pytest -m "not slow" -q` — Expected: PASS.
- [ ] **Step 5: Commit** `feat(api): profile import, review and confirm routes`.

---

### Task 6: Jobs do worker (extração e vetorização) e embedder seguro

**Files:**
- Create: `worker/jobs.py`
- Modify: `worker/main.py` (registrar jobs; ao iniciar, voltar `extracting` → `pending`), `rumo_core/embeddings/bge_m3.py` (`model_version` derivado), `Dockerfile` (extra `embeddings` com torch CPU), `docker-compose.yml` (volume de cache do Hugging Face no worker), `.env.example`
- Test: `tests/test_worker_jobs.py` (`db`; LLM e embedder falsos)

**Interfaces:**
- Consumes: `extract_profile`, `LLMProvider`, `Embedder`, modelos da Task 1.
- Produces:
  - `async def process_pending_imports(session_factory, llm: LLMProvider, max_chars: int) -> int`: pega um `ProfileImport` `pending` (`FOR UPDATE SKIP LOCKED`), marca `extracting`, chama `extract_profile`; sucesso → `ready` com `extracted` e `truncated`; `LLMError`/validação → `error` com mensagem curta, nada em `extracted`. Retorna quantos processou.
  - `async def embed_pending_chunks(session_factory, embedder: Embedder, batch_size: int) -> int`: seleciona chunks com `embedding IS NULL` (ou `model_version` diferente do atual) da **versão atual** primeiro, em lotes; `embedder.embed` via `asyncio.to_thread` sob um `asyncio.Lock`; grava `embedding` e `model_version`; commit por lote (reinício retoma). Retorna quantos vetorizou.
  - `BgeM3Embedder.model_version` passa a `f"bge-m3@1:max{max_tokens}"`.
  - O embedder real só é carregado quando há chunks pendentes (carga lazy já existente).

- [ ] **Step 1: Write the failing tests** — `test_import_success_sets_ready_and_extracted`; `test_import_llm_error_sets_error_and_no_extracted`; `test_two_workers_do_not_take_same_import` (duas chamadas concorrentes, uma processa); `test_startup_resets_stuck_extracting`; `test_embed_fills_pending_in_batches` (batch_size=2, 5 chunks → 3 chamadas ao embedder falso); `test_embed_skips_when_nothing_pending_without_loading_model`; `test_embed_failure_midway_keeps_committed_batches`; `test_model_version_includes_max_tokens`.
- [ ] **Step 2: Run** `pytest tests/test_worker_jobs.py -v` — Expected: FAIL.
- [ ] **Step 3: Implement** os jobs e registrá-los em `worker/main.py` (extração a cada 3 s, vetorização a cada 5 s); no Dockerfile instalar `torch` do índice CPU antes de `pip install ".[embeddings]"` (somente a imagem do worker usa o extra — `build.args` ou segundo stage `worker`); montar `hf_cache:/root/.cache/huggingface` no serviço `worker`; `.env.example` ganha `HF_HOME` documentado.
- [ ] **Step 4: Run** `pytest -m "not slow" -q` e `docker compose build worker` — Expected: PASS e build concluído.
- [ ] **Step 5: Commit** `feat(worker): resume extraction and chunk embedding jobs`.

---

### Task 7: Wizard de onboarding no frontend (passos 1–3)

**Files:**
- Create: `web/app/onboarding/page.tsx`, `web/components/onboarding/UploadStep.tsx`, `web/components/onboarding/ReviewStep.tsx`, `web/components/onboarding/VectorStatus.tsx`, `web/lib/profile.ts` (cliente da api + tipos espelhando `CanonicalProfile`)
- Modify: `web/app/page.tsx` (link para o onboarding), `web/next.config.ts` se precisar de proxy/CORS para `http://localhost:8000`, `rumo_core`/`api/main.py` (CORS para `http://localhost:3000`)
- Test: `web/components/onboarding/*.test.tsx`, `web/lib/profile.test.ts`

**Interfaces:**
- Consumes: rotas da Task 5.
- Produces: `uploadResume(file: File): Promise<{id: number}>`, `getImport(id): Promise<ImportState>`, `confirmProfile(profile, narrative): Promise<ConfirmResult>`, `getProfile(): Promise<ProfileState | null>`; componentes `UploadStep({onUploaded})`, `ReviewStep({profile, narrative, onConfirm})` (formulário editável: dados básicos, experiências com conquistas adicionáveis/removíveis, formação, habilidades, idiomas; campo de narrativa livre do passo 1), `VectorStatus({total, embedded})`.

- [ ] **Step 1: Write the failing tests** — `UploadStep`: rejeita arquivo > 5 MB e tipo errado com mensagem; mostra estado "enviando"; `ReviewStep`: edita um campo e `onConfirm` recebe o valor editado; botão "Confirmar" desabilita após o primeiro clique (duplo clique); remover conquista funciona; `VectorStatus`: mostra "3 de 8" e estado concluído; polling de `getImport` para em `ready` e mostra erro legível em `error`; estado vazio e de erro de rede presentes.
- [ ] **Step 2: Run** `cd web && npm test` — Expected: FAIL.
- [ ] **Step 3: Implement** seguindo spec 9.1–9.4 (tokens existentes, rótulos claros, foco visível, mensagens específicas, sem skeleton genérico decorativo); página `onboarding` orquestra: upload → polling até `ready` → revisão → confirmar → progresso da vetorização.
- [ ] **Step 4: Run** `cd web && npm test && npm run lint && npm run build` — Expected: PASS. Verificar visualmente em 3 larguras (mobile 375, tablet 768, desktop) com o preview do navegador.
- [ ] **Step 5: Commit** `feat(web): onboarding wizard for resume upload and review`.

---

## Verificação ponta a ponta (fim do marco)

Com `docker compose up -d` e a chave do Groq em `.env` (o usuário coloca): subir um currículo real em `/onboarding`, revisar, confirmar e conferir `GET /profile` com `chunks_embedded == chunks_total`. Este passo usa a chave real e o modelo bge-m3 já baixado; é feito pelo usuário ou com a aprovação dele.
