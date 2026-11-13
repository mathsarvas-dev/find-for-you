# Rumo — Redesign da casca, navegação e trilha — Plano de implementação

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Uma casca única (barra lateral + barra superior com menu do avatar), um painel Início com a trilha "Seu caminho" (linha do tempo com bolinhas), steppers horizontais nos fluxos, botões de ação em todas as telas e uma página Configurações.

**Architecture:** `GET /overview` (somente leitura) alimenta `computeSteps` (função pura) e o componente `Stepper`; `AppShell` (lateral + topo) envolve todas as páginas em `app/layout.tsx`; as páginas existentes só ganham `PageHeader` + stepper, sem trocar conteúdo.

**Tech Stack:** FastAPI/SQLAlchemy (um endpoint), Next.js 16 App Router + vitest/RTL, CSS em `web/app/globals.css` (tokens existentes), ícones SVG inline.

**Spec:** `docs/superpowers/specs/2026-11-13-rumo-redesign-shell-design.md` (autoridade).

## Global Constraints
- Sem dependência nova de UI; ícones SVG inline; tokens de cor/espaço/tipografia de `globals.css` (claro e escuro); texto em PT-BR, específico e curto, verbo no infinitivo nos botões; sem dados falsos óbvios.
- Sem `Date.now()`/`Math.random()`/acesso a `window` durante o render (pré-render seguro): larguras/horas lidas em efeito.
- Acessibilidade: `aria-current="page"` na navegação, `aria-current="step"` no passo atual, estado nunca só por cor (ícone + texto oculto), foco visível, gaveta com foco preso/Escape/`aria-modal`, menu do avatar por teclado (Escape/clique fora), alvos ≥ 40 px no celular.
- Texto vindo de API/fonte sempre como texto (nunca HTML); links só http/https para URLs externas.
- `/overview`: escopado por `user_id`, sem texto de vagas/justificativas, campos com padrão seguro (uma parte falhando não derruba as outras).
- Conteúdo e testes existentes das telas continuam verdes; `web/AGENTS.md`: ler `node_modules/next/dist/docs/` antes de escrever código Next.
- Ordem de execução: **depois** do plano da coleta automática (ambos tocam `/sources` e `/settings` hospeda o `DiscoveryPanel` dele).

## Review Focus
1. A trilha nunca contradiz o servidor: cada passo vem de `overview`, e o passo atual é sempre o primeiro não concluído obrigatório (Task 2, 4).
2. Erro de rede/API fora do ar em qualquer página: casca continua navegável, Início mostra "Tentar de novo", nada de página em branco (Task 3, 4).
3. Navegação por teclado e leitor de tela: gaveta, menu do avatar, `aria-current`, ordem de foco (Task 3).
4. Nome/e-mail hostil no perfil (`<img onerror>`, 5000 caracteres, vazio) aparece só como texto, truncado (Task 3, 5).
5. 375 px sem rolagem horizontal; lateral não cobre o conteúdo no desktop; claro/escuro (Task 3–6).

---

### Task 1: API `GET /overview`

**Files:**
- Create: `api/overview.py`, `tests/test_api_overview.py`
- Modify: `api/main.py` (router)

**Interfaces:**
- Consumes: `get_current_profile`, `ProfileChunk`/status helpers de `api/profile.py` (reutilizar a lógica de "vetorizado" existente), `InterviewSession`, `Company`, `Job`, `Match`, `current_user_id`, `get_session`.
- Produces: `GET /overview` → `{profile: {confirmed: bool, version: int|null, vectorized: bool, name: str|null, email: str|null}, interview: {state: "none"|"in_progress"|"done"}, companies: {total: int, active: int, auto: int}, jobs: {open: int}, matches: {scored: int, pending: int}}`. `name`/`email` de `Basics` do perfil atual (≤ 200 caracteres cada, aparados, vazio → null); `interview.state`: `none` sem sessão, `done` se a sessão mais recente do usuário na versão atual está concluída, senão `in_progress` (ler os estados reais em `rumo_core/models.py`); `companies.auto` conta `origin='auto'` **se a coluna existir**, senão 0 (o plano da coleta automática a adiciona; use `getattr`/introspecção segura, sem quebrar antes dela); `matches` conta só a versão atual do perfil (`ok` com `content_hash` igual ao da vaga = `scored`; `pendente` = `pending`).

