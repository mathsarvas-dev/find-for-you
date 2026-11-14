# Rumo — Marco 3b: mais fontes de vagas e funil de aderência

Complementa `2026-11-11-rumo-design.md` (§5.2 coleta, §2 fora de escopo) e `2026-11-13-rumo-coleta-automatica-design.md`. Hoje só o Greenhouse é coletado e a descoberta propõe tokens de empresas conhecidas. Este marco amplia **onde** se procura (ATS, páginas de carreira sem ATS, agregadores e portais) e muda **o que se guarda e processa** (só vagas aderentes).

## 1. Objetivo e critério de sucesso
Com o sistema instalado, a pessoa vê vagas de empresas de todos os portes e países vindas de várias fontes, sem lista gigante de vagas irrelevantes e sem estourar a cota do LLM. Pronto quando:
- além do Greenhouse há conectores de ATS verificados, um leitor genérico de páginas de carreira e feeds de agregadores;
- cada portal só é coletado se o **portão de conformidade** permitir, e a coleta recusa em código o que o portão ou o `robots.txt` proíbem;
- vagas que não passam nos filtros duros ou ficam abaixo do corte de similaridade **não gastam descrição guardada, vetor nem LLM**;
- a mesma vaga vinda por ATS e por agregador aparece uma vez, preferindo a fonte direta;
- a pessoa pode colar o link de uma vaga qualquer e importá-la.

Fora de escopo: portais que exigem login, LinkedIn/Indeed/Seek (proibidos), raspar páginas de resultado de busca de portais, captcha, proxies ou qualquer forma de contornar bloqueio (**o sistema nunca evita um 403/anti-robô: registra e para**), Workable e Gupy pela API interna não documentada (ver §3), SaaS/multiusuário.

## 2. Evidências (pesquisa de 2026-11-14; só o que foi verificado)
- ✅ **Lever** `api.lever.co/v0/postings/{site}?mode=json` (UE: `api.eu.lever.co`), JSON, paginação `skip`/`limit`; **Ashby** `api.ashbyhq.com/posting-api/job-board/{board}?includeCompensation=true`, chave `jobs`, sem paginação; **SmartRecruiters** `api.smartrecruiters.com/v1/companies/{id}/postings`, `offset/limit/totalFound/content`, sem credencial; **Breezy** `{co}.breezy.hr/json`, array JSON. Os quatro responderam sem autenticação.
- 🔑/❓ **Workable** (API oficial exige token), **Gupy** (API oficial exige token Bearer; a API do portal é interna, sem documentação), **Recruitee, Personio, Teamtailor, BambooHR, Comeet, Pinpoint, JazzHR**: formato público **não verificado**. Cada um só entra depois de verificado com uma resposta real salva como fixture (§9, tarefa de verificação).
- ❓ **InHire**: existe API para clientes (`docs.inhire.com.br`); lista pública e autenticação não confirmadas; `robots.txt` permissivo e com sitemap. Entra pelo leitor genérico (§4) até haver conector verificado.
- ✅ **Google JobPosting (JSON-LD)**: obrigatórios `title, description, datePosted, hiringOrganization, jobLocation`; recomendados `validThrough, employmentType, identifier, baseSalary, directApply, jobLocationType, applicantLocationRequirements`; um JobPosting por página de vaga; descrição em HTML.
- **Agregadores:** 🔑 Adzuna (app_id+app_key), Jooble (chave), Careerjet (chave de publisher, exige `user_ip`/`user_agent`); ✅ sem chave: Remotive (termos pedem link de volta e dão atraso de 24 h e cerca de 4 req/dia), RemoteOK (link e crédito), Himalayas (link de origem), Arbeitnow; ✅ Bundesagentur (só Alemanha, chave pública do projeto bund.dev). Termos de Arbeitnow e Bundesagentur: não verificados.
- **Portais:** ⛔ Indeed e LinkedIn (proíbem); ⛔ Jobbol (páginas e sitemap deram 403; `robots.txt` bloqueia só 4 caminhos para `*` e bloqueia rastreadores de treino de IA); ⚠ Vagas.com (`robots.txt` bloqueia `/api/`, `/v1/`, `/users/` e a busca `/vagas/pesquisas`; sitemap público com páginas `/vagas/v{id}/{slug}`; a página de termos lida não cita robôs; JSON-LD e API oficial não verificados); ⚠ Catho (bloqueia `/buscar/vagas/`; sitemap público; termos não verificados); ❓ InfoJobs BR (`robots.txt` ilegível); ⛔ We Work Remotely (403).

