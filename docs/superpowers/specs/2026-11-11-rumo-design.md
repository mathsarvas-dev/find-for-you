# Rumo — Spec de design (nome provisório)

Data: 2026-11-11 · Status: rascunho para revisão

## 1. Objetivo

Ferramenta **pessoal** (um usuário, rodando localmente) que caça vagas de emprego internacionais com base em um perfil vetorizado, adapta o currículo a cada vaga e acompanha as candidaturas. **Desenhada para virar SaaS depois**: todos os dados carregam `user_id` desde o primeiro dia; login, multi-tenant e cobrança ficam fora do MVP.

**Critério de sucesso real:** dado um conjunto de ~40 vagas marcadas manualmente como "boa" ou "ruim", pelo menos 6 das 10 primeiras do ranking devem ser "boas" (precision@10 ≥ 0,6).

**Princípio de produto:** o sistema prepara tudo; **o envio da candidatura é sempre humano**. O LLM nunca inventa fatos sobre o candidato.

### Premissas assumidas (corrigir se estiverem erradas)
- Mercados: Brasil, EUA, Alemanha, Austrália e outros; idiomas PT, EN e DE no MVP.
- Provedor de LLM padrão: **Groq** (API compatível com OpenAI, plano gratuito), atrás de uma interface trocável.
- Embeddings: **bge-m3 local** (multilíngue, 1024 dimensões), custo zero.
- Nome "Rumo" é provisório.

## 2. Fora do escopo do MVP

Central de e-mails (IMAP) · Workable e leitor genérico de páginas de carreira · login multiusuário e cobrança · notificações fora do app · automação de envio de candidaturas · scraping de LinkedIn, Indeed, Seek e Vagas.com (termos de uso proíbem; ficam apenas via "importar por link" colado pelo usuário).

## 3. Arquitetura

```
web (Next.js) ──▶ api (FastAPI) ──▶ Postgres + pgvector (Docker)
                                         ▲
                  worker (Python) ───────┘
                    ├─▶ Groq API (LLM)
                    └─▶ bge-m3 local (embeddings)
```

- **web**: onboarding, chat, lista de vagas com filtros, kanban, tela "preparar candidatura", relatórios.
- **api**: regras de negócio; tudo escopado por `user_id`.
- **worker**: coleta, normalização, vetorização e análise. Tem **controle de ritmo** (fila com limite de taxa) para respeitar o plano gratuito do Groq.
- **Módulos trocáveis** (cada um atrás de uma interface):
  - `llm/` — Groq hoje; outro provedor por configuração.
  - `embeddings/` — bge-m3 local; versão do modelo registrada em cada vetor.
  - `sources/` — um conector por fonte; adicionar fonte = adicionar um arquivo.
  - `documents/` — currículo em HTML → PDF, um template por país.

Stack: Python 3.12, FastAPI, SQLAlchemy + Alembic, Pydantic, Postgres 16 + pgvector, Next.js (App Router) + TypeScript, Docker Compose.

## 4. Modelo de dados

Todas as tabelas têm `user_id`, `created_at`, `updated_at`.

| Tabela | Conteúdo |
|---|---|
| `profile` | Perfil canônico em JSON (experiências, habilidades, conquistas, formação, idiomas) com **versionamento**. |
| `profile_chunks` | Pedaços do perfil e das respostas da entrevista + vetor + versão do modelo de embeddings + versão do perfil. |
| `companies` | Empresa, fonte, identificador (ex.: `greenhouse:nubank`), país, ativa/inativa. É a lista que o robô vigia. |
| `jobs` | Vaga normalizada: título, empresa, país, cidade, modelo (presencial/híbrido/remoto), senioridade, idioma, departamento, salário (mín, máx, moeda, período, valor convertido para moeda de referência, `salary_known`), texto completo, conteúdo bruto original, vetor, impressão digital (dedupe), status (aberta/encerrada), datas de visto. |
| `search_profiles` | Filtros salvos: filtros duros em JSON + texto e vetor da consulta semântica. |
| `matches` | Vaga × perfil: nota vetorial, nota da IA (0–100), justificativa, pontos fortes, lacunas, versão do perfil usada, estado (`pendente`/`ok`/`erro`). Cache por (vaga, versão do perfil). |
| `applications` | Status no funil, datas, anotações, link da vaga. |
| `documents` | Currículos, cartas e respostas gerados, ligados à candidatura, com mapa de **origem de cada linha** no perfil. |
| `answer_bank` | Respostas reutilizáveis (pretensão salarial, visto, disponibilidade, mudança de cidade). |
| `chat_messages` | Histórico da entrevista e do chat de filtros. |

## 5. Fluxos