- [ ] **Step 1: Write failing tests** (`db`, TestClient, usuário descartável): `test_overview_empty_user_defaults`, `test_profile_confirmed_and_vectorized_flags`, `test_name_email_trimmed_capped_and_nullable`, `test_companies_and_jobs_counts_scoped_by_user`, `test_matches_scored_pending_current_version_only`, `test_interview_state_mapping`, `test_overview_never_500_when_part_missing` (perfil sem chunks, sem sessões), `test_overview_has_no_job_text` (corpo contém só os campos do contrato).
- [ ] **Step 2: Run** `pytest tests/test_api_overview.py -v` → FAIL.
- [ ] **Step 3: Implement** o router (um conjunto pequeno de `count` por seção; cada seção protegida para devolver o padrão seguro em caso de erro esperado de dados).
- [ ] **Step 4: Run** `pytest tests/test_api_overview.py tests/test_api_cors.py -v` → PASS; depois `pytest -m "not slow"`.
- [ ] **Step 5: Commit** `feat(api): overview endpoint for the journey`.

---

### Task 2: Cliente, `computeSteps` e `Stepper`

**Files:**
- Create: `web/lib/overview.ts`, `web/lib/overview.test.ts`, `web/components/stepper/computeSteps.ts`, `web/components/stepper/computeSteps.test.ts`, `web/components/stepper/Stepper.tsx`, `web/components/stepper/Stepper.test.tsx`
- Modify: `web/app/globals.css` (estilos `.stepper*`)

**Interfaces:**
- Produces: `type Overview` (espelha o contrato da Task 1), `getOverview(): Promise<Overview>` (usa `request`/`ApiError` de `web/lib/profile.ts`); `type StepState = "done"|"current"|"upcoming"|"blocked"|"optional"`; `type Step = {id: string, title: string, detail: string, state: StepState, cta?: {label: string, href: string}, reviewHref?: string, blockedReason?: string}`; `computeSteps(o: Overview): Step[]` com os 7 passos da spec §3 (ids `profile`, `confirm`, `interview`, `sources`, `jobs`, `evaluate`, `apply`); o passo atual = primeiro obrigatório não concluído (a entrevista é `optional` e nunca é o atual se o seguinte obrigatório está pronto para agir); `apply` sempre `blocked` com "em breve"; textos de `detail` com os números reais; `Stepper({steps, orientation: "vertical"|"horizontal", compactLabel?})` renderiza `<ol>` com bolinhas e linha conectora (`aria-hidden`), `aria-current="step"` no atual, texto oculto por estado (`concluído`, `passo atual`, `opcional`, `bloqueado`), botão/link do `cta` só no atual, "Revisar" nos concluídos, e na variante horizontal < 560 px o texto compacto "Passo N de M · título" (decidido por CSS/`matchMedia` em efeito, nunca no render).

- [ ] **Step 1: Write failing tests**: `computeSteps` — `test_fresh_user_current_is_profile`, `test_profile_confirmed_not_vectorized_current_is_confirm`, `test_interview_optional_not_blocking_sources`, `test_no_companies_current_is_sources_with_cta`, `test_companies_but_no_jobs_waits_collection`, `test_jobs_without_scores_current_is_evaluate`, `test_all_done_apply_blocked`, `test_details_show_real_numbers`; `Stepper` — ordem e semântica `<ol>`, um único `aria-current="step"`, texto oculto de estado, CTA só no atual, vertical vs horizontal, hostil `<img onerror>` em `detail` aparece literal; `overview.ts` — erro com `detail`, tipos.
- [ ] **Step 2: Run** `npm test` (em `web/`) → FAIL.
- [ ] **Step 3: Implement** os módulos e o CSS (linha conectora por pseudo-elemento, bolinha 18 px com ✓/número, atual com anel, esmaecido para futuros; claro e escuro).
- [ ] **Step 4: Run** `npx vitest run --maxWorkers=4` e `npm run lint` → PASS.
- [ ] **Step 5: Commit** `feat(web): overview client and journey stepper`.

