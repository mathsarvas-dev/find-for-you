# Rumo — Redesign da casca, navegação e trilha de passos

Complementa `2026-11-11-rumo-design.md` §9 (design) e substitui a página inicial atual ("Estado do sistema"). Aplica-se ao frontend (`web/`) e a um endpoint novo de resumo. Marcos 4b e 5 herdam esta casca.

## 1. Problema e objetivo
Hoje: uma barra superior só com o logo; a página inicial mistura estado do sistema com três cartões de texto; cada tela tem cabeçalho e botões próprios; a pessoa não vê **onde está no processo nem qual o próximo passo**. Objetivo: uma casca única (lateral + topo), um painel inicial que mostra o caminho como **linha do tempo** com o passo atual e um botão claro, e botões de ação evidentes em cada tela.

Pronto quando: de qualquer página a pessoa sabe em que passo está (trilha), vê o botão do próximo passo sem rolar, e acessa perfil/configurações pelo topo; funciona de 375 px a desktop, claro e escuro.

Fora de escopo: login/contas (SaaS), alternador de tema, mudar o conteúdo interno das telas (lista de vagas, tabelas), i18n.

## 2. Casca (layout raiz)
- **Barra lateral** (esquerda, fixa, ~232 px): logo + nome "Rumo" no topo; navegação: `Início` (/), `Perfil` (/onboarding), `Entrevista` (/onboarding/interview), `Fontes` (/sources), `Vagas` (/jobs), `Configurações` (/settings) separada por uma linha no fim. Cada item tem ícone SVG inline, rótulo e, quando útil, um **indicador de pendência** (ponto) e texto acessível ("perfil não confirmado"). O item ativo usa `aria-current="page"`. Rodapé da lateral: "Sistema" com um ponto de estado (ok / problema) que leva a /settings#sistema.
- **Barra superior** (fina, ~52 px): à esquerda, botão de menu (só < 860 px), o **título da página** e o caminho de navegação (breadcrumb curto); à direita, **menu do avatar**: iniciais, nome e e-mail do perfil confirmado (sem perfil: "Você" e "Perfil não confirmado"), itens `Meu perfil`, `Configurações`; o menu abre por botão, fecha com Escape/clique fora, é navegável por teclado.
- **Celular (< 860 px):** a lateral vira gaveta (overlay + foco preso + Escape fecha + `aria-modal`), aberta pelo botão de menu; a barra superior permanece.
- **Conteúdo:** área `main` com largura máxima, `PageHeader` padrão (título, descrição curta, ações à direita: um botão primário e até dois secundários).
- Sem `Date.now()`/`Math.random()` em render (pré-render seguro); sem dependência nova de UI; ícones SVG inline; tokens de cor/espaço existentes em `globals.css`.

## 3. Trilha (linha do tempo) — `Stepper`
Componente reutilizável com duas variantes, sempre uma lista ordenada (`<ol>`), com **linha conectora** e **bolinhas**:
- **Vertical (`/` Início, "Seu caminho"):** passos: 1 `Montar o perfil`, 2 `Confirmar e vetorizar` (perfil confirmado e vetores prontos), 3 `Entrevista rápida` (opcional, marcada como tal), 4 `Fontes de vagas` (≥ 1 empresa ativa; a descoberta automática conta), 5 `Vagas coletadas` (≥ 1 vaga aberta), 6 `Avaliar melhores vagas` (≥ 1 avaliada), 7 `Candidatar-se` (bloqueado até o marco 5; mostra "em breve"). Cada passo: bolinha, título, uma linha de explicação com o **dado real** ("12 empresas · 340 vagas abertas") e **um botão** quando é o atual ("Montar meu perfil", "Adicionar empresas", "Avaliar melhores vagas"). O passo atual é destacado; concluídos mostram ✓ e link "Revisar"; futuros ficam esmaecidos; bloqueados explicam o porquê.
- **Horizontal (fluxos internos):** `/onboarding`: Enviar currículo → Revisar → Confirmar → Entrevista; `/sources`: Adicionar → Coletar → Ver vagas; `/jobs`: Filtrar → Avaliar → Ler. Em < 560 px vira vertical compacta ou "Passo 2 de 4 · Revisar".
- **Estados e acessibilidade:** `done | current | upcoming | blocked | optional`; o estado nunca depende só de cor (ícone + texto oculto "concluído", "passo atual"); `aria-current="step"` no atual; contraste AA; linha conectora decorativa (`aria-hidden`); nenhum texto de fonte externa vira HTML.
- **Dados:** os passos são derivados de `GET /overview` (abaixo) por uma função pura `computeSteps(overview) -> Step[]` testável.

