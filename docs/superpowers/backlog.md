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
