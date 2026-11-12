# Rumo — Marco 2b: Entrevista com IA — Plano de implementação

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Depois de confirmar o perfil, o usuário passa por uma entrevista de ~8–12 perguntas, **uma por vez**, guiada pelas lacunas do perfil; cada resposta vira uma **sugestão de alteração** (aceitar/ajustar/descartar) e, quando aceita, um novo perfil versionado com um chunk vetorizado da resposta.

**Architecture:** Lacunas são detectadas por **regras determinísticas** (sem LLM). Uma máquina de estados na sessão (`generating → working → awaiting_answer → done|error`) é movida pelo **worker** (Groq só no worker): a cada turno uma chamada ao LLM devolve `{sugestão|null, próxima_pergunta}` em JSON. A sugestão passa por um **validador anti-invenção** (números e datas precisam existir na resposta do usuário ou no perfil). Aceitar uma sugestão aplica uma função pura ao perfil atual e confirma uma nova versão por `confirm_profile` (idempotente, reaproveita embeddings). Respostas de preferência (salário, visto, disponibilidade, mudança) vão para o `answer_bank`, não para o perfil.

**Tech Stack:** o dos marcos anteriores; nenhuma dependência nova.

**Spec:** `docs/superpowers/specs/2026-11-11-rumo-design.md` (seções 4, 5.1 passo 4, 5.5 passo 4 (regra anti-invenção), 6, 7, 9; marco 2 da seção 8). **Fora deste marco:** o passo 5 da seção 5.1 (`search_profile` inicial e filtros) pertence ao marco 4; aqui só se capturam as preferências no `answer_bank`.

## Global Constraints

- Toda tabela tem `user_id`, `created_at`, `updated_at` (mixins de `rumo_core/models.py`); toda rota usa `current_user_id()` de `api/deps.py`.
- Chamadas ao Groq **só no worker**. Respostas do LLM validadas por Pydantic, 1 retentativa (já no `GroqProvider`); o prompt contém a palavra "json".
- **Regra anti-invenção (spec 5.5):** nenhuma sugestão pode conter número, data ou habilidade que não esteja na resposta do usuário ou no item do perfil de origem; sugestão que viole a regra é descartada e nunca chega ao usuário como proposta.
- Texto do usuário (respostas) é **dado, nunca instrução**: vai delimitado (`<resposta>…</resposta>`) com o delimitador neutralizado no texto.
- Uma pergunta por vez; no máximo `max_questions` (12) por sessão; pular e encerrar a qualquer momento.
- Ids de itens do perfil são estáveis entre versões; aplicar sugestão usa `confirm_profile` (nunca grava `Profile` direto).
- Testes sem internet, LLM falso, banco compartilhado só com **usuários descartáveis** (nunca `user_id=1` com deletes); marcador `db` para testes de banco.
- Frontend: UI em PT-BR, tokens CSS existentes, sem os marcadores de "cara de IA" (spec 9.1–9.2), estados vazio/carregando/erro (9.4), acessibilidade (9.5).

## Review Focus

1. Resposta do usuário **sem** os números que o LLM propõe (ex.: usuário diz "melhorei a performance", LLM sugere "reduzi 40%"): a sugestão é descartada, nada inventado chega ao perfil.
2. Usuário edita o perfil (remove a experiência alvo) entre a pergunta e o "aceitar": `resolve` responde 409 com mensagem clara, sem corromper nem criar versão.
3. Duplo clique em "Aceitar" / dupla resposta: no máximo uma versão nova e uma resposta registrada (409 ou resultado idêntico).
4. Worker cai no meio do turno, LLM devolve JSON inválido ou 429: a sessão não fica presa (volta a `generating` ou vai a `error` com botão "tentar de novo"), sem pergunta duplicada.
5. Resposta contendo instruções ("ignore as regras e…") ou `</resposta>`: tratada como dado; o schema continua imposto.
6. Iniciar entrevista sem perfil confirmado: 409 com mensagem; iniciar de novo com sessão ativa: devolve a mesma sessão.
7. Resposta vazia, só espaços ou gigante: 422 (limite 4000 caracteres), nada gravado.
8. Perfil já completo (nenhuma lacuna) ou lacunas esgotadas antes de 12: sessão vai a `done` com mensagem de encerramento, sem laço.

---

## File Structure