---

### Task 3: Casca — barra lateral, topo, menu do avatar, gaveta

**Files:**
- Create: `web/components/shell/{AppShell,Sidebar,NavItem,Topbar,UserMenu,PageHeader,icons}.tsx` com testes `*.test.tsx`
- Modify: `web/app/layout.tsx`, `web/app/globals.css`

**Interfaces:**
- Consumes: `getOverview`, `Overview`.
- Produces: `AppShell({children})` (client component; busca `getOverview` em efeito, falha silenciosa para a casca); `NAV_ITEMS` com `Início` (/), `Perfil` (/onboarding), `Entrevista` (/onboarding/interview), `Fontes` (/sources), `Vagas` (/jobs), e `Configurações` (/settings) após uma linha separadora; ativo por `usePathname` com `aria-current="page"` (`/onboarding` ativo só em `/onboarding` exato; `/onboarding/interview` ativa `Entrevista`); indicador de pendência por item derivado do overview (Perfil: não confirmado; Fontes: sem empresas; Vagas: nada avaliado) com texto acessível; rodapé "Sistema" com ponto (ok/problema) que leva a `/settings#sistema`; `Topbar` com botão de menu (< 860 px), título da página e breadcrumb via `PageHeader`/contexto de título (cada página define o título; fallback do `metadata`), `UserMenu` (botão com iniciais, nome/e-mail do overview truncados e como texto; sem perfil: "Você" e "Perfil não confirmado"; itens `Meu perfil` → /onboarding e `Configurações` → /settings; Escape/clique fora fecham; setas/Tab navegáveis); gaveta < 860 px com overlay, foco preso, Escape e `aria-modal`; `PageHeader({title, description?, actions?, stepper?})`.
- Substitui a `header.topbar` atual do layout raiz (remover os estilos `.topbar*` não usados).

- [ ] **Step 1: Write failing tests**: nav ativa por rota (incl. `/onboarding/interview`), indicador de pendência com texto acessível, gaveta (abre/foca/Escape/fecha e devolve foco), `UserMenu` (abrir/fechar por teclado e clique fora; nome hostil como texto e truncado; fallback "Você"), `AppShell` com API fora do ar (casca navegável, sem erro), `PageHeader` (ações), rodapé "Sistema" com estado.
- [ ] **Step 2: Run** `npm test` → FAIL.
- [ ] **Step 3: Implement**; ler `node_modules/next/dist/docs/` (layouts, `usePathname`, client components) antes. A casca não pode causar mismatch de hidratação.
- [ ] **Step 4: Verificação** `npx vitest run --maxWorkers=4`, `npm run lint`, `npm run build`; conferência visual em 375, 768 e desktop, claro e escuro, com mock descartável da API (não a real).
- [ ] **Step 5: Commit** `feat(web): app shell with sidebar, topbar and user menu`.

---

### Task 4: Página Início com a trilha

**Files:**
- Create: `web/components/home/{SummaryCards,JourneyPanel}.tsx` com testes
- Modify: `web/app/page.tsx` (substitui o painel de estado), `web/app/globals.css`

**Interfaces:**
- Consumes: `getOverview`, `computeSteps`, `Stepper`, `PageHeader`.
- Produces: Início = `PageHeader` com o CTA do passo atual; coluna principal `Stepper` vertical "Seu caminho"; cartões clicáveis Perfil (versão/estado), Fontes (ativas/automáticas), Vagas (abertas), Avaliadas (`scored` de `open`); estados: carregando (esqueleto), erro de rede com "Tentar de novo" e dica específica, tudo concluído (CTA "Ver vagas avaliadas"); saudação calculada em efeito (sem `Date` no render); o `HealthPanel` e `checkHealth` saem daqui (vão para Configurações na Task 5, sem perder testes).

