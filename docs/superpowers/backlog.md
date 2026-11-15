# Backlog técnico (itens adiados, por marco)

## Do marco 2a (revisão final: "pode esperar")
- **Segurança/SaaS:** sem limite de tamanho descomprimido/páginas em DOCX/PDF (zip bomb) — bloqueia a versão SaaS; o upload bruto já é limitado a 5 MB.
- **Extração de arquivo:** xlsx/pptx rejeitados com a mensagem "DOCX corrompido?"; PDF com senha reportado como "corrompido?"; faltam testes (bytes vazios, .doc legado, xlsx, PDF criptografado).
- **Testes:** `TEST_DATABASE_URL` separado (hoje os testes usam o banco de desenvolvimento, escopados a usuários descartáveis; um worker em execução pode disputar linhas dos testes — pare o worker antes de rodar `pytest`).
- **Worker:** vetoriza também versões antigas do perfil e nunca as poda; progresso em `GET /profile` ignora `model_version` defasado; `run_due` é sequencial (extração espera o backlog de vetorização); imports abandonados ainda gastam cota do Groq; locks de linha mantidos durante o embed.
- **Docker:** camada de dependências depois do `COPY` (torch ~2 GB rebaixado a cada mudança de código).
- **Web:** foco/anúncio ao trocar de etapa, "Voltar" descarta edições, aria-labels repetidos em habilidades/idiomas, `ready` sem perfil faz polling infinito, certificações/projetos não editáveis (preservados).
- **API:** `get_settings()` avaliado na importação de `api.main`; JSON armazenado inválido gera 500.
- **Marco 1 ainda pendente:** SIGTERM do worker, estreitar o handler global de `OperationalError`, tema claro/escuro sem alternador, i18n não ligado, `npm audit` (7 moderadas).

## Marco 2b (revisão final)
- **Notas (pré-requisito para qualquer marco que recupere chunks do perfil):** as notas são invisíveis e não podem ser apagadas na UI, e guardam a resposta bruta mesmo depois de "Ajustar", com um tópico interno em inglês (o `kind` da lacuna). Mostrar/editar/apagar notas na revisão do perfil e guardar o texto ajustado ou um tópico em PT-BR.
- **Aba desatualizada:** confirmar o perfil sobrescreve alterações feitas em outra aba; precisa de um `base_version` otimista.
- **Anti-invenção:** o validador só checa habilidades/empresas inventadas em `add_skill`, não em texto livre.
- **`add_skill`:** uma lista separada por vírgulas vira uma única habilidade.
- **Testes e worker:** os testes e um worker de desenvolvimento rodando compartilham o banco de desenvolvimento (pare o worker ao rodar os testes de banco, ou adicione `TEST_DATABASE_URL`).
- **Rate limit:** a pausa de 60 s do worker mostra o aviso enganoso de "worker parado".
- **Menores adiados da revisão do plano:** o léxico casa palavra por palavra e rejeita falsamente; números não são amarrados a unidade/contexto; `TargetMissing` vs `InvalidSuggestion` decidido pela string da mensagem (usar um código de motivo); `GET /interview/current` carrega todas as sessões; polling sem limite enquanto gera.