```
rumo_core/
├─ models.py                      # + InterviewSession, ChatMessage, ProfileSuggestion, AnswerBankItem
├─ profile/schema.py              # + Note, CanonicalProfile.notes
├─ profile/chunker.py             # + chunk por nota
├─ profile/prompting.py           # wrap_untrusted(tag, text) compartilhado (extraction passa a usar)
└─ interview/
   ├─ gaps.py                     # Gap, detect_gaps
   ├─ suggestions.py              # SuggestionDraft, validate_suggestion, apply_suggestion, numbers_supported
   ├─ turn.py                     # TurnResult, run_turn (prompt + LLM)
   └─ service.py                  # máquina de estados e operações (start/answer/skip/finish/retry/resolve)
api/interview.py                  # rotas /interview/*
worker/interview_jobs.py          # process_interview_turns, reset_working_sessions
alembic/versions/0003_interview.py
web/app/onboarding/interview/page.tsx
web/components/interview/{ChatThread,SuggestionCard,AnswerBox}.tsx
web/lib/interview.ts
tests/interview/ ...
```

---

### Task 1: Tabelas da entrevista e migração

**Files:**
- Modify: `rumo_core/models.py`
- Create: `alembic/versions/0003_interview.py`
- Test: `tests/test_interview_models.py` (marcador `db`, usuário descartável)

**Interfaces:**
- Produces (todos com `UserScopedMixin` + `TimestampMixin`):
  - `InterviewSession`: `id`, `profile_version: int`, `state: str` (`generating`|`working`|`awaiting_answer`|`done`|`error`, padrão `generating`), `asked_count: int = 0`, `max_questions: int = 12`, `handled_gaps: JSONB list[str] = []`, `current_gap_id: str | None`, `error: str | None`.
  - `ChatMessage`: `id`, `session_id` (FK `interview_sessions.id`, `ondelete=CASCADE`), `channel: str = "interview"` (`interview`|`filters` — o chat de filtros do marco 4 reutiliza a tabela), `role: str` (`assistant`|`user`), `kind: str` (`question`|`answer`|`skip`|`info`), `content: Text`, `gap_id: str | None`.
  - `ProfileSuggestion`: `id`, `session_id` (FK, CASCADE), `message_id` (FK `chat_messages.id`, a resposta de origem), `gap_id: str`, `op: str`, `payload: JSONB`, `status: str` (`pending`|`accepted`|`adjusted`|`rejected`, padrão `pending`), `applied_version: int | None`.
  - `AnswerBankItem` (`answer_bank`): `id`, `key: str(64)`, `value: Text`, `source_message_id: int | None`; `UniqueConstraint(user_id, key)`.
  - Índices: `chat_messages(session_id, id)`, `interview_sessions(user_id, state)`, `profile_suggestions(session_id, status)`.

- [ ] **Step 1: Write the failing tests** — `test_interview_tables_roundtrip` (insere sessão, duas mensagens, uma sugestão e um item do answer_bank para um usuário descartável e lê de volta, incluindo defaults `state="generating"`, `status="pending"`, `channel="interview"`); `test_answer_bank_unique_per_user_key` (segunda linha com mesmo `(user_id,key)` → `IntegrityError`; outro usuário com a mesma chave é aceito); `test_cascade_delete_session_removes_messages_and_suggestions`.
- [ ] **Step 2: Run** `.venv/Scripts/python -m pytest tests/test_interview_models.py -v` — Expected: FAIL.
- [ ] **Step 3: Implement** os modelos e a migração `0003` (down_revision `"0002"`, `downgrade` derruba as quatro tabelas em ordem de FK).
- [ ] **Step 4: Run** o mesmo comando e `-m "not slow" -q` — Expected: PASS.
- [ ] **Step 5: Commit** `feat(interview): tables and migration 0003`.

---

### Task 2: Detecção de lacunas do perfil

**Files:**
- Create: `rumo_core/interview/__init__.py`, `rumo_core/interview/gaps.py`
- Modify: `pyproject.toml` (`packages` inclui `rumo_core.interview`)
- Test: `tests/interview/test_gaps.py`