### 5.1 Onboarding (em etapas)
1. **Perfil básico + narrativa livre**.
2. **Upload do currículo** (PDF/DOCX). Texto extraído localmente; Groq devolve JSON validado por schema.
3. **Revisão**: o JSON aparece como formulário editável. **Nada é vetorizado antes da confirmação.**
4. **Entrevista**: a IA recebe o perfil e a lista de lacunas detectadas (conquista sem número, período sem descrição, preferência salarial ausente etc.) e faz **uma pergunta por vez**, ~8–12 no total. Cada resposta vira uma **sugestão de alteração no perfil** (aceitar/ajustar) e um chunk vetorizado.
5. **Preferências iniciais** (manual e/ou chat) → primeiro `search_profile`.

Toda mudança no perfil gera nova versão; os chunks afetados são re-vetorizados.

### 5.2 Coleta (worker, a cada X horas)
1. Semente de empresas por fonte e país + empresas adicionadas pelo usuário.
2. Busca na API da fonte; **normalização por regras primeiro**; o **LLM só preenche campos faltantes** (modelo de trabalho, salário no texto).
3. Dedupe por impressão digital; vagas que sumiram → `encerrada`.
4. **Vetoriza apenas vagas novas ou alteradas**, localmente.

Fontes do MVP: Greenhouse, Lever, Ashby, Gupy (a confirmar estabilidade da API), Bundesagentur für Arbeit, importar por link (JSON-LD `JobPosting` ou texto colado).

### 5.3 Matching (funil)
1. **Filtros duros em SQL** (país, modelo, salário, senioridade, idioma, departamento).
2. **Similaridade vetorial** da vaga com o perfil e com a consulta semântica (soma ponderada, pesos configuráveis).
3. **Top 30 → Groq**: nota 0–100, justificativa, pontos fortes e lacunas; cache por (vaga, versão do perfil).
4. Vagas novas com nota alta viram **destaques** no painel.

### 5.4 Filtros
- **Manuais**: departamento, país/localidade, modelo, salário (com opção "aceitar vagas sem salário"), senioridade, idioma.
- **Por chat**: a frase vai ao Groq, que devolve `{filtros_duros, consulta_semantica}`.
  - Restrições (inclusive negações) viram **filtros estruturados** exibidos como **chips editáveis**; chat e filtro manual ficam sincronizados.
  - Só o "gosto" restante vira consulta vetorial. **Nunca se vetoriza uma restrição dura** (embeddings falham com negação).

### 5.5 Candidatura
1. **Leitura da vaga**: Groq extrai requisitos obrigatórios, desejáveis e palavras-chave.
2. **Seleção** vetorial, no perfil canônico, do que é mais relevante para esses requisitos.
3. **Reescrita** no idioma da vaga e no formato do país (EUA: 1–2 páginas, sem foto; Alemanha: Lebenslauf tabular, foto/data de nascimento opcionais, *Anschreiben* opcional; Austrália: 2–3 páginas, sem foto, *referees*; Brasil: formato livre).
4. **Regra anti-invenção:** toda linha gerada deve referenciar o item do perfil de origem. Um **validador rejeita linhas sem origem** e qualquer número, data ou habilidade ausente do perfil. A IA pode reformular, traduzir e priorizar; **nunca criar fatos**.
5. **Cobertura de requisitos** ("14 de 18"), com o que falta separado em: *"você tem, mas não está no perfil"* (o sistema pergunta e, se confirmado, adiciona) e *"você não tem"* (apenas aviso).
6. **Revisão** lado a lado com o perfil, editável → PDF pelo template do país + versão texto puro.
7. **Carta opcional** sob a mesma regra.
8. **Perguntas extras**: Greenhouse/Ashby informam via API; senão o usuário cola. O `answer_bank` reaproveita respostas (adaptando idioma e moeda); perguntas abertas recebem rascunho baseado no perfil + descrição da empresa.
9. **Kit de candidatura**: PDF + respostas copiáveis + link da vaga. O usuário aplica no site e clica **"Apliquei"**.

### 5.6 Pipeline e relatórios
- Kanban: salva → preparando → aplicada → entrevista → oferta / recusada / sem resposta.
- Lembrete de follow-up após X dias; anotações por vaga.
- Relatórios: vagas encontradas/aplicadas/respondidas; taxa de resposta por país, fonte e faixa de nota (para checar se a nota prevê resposta).

### 5.7 Otimizador de perfil do LinkedIn (pós-MVP, adicionado em 2026-11-11)

**Escopo definido pelo usuário:** o sistema **não lê** o perfil do LinkedIn (sem scraping, sem URL); ele **sugere como o perfil deve ficar**, a partir do perfil canônico do próprio usuário, e propõe o preenchimento completo.