## Marco 3
- **Coleta:** empresa com quadro realmente esvaziado fica em falha enquanto tiver ≥5 vagas abertas; considerar aceitar após N falhas seguidas ou mostrar claramente na UI.
- **Ingestão:** o SELECT de vagas existentes carrega todo o histórico fechado a cada coleta.
- **Ingestão:** `raw`/`url`/`posted_at` não atualizam quando só eles mudam (o `content_hash` não os cobre).
- **Quota do Groq:** o preenchimento por LLM limita só requisições/min; falta um orçamento de tokens/dia compartilhado com a entrevista (hoje mitigado: 4000 caracteres e lote de 3).
- **UI Fontes:** "Coletar agora" durante uma coleta em andamento não aparece (lease de 30 min sobrescreve `next_run_at`); a lista de vagas volta à página 1 após uma coleta; o refresh de empresas não tem guarda de sequência; `pollError` só limpa no botão de nova tentativa; sem indicação de carregamento ao filtrar; `ADD_FALLBACK` depende do prefixo de erro de `profile.ts`.
- **Preenchimento:** o LLM pode preencher um salário que as regras deixaram desconhecido de propósito (faixas em duas moedas) — validar a moeda contra o país/menção única.
- **Ingestão:** vaga pulada por erro de banco fica fora do dedupe por fingerprint até a próxima coleta; adicionar empresa lê até 20 MB de JSON de forma síncrona na API.
- **Marco 3 (menores das tarefas 1–7):** ver lista de itens adiados na revisão final (embed mantém locks de linha durante `EMBED_LOCK`, `zip(strict=True)` fora do try, spans do fill herdam a regra mais fraca de `extract_salary`, adicionar empresa duplicada busca antes de dar 409).

## Marco 4a
- **Groq:** o teto diário conta vagas avaliadas (`MATCH_DAILY_LIMIT=20`), não tokens; falta orçamento de tokens/minuto e por dia compartilhado com a entrevista. Falhas e timeouts não entram na conta; uma vaga que sempre dá timeout é tentada de novo a cada minuto; sem índice em `matches.scored_at`.
- **Validação da justificativa:** afirmação positiva depois de uma negação na mesma oração passa ("não tem lacunas e domina X"); domínios em CAIXA ALTA passam em lacunas; `evil[.]com` passa; formas derivadas ("dockerizados") e ofuscação por unicode passam nos pontos fortes; habilidades curtas minúsculas (aws, git) vindas da vaga passam; truncar a justificativa pode cortar um decimal ("3.5" → "3.").
- **Fila:** a avaliação ordena por `vec_score` entre todos os usuários (um usuário que atualiza muito pode atrasar os outros no SaaS); duas instâncias do worker podem avaliar a mesma linha (precisa de lease); atualizar repetido não recalcula `vec_score`; refresh concorrente pode dar deadlock.
- **Tela de vagas:** PUTs fora de ordem; recarga descartada quando um segundo salvamento começa; "Mais vagas" só devolve duplicadas se um job novo entrou acima da janela; linha some do polling com >100 vagas carregadas; "Avaliar" usa os filtros antigos se o parse falhar; polling a cada 10 s em aba oculta e sem GROQ_KEY; `total=0` não distingue "sem vagas" de "filtros sem resultado"; painel fixo no mobile sem anúncio/foco; salário `0` aceito.
- **Filtros:** `salary_max` sem `salary_min` conta como desconhecido; departamento não é aparado; ordem de imports em `api/sources.py`.
- **Menores das tarefas 1–7:** ver lista na revisão final (leitura dupla de `jobs` na página, score calculado duas vezes por chamada, `top_candidate_ids` com argumento extra, detalhe roda `_usable` duas vezes, limitador por processo).

## Coleta automática (descoberta de empresas)
- **Teto e prioridade:** candidatos do catálogo podem encher o teto de 100 empresas automáticas antes de o perfil ser confirmado; alvos/perfil depois esperam (contar só o catálogo no teto, ou deixar alvo/perfil passarem por cima). Um alvo removido cujo quadro estava vazio continua `invalid` com reavaliação em 30 dias e pode criar empresa automática depois; um alvo removido que o catálogo/perfil ainda gera mantém o rótulo e a prioridade de alvo.
- **SaaS:** todo usuário habilitado recebe ~165 validações do catálogo e até 100 empresas automáticas; compartilhar o resultado da validação por token entre usuários ou condicionar o catálogo a "perfil confirmado"; a rotação entre usuários avança um usuário por ciclo e divide a cota por todos os habilitados.
- **Rede:** a validação baixa o quadro completo (`content=true`) e a coleta baixa de novo; o timeout de busca (30 s) é maior que `max_seconds` (20 s) e pode segurar o loop ~51 s; uma busca que mata o processo é re-arrendada a cada 10 min sem contar tentativa.
- **UI:** contadores do painel e da página podem ficar defasados em corridas raras (empresa criada pelo worker entre duas leituras; `refreshKey` por contagem); depois de ligar a descoberta, `pending` é 0 e o polling só começa após recarregar; paginação por offset dos candidatos pode repetir/pular linhas.
- **API/validação:** surrogate solto em chave de objeto no corpo ainda gera 500 do tratador de 422 (`{_json_safe(k): _json_safe(v)}`); o limite de 50 nomes vale antes da deduplicação, com mensagem em inglês do pydantic; catálogo escrito de memória: tokens errados viram `invalid` na validação; `name` do catálogo sem saneamento (a UI escapa); letras não decompostas por NFKD (ß, ı, ø, ł) geram variantes ruins.
- **Testes:** sem teste para candidato removido entre a reivindicação e o registro; asserção fraca no sufixo Stone.