**Interfaces:**
- Consumes: `CanonicalProfile` (ids já preenchidos).
- Produces:
  - `@dataclass(frozen=True) Gap: id: str; kind: str; target_ref: str | None; hint: str; priority: int` (`id = f"{kind}:{target_ref or '-'}"`; `hint` é texto em PT-BR curto para o prompt, ex.: "a experiência 'Dev @ Acme' não tem nenhuma conquista com número").
  - `PREFERENCE_KEYS = ("salary_expectation", "work_authorization", "availability", "relocation")`.
  - `detect_gaps(profile: CanonicalProfile, answered_preferences: frozenset[str] = frozenset()) -> list[Gap]`: ordenadas por `priority` (menor primeiro) e depois `id`; regras: `achievement_without_number` (experiência cujas conquistas existem mas nenhuma contém dígito, ou que não tem conquistas e tem descrição), `experience_without_description` (sem descrição e sem conquistas), `experience_missing_dates` (sem `start`), `missing_summary`, `missing_skills`, `missing_target_role` (sem `basics.headline`), e uma lacuna `pref_<chave>` para cada chave de `PREFERENCE_KEYS` ainda não respondida. Prioridade: conquistas/descrição das experiências mais recentes primeiro (ordem da lista), depois resumo/cargo-alvo/habilidades, por fim preferências.

- [ ] **Step 1: Write the failing tests** — `test_empty_profile_has_summary_skills_headline_and_preference_gaps`; `test_experience_with_numbered_achievement_has_no_achievement_gap` ("Reduzi o custo em 30%"); `test_experience_without_numbers_gets_gap_with_target_ref`; `test_experience_without_description_or_achievements`; `test_answered_preferences_suppressed`; `test_order_is_stable_and_deterministic` (duas chamadas iguais; ordem recente-primeiro); `test_complete_profile_only_preference_gaps_remain`; `test_gap_ids_unique`.
- [ ] **Step 2: Run** `pytest tests/interview/test_gaps.py -v` — Expected: FAIL.
- [ ] **Step 3: Implement** `Gap`, `PREFERENCE_KEYS` e `detect_gaps` em Python puro (regex `\d` para "tem número").
- [ ] **Step 4: Run** — Expected: PASS.
- [ ] **Step 5: Commit** `feat(interview): deterministic profile gap detection`.

---

### Task 3: Sugestões — schema, validador anti-invenção e aplicação ao perfil (+ notas no perfil)

**Files:**
- Modify: `rumo_core/profile/schema.py` (modelo `Note`, campo `notes`), `rumo_core/profile/chunker.py` (chunk por nota), `web/lib/profile.ts` e `web/components/onboarding/ReviewStep.tsx` (tipo `notes?` preservado sem edição, como certificações/projetos)
- Create: `rumo_core/interview/suggestions.py`
- Test: `tests/interview/test_suggestions.py`, ajustes em `tests/profile/test_chunker.py`, `tests/profile/test_service.py`, teste web de round-trip de `notes`

**Interfaces:**
- Produces (schema): `Note(BaseModel)`: `id: str = ""`, `topic: str = ""`, `text: str = ""`; `CanonicalProfile.notes: list[Note] = []`. O chunker emite um chunk `kind="note"` por nota não vazia (`source_ref = note.id`); `assign_ids`/`blank_ids`/`_fill_empty_ids` já tratam toda lista de itens com `id` (cobrir `notes` por teste).
- Produces (`suggestions.py`):
  - `SuggestionOp = Literal["append_achievement","set_experience_description","set_experience_dates","set_summary","add_skill","set_preference","add_note"]`.
  - `SuggestionDraft(BaseModel)`: `op: SuggestionOp`, `target_ref: str | None = None` (id da experiência quando a op exige), `text: str = ""`, `start: str = ""`, `end: str = ""`, `name: str = ""` (habilidade), `level: str | None = None`, `key: str = ""`, `value: str = ""` (preferência).
  - `numbers_supported(text: str, sources: list[str]) -> bool`: todo token numérico de `text` (sequências de dígitos, separadores `.`/`,` normalizados, anos incluídos) aparece em alguma fonte; sem números → `True`.
  - `validate_suggestion(draft: SuggestionDraft, *, answer: str, profile: CanonicalProfile, gap: Gap) -> str | None`: devolve `None` se válida, senão o **motivo** (PT-BR curto): op desconhecida para a lacuna, `target_ref` inexistente no perfil, `key` fora de `PREFERENCE_KEYS`, texto vazio, ou número/data não suportado (fontes: `answer` + texto do item alvo); habilidade nova precisa aparecer (case-insensitive) em `answer`.
  - `class SuggestionTargetMissing(Exception)`.
  - `apply_suggestion(profile: CanonicalProfile, draft: SuggestionDraft, *, answer: str, gap: Gap) -> CanonicalProfile`: função pura (cópia profunda); aplica a op nas listas pelo `id`, **anexa** uma `Note(topic=gap.kind, text=answer)` (exceto `set_preference`, que não altera o perfil e devolve o perfil igual); `SuggestionTargetMissing` se o alvo não existe.