## 3. Decisões
- **Portão de conformidade por portal.** Arquivo versionado `rumo_core/sources/portals/policy.json` com, por portal: `status` (`allowed` = API/feed documentado ou robots+termos revisados; `conditional` = robots permite mas os termos não foram totalmente verificados; `blocked` = robots proíbe, 403 ou termos proíbem), `checked_at`, `evidence` (URLs e resumo), `paths_allowed`, `min_interval_seconds`, `daily_cap`. `conditional` vem **desligado**: só roda se a pessoa ligar em Configurações, com aviso do que não foi verificado e do risco. `blocked` só admite **importar por link**. O veredito é apoio à decisão, não parecer jurídico.
- **Conformidade em código:** todo acesso a site de terceiros passa por um cliente seguro (§4) que consulta e guarda em cache (24 h) o `robots.txt` do host, recusa caminhos proibidos para o nosso agente, respeita `Crawl-delay`/`Retry-After`, aplica intervalo e teto diário por host, e ao receber 403/429 repetido marca o portal como `blocked_by_site` e suspende (sem repetir, sem contornar).
- **Agente identificado e honesto:** `User-Agent: RumoJobSearch/<versão> (uso pessoal)`; sem e-mail nem dado pessoal.
- **Chaves de agregadores** (Adzuna, Jooble, Careerjet) ficam no `.env` (`ADZUNA_APP_ID`/`ADZUNA_APP_KEY`, `JOOBLE_KEY`, `CAREERJET_KEY`), nunca no banco nem no repositório; a UI só mostra "configurada/não configurada".
- **Fonte direta vence:** `source_rank` (ATS = 0, página de carreira = 1, agregador/portal = 2). Em colisão de `fingerprint`, a vaga de menor rank fica `open` e a outra `duplicate`.
- **Funil de aderência** (§6) é o modo padrão de coleta para empresas automáticas, páginas de carreira e feeds; o modo "tudo" é opção por empresa.

## 4. Cliente seguro e leitor genérico
- `rumo_core/sources/http.py`: `SafeClient` — somente `http(s)`; **bloqueia IPs privados, loopback, link-local e metadados de nuvem** (resolve o nome e confere o IP conectado; vale também para redirecionamentos, que só são seguidos dentro do mesmo domínio registrável e até 3 saltos); limite de tamanho (5 MB), de tempo (20 s) e de tipos de conteúdo (HTML/JSON/XML/texto); robots, intervalo por host, teto diário e `Retry-After` do §3. As URLs digitadas pela pessoa ou lidas de páginas são **dados não confiáveis**.
- `rumo_core/sources/jsonld.py`: `extract_job_postings(html) -> list[RawJob]` — lê todos os blocos `application/ld+json` (incl. `@graph` e listas), aceita `JobPosting`, converte descrição HTML por `html_to_text`, `jobLocation`/`jobLocationType`/`applicantLocationRequirements` → `location_text`/modelo de trabalho, `baseSalary`, `datePosted`; tolera campos ausentes; ignora o que não for `JobPosting`.
- `rumo_core/sources/careers.py` (fonte `site`): recebe a URL da página de carreira; (1) procura JSON-LD na própria página; (2) **detecta ATS embutido** (hosts de `iframe/script/link/href` como greenhouse, lever, ashby, smartrecruiters, breezy…) e converte a empresa para o conector direto; (3) se for página de lista, segue links de vagas (mesmo domínio, até 200 páginas, usando o sitemap quando houver) e lê o JSON-LD de cada uma; (4) sem JSON-LD, não inventa: marca a empresa "sem dados estruturados" e sugere "importar por link". `Company.source = 'site'`, `identifier` = URL canônica.
- **Importar por link** (`POST /jobs/import-link {url}`): o mesmo leitor para uma URL só; cria a vaga com `origin='link'` e `source_rank` 1; sem JSON-LD aceita texto colado (título + descrição) escrito pela pessoa. Funciona para qualquer portal, inclusive os `blocked`, porque é uma ação manual de um humano sobre uma página que ele abriu.

