# Rumo — Marco 4a: filtros manuais e funil de matching

Complementa `2026-11-11-rumo-design.md` (seções 5.3, 5.4, 9.3). O marco 4 se divide em **4a** (este) e **4b** (filtros por chat com chips + teste de precision@10 com ~40 vagas rotuladas).

## 1. Objetivo e critério de sucesso
Transformar as vagas coletadas (marco 3) em um **ranking pessoal**: filtros duros em SQL, nota vetorial contra o perfil confirmado, nota 0–100 do Groq para as 30 melhores, tudo numa tela de vagas com a nota como elemento dominante.

Pronto quando: com um perfil confirmado e vagas coletadas, a pessoa filtra manualmente, vê a lista ordenada por nota, abre o painel lateral e lê justificativa, pontos fortes e lacunas — sem que nenhuma frase do Groq cite algo que não esteja no perfil.

Fora de escopo (4b ou depois): chat de filtros, chips vindos do chat, consulta semântica livre, destaques no painel, teste de precision@10, preparar candidatura.

## 2. Decisões
- **Nota vetorial = média dos 3 maiores cossenos** entre a vaga e os chunks da **versão atual** do perfil (só chunks com embedding e `model_version` igual ao do embedder). Menos de 3 chunks: média dos que existem. Sem chunks vetorizados: a tela avisa que o perfil ainda está sendo vetorizado; nada é ranqueado.
- **Pesos configuráveis** (`match_weight_profile`, `match_weight_query`): em 4a só o termo do perfil é usado (query = 0); o termo da consulta existe na fórmula para o 4b plugar sem migração. Nota combinada `vec_score` em 0–1 (cosseno normalizado: `(cos+1)/2`).
- **"Desconhecido" não elimina** (política conservadora do projeto): cada filtro duro tem a opção "incluir desconhecidos" (padrão: ligada); vagas assim aparecem com marca discreta "salário/modelo desconhecido".
- **Filtros duros ficam em SQL, na API, em tempo de consulta.** O Groq **nunca** roda na API; só no worker.
- **Anti-invenção:** a justificativa do Groq é validada contra o perfil (mesmos validadores `numbers_supported`/léxico de `rumo_core/interview/suggestions.py`); pontos fortes/lacunas que citem número, empresa ou habilidade ausentes do perfil são descartados; se sobrar nada válido, o estado é `erro` com motivo, não texto inventado.

## 3. Dados (migração 0005)
- `search_profiles`: `id, user_id, name, filters JSONB, query_text, query_embedding Vector(1024) NULL, is_default bool, created/updated`. Único por (user_id, name). Em 4a existe exatamente um perfil de busca "padrão" por usuário, criado sob demanda; o 4b adiciona vários e o chat.
  - `filters` (validado por Pydantic): `countries: list[str]`, `work_models: list[str]`, `seniorities: list[str]`, `languages: list[str]`, `departments: list[str]`, `salary_min: number|null`, `salary_currency: str|null`, `include_unknown: bool` (padrão true), `accept_no_salary: bool` (padrão true).
- `matches`: `id, user_id, job_id, profile_version, vec_score float, ai_score int NULL (0–100), rationale text NULL, strengths JSONB NULL, gaps JSONB NULL, state ('pendente'|'ok'|'erro'), error text NULL, attempts int, scored_at NULL, created/updated`. Único por (user_id, job_id, profile_version); índice (user_id, profile_version, ai_score desc).

## 4. API (`api/matching.py`)
- `GET /search-profile` / `PUT /search-profile`: lê/salva os filtros do perfil padrão. Rejeita valores fora dos enums conhecidos (modelo, senioridade) e países que não normalizam.
- `GET /matches`: aplica filtros → calcula `vec_score` em SQL (top-3 por vaga com lateral/`array_agg` ordenado) → ordena por `ai_score` quando houver, senão `vec_score` → `limit ≤ 100`, `offset`. Resposta por item: dados da vaga (sem descrição), `vec_score`, `ai_score`, `state`, marcas de desconhecido. Parâmetro `status` (padrão `open`). Retorna também `profile_version` e `vectorized: bool`.
- `GET /matches/{job_id}`: vaga completa + justificativa, pontos fortes, lacunas, estado.
- `POST /matches/refresh`: marca as 30 melhores por `vec_score` sob os filtros atuais como `pendente` (cria/reaproveita linhas `matches` da versão atual; `ok` não é refeito). Idempotente; limite de uma chamada por 10 s por usuário.
- Todas as consultas escopadas por `user_id`.

## 5. Worker (`worker/match_jobs.py`)
- Job `score_matches`, registrado após `fill_missing_job_fields`, a cada 60 s, `match_batch=3` por tick: `FOR UPDATE SKIP LOCKED` nas linhas `pendente` do usuário, commit antes da chamada lenta, `attempts` incrementado, máximo 3 → `erro`.
- Prompt: perfil canônico resumido (não os chunks brutos) + vaga em `wrap_untrusted`; saída JSON `{score, rationale, strengths[], gaps[]}`, `rationale` ≤ 600 caracteres, listas ≤ 5 itens de ≤ 160 caracteres, PT-BR.
- Mesmo limitador do Groq (25/min compartilhado com entrevista e preenchimento); orçamento diário de tokens continua no backlog.
- Cache: a linha `matches` da versão do perfil é o cache. Perfil novo (nova versão) → linhas novas; vaga que muda de `content_hash` → linha da vaga volta a `pendente`.

## 6. Frontend (`web/app/jobs`, substitui a lista simples de `/sources` como tela principal de vagas; `/sources` continua gerenciando empresas)
- Lista densa: título, empresa, país, modelo, salário, **nota** (número tabular dominante + barra fina; sem selo colorido gritante). Sem nota da IA: mostra o vetorial em cinza com a legenda "pré-seleção".
- Filtros manuais acima da lista (departamento, país, modelo, salário com "aceitar sem salário", senioridade, idioma, "incluir desconhecidos"); mudar um filtro recarrega a lista (debounce 300 ms) e salva o perfil de busca.
- Botão "Avaliar melhores vagas" (`POST /matches/refresh`), com polling a cada 10 s só enquanto houver `pendente`.
- Painel lateral (não modal) com justificativa, pontos fortes e lacunas; atalhos j/k (navegar), s (salvar — reservado, sem efeito até o marco 6), p (reservado para o marco 5). Texto sempre como texto; links só http/https.
- Estados: sem perfil confirmado, perfil vetorizando, sem vagas, sem resultados para os filtros — cada um com texto específico em PT-BR.
- Verificação visual em 375, 768 e desktop, claro e escuro.

## 7. Riscos e tratamento
- Perfil com poucos chunks → nota instável: exibir quantos chunks foram usados no painel de detalhes.
- Groq indisponível/limitado: linhas ficam `pendente`; a lista segue ordenada por `vec_score`.
- Vagas sem embedding ainda: ficam fora do ranking e a tela diz quantas faltam.
- Qualidade em PT/DE só é medida no 4b; 4a deixa os pesos configuráveis para ajustar depois.