- **Entrada:** o perfil canônico (seção 5.1) e as preferências de busca (cargo-alvo, países). Opcional: o usuário cola o texto atual de cada seção para receber um "antes/depois".
- **Saída (um rascunho por seção do LinkedIn):** headline, sobre (About), experiências (bullets orientados a resultado), habilidades (ordem e quais fixar), formação, certificações, projetos, idioma do perfil, URL personalizada, sugestão de foto/banner e *open to work*.
- **Pontos de vista de análise**, cada um com critérios explícitos e uma nota por seção, combinados em um relatório de melhorias priorizado:
  1. **Recrutador:** leitura em 6–10 s, clareza do cargo-alvo, prova de impacto, senioridade.
  2. **ATS / busca por palavras-chave:** cobertura de termos do cargo-alvo (extraídos das vagas coletadas), títulos padronizados, sem jargão interno.
  3. **Gestor técnico:** profundidade, escopo e decisões técnicas.
  4. **Internacional:** adaptação por mercado (EUA, Alemanha, Austrália, Brasil) e idioma.
- **Regra anti-invenção da seção 5.5 vale integralmente:** cada linha sugerida aponta o item do perfil de origem; o que o usuário "deveria ter" vira lista de lacunas, nunca texto inventado.
- **Fundamentação:** os critérios ficam em um arquivo de rubricas versionado (`rubrics/linkedin_*.md`), escrito a partir de boas práticas públicas de recrutamento e ATS. Não se afirma ter acesso a algoritmos proprietários do LinkedIn ou de ATS comerciais; as notas são **heurísticas explicadas**, não pontuações oficiais.
- **Tela:** relatório com lista priorizada de melhorias, seção a seção, texto sugerido editável e botão de copiar.

## 6. Erros e resiliência
- **Fontes isoladas**: falha de um conector não afeta os outros; retentativa com espera crescente; painel de saúde por fonte (última coleta, erros).
- **Groq 429**: fila com controle de ritmo e retentativa; vaga exibida só com nota vetorial e selo "análise pendente".
- **JSON inválido do LLM**: validação Pydantic; 1 retentativa; se falhar, estado `erro`, nada gravado como válido.
- **Conteúdo bruto da vaga guardado**: permite renormalizar sem nova coleta.
- **Versão do modelo de embeddings** registrada: trocar de modelo mostra exatamente o que re-vetorizar.
- **Sem segredos no repositório**: chave do Groq em `.env`, fora do git.

## 7. Testes
- **Conectores e normalização**: respostas reais salvas como fixtures; sem internet nos testes.
- **LLM mockado** nos testes unitários; **validador anti-invenção com cobertura pesada** (peça mais crítica).
- **Qualidade do matching**: ~40 vagas rotuladas pelo usuário; métrica precision@10 (critério da seção 1).
- **Frontend**: testes de componente nos fluxos críticos (revisão do perfil, chips de filtro, revisão do currículo) e verificação visual em 3 larguras (mobile, tablet, desktop).

## 8. Ordem de construção
Cada marco funciona e é testável sozinho.
1. **Base**: Docker/Postgres, esqueletos de api e worker, provedores `llm/` e `embeddings/`.
2. **Perfil**: upload → extração → revisão → vetorização; depois a entrevista.
3. **Coleta**: Greenhouse primeiro; depois as demais fontes.
4. **Matching e filtros**: manuais, depois chat; em seguida o teste de qualidade com vagas rotuladas.
5. **Candidatura**: currículo adaptado, perguntas, PDF.
6. **Pipeline e relatórios.**

Cada marco terá o seu próprio plano de implementação.

---

## 9. Diretrizes de frontend (design sem "cara de IA")

**Natureza do produto:** é uma ferramenta de trabalho que a pessoa abre todos os dias, não uma landing page. O design deve passar **calma, foco e confiança**: parecer um instrumento bem feito (pense em editor de texto, planilha boa, caderno de campo), não um template de startup.

### 9.1 Direção visual
- **Densidade informacional alta, hierarquia clara.** Telas de listas e tabelas são o centro do produto; priorize leitura rápida, alinhamento e ritmo vertical consistente.
- **Paleta restrita:** uma cor neutra de base (cinzas levemente quentes, não azulados), **uma única cor de acento** com personalidade (escolher uma e usá-la só em ações primárias e estados ativos) e cores semânticas discretas para status. Definir tudo como **tokens CSS** (`--bg`, `--surface`, `--text`, `--muted`, `--accent`, `--border`...), com **modo claro e escuro** desde o início.
- **Tipografia com caráter:** um par de fontes escolhido de propósito (ex.: uma sans humanista ou grotesca para a interface + uma serifada ou mono apenas para números, notas e rótulos técnicos). **Evitar Inter/Roboto/Arial/system-ui como identidade.** Escala tipográfica fixa (6 tamanhos no máximo), números tabulares em tabelas e notas.
- **Superfícies planas:** bordas finas de 1px e variação sutil de fundo para separar áreas; sombras raras e curtas. Raio de borda pequeno e **consistente** (um valor para campos, um para cartões).

