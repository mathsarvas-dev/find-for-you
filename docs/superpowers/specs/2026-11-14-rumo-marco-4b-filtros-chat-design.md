# Rumo — Marco 4b: filtros por chat, consulta semântica e teste de qualidade

Complementa `2026-11-11-rumo-design.md` (§5.3, §5.4, §7) e `2026-11-12-rumo-marco-4a-matching-design.md`. O 4a entregou filtros manuais, o ranking por similaridade e a nota do LLM. O 4b acrescenta **filtros por frase (chat) com chips editáveis**, a **consulta semântica** no ranking e o **teste de precision@10** com vagas rotuladas por você.

## 1. Objetivo e critério de sucesso
Escrever "remoto na Europa, sem plantão, sênior, algo com dados" em `/jobs` e ver a frase virar chips de filtro que você pode editar ou remover, mais um trecho de "gosto" que reordena o ranking. Pronto quando:
- a frase vira `{filtros, consulta_semântica}` validados; JSON inválido nunca chega ao banco;
- cada chip reflete exatamente um valor do filtro manual (os dois lados ficam sincronizados);
- uma negação ("sem", "não quero") vira filtro de exclusão estruturado, **nunca** entra na consulta vetorial;
- o teste de qualidade mede precision@10 sobre ~40 vagas rotuladas e mostra se a meta (≥ 0,6) foi atingida.

Fora de escopo: destaques no painel inicial, vagas salvas/pipeline (marco 6), chat com histórico de várias mensagens (cada frase é independente), ajuste automático de pesos.

## 2. Decisões
- **Chat → LLM → JSON validado.** O Groq recebe a frase, os filtros atuais e os valores permitidos, e devolve só JSON no schema `ParsedQuery`. Validação Pydantic; 1 retentativa; se falhar, a API responde 422 com mensagem clara e **nada muda** nos filtros. O LLM nunca grava filtros: devolve uma **proposta**; quem grava é o `PUT /search-profile` já existente, depois que a pessoa confirma.
- **Negações = filtros de exclusão.** `SearchFilters` ganha: `exclude_countries`, `exclude_work_models`, `exclude_seniorities`, `exclude_languages`, `exclude_departments` (mesmas regras de valor das listas de inclusão) e `title_exclude` (até 10 termos, comparados sem acento e sem diferenciar maiúsculas contra o título). Exclusão só retira o que casa com certeza: valores desconhecidos/NULL nunca são excluídos, com ou sem `include_unknown` (que não se aplica a exclusões). Inclusão e exclusão do mesmo valor no mesmo filtro é erro de validação.
- **Consulta semântica só para o gosto.** `query_text` (≤ 300 caracteres) é o que sobra depois de extrair as restrições. Nunca contém negação nem restrição dura; o prompt proíbe e um validador rejeita `query_text` que contenha marcadores de negação ("sem", "não", "no", "without", "ohne") seguidos de termo.
- **Embedding da consulta é feito pelo worker**, não pela API (a API não carrega o modelo). Job `embed_queries` (a cada 15 s): para cada `SearchProfile` com `query_text` e sem `query_embedding` (ou `model_version` diferente), vetoriza e grava. Até lá a lista usa só o perfil e mostra o selo "consulta semântica pendente".
- **Ranking:** `vec_score = (wp·s_perfil + wq·s_consulta) / (wp + wq)`, com `wp = match_weight_profile` e `wq = match_weight_query` (já em `config.py`; hoje `wq = 0`; passa a 0,5 por padrão). Sem `query_embedding` ou sem consulta, `wq` não entra. `s_consulta` usa a mesma regra do `s_perfil` (média das 3 maiores similaridades, fórmula `1 − d/2`, limitada a [0, 1]) contra os vetores da vaga. A nota do LLM e o cache `Match` não mudam; mudar `query_text` **não** invalida notas do LLM (só reordena a pré-seleção e a janela das 30 melhores).
- **Privacidade e custo:** a frase vai ao Groq com os valores permitidos e os filtros atuais; nunca vai o perfil nem texto de vagas. Limite por processo: 20 interpretações por hora e por usuário (429 com mensagem em português); texto de entrada ≤ 300 caracteres.
- **Teste de qualidade:** rótulos `good`/`bad` por vaga em `job_labels`; a métrica é calculada sobre as vagas rotuladas (§6).