- [ ] **Step 1: Write failing tests**: renderiza trilha com dados mockados, CTA do passo atual leva à rota certa, cartões clicáveis com números, erro com "Tentar de novo" refaz a busca, esqueleto no carregamento, nenhum `Date.now()` no render (teste de pré-render por `renderToString`), polling **não** existe (uma busca por visita + botão).
- [ ] **Step 2: Run** `npm test` → FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Verificação** testes, lint, build (`/` ainda pré-renderiza), conferência visual nas 3 larguras e dois temas.
- [ ] **Step 5: Commit** `feat(web): home dashboard with journey timeline`.

---

### Task 5: Página Configurações

**Files:**
- Create: `web/app/settings/{layout,page}.tsx`, `web/components/settings/{AccountSection,SystemSection}.tsx` com testes
- Modify: `web/components/StatusRow` usos (mover, sem mudar o componente)

**Interfaces:**
- Produces: `/settings` com `PageHeader` e três seções com âncoras: `#conta` (nome/e-mail do overview como texto, versão do perfil, botões "Editar perfil" → /onboarding e "Continuar entrevista" → /onboarding/interview), `#descoberta` (slot que monta `DiscoveryPanel` do plano da coleta automática **se o módulo existir**; senão mostra o texto "Descoberta automática disponível em breve" sem botão), `#sistema` (o conteúdo do antigo `HealthPanel`: API, banco, "Verificar de novo"; reaproveitar `checkHealth`/`StatusRow`; o painel continua sendo renderizado no servidor com `connection()` como hoje).

- [ ] **Step 1: Write failing tests**: seções e âncoras, conta com dados hostis como texto, botões e links corretos, `#sistema` com API fora (aviso específico) e ok, slot da descoberta com e sem módulo.
- [ ] **Step 2: Run** `npm test` → FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Verificação** testes, lint, build; conferência visual nas 3 larguras e dois temas.
- [ ] **Step 5: Commit** `feat(web): settings page with account, discovery slot and system status`.

---

### Task 6: Cabeçalhos, steppers horizontais e CTAs nas telas existentes

**Files:**
- Modify: `web/app/onboarding/page.tsx`, `web/app/onboarding/interview/page.tsx`, `web/app/sources/page.tsx`, `web/app/jobs/page.tsx` (+ seus testes), `web/components/**` apenas se necessário para os botões, `web/app/globals.css`

**Interfaces:**
- Consumes: `PageHeader`, `Stepper` (horizontal).
- Produces: cada página usa `PageHeader` com título/descrição e ações: `/onboarding` — stepper Enviar currículo → Revisar → Confirmar → Entrevista (passo derivado do estado local já existente da página), ações "Enviar currículo"/"Confirmar perfil" onde couber e "Continuar entrevista" quando há perfil; `/onboarding/interview` — título e ação "Voltar ao perfil"; `/sources` — stepper Adicionar → Coletar → Ver vagas, ações "Adicionar empresa" (foca o formulário) e "Ver vagas"; estado sem empresas com o botão "Adicionar empresa"; `/jobs` — stepper Filtrar → Avaliar → Ler, ações "Avaliar melhores vagas" (a ação existente, sem duplicar a lógica) e "Filtros" (abre o painel de filtros existente); estados sem perfil/sem vagas levam a /onboarding ou /sources com botão. Nenhum conteúdo interno é reescrito; testes existentes continuam verdes (ajustar apenas seletores que dependiam do cabeçalho antigo).

- [ ] **Step 1: Write failing tests** por página: título e ação principal presentes, stepper com o passo certo para cada estado, botão de estado vazio leva à rota certa, o botão "Avaliar melhores vagas" do cabeçalho dispara a mesma função que o botão atual (sem duplicar requisição).
- [ ] **Step 2: Run** `npm test` → FAIL.
- [ ] **Step 3: Implement** reutilizando os handlers existentes (nada de lógica nova de dados).
- [ ] **Step 4: Verificação** `npx vitest run --maxWorkers=4`, lint, build; conferência visual de todas as telas nas 3 larguras e dois temas, com mock descartável da API.
- [ ] **Step 5: Commit** `feat(web): page headers, step trails and action buttons on existing screens`.