- [ ] **Step 1: Write the failing tests** — validador: `test_numbers_must_come_from_answer` (resposta "melhorei a performance", sugestão "reduzi 40%" → motivo não nulo), `test_numbers_from_answer_or_target_item_accepted` ("30%" na resposta; ano presente no item alvo), `test_thousand_separators_normalized` ("1.000" vs "1000"), `test_unknown_target_rejected`, `test_preference_key_whitelist`, `test_new_skill_must_appear_in_answer`; aplicação: `test_append_achievement_adds_to_target_and_appends_note`, `test_set_experience_dates`, `test_set_summary`, `test_target_missing_raises`, `test_set_preference_does_not_change_profile`, `test_apply_is_pure` (entrada não mutada); chunker/serviço: `test_notes_become_chunks_with_stable_source_ref`, `test_confirm_with_notes_idempotent_and_reuses_embeddings`; web: `ReviewStep` devolve `notes` intactas.
- [ ] **Step 2: Run** `pytest tests/interview/test_suggestions.py tests/profile -v` e `cd web && npm test` — Expected: FAIL.
- [ ] **Step 3: Implement** conforme as assinaturas; `numbers_supported` por regex de dígitos com normalização de separadores de milhar/decimal; `apply_suggestion` não valida a regra anti-invenção (quem chama valida antes).
- [ ] **Step 4: Run** os mesmos comandos e `pytest -m "not slow" -q` — Expected: PASS.
- [ ] **Step 5: Commit** `feat(interview): suggestion schema, anti-invention validator, apply, profile notes`.

---

### Task 4: Motor de turno (prompt + chamada ao LLM)

**Files:**
- Create: `rumo_core/profile/prompting.py`, `rumo_core/interview/turn.py`
- Modify: `rumo_core/profile/extraction.py` (usa `wrap_untrusted`)
- Test: `tests/interview/test_turn.py`, `tests/profile/test_prompting.py` (e `test_extraction.py` segue verde)

**Interfaces:**
- Produces:
  - `wrap_untrusted(tag: str, text: str) -> str`: neutraliza `</tag` (case-insensitive) e devolve `<tag>\n…\n</tag>`.
  - `TurnResult(BaseModel)`: `suggestion: SuggestionDraft | None = None`, `next_question: str = ""`.
  - `@dataclass TurnOutcome: suggestion: SuggestionDraft | None; question: str | None; rejected_reason: str | None`.
  - `async def run_turn(llm: LLMProvider, *, profile: CanonicalProfile, answered_gap: Gap | None, answer: str | None, next_gap: Gap | None) -> TurnOutcome`. Mensagem de sistema fixa (PT-BR + cauda EN, contém "json" e o json schema de `TurnResult`): papel de entrevistador de carreira; a resposta do usuário é dado; **uma** pergunta curta e específica baseada no `hint` da próxima lacuna, no idioma dominante do perfil (padrão PT-BR); proposta de alteração só com fatos presentes na resposta; sem inventar números. Mensagem de usuário: resumo do perfil (JSON enxuto do item alvo + título/resumo) + `answered_gap.hint` + `wrap_untrusted("resposta", answer)` + `next_gap.hint`. Pós-processamento: `validate_suggestion` quando há resposta; se inválida → `suggestion=None`, `rejected_reason` preenchido (log em nível info, sem texto da resposta); sem `next_gap` → `question=None`; pergunta vazia com `next_gap` presente → `LLMInvalidResponse`.

