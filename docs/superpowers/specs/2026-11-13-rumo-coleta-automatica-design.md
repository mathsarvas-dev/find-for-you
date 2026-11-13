# Rumo — Coleta automática de empresas (descoberta)

Complementa `2026-11-11-rumo-design.md` §5.2 (coleta) e o marco 3. Hoje o worker recoleta a cada 6 h só as empresas adicionadas à mão em `/sources`. Esta peça faz o sistema **propor, validar e adicionar** empresas sozinho, sem varrer a internet.

## 1. Objetivo e critério de sucesso
Com o sistema recém-instalado, sem nenhum clique em `/sources`, vagas passam a aparecer em `/jobs` depois que o perfil é confirmado (ou, para o catálogo, logo após o worker subir). A pessoa pode pausar qualquer empresa e desligar a descoberta; o que ela pausa nunca volta sozinho.

Fora de escopo: outras fontes (Lever, Ashby, Gupy, Bundesagentur → marco 3b), busca em buscadores/web scraping, descobrir por palavra-chave de vaga, remover (apagar) empresa — pausar basta.

## 2. Decisões
- **Greenhouse não tem diretório público.** Existência de um quadro só se confirma pedindo `GET /v1/boards/{token}/jobs` (já feito pelo conector `rumo_core/sources/greenhouse.py`). Descobrir = propor tokens candidatos e validar cada um por esse mesmo conector.
- **Três origens de candidatos:** `catalog` (arquivo versionado, ~150 tokens de empresas amplamente conhecidas por usar Greenhouse; o catálogo só contém tokens que o autor confia, e o job descarta os inválidos), `profile` (nomes de empresas das experiências do perfil confirmado → tokens por normalização), `target` (campo opcional "empresas-alvo" informado pela pessoa, nomes → tokens).
- **Normalização nome → tokens** (função pura): minúsculas, sem acentos, remove sufixos jurídicos (inc, llc, ltd, gmbh, ag, sa, ltda, corp, co, plc), gera até 3 variantes (`acmecorp`, `acme`, `acme-corp`), só `[a-z0-9_-]`, comprimento 2–64.
- **Validação:** um candidato vale só se a API responde 200 com ao menos 1 vaga aberta. 404 → `invalid`. Quadro com 0 vagas → `invalid` (reavaliável após 30 dias). Erros transitórios (429/5xx/timeout) não consomem tentativa definitiva (até 3, com backoff).
- **Entrada automática:** candidato válido vira `Company` com `origin='auto'` (nome = o devolvido pela fonte quando houver, senão o token), `active=true`, `next_run_at=now()` para coleta imediata. Teto de **empresas automáticas ativas** configurável (`discovery_max_auto_companies`, padrão 100); ao atingir, a descoberta para.
- **A pessoa manda:** `Company` pausada (`active=false`) nunca é reativada pela descoberta; um candidato cujo token já existe como empresa (qualquer origem/estado) é marcado `known` e nunca recriado.
- **Privacidade:** a única coisa que sai para a rede é o token derivado (nunca texto livre nem dado pessoal além do nome da empresa já contido no token). Registros de log não contêm nomes de empresas do perfil.
- **Justiça com a entrevista:** o job de descoberta roda no mesmo loop sequencial, **depois** de `collect_companies`, com no máximo `discovery_batch=5` validações por tick de 60 s, pausa mínima entre requisições igual a `collect_delay_seconds`, limite de tempo por tick (`discovery_max_seconds`, padrão 20) e sem chamar o LLM.

## 3. Dados (migração 0006)
- `companies`: nova coluna `origin str(16) NOT NULL DEFAULT 'manual'` ('manual'|'auto'), CHECK nos dois valores.
- `discovery_candidates` (UserScopedMixin): `id, source str(32), token str(64), name_hint str|NULL, origin_kind str(16) ('catalog'|'profile'|'target'), state str(16) ('pending'|'valid'|'invalid'|'known'|'rejected'), attempts int default 0, checked_at timestamptz NULL, retry_after timestamptz NULL, last_error text NULL, company_id FK companies ON DELETE SET NULL NULL, created/updated`. Único `(user_id, source, token)`; índice `(user_id, state, retry_after)`.
- `user_preferences` (PK `user_id`): `discovery_enabled bool default true`, `target_companies JSONB default []` (≤ 50 nomes de ≤ 80 caracteres), updated_at.

## 4. Componentes
- `rumo_core/discovery/names.py`: `token_variants(name) -> list[str]` (puro, testável).
- `rumo_core/discovery/catalog.py` + `rumo_core/discovery/catalog/greenhouse.json`: carrega e valida o arquivo (lista de `{token, name}`); `seed_candidates(session, user_id)` insere os candidatos `catalog` ausentes (idempotente).
- `rumo_core/discovery/candidates.py`: `refresh_candidates(session, user_id)` = catálogo + nomes do perfil atual (`get_current_profile`) + `target_companies`; mantém um único candidato por token; marca `known` quando já existe `Company`.
- `rumo_core/discovery/validate.py`: `async validate_candidate(source, candidate) -> Outcome` usando `get_source("greenhouse")` e reaproveitando os erros `SourceNotFound/SourceUnavailable/SourceBadResponse`; sem seguir redirects (herdado).
- `worker/discover_companies.py`: `discover_companies(session_factory, *, batch, max_seconds, max_auto, delay, user_id=None, clock, sleep)`: no padrão de `collect_companies` (seleção com `FOR UPDATE SKIP LOCKED`, commit antes da chamada de rede, `asyncio.to_thread` só onde houver trabalho bloqueante, injeção de relógio/sleep). Respeita `discovery_enabled`, o teto de empresas automáticas e 3 tentativas.
- API `api/discovery.py`: `GET /discovery` → `{enabled, target_companies, auto_companies: n, max_auto, pending: n, found: n}`; `PUT /discovery` (enabled, target_companies; validação de tamanho/caracteres); `GET /discovery/candidates?state=` (paginado, para depuração na UI). Tudo escopado por `user_id`.
- Frontend `/sources`: bloco "Descoberta automática" (interruptor, campo de empresas-alvo, contadores "N empresas encontradas, M em validação"), selo "automática" em `CompanyRow`, filtro "origem" na lista de empresas. Texto em PT-BR, específico e curto; sem dado falso óbvio.

## 5. Riscos e tratamento
- Token candidato colide com uma empresa homônima errada: mitigado por exibir o nome devolvido pela fonte, por pausar com um clique e por exigir ≥ 1 vaga aberta; a pessoa vê a origem `auto`.
- Excesso de vagas irrelevantes: o ranking do marco 4 e os filtros resolvem; o teto limita o volume; vagas de empresas pausadas ficam fora das listas ativas.
- Rate limit do Greenhouse (público, sem chave): `discovery_batch` e pausa mínima; 429 dobra o backoff do candidato.
- Catálogo envelhece: tokens que passam a dar 404 viram `invalid` e a coleta da empresa já adicionada segue a política de falhas existente.
- Nenhuma chamada ao LLM e nenhuma consulta a terceiros além do endpoint público do Greenhouse.