## 4. Painel Início
- Cabeçalho: saudação curta ("Bom dia" opcional; sem `Date` no render — calculada em efeito) e o botão primário do **passo atual**.
- Coluna principal: `Stepper` vertical "Seu caminho".
- Coluna lateral/abaixo: quatro cartões-resumo clicáveis (Perfil: versão e estado; Fontes: empresas ativas/automáticas; Vagas: abertas; Avaliadas: n de N) e um aviso discreto se o sistema tiver problema (link para Configurações).
- Estado de erro de rede: mensagem específica e botão "Tentar de novo"; nunca página vazia.

## 5. Botões de ação por tela (CTAs)
- `/onboarding` (Perfil): "Enviar currículo", "Continuar entrevista" (se houver perfil), "Confirmar perfil".
- `/sources`: "Adicionar empresa", "Coletar agora" em cada empresa, "Ativar descoberta"; estado vazio com o próximo passo.
- `/jobs`: "Avaliar melhores vagas", "Filtros" (abre o painel), "Ver vaga" no painel lateral; estado "sem perfil" leva a /onboarding com botão.
- `/settings` (nova): seções com âncoras — `Conta e perfil` (nome/e-mail do perfil, link "Editar perfil"), `Descoberta automática` (interruptor e empresas-alvo, reaproveita o painel do plano de coleta automática), `Estado do sistema` (o conteúdo da página inicial atual: API/banco, "Verificar de novo").
- Todo estado vazio/erro traz exatamente um botão que resolve o próximo passo; rótulos em PT-BR, verbo no infinitivo, específicos.

## 6. API de resumo (`api/overview.py`)
`GET /overview` → `{profile: {confirmed: bool, version: int|null, vectorized: bool, name: str|null, email: str|null}, interview: {state: 'none'|'in_progress'|'done'}, companies: {total, active, auto}, jobs: {open}, matches: {scored, pending}}`. Somente leitura, escopada por `user_id`, sem texto de vagas nem justificativas; `name`/`email` vêm de `Basics` do perfil atual (podem ser vazios). Tipos espelhados em `web/lib/overview.ts`. Falhas de uma parte não devem derrubar as outras (campos com padrão seguro).

## 7. Estrutura de código
`web/components/shell/{AppShell,Sidebar,Topbar,UserMenu,NavItem,PageHeader}.tsx`, `web/components/Stepper/{Stepper,computeSteps}.tsx|ts`, `web/app/layout.tsx` usa `AppShell`, `web/app/page.tsx` (Início), `web/app/settings/{layout,page}.tsx`, `web/lib/overview.ts`; as páginas existentes ganham `PageHeader` e `Stepper` horizontal sem trocar o conteúdo. Testes (vitest/RTL): navegação ativa, gaveta (foco/Escape), menu do avatar (teclado), `computeSteps` (todos os estados), `Stepper` (ordem, `aria-current`, texto oculto), páginas com os botões. Verificação visual em 375, 768 e desktop, claro e escuro.

## 8. Riscos
- Muitas páginas tocadas: mitigado por trocar só cabeçalhos/casca, mantendo conteúdo e testes existentes verdes.
- Trilha mentindo sobre o estado: os passos vêm de contadores reais do servidor, não de estado local.
- Sem login: o menu do avatar mostra dados do perfil e não sugere sessão; fica pronto para trocar a fonte de identidade no SaaS.