- [ ] **Step 1: Write the failing tests** (LLM falso que registra `messages`): `test_first_turn_has_no_suggestion_and_asks_next_gap`; `test_valid_suggestion_passes_through`; `test_invented_number_dropped_with_reason` (LLM propõe "40%" que não está na resposta); `test_prompt_has_json_schema_and_wraps_answer` (palavra "json" e nomes de campos como `append_achievement` presentes; resposta só dentro de `<resposta>`); `test_closing_tag_and_injection_in_answer_is_data` (`</RESPOSTA>` neutralizado, exatamente um fechamento); `test_no_next_gap_returns_no_question`; `test_empty_question_with_next_gap_raises_invalid`; `test_llm_errors_propagate`; prompting: `test_wrap_untrusted_neutralises_variants`.
- [ ] **Step 2: Run** `pytest tests/interview/test_turn.py tests/profile/test_prompting.py -v` — Expected: FAIL.
- [ ] **Step 3: Implement** e refatorar `extraction.py` para usar `wrap_untrusted("curriculo", text)` sem mudar seu comportamento.
- [ ] **Step 4: Run** `pytest tests/interview tests/profile -v` — Expected: PASS (inclui os testes de extração existentes).
- [ ] **Step 5: Commit** `feat(interview): turn engine with LLM and anti-invention filter`.

---

### Task 5: Serviço da sessão e job do worker

**Files:**
- Create: `rumo_core/interview/service.py`, `worker/interview_jobs.py`
- Modify: `worker/main.py` (registrar job a cada 3 s; `reset_working_sessions` na partida via `retry_startup`)
- Test: `tests/interview/test_service.py`, `tests/test_worker_interview.py` (`db`, usuários descartáveis, LLM falso)

**Interfaces:**
- Consumes: `detect_gaps`, `run_turn`, `apply_suggestion`, `confirm_profile`, `get_current_profile`, modelos da Task 1.
- Produces (`service.py`, todas recebem `session: Session, user_id: int`):
  - `class InterviewError(Exception)` com subclasses `NoProfile`, `WrongState`, `NotFound`, `TargetMissing`, cada uma com `message` PT-BR.
  - `start_interview(session, user_id) -> InterviewSession`: exige perfil atual (`NoProfile`); se há sessão ativa (`state` ≠ `done`) para o usuário, devolve a mesma (idempotente); senão cria com `profile_version` atual, `state="generating"`.
  - `submit_answer(session, user_id, session_id, text: str) -> None`: exige `state="awaiting_answer"` (`WrongState`), `text.strip()` não vazio e ≤ 4000 (`ValueError`); grava `ChatMessage(role="user", kind="answer", gap_id=current_gap_id)` e `state="generating"`.
  - `skip_question(session, user_id, session_id) -> None`: grava `kind="skip"`, marca a lacuna em `handled_gaps`, `state="generating"`.
  - `finish_interview(session, user_id, session_id) -> None`: `state="done"` (qualquer estado ativo).
  - `retry_interview(session, user_id, session_id) -> None`: só de `error` → `generating`, limpa `error`.
  - `resolve_suggestion(session, user_id, suggestion_id, action: Literal["accept","adjust","reject"], payload: SuggestionDraft | None) -> ProfileSuggestion`: só de `pending` (`WrongState` se já resolvida — idempotência de duplo clique: resolver de novo devolve 409); `reject` marca `rejected`; `accept`/`adjust` (adjust usa `payload` editado, revalidado com `validate_suggestion` contra a resposta de origem) aplica em cima do **perfil atual mais recente** com `apply_suggestion` (`TargetMissing` se o alvo sumiu) e `confirm_profile(..., narrative=latest.narrative)`; `set_preference` faz upsert no `answer_bank` (`key`, `value`, `source_message_id`) sem criar versão; grava `applied_version` e `status` (`accepted`/`adjusted`).
- Produces (`worker/interview_jobs.py`):
  - `reset_working_sessions(session_factory, *, user_id: int | None = None) -> int` (`working` → `generating`; docstring: assume um único worker).
  - `async def process_interview_turns(session_factory, llm: LLMProvider | None, *, user_id: int | None = None, clock=time.monotonic) -> int`: reivindica **uma** sessão `generating` com `FOR UPDATE SKIP LOCKED`, marca `working` e faz commit **antes** de chamar o LLM; calcula `answered_gap`/`answer` (última mensagem `answer` sem sugestão registrada) e `next_gap` (primeira de `detect_gaps(perfil atual, preferências já respondidas)` fora de `handled_gaps`); se `asked_count >= max_questions` ou não há `next_gap` → grava mensagem `info` de encerramento e `state="done"`; senão `run_turn`, grava `ProfileSuggestion(pending)` (se houver), `ChatMessage(question)`, incrementa `asked_count`, `current_gap_id=next_gap.id`, adiciona a lacuna respondida a `handled_gaps` e `state="awaiting_answer"` — tudo numa transação. `llm is None` (sem chave) → não faz nada e avisa uma vez. `LLMRateLimited` → volta a `generating` e pausa o job 60 s (relógio injetável, um aviso por pausa); `LLMError`/`LLMInvalidResponse` → `state="error"` com mensagem PT-BR curta; exceção inesperada → `state="error"` com mensagem genérica, traceback uma vez; `CancelledError` devolve a `generating` e propaga.

