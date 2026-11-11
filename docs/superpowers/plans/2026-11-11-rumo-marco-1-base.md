# Rumo — Marco 1: Base — Plano de implementação

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Entregar a fundação do Rumo: Postgres+pgvector em Docker, esqueletos de api, worker e web, e os dois provedores trocáveis (`llm/` com Groq e `embeddings/` com bge-m3 local), tudo testável isoladamente.

**Architecture:** Monorepo com `api/` (FastAPI + SQLAlchemy + Alembic), `worker/` (processo Python que compartilha o pacote `rumo_core` com a api), `web/` (Next.js). Provedores ficam atrás de interfaces (`Protocol`) em `rumo_core`. Toda tabela carrega `user_id` desde já.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy 2, Alembic, Pydantic v2, pydantic-settings, httpx, pytest, pytest-asyncio, sentence-transformers (bge-m3), pgvector, Postgres 16, Docker Compose, Next.js (App Router) + TypeScript.

**Spec:** `docs/superpowers/specs/2026-11-11-rumo-design.md` (seções 3, 6, 7, 9; marco 1 da seção 8)

## Global Constraints

- Python 3.12; Postgres 16 com extensão `pgvector`; vetores de **1024 dimensões** (bge-m3).
- Toda tabela tem `user_id`, `created_at`, `updated_at`.
- Provedor de LLM padrão: **Groq**, por API compatível com OpenAI, atrás de interface trocável; chave só em `.env`, **nunca no git**.
- Respostas do LLM validadas por **Pydantic**; **1 retentativa** se inválidas; depois erro explícito.
- Groq 429: fila com controle de ritmo e retentativa com espera crescente.
- A versão do modelo de embeddings é registrada junto de cada vetor (`model_version`).
- Testes de unidade **sem internet** e com LLM mockado.
- Frontend: tokens CSS com modo claro e escuro, uma única cor de acento, **sem** Inter/Roboto/Arial como identidade, sem gradientes roxo→azul, sem glassmorphism, UI em PT-BR (spec 9.1–9.2).

## Review Focus

1. Groq devolve JSON dentro de cerca de markdown (```json ... ```): o parser deve aceitar, não falhar.
2. `Retry-After` ausente ou não numérico em um 429: usar espera crescente padrão, sem quebrar.
3. Texto vazio ou lista vazia em `embed([])` e `embed([""])`: retornar lista vazia / vetor válido, sem exceção do modelo.
4. Texto muito longo (> limite de tokens do bge-m3): truncar sem erro.
5. Postgres ainda iniciando quando a api sobe: `/health` deve responder `db: down` com status 503, não derrubar o processo.

---

## File Structure

```
.
├─ docker-compose.yml          # postgres (pgvector) + serviços
├─ .env.example                # variáveis sem segredos
├─ .gitignore
├─ pyproject.toml              # pacote rumo_core + deps + pytest
├─ rumo_core/
│  ├─ config.py                # Settings (pydantic-settings)
│  ├─ db.py                    # engine, SessionLocal, Base, mixins
│  ├─ models.py                # User (marco 1); demais tabelas nos marcos seguintes
│  ├─ llm/
│  │  ├─ base.py               # LLMProvider (Protocol), erros
│  │  ├─ ratelimit.py          # RateLimiter
│  │  └─ groq.py               # GroqProvider
│  └─ embeddings/
│     ├─ base.py               # Embedder (Protocol)
│     └─ bge_m3.py             # BgeM3Embedder
├─ api/main.py                 # FastAPI app, /health, dependência current_user_id
├─ worker/main.py              # loop do worker + registro de jobs
├─ alembic/ + alembic.ini      # migrações
├─ scripts/bench_embeddings.py # mede velocidade da vetorização
├─ tests/                      # espelha a estrutura acima
└─ web/                        # Next.js: tokens, layout, página de status
```

---

### Task 1: Repositório, Docker e configuração

**Files:**
- Create: `.gitignore`, `.env.example`, `docker-compose.yml`, `pyproject.toml`, `rumo_core/__init__.py`, `rumo_core/config.py`
- Test: `tests/test_config.py`