## 5. Conectores e feeds
- **ATS verificados (primeira leva):** `lever`, `ashby`, `smartrecruiters`, `breezy` (mesmo protocolo `Source` do Greenhouse; `identifier` = slug/id; normalização reaproveita `normalize_job`). Os demais da lista de ❓ entram em tarefas separadas **somente** depois de verificados.
- **Feeds de agregadores** (`job_feeds`: `source`, `params` JSONB — palavras-chave e país vindos do perfil de busca e do perfil —, `active`, `last_run_at`, `next_run_at`, erros): conectores `remotive`, `remoteok`, `himalayas`, `arbeitnow` (sem chave); `adzuna`, `jooble`, `careerjet` (com chave); `bundesagentur` (Alemanha). Respeitam os termos de uso de cada um (link de volta e crédito na UI; Remotive: atraso e teto de requisições; nada é republicado).
- **Empregador da vaga de feed:** vira uma `Company` sintética (`origin='feed'`, `source` = nome do feed, `identifier` = nome normalizado, `active=false` — não é coletada diretamente) para a vaga ter dono. O nome (e a URL do empregador, se vier) vira **candidato de descoberta** (`origin_kind='feed'`) para tentar achar o ATS ou a página de carreira dele: assim empresas pequenas viram coleta direta.
- **Descoberta ampliada:** o catálogo passa a ter arquivos por ATS e por país (`catalog/<ats>.json`), a normalização de nomes gera tokens por ATS, e `validate_candidate` usa o conector do ATS em questão; um candidato só vale com ≥ 1 vaga aberta **que passe nos filtros duros** (§6, etapa 1).

## 6. Funil de aderência
Etapas da mais barata à mais cara; cada uma só vê o que a anterior deixou passar.
1. **Filtros duros sem IA, na entrada:** a vaga normalizada é testada contra os filtros salvos do perfil de busca padrão (país, modelo, senioridade, idioma, departamento, exclusões, salário quando conhecido; desconhecido passa se `include_unknown`). Quem **não passa** vira **registro mínimo** (`jobs.intake = 'stub'`: título, url, hash, `stub_reason`; descrição vazia, sem vetor e sem preenchimento por LLM). Quem passa é `intake = 'full'`.
2. **Corte vetorial opcional:** vagas `full` já vetorizadas com `vec_score` abaixo de `adherence_vec_floor` ficam `intake = 'gated'` (mantêm o vetor, **não** recebem preenchimento por LLM nem avaliação). O corte é `None` (desligado) por padrão; `suggest_floor(session, user_id)` (em `quality.py`, 4b) sugere o maior valor que ainda mantém ≥ 95% das vagas rotuladas "boas"; a UI mostra a sugestão e a pessoa ativa.
3. **LLM só para o que importa:** o preenchimento (`fill_missing_job_fields`) e a avaliação (`score_matches`) passam a considerar apenas `intake = 'full'`.
4. **Reavaliação:** mudar os filtros do perfil de busca marca as empresas com stubs para recoleta (`next_run_at = now()`), pois a recoleta já baixa o quadro inteiro; stubs promovidos a `full` entram no funil de novo. Um stub nunca é mostrado na lista, mas conta em "vagas fora dos seus filtros: N" na tela de Fontes.
- **Modo "tudo":** por empresa, `collect_mode = 'all'` desliga as etapas 1–2 (útil para empresas-alvo).
- O funil economiza LLM mas pode perder vagas por um corte mal calibrado; por isso o corte vetorial começa desligado e é calibrado com os rótulos do 4b e, depois, o feedback do 4c.

## 7. Dados (migração 0008; o plano do marco 5 passa a usar 0009)
- `companies`: `collect_mode str(8) default 'funnel' ('funnel'|'all')`; `origin` aceita também `'feed'` e `'link'` (CHECK atualizado); `source` aceita os novos nomes.
- `jobs`: `intake str(8) default 'full' ('full'|'stub'|'gated')` + `stub_reason str(32) NULL`, `source_rank smallint default 0`, `origin str(8) default 'ats'` (`'ats'|'site'|'feed'|'link'`).
- `job_feeds` (UserScopedMixin): `id, source, params JSONB, active, last_run_at, next_run_at, last_error, consecutive_failures, jobs_seen int`; único `(user_id, source, md5(params))`.
- `portal_state` (UserScopedMixin): `portal str, enabled bool, blocked_by_site bool, blocked_at, last_check_at` (o veredito do arquivo `policy.json` + a escolha da pessoa).
- `discovery_candidates.origin_kind` aceita `'feed'`.
- Índices em `jobs (user_id, intake, status)` para as consultas do funil.