- [ ] **Step 1: Write the failing tests** — serviço: `test_start_requires_confirmed_profile`, `test_start_twice_returns_same_active_session`, `test_submit_answer_wrong_state_409`, `test_submit_empty_or_huge_answer_rejected`, `test_skip_marks_gap_handled`, `test_resolve_accept_creates_new_version_with_note_and_reuses_embeddings`, `test_resolve_twice_is_wrong_state_and_one_version` (duplo clique), `test_resolve_target_removed_raises_target_missing_no_version`, `test_resolve_adjust_revalidated_against_answer` (ajuste com número inventado é recusado), `test_resolve_preference_goes_to_answer_bank_no_version`, `test_resolve_applies_to_latest_profile_not_session_version`, `test_retry_only_from_error`; worker: `test_first_turn_creates_question_and_awaiting_state`, `test_answer_turn_creates_suggestion_and_next_question`, `test_invented_number_suggestion_not_stored`, `test_no_gaps_left_ends_with_done_and_info_message`, `test_max_questions_ends_session`, `test_claim_skips_row_locked_by_another_session` (segunda sessão segura `FOR UPDATE`), `test_invalid_llm_json_sets_error_and_retry_resumes_without_duplicate_question`, `test_rate_limited_returns_to_generating_and_pauses`, `test_no_llm_key_leaves_generating_and_warns_once`, `test_startup_reset_working_to_generating`, `test_user_scope_filter`.
- [ ] **Step 2: Run** `pytest tests/interview/test_service.py tests/test_worker_interview.py -v` — Expected: FAIL.
- [ ] **Step 3: Implement** conforme as assinaturas; limpar estado de módulo entre testes com fixture autouse (como em `tests/test_worker_jobs.py`).
- [ ] **Step 4: Run** `pytest -m "not slow" -q` — Expected: PASS.
- [ ] **Step 5: Commit** `feat(interview): session service and worker turn job`.

---

### Task 6: Rotas da API da entrevista

**Files:**
- Create: `api/interview.py`
- Modify: `api/main.py` (incluir o router no topo, sem `noqa`)
- Test: `tests/test_api_interview.py` (`db`, `TestClient`, usuários descartáveis)

**Interfaces:**
- Consumes: `rumo_core.interview.service` (erros mapeados: `NoProfile`/`WrongState`/`TargetMissing` → 409, `NotFound` → 404, `ValueError` de texto → 422 com mensagem PT-BR).
- Produces (todas escopadas por `current_user_id`):
  - `POST /interview` → `201 {id, state}` (ou `200` com a sessão ativa existente).
  - `GET /interview/current` → `{id, state, error, progress: {asked, max}, messages: [{id, role, kind, content, gap_id}], suggestions: [{id, message_id, gap_id, op, payload, status, applied_version, before: str, after: str}]}` ou `404`; `before/after` são textos legíveis para a tela (valor atual do campo alvo e o proposto).
  - `POST /interview/{id}/answer` body `{text}` → `202 {state}`.
  - `POST /interview/{id}/skip`, `/finish`, `/retry` → `200 {state}`.
  - `POST /interview/suggestions/{id}/resolve` body `{action, payload?}` → `{status, applied_version}`.