**Interfaces:**
- Produces: `rumo_core.config.Settings` (campos: `database_url: str`, `groq_api_key: str`, `groq_model: str = "llama-3.3-70b-versatile"`, `groq_max_per_minute: int = 25`, `default_user_id: int = 1`) e `get_settings() -> Settings` (cacheada).

- [ ] **Step 1: Criar o repositório.** `git init` em ``; `.gitignore` deve cobrir `.env`, `__pycache__`, `.venv`, `node_modules`, `.next`, `pgdata/`.
- [ ] **Step 2: Escrever o teste falhando** em `tests/test_config.py`: `test_settings_read_env` (com `monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://u:p@localhost:5432/rumo")` e `GROQ_API_KEY="x"`, `get_settings.cache_clear()`, assert `get_settings().groq_model == "llama-3.3-70b-versatile"` e `groq_max_per_minute == 25`) e `test_settings_requires_database_url` (sem a variável, `pytest.raises(ValidationError)`).
- [ ] **Step 3: Rodar e ver falhar.** Run: `pytest tests/test_config.py -v` → Expected: FAIL (módulo inexistente).
- [ ] **Step 4: Implementar** `Settings(BaseSettings)` lendo `.env`, e `get_settings()` com `functools.lru_cache`. Em `pyproject.toml`, declarar as dependências do Tech Stack e `[tool.pytest.ini_options] asyncio_mode = "auto"`.
- [ ] **Step 5: Docker Compose.** Serviço `db` com imagem `pgvector/pgvector:pg16`, volume `./pgdata`, porta 5432, `healthcheck` com `pg_isready`. `.env.example` com todas as variáveis de `Settings` (valores vazios para segredos).
- [ ] **Step 6: Verificar.** Run: `pytest tests/test_config.py -v` → PASS. Run: `docker compose up -d db && docker compose ps` → serviço `db` com estado `healthy`.
- [ ] **Step 7: Commit** — `git add -A && git commit -m "chore: scaffold repo, docker postgres and settings"`.

---

### Task 2: Banco de dados, Alembic e vetores

**Files:**
- Create: `rumo_core/db.py`, `rumo_core/models.py`, `alembic.ini`, `alembic/env.py`, `alembic/versions/0001_base.py`
- Test: `tests/test_db.py`

**Interfaces:**
- Consumes: `get_settings().database_url` (Task 1).
- Produces: `Base` (DeclarativeBase), `TimestampMixin` (`created_at`, `updated_at`), `UserScopedMixin` (`user_id: Mapped[int]` FK → `users.id`, indexado), `SessionLocal`, `get_session() -> Iterator[Session]`, `models.User(id, email, created_at, updated_at)`. A migração `0001_base` cria a extensão `vector` e a tabela `users`, e insere o usuário padrão (`id=1`).

- [ ] **Step 1: Escrever os testes falhando** (usam o Postgres do Compose; marcar com `@pytest.mark.db`): `test_default_user_exists` (após `alembic upgrade head`, `session.get(User, 1)` não é `None`); `test_pgvector_roundtrip` (cria tabela temporária com `Vector(1024)`, grava `[0.1]*1024`, consulta por `l2_distance` e assert distância 0 para o mesmo vetor); `test_user_scoped_mixin_requires_user_id` (modelo de teste com `UserScopedMixin`: inserir sem `user_id` levanta `IntegrityError`).
- [ ] **Step 2: Rodar e ver falhar.** Run: `pytest tests/test_db.py -v` → Expected: FAIL.
- [ ] **Step 3: Implementar** `db.py` e `models.py` conforme Interfaces. `updated_at` usa `onupdate=func.now()`.
- [ ] **Step 4: Alembic.** Configurar `env.py` para ler `database_url` de `Settings` e usar `Base.metadata`. Escrever `0001_base.py` com `CREATE EXTENSION IF NOT EXISTS vector`, `users` e o insert do usuário 1.
- [ ] **Step 5: Verificar.** Run: `alembic upgrade head && pytest tests/test_db.py -v` → PASS. Run: `alembic downgrade base && alembic upgrade head` → sem erro.
- [ ] **Step 6: Commit** — `git add -A && git commit -m "feat: database base, alembic migration, pgvector"`.

---

### Task 3: API com /health

**Files:**
- Create: `api/__init__.py`, `api/main.py`
- Test: `tests/test_api.py`