## 8. Componentes
- `rumo_core/sources/`: `http.py` (SafeClient + política), `jsonld.py`, `careers.py`, `lever.py`, `ashby.py`, `smartrecruiters.py`, `breezy.py`, `feeds/` (um módulo por agregador, mesmo padrão), `portals/policy.json` + `portals/gate.py` (`check(portal, url) -> Verdict`).
- `rumo_core/sources/funnel.py`: `hard_filter(raw/normalized, filters) -> (bool, reason)` (reusa `apply_filters` em Python com a mesma semântica do SQL — testes de paridade) e as transições `full/stub/gated`.
- Worker: `collect_companies` passa a chamar o funil; `collect_feeds` (novo, mesma fila e justiça com a entrevista); `discover_companies` ganha o ATS e o candidato `feed`; `fill_missing_job_fields`/`score_matches` ignoram `stub/gated`.
- API: `GET/POST/DELETE /feeds`, `GET /portals` e `PUT /portals/{id}` (ligar/desligar `conditional`), `POST /jobs/import-link`, `POST /companies` aceita `source` e URL de carreira, `GET /funnel` (contagens `full/stub/gated`, sugestão de corte), `PUT /funnel` (corte vetorial). Tudo escopado por `user_id`.
- Web: `/sources` ganha abas **Empresas · Feeds · Portais · Importar por link**; Portais mostra o status do portão (com evidência e data) e o interruptor dos `conditional` com aviso; "Fontes" na trilha conta feeds ativos; contadores "N vagas fora dos seus filtros"; textos em PT-BR, estado nunca só por cor.

## 9. Fatias do plano (cada uma testável sozinha)
0. **Verificação de ❓ + fixtures** (Recruitee, Personio, Teamtailor, BambooHR, InHire…); o que não passar sai do escopo e vai ao backlog.
1. Cliente seguro (SSRF, robots, limites) + portão.
2. Conectores de ATS verificados.
3. JSON-LD + leitor de páginas de carreira + importar por link.
4. Feeds de agregadores + empregadores sintéticos + descoberta ampliada.
5. Funil de aderência (migração, intake, corte, reordenação do LLM, reavaliação).
6. Web (abas, Portais, funil) e `/overview`.

## 10. Erros e resiliência
- Falha de uma fonte nunca derruba as outras; política atual de falhas e backoff por empresa/feed.
- 403/429 repetidos de um host: `blocked_by_site`, suspensão com aviso na UI, sem tentativa de contorno.
- `robots.txt` inacessível: trata-se como "não permitido" (não coleta) para `conditional`; para APIs oficiais documentadas segue a política da API.
- Resposta enorme, tipo inesperado, redirecionamento para outro domínio ou IP privado: erro `SourceBadResponse`, nada gravado.
- Texto de vaga e de página sempre tratado como dado; nada vira HTML sem escape; instruções dentro de vagas não valem.

## 11. Testes
- `SafeClient`: IP privado/loopback/metadados (inclusive via DNS e redirecionamento), limite de tamanho/tempo/tipo, `robots.txt` (permitido/proibido/ausente/ilegível), `Crawl-delay`, 403/429 → `blocked_by_site`.
- Conectores: fixtures reais salvas (sem internet nos testes), paginação, campos ausentes, ids duplicados, HTML hostil.
- JSON-LD: `@graph`, vários blocos, lista, tipos mistos, `TELECOMMUTE`, salário, datas, descrição com HTML/scripts.
- Leitor de carreira: detecção de ATS embutido, limite de 200 páginas, sem JSON-LD → mensagem, SSRF.
- Funil: paridade entre `hard_filter` (Python) e `apply_filters` (SQL) em tabelas de casos; stubs sem descrição/vetor/LLM; corte vetorial; `suggest_floor` com rótulos; recoleta promove stubs; modo `all`.
- Dedupe por `source_rank`; feeds com empregador sintético e candidato de descoberta; portões (`blocked/conditional/allowed`), interruptores.
- API (escopo por usuário, limites) e web (abas, avisos do portão, importar por link) com verificação visual em 375, 768 e desktop, claro e escuro.

## 12. Riscos
- **Termos de uso e bloqueio:** o portão reduz mas não elimina o risco dos `conditional`; ficam desligados por padrão, com evidência e aviso, e a conformidade é imposta em código.
- **Verificação incompleta:** vários ATS e portais estão ❓; a fatia 0 decide o que entra, e nada entra por suposição.
- **SSRF e conteúdo hostil** (URLs que a pessoa digita ou que vêm de páginas): cliente seguro e testes dedicados; é o item de segurança mais crítico do marco.
- **Corte perde vagas boas:** começa desligado, calibrado por rótulos; stubs permitem reavaliar sem perda.
- **Volume:** muitas fontes aumentam coleta e embeddings; tetos por host/dia, filas justas com a entrevista e funil limitam o custo.
- **Conflito de migração com o marco 5:** 3b usa a 0008; o plano do 5 será renumerado (0009) antes de executar.