## 3. Dados (migração 0007)
- Extensão `unaccent` (para `title_exclude`).
- `search_profiles`: sem coluna nova (já tem `query_text` e `query_embedding`); acrescentar `query_model_version str(128) NULL`.
- `job_labels` (UserScopedMixin + TimestampMixin): `id, job_id FK jobs ON DELETE CASCADE, label str(8) CHECK in ('good','bad')`; único `(user_id, job_id)`.
- Os filtros ficam no JSONB `search_profiles.filters`; o schema `SearchFilters` aceita as novas chaves (todas com padrão vazio, então perfis salvos antigos continuam válidos).

## 4. Componentes
- `rumo_core/matching/filters.py`: novos campos de exclusão + `apply_filters` com `NOT IN`/`NOT ILIKE` sem acento comparando `lower(unaccent(title)) ILIKE '%termo%'` (o termo também é normalizado sem acento e minúsculo, com `%`/`_`/`\` escapados). A migração 0007 faz `CREATE EXTENSION IF NOT EXISTS unaccent` (vem com a imagem `pgvector/pgvector:pg16`; o plano confirma isso antes de depender dela).
- `rumo_core/matching/chat_filters.py`: `ParsedQuery` (`filters: SearchFilters`, `query_text: str | None`, `notes: list[str]` com até 3 avisos como "ignorei 'ótimo salário' (sem valor)"), `build_messages(text, current, allowed)` e `async parse_query(llm, text, current) -> ParsedQuery` com a validação do §2.
- `rumo_core/matching/ranking.py`: `_scores` passa a combinar perfil + consulta (§2); `rank_jobs`/`top_candidate_ids` recebem o `query_embedding` opcional do perfil de busca.
- `worker/embed_queries.py` + registro em `worker/main.py`.
- `rumo_core/matching/quality.py`: `precision_at_k(labels, ranked_ids, k=10)`; `build_sample(session, user_id, n=40)`; `evaluate(session, user_id) -> QualityReport`.
- API:
  - `POST /filters/parse` `{text}` → `{filters, query_text, notes}` (proposta; não grava; escopado por `user_id`).
  - `PUT /search-profile` passa a aceitar `query_text` e zera `query_embedding`/`query_model_version` quando o texto muda.
  - `GET /quality/sample` → até 40 vagas para rotular (§6); `PUT /quality/labels/{job_id}` `{label}` e `DELETE` do rótulo; `GET /quality/report`.
- Web: barra de chat acima dos filtros em `/jobs` ("Descreva o que procura…" + botão "Aplicar"); faixa de **chips** (um por valor; "×" remove; inclusão em azul, exclusão em vermelho com texto "sem …", estado nunca só por cor); chip da consulta semântica ("gosto: dados") editável; página `/jobs/qualidade` com a tela de rotulagem e o relatório. Rótulos em PT-BR, verbo no infinitivo.

## 5. Fluxo do chat
1. Frase → `POST /filters/parse` → proposta. A UI mostra os chips propostos com "Aplicar" e "Descartar"; nada é salvo antes.
2. "Aplicar" funde a proposta nos filtros atuais (a frase vence em conflito) e chama o `PUT /search-profile` já existente (que já cuida de corridas e erros).
3. Editar um chip chama o mesmo `PUT`; o painel manual e os chips leem do mesmo estado (uma fonte de verdade).
4. Erro do LLM, limite ou rede: mensagem específica e o botão "Tentar de novo"; os filtros atuais permanecem.

## 6. Teste de qualidade (precision@10)
- **Amostra** (`GET /quality/sample`): até 40 vagas abertas e vetorizadas, sem filtros: 20 sorteadas entre as 50 melhores do ranking atual e 20 sorteadas entre as demais (para não medir só o topo). Sorteio fixado por semente guardada em `user_preferences` (JSON `quality_seed`) para a amostra não mudar entre visitas; "Nova amostra" troca a semente e **mantém os rótulos já dados** (rótulos são por vaga, não por amostra).
- **Rotulagem:** uma vaga por vez (título, empresa, país, descrição curta, link), botões "Boa" e "Ruim", atalhos `g`/`b`, "Pular". Sem LLM.
- **Métrica:** entre as vagas rotuladas, ordena-se pela ordem atual (nota do LLM quando existir e estiver atual, senão `vec_score`; a mesma regra de `rank_jobs`) e conta-se quantas das 10 primeiras são "boas". Mostra também precision@10 só por `vec_score` (o funil). Só é válido com ≥ 30 rótulos e ≥ 10 "boas" e ≥ 10 "ruins"; antes disso a tela diz o que falta ("faltam 12 rótulos").
- **Meta:** ≥ 0,6 mostra "dentro da meta"; abaixo disso mostra os números e os rótulos mal classificados (boas fora das 10 primeiras, ruins dentro), para ajustar `match_weight_*`. Nenhum ajuste é automático.

## 7. Erros e resiliência
- JSON do LLM inválido, vazio ou com valor fora do conjunto: 1 retentativa; depois 422 em português, sem alterar nada.
- `Groq 429`/timeout: 503 com "tente em instantes"; sem bloquear o painel manual.
- Frase sem restrições nem gosto (ex.: "oi"): proposta vazia com a nota "não encontrei filtros nessa frase".
- Injeção na frase ("ignore as instruções…"): o único efeito possível é um JSON que passa pelo mesmo validador; nenhum campo livre vira SQL (valores comparados por parâmetro, listas limitadas, `title_exclude` por `ILIKE` com escape de `%`/`_`).
- Texto vindo do LLM (notas, chips) é sempre exibido como texto.

## 8. Testes
- `SearchFilters`: exclusões (aceitar/rejeitar valores, conflito inclusão×exclusão, `title_exclude` com acento e `%`), perfis antigos sem as chaves novas.
- `apply_filters`: exclusão não preserva desconhecido; `title_exclude` sem acento/caixa; combinado com inclusões.
- `parse_query` com LLM falso: negação vira exclusão; `query_text` sem negação; JSON inválido → retentativa → erro; valor fora do conjunto rejeitado; frase vazia → proposta vazia.
- Ranking: combinação com `wq` > 0; sem `query_embedding` igual ao 4a; vetores zero; limites [0, 1].
- Worker `embed_queries`: vetoriza pendentes, ignora já vetorizados, falha do embedder não derruba o loop, troca de `model_version` revetoriza.
- API: `/filters/parse` (escopo, limite por hora, 422/503), `PUT` zera o embedding ao mudar o texto, rótulos (escopo por usuário, único por vaga), `report` (< 30 rótulos → "insuficiente"; cálculo com casos conhecidos, empates por `job_id`).
- `precision_at_k` com casos manuais; amostra estável com a mesma semente.
- Web (vitest/RTL): chat → proposta → aplicar/descartar; chips editáveis e sincronizados com o painel manual; estado de erro com "Tentar de novo"; tela de rotulagem (atalhos, pular, contador); verificação visual em 375, 768 e desktop, claro e escuro.

## 9. Riscos
- **Meta não atingida (precision@10 < 0,6):** é um resultado, não uma falha de entrega; o relatório mostra o que ajustar. O marco entrega o instrumento e o relatório.
- **Viés da amostra:** mitigado pela metade da amostra fora do topo; ainda é uma amostra pequena (±): o relatório mostra o tamanho.
- **Rotular é trabalho seu (~10 min):** a tela funciona aos poucos e guarda tudo; nada trava a entrega do chat.
- **`wq` > 0 muda a ordem da lista sem aviso:** o chip "gosto" e o selo "consulta semântica pendente" deixam visível quando a consulta está valendo.
- **Conflito de arquivos com o marco 5** em `web/app/jobs/page.tsx`: o 4b executa antes e o 5 parte da versão mesclada.