**Interfaces:**
- Consumes: `get_session`, `get_settings` (Tasks 1–2).
- Produces: `api.main.app` (FastAPI); `GET /health` → `200 {"status":"ok","db":"up"}` ou `503 {"status":"degraded","db":"down"}`; dependência `current_user_id() -> int` (retorna `settings.default_user_id`; é o ponto único a trocar por autenticação no SaaS).

- [ ] **Step 1: Escrever os testes falhando** com `TestClient`: `test_health_ok` (DB disponível → 200 e `db == "up"`); `test_health_db_down` (substituir `get_session` por uma versão que levanta `OperationalError` → 503 e `db == "down"`); `test_current_user_id_default` (`current_user_id() == 1`).
- [ ] **Step 2: Rodar e ver falhar.** Run: `pytest tests/test_api.py -v` → Expected: FAIL.
- [ ] **Step 3: Implementar** o app. `/health` executa `SELECT 1`; captura `OperationalError` e responde 503 em vez de propagar (Review Focus 5).
- [ ] **Step 4: Verificar.** Run: `pytest tests/test_api.py -v` → PASS. Run: `uvicorn api.main:app` e `curl localhost:8000/health` → `{"status":"ok","db":"up"}`.
- [ ] **Step 5: Commit** — `git add -A && git commit -m "feat(api): health endpoint and current_user_id dependency"`.

---

### Task 4: Provedor de LLM (Groq) com controle de ritmo

**Files:**
- Create: `rumo_core/llm/__init__.py`, `base.py`, `ratelimit.py`, `groq.py`
- Test: `tests/llm/test_ratelimit.py`, `tests/llm/test_groq.py`

**Interfaces:**
- Consumes: `Settings.groq_*` (Task 1).
- Produces:
  - `llm.base.LLMProvider` (Protocol): `async def complete_json(self, messages: list[dict[str, str]], schema: type[T]) -> T` (`T` bound a `BaseModel`).
  - Erros: `LLMError`, `LLMInvalidResponse(LLMError)`, `LLMRateLimited(LLMError)`.
  - `llm.ratelimit.RateLimiter(max_per_minute: int, clock=time.monotonic, sleep=asyncio.sleep)` com `async acquire() -> None`.
  - `llm.groq.GroqProvider(settings, client: httpx.AsyncClient, limiter: RateLimiter)` implementando `LLMProvider`.

- [ ] **Step 1: Testes do limitador** (`clock` e `sleep` falsos): `test_allows_burst_up_to_limit` (3 chamadas com `max_per_minute=3` não dormem); `test_blocks_when_exceeded` (a 4ª dorme até a janela de 60 s liberar uma vaga); `test_window_slides` (após 60 s simulados, volta a permitir).
- [ ] **Step 2: Rodar e ver falhar.** Run: `pytest tests/llm/test_ratelimit.py -v` → FAIL. **Implementar** `RateLimiter` com janela deslizante (deque de timestamps). Run de novo → PASS.
- [ ] **Step 3: Testes do Groq** com `httpx.MockTransport` (nenhuma chamada real) e um `schema` de teste `Out(name: str, score: int)`:
  - `test_valid_json_returns_model` — resposta `{"choices":[{"message":{"content":"{\"name\":\"a\",\"score\":3}"}}]}` → `Out(name="a", score=3)`; request vai para `https://api.groq.com/openai/v1/chat/completions` com `Authorization: Bearer <key>` e `response_format={"type":"json_object"}`.
  - `test_accepts_markdown_fenced_json` — conteúdo ` ```json\n{...}\n``` ` é aceito (Review Focus 1).
  - `test_invalid_json_retries_once_then_raises` — duas respostas inválidas → `LLMInvalidResponse`; exatamente 2 requests.
  - `test_invalid_then_valid_succeeds` — inválida seguida de válida → modelo; 2 requests.
  - `test_429_respects_retry_after` — 429 com `Retry-After: 2` seguido de 200 → sucesso; `sleep` chamado com 2.
  - `test_429_without_retry_after_uses_backoff` — 429 sem header (e outro com `Retry-After: abc`) → espera crescente (1, 2, 4…), sem exceção de parsing (Review Focus 2).
  - `test_429_exhausted_raises` — 5 respostas 429 seguidas → `LLMRateLimited`.