## Redesign da casca (início, navegação, trilha)
- **Atualização do resumo:** o painel de descoberta em Configurações (`DiscoverySection`) não passa `onSaved`; as ações da entrevista (iniciar, concluir, aceitar sugestão) não chamam `refresh()` (o resumo só atualiza ao navegar).
- **Rótulos:** "Adicionar empresas" quando todas estão pausadas (deveria ser "Reativar empresas"); o botão da entrevista em /onboarding diz sempre "Continuar entrevista" (Início: "Fazer a entrevista", Conta: "Começar entrevista") — compartilhar um helper; "Ativar descoberta" da spec §5 não existe como botão em /sources (o interruptor do painel cobre).
- **/onboarding:** "Confirmar perfil" só no fim do formulário (não no cabeçalho); trilha mostra "Lendo o currículo…" durante `read-error`; foco se perde após o "Enviar currículo" do cabeçalho; a trilha volta ao passo "Revisar/Enviar" ao ver ou reenviar o perfil.
- **/jobs e /sources:** "Avaliar melhores vagas" desabilitado sem explicação; "Filtros" do cabeçalho usa `querySelector(".filters__toggle")` e não tem `aria-controls`; `focusAddForm` depende do primeiro `input`; duas buscas de overview ao ligar a descoberta; estados vazios filtrados sem botão.
- **Casca:** fundo não inerte atrás da gaveta; título da página pisca; literal 1091.98px repetido; botão primário duplicado em Início; "Ver vetorização"; empresas pausadas contadas como automáticas; Stepper estreito em 559.98px vs 560px nos demais blocos.
- **API /overview:** falha de uma seção ainda derruba as outras; ao isolar, usar `begin_nested` (um erro do Postgres aborta a transação).
- **Verificação visual:** 375/768/desktop em claro e escuro feita só pelo implementador com API simulada.

## Ideias futuras (spec própria)
- **Calibração por feedback (marco 4c, a brainstormar depois dos planos 4b/5):** em cada vaga, "recomendação certa / errada" + motivo por tipo (localidade errada, competências não batem, senioridade, salário, empresa/setor, idioma, outro) + campo livre opcional. Base já planejada: `job_labels` (boa/ruim) do 4b e o relatório de precision@10. Hipóteses a decidir: (1) usar o feedback primeiro como **sinal explícito** — pesos `match_weight_*`, filtros sugeridos ("você rejeitou 5 vagas fora da Europa: excluir?") e ajuste do prompt/penalidades por tipo de erro — antes de qualquer treino; (2) treino leve por usuário (regressão logística sobre vec_score, ai_score e atributos) quando houver rótulos suficientes; (3) no SaaS, agregação entre usuários só com consentimento e sem texto pessoal; (4) métrica de acompanhamento: precision@10 antes/depois e taxa de feedback "errada" por tipo. Cuidados: feedback ruidoso, ciclo de realimentação (só ver o que o ranking mostra), nunca enviar texto livre do feedback a terceiros sem aviso.