- [ ] **Step 1: Write the failing tests** — `test_start_without_profile_409`, `test_start_then_get_current_shape`, `test_get_current_404_when_none`, `test_answer_wrong_state_409_and_empty_422_and_too_long_422`, `test_cross_user_session_and_suggestion_404`, `test_resolve_accept_returns_version_and_get_profile_reflects_it`, `test_resolve_double_post_second_409`, `test_resolve_target_missing_409_message`, `test_before_after_rendered_for_each_op`, `test_operational_error_503_shape` (rota não-health), `test_import_api_interview_first_in_fresh_subprocess` (sem circularidade).
- [ ] **Step 2: Run** `pytest tests/test_api_interview.py -v` — Expected: FAIL.
- [ ] **Step 3: Implement** o router com modelos de resposta Pydantic; nenhuma regra de negócio na rota.
- [ ] **Step 4: Run** `pytest -m "not slow" -q` — Expected: PASS.
- [ ] **Step 5: Commit** `feat(api): interview routes`.

---

### Task 7: Tela da entrevista no frontend

**Files:**
- Create: `web/lib/interview.ts`, `web/app/onboarding/interview/page.tsx`, `web/components/interview/ChatThread.tsx`, `web/components/interview/SuggestionCard.tsx`, `web/components/interview/AnswerBox.tsx` e os testes `*.test.tsx`/`*.test.ts` ao lado
- Modify: `web/components/onboarding/VectorStatus.tsx` ou `web/app/onboarding/page.tsx` (link "Continuar para a entrevista" ao confirmar o perfil), `web/app/globals.css` (estilos com tokens existentes)

**Interfaces:**
- Consumes: rotas da Task 6; cliente HTTP/erros de `web/lib/profile.ts` (reusar o helper de base URL e de mensagens de erro).
- Produces: `startInterview()`, `getInterview()` (404 → `null`), `answerInterview(id, text)`, `skipQuestion(id)`, `finishInterview(id)`, `retryInterview(id)`, `resolveSuggestion(id, action, payload?)`; componentes `ChatThread({messages, suggestions, onResolve})`, `SuggestionCard({suggestion, onResolve})` (mostra antes/depois; "Aceitar", "Ajustar" com edição inline do texto, "Descartar"; botões desabilitam após o primeiro clique), `AnswerBox({disabled, onSubmit, onSkip})` (contador até 4000, Enter+Ctrl envia, "Pular", rótulo associado).
- Comportamento: abre `POST /interview`; faz polling a cada 2 s enquanto `state` ∈ {`generating`,`working`} (para em `awaiting_answer`/`done`/`error`; limpa timers ao desmontar); `awaiting_answer` mostra a pergunta e a caixa; `error` mostra a mensagem e "Tentar de novo"; `done` mostra o encerramento e link para "ver perfil"; após 30 s em `generating`, aviso de que o worker pode não estar rodando/falta `GROQ_API_KEY` (mesmo padrão do onboarding); progresso "pergunta N de ~12" e botão "Encerrar entrevista"; 409 de `resolve` mostra "O item mudou; descarte esta sugestão".

- [ ] **Step 1: Write the failing tests** — `lib/interview.ts` (404 → null, mapeamento de erros 409/422); `SuggestionCard` (aceitar chama `onResolve("accept")` uma vez mesmo com duplo clique; ajustar envia o texto editado; descartar; mostra antes/depois); `AnswerBox` (vazio desabilitado, contador, Pular, Ctrl+Enter); página: polling para em `awaiting_answer` e em `done`, timers limpos ao desmontar (contagem de chamadas estável), erro com "Tentar de novo", aviso após 30 s (timers falsos), 409 do resolve exibido, estados vazio/carregando.
- [ ] **Step 2: Run** `cd web && npm test` — Expected: FAIL.
- [ ] **Step 3: Implement** seguindo spec 9.1–9.5 (tokens existentes, mensagens específicas, foco visível, `aria-live` na pergunta nova, sem decoração genérica); mover o foco para a nova pergunta/caixa de resposta quando ela aparece.
- [ ] **Step 4: Run** `cd web && npm test && npm run lint && npm run build` — Expected: PASS. Verificar visualmente em 3 larguras (375, 768, desktop) e claro/escuro com o navegador de preview (usar um mock local descartável da API se a real não estiver acessível; dizer qual foi usado).
- [ ] **Step 5: Commit** `feat(web): interview screen`.

---

## Verificação ponta a ponta (fim do marco)

Com `docker compose up -d --build`, chave do Groq em `.env` e um perfil já confirmado: abrir `/onboarding/interview`, responder 2–3 perguntas, aceitar uma sugestão e conferir que `GET /profile` mostra nova versão com a nota vetorizada (`chunks_embedded == chunks_total`). Usa a chave real; feito pelo usuário ou com a aprovação dele.