- [ ] **Step 4: Rodar e ver falhar.** Run: `pytest tests/llm/test_groq.py -v` → FAIL.
- [ ] **Step 5: Implementar `GroqProvider.complete_json`.** Antes de cada request, `await limiter.acquire()`. Extrair o JSON removendo cercas de markdown; validar com `schema.model_validate_json`; em falha de validação, uma retentativa. 429: até 5 tentativas, espera = `Retry-After` se numérico, senão `2**n` segundos. `sleep` injetável para os testes.
- [ ] **Step 6: Verificar.** Run: `pytest tests/llm -v` → PASS.
- [ ] **Step 7: Commit** — `git add -A && git commit -m "feat(llm): groq provider with rate limiting and schema validation"`.

---

### Task 5: Provedor de embeddings (bge-m3 local) e benchmark

**Files:**
- Create: `rumo_core/embeddings/__init__.py`, `base.py`, `bge_m3.py`, `scripts/bench_embeddings.py`
- Test: `tests/embeddings/test_bge_m3.py`

**Interfaces:**
- Produces:
  - `embeddings.base.Embedder` (Protocol): `dimensions: int`, `model_version: str`, `def embed(self, texts: list[str]) -> list[list[float]]`.
  - `embeddings.bge_m3.BgeM3Embedder(model_name: str = "BAAI/bge-m3", max_tokens: int = 512)` com `dimensions == 1024` e `model_version == "bge-m3@1"`; carrega o modelo de forma preguiçosa na primeira chamada; vetores **normalizados** (norma 1).

- [ ] **Step 1: Escrever os testes** (marcar com `@pytest.mark.slow`; baixam o modelo na primeira execução): `test_dimensions_and_normalization` (vetor de 1024 floats, norma ≈ 1); `test_empty_list_returns_empty_list` e `test_empty_string_returns_valid_vector` (Review Focus 3); `test_long_text_is_truncated` (texto de 20 000 palavras retorna 1 vetor sem erro, Review Focus 4); `test_cross_language_similarity` (similaridade de "Engenheiro de software backend com Python" com "Backend software engineer using Python" **maior** que com "Chef de cozinha em restaurante italiano").
- [ ] **Step 2: Rodar e ver falhar.** Run: `pytest tests/embeddings -v -m slow` → FAIL.
- [ ] **Step 3: Implementar** `BgeM3Embedder` com `sentence_transformers.SentenceTransformer`, `normalize_embeddings=True` e `max_seq_length=max_tokens`. `embed([])` retorna `[]` sem carregar o modelo.
- [ ] **Step 4: Verificar.** Run: `pytest tests/embeddings -v -m slow` → PASS.
- [ ] **Step 5: Benchmark.** `scripts/bench_embeddings.py` vetoriza 200 textos de ~300 palavras e imprime textos/segundo e uso de memória. Run: `python scripts/bench_embeddings.py` → imprime os números; registrar o resultado em `docs/benchmarks.md` (responde ao risco de desempenho local da spec, seção 10).
- [ ] **Step 6: Commit** — `git add -A && git commit -m "feat(embeddings): local bge-m3 embedder and benchmark"`.

---

### Task 6: Esqueleto do worker

**Files:**
- Create: `worker/__init__.py`, `worker/main.py`
- Test: `tests/test_worker.py`

**Interfaces:**
- Produces: `worker.main.JobRegistry` com `register(name: str, fn: Callable[[], Awaitable[None]], every_seconds: int) -> None` e `async run_due(now: float) -> list[str]` (executa os jobs vencidos e devolve os nomes executados); `async main() -> None` (loop que chama `run_due` a cada segundo e registra o job `heartbeat`, que escreve uma linha de log). Um job que levanta exceção é registrado em log e **não derruba** os demais nem o loop.