## Marco 4b (filtros por chat, consulta semântica, teste de qualidade)
- **Regra de negação:** os marcadores `no`/`sem` na consulta de gosto geram falsos positivos ("foco no cliente", "no-code"); um gosto negado vindo do LLM agora é descartado com uma nota, mas o texto digitado pela pessoa ainda é recusado. Considerar tratar só "sem"/"não" ou analisar a frase.
- **Retentativas e custo:** uma interpretação pode custar até 4 requisições ao Groq (2 do `parse_query` × 2 do provedor); o `httpx.AsyncClient` do `/filters/parse` não é fechado; o 422 do Pydantic devolve ao próprio chamador a frase longa demais.
- **Worker `embed_queries`:** o recuo é global (um texto ruim segura todos), reaproveita `embed_batch_size`, embeda todos os perfis e não só o padrão; teste do recuo cobre só o primeiro passo.
- **Ranking:** pesos `inf`/`NaN`/negativos passados direto a `rank_jobs` não são validados (só vêm do `Settings`, que aceita `inf`); `missing_embeddings` com `only_ids` sem teste.
- **API de qualidade:** `url` sem checagem de esquema no servidor (o web só renderiza http/https); `labeled_total` carrega todos os rótulos; testes faltando para o limite de inteiro (> int8), troca de semente no endpoint e vazamento entre usuários na contagem; `_profile_out` lê a linha duas vezes.
- **Tela de vagas:** rollback pode restaurar um estado mais antigo sobre um confirmado quando há falha + sucesso em voo; rascunho inválido do painel é descartado por uma ação externa; mudança de consulta pode ser dada como salva se um salvamento do painel (que não envia `query_text`) a substituir; "não vetorizado" é detectado no web pelo texto "sendo processado" da mensagem (usar uma constante compartilhada ou um código); reabrir o editor do chip após erro não foca e pode reenviar o texto ruim ao perder o foco.
- **Tela de qualidade:** link do 409 só na carga inicial, plural "1 rotuladas", falha silenciosa ao atualizar o relatório, perda de foco depois de rotular, `window.confirm` na "Nova amostra".
- **Migração 0007:** o downgrade remove `unaccent` mesmo se já existia antes.
- **Amostra de qualidade:** deriva do ranking atual e pode se reorganizar quando entram vagas ou notas de IA (decisão registrada).

## Modelo do Groq
- O modelo padrão mudou para `openai/gpt-oss-120b` (o `llama-3.3-70b-versatile` foi removido); o preenchimento de vagas mostra falhas ocasionais `LLMInvalidResponse` com o modelo novo — observar a taxa e, se alta, ajustar o prompt ou usar o `qwen/qwen3.8-27b`.

## Marco 3b — fontes não verificadas
- **Workable:** fora de escopo pela spec §1; a API oficial exige token.
- **Gupy:** fora de escopo pela spec §1; a API oficial exige token.
- **Comeet (verificado em 2026-11-14):** a Careers API pública (`/careers-api/2.0/company/{uid}/positions`) exige `company-uid` e `token` da empresa (documentação oficial em developers.comeet.com); sem lista pública sem credencial. Task condicional pulada.
- **InHire (verificado em 2026-11-14):** a página de vagas pública (`<empresa>.inhire.app/vagas`, ex. priner) é um SPA sem JSON-LD e sem links de vagas no HTML; as vagas vêm de `api.inhire.app` (não documentado; a página de docs `docs.inhire.com.br/api/obter-vagas` devolveu 404). Reavaliar se a InHire publicar uma API pública ou JSON-LD nas páginas de vaga. Task 29 pulada.
- **Bundesagentur (verificado em 2026-11-14):** `GET /jobboerse/jobsuche-service/pc/v4/jobs` com `X-API-Key: jobboerse-jobsuche` devolveu 403 "No match found for request" ao User-Agent do Rumo; não repetido nem contornado. Reavaliar (chave/cabeçalhos exigidos pelo gateway). Task condicional pulada.