### 9.2 O que NÃO fazer (marcadores clássicos de design gerado por IA)
- Gradientes roxo→azul, brilhos neon, *glassmorphism*, blobs e orbes decorativos.
- Cartões idênticos empilhados com ícone em círculo colorido + título + texto genérico.
- Hero com título gigante e "Welcome back 👋". Sem emojis como ícones e sem texto de preenchimento motivacional.
- Ícones de uma biblioteca em tamanhos e pesos misturados; usar **um só conjunto** (ex.: Lucide ou Phosphor, traço único) e só onde ajudam a entender.
- Sombras grandes difusas em tudo, cantos muito arredondados em tudo, animações em tudo.
- Copy genérica ("Supercharge your job search"). Todo texto da interface deve ser **específico, curto e em tom humano**, escrito em PT-BR (com i18n preparado para EN).
- Dados falsos óbvios (Acme Corp, John Doe). Estados de exemplo devem usar dados plausíveis.

### 9.3 Telas e padrões que importam
- **Lista de vagas:** tabela/lista densa com colunas escaneáveis (título, empresa, país, modelo, salário, nota). **A nota é o elemento visual dominante**, mostrada como número tabular com uma barra fina, não como selo colorido gritante. Painel lateral de detalhes (não modal) com justificativa, pontos fortes e lacunas. Atalhos de teclado (j/k para navegar, s para salvar, p para preparar).
- **Chips de filtro:** o mesmo componente serve ao filtro manual e ao resultado do chat; editar um chip atualiza a lista na hora; mostrar claramente o que é **restrição** e o que é **preferência semântica**.
- **Revisão do currículo:** duas colunas, perfil original à esquerda e texto gerado à direita; cada linha gerada tem **marca discreta de origem** (hover mostra o item do perfil). Linhas sem origem aparecem bloqueadas. Cobertura de requisitos em lista simples, não em gráfico decorativo.
- **Kanban:** colunas estreitas e sóbrias, cartões compactos, arrastar e soltar com alternativa por teclado e menu. Contagem por coluna no cabeçalho.
- **Onboarding:** um passo por tela, indicador de progresso simples, **botão de voltar sempre disponível**. A entrevista usa uma pergunta por vez, em tipografia generosa; as sugestões de alteração do perfil aparecem como **diff** (antes/depois) que a pessoa aceita ou edita.
- **Relatórios:** poucos gráficos, honestos (eixos com zero, rótulos diretos nas séries em vez de legenda, sem 3D). Carregar a skill `dataviz` ao construir.

### 9.4 Estados e microinterações
- **Todos os estados desenhados:** carregando (esqueleto com a forma do conteúdo, nunca spinner genérico em tela cheia), vazio (explica o que fazer a seguir, com uma ação), erro (diz o que houve e como seguir) e **"análise pendente"** (selo discreto).
- **Movimento com propósito:** transições de 120–200 ms para mudanças de estado e entrada de painéis; nada de animação de entrada em cada elemento; respeitar `prefers-reduced-motion`.
- Feedback imediato em ações (salvar, aplicar, copiar), com confirmação discreta no próprio elemento em vez de *toasts* gigantes.

### 9.5 Acessibilidade e responsividade
- Contraste mínimo WCAG AA nos dois temas; foco visível e navegação completa por teclado; rótulos reais nos campos; alvos de toque ≥ 40 px.
- Layout funcional em celular (lista vira cartões, painel de detalhes vira tela cheia), mas **desktop é o alvo principal**.

### 9.6 Processo de design durante a implementação
1. Antes de codar a interface, definir **tokens, escala tipográfica e 2–3 componentes-base** (tabela, chip, painel lateral) e validar no navegador.
2. Usar os skills de design disponíveis (`taste-skill` para direção de arte e anti-slop, `ui-ux-pro-max` e `ui-styling` para componentes, `design-motion-principles` para movimento) ao construir cada tela.
3. **Ciclo de verificação visual:** abrir cada tela no navegador, capturar em mobile/tablet/desktop, nos temas claro e escuro, e corrigir antes de seguir.
4. Revisar cada tela contra a lista 9.2 antes de considerá-la pronta.

## 10. Riscos e perguntas abertas
- **API da Gupy:** confirmar se é estável e permitida; se não, cai para "importar por link".
- **Limite gratuito do Groq:** pode apertar com muitas vagas; mitigado por cache, filtros duros antes do LLM e fila com ritmo.
- **bge-m3 local:** exige RAM/CPU razoáveis; medir velocidade de vetorização no PC do usuário no marco 1.
- **Qualidade do match em PT/DE:** validar já no marco 4 com o teste de precision@10.
- **Nome do projeto e pasta definitiva:** a definir; a spec está numa pasta temporária da sessão.