- [ ] **Step 1: Escrever os testes falhando:** `test_runs_job_when_due` (job a cada 60 s: roda em t=0, não roda em t=30, roda em t=60); `test_failing_job_does_not_stop_others` (job A levanta; job B executa e aparece no retorno); `test_failing_job_is_retried_next_interval` (A volta a rodar no intervalo seguinte).
- [ ] **Step 2: Rodar e ver falhar.** Run: `pytest tests/test_worker.py -v` → FAIL.
- [ ] **Step 3: Implementar** `JobRegistry` guardando `next_run` por job; `run_due` captura exceções por job com `logging.exception`.
- [ ] **Step 4: Compose.** Adicionar serviços `api` e `worker` ao `docker-compose.yml` (mesma imagem Python, comandos `uvicorn api.main:app --host 0.0.0.0` e `python -m worker.main`, `depends_on: db: condition: service_healthy`).
- [ ] **Step 5: Verificar.** Run: `pytest tests/test_worker.py -v` → PASS. Run: `docker compose up -d && docker compose logs worker --tail 5` → mostra linhas de `heartbeat`.
- [ ] **Step 6: Commit** — `git add -A && git commit -m "feat(worker): job registry and heartbeat loop"`.

---

### Task 7: Esqueleto do web com tokens de design

**Files:**
- Create: `web/` (Next.js + TypeScript), `web/app/globals.css` (tokens), `web/app/layout.tsx`, `web/app/page.tsx` (página de status), `web/components/StatusRow.tsx`

**Interfaces:**
- Consumes: `GET {API_URL}/health` (Task 3).
- Produces: tokens CSS em `globals.css` (`--bg`, `--surface`, `--text`, `--muted`, `--border`, `--accent`, `--ok`, `--warn`, `--danger`, `--radius-field`, `--radius-card`, escala tipográfica de 6 tamanhos) com **modo claro e escuro** via `prefers-color-scheme` e `[data-theme]`; `StatusRow({label, state})`.

- [ ] **Step 1: Criar o app.** `create-next-app` com TypeScript, App Router, sem Tailwind obrigatório. Escolher **um par de fontes com personalidade** via `next/font` (uma sans humanista/grotesca para a interface e uma mono para números e rótulos técnicos) e **uma cor de acento** única; registrar a escolha e o motivo em um comentário no topo de `globals.css`.
- [ ] **Step 2: Tokens e layout.** Implementar os tokens e o tema claro/escuro; `layout.tsx` com `lang="pt-BR"`, fundo explícito e foco visível (`:focus-visible`). Bordas de 1px, raio pequeno e consistente, sem sombras grandes, sem gradientes.
- [ ] **Step 3: Página de status.** Lista densa com `StatusRow` para "API" e "Banco de dados" lendo `/health` no servidor; estados: carregando (esqueleto com a forma da linha), ok, erro (diz o que houve e como seguir). Texto em PT-BR, curto e específico, sem emoji.
- [ ] **Step 4: Teste de componente.** `StatusRow` renderiza o rótulo e o estado como texto (não apenas cor) para `ok`, `down` e `loading`. Run: `npm test` → PASS.
- [ ] **Step 5: Verificação visual** (spec 9.6): `npm run dev`, abrir no navegador e capturar a página em **375, 768 e 1280 px**, nos temas claro e escuro; conferir contra a lista 9.2 (sem gradiente roxo→azul, sem cartões iguais com ícone em círculo, contraste AA, sem rolagem horizontal). Corrigir antes de seguir.
- [ ] **Step 6: Commit** — `git add -A && git commit -m "feat(web): next.js skeleton with design tokens and status page"`.

---

## Auto-revisão

- **Cobertura da spec (marco 1):** Docker/Postgres (T1–2), esqueletos de api e worker (T3, T6), provedor `llm/` (T4), provedor `embeddings/` (T5), `user_id` em todas as tabelas via mixin (T2), validação Pydantic e retentativa (T4), 429 com fila (T4), versão do modelo de embeddings (T5), segredos fora do git (T1), tokens de frontend e verificação visual (T7), benchmark do bge-m3 (T5).
- **Fora deste plano (marcos seguintes):** tabelas de domínio além de `users`, conectores, matching, candidatura, kanban e relatórios.
- **Consistência de tipos:** `Settings` (T1) é consumido por T2–T4 com os mesmos nomes; `RateLimiter.acquire` (T4) é a única porta de ritmo usada pelo `GroqProvider`; `Embedder.model_version` fica pronto para a coluna de versão dos marcos seguintes.
- **Proporção:** o plano descreve decisões, assinaturas e testes; os corpos ficam para quem implementa.
