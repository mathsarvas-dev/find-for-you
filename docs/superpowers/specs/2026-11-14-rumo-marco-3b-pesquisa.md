# Pesquisa 3b - fontes de vagas (evidência)

Método: WebFetch/WebSearch em páginas públicas, sem contornar bloqueios. Nota: o WebFetch não expõe o código HTTP em respostas 200; "ok" = corpo recebido. "NÃO VERIFICADO" = não confirmado.

## PARTE A - APIs públicas de ATS

### Lever - ✅
- Doc: https://github.com/lever/postings-api
- Lista: `GET https://api.lever.co/v0/postings/{site}?mode=json` (UE: `api.eu.lever.co`). Detalhe: `/{site}/{postingId}`.
- Params: `skip`, `limit`, `location`, `team`, `department`, `commitment`, `level`, `group`.
- Paginação: skip/limit, sem metadados. Rate limit: só documentado para POST de candidatura (2/s); listagem NÃO VERIFICADO.
- Identificação: slug do site.
- Verificado (site `leverdemo`): corpo JSON = array. Chaves do item: id, text, categories, country, workplaceType, description/descriptionPlain, descriptionBody, additional, lists, opening, salaryRange, createdAt, hostedUrl, applyUrl.

### Ashby - ✅
- Doc: https://developers.ashbyhq.com/docs/public-job-posting-api
- `GET https://api.ashbyhq.com/posting-api/job-board/{board}?includeCompensation=true`. Sem auth.
- Verificado (board `ashby`): chave de topo `jobs` (+ `apiVersion` na doc). Job: id, title, department, team, employmentType, location, secondaryLocations, publishedAt, isListed, isRemote, workplaceType, address, jobUrl, applyUrl, descriptionHtml, descriptionPlain.
- Sem paginação (lista completa; a resposta do board "ashby" é enorme, ~4,3M caracteres). Rate limit NÃO VERIFICADO.

### Greenhouse (já implementado) - ✅
- Doc: https://docs.greenhouse.io/job-board.html (developers.greenhouse.io redireciona 301).
- `GET /v1/boards/{token}/jobs?content=true` (descrição, departamento, escritório). `GET /v1/boards/{token}/jobs/{id}` com `?questions=true` DOCUMENTADO (campos do formulário de candidatura); também `pay_transparency` (faixa salarial). GET sem auth. Rate limit: não mencionado.

### SmartRecruiters - ✅ (verificado)
- Doc: https://developers.smartrecruiters.com/docs/posting-api (a doc cita API key/OAuth para a Posting API, mas a lista pública por empresa respondeu sem credencial).
- `GET https://api.smartrecruiters.com/v1/companies/{companyId}/postings?limit=1` (empresa `smartrecruiters`): chaves de topo offset, limit, totalFound, content. Item: id, name, uuid, jobAdId, defaultJobAd, refNumber, company, releasedDate, location, industry, department, function, typeOfEmployment, experienceLevel, customField, visibility, ref, language. Descrição exige GET do `ref`. Paginação offset/limit; limite máximo NÃO VERIFICADO.

### Workable - ❓/parcial
- A API oficial (help.workable.com) exige token com escopo r_jobs: 🔑.
- Widget público citado por fonte de terceiros (TheirStack): `https://apply.workable.com/api/v1/widget/accounts/{slug}` - minha requisição (conta `workable`) retornou 404 (e `?details=true` também; /api/v3/accounts/.../jobs também 404). NÃO VERIFICADO (pode ser a conta de teste ser inexistente).

### Recruitee - ❓
- Terceiros: `https://{slug}.recruitee.com/api/offers`. Teste com slug `recruitee` = 404. apidocs.recruitee.com não carregou (>10MB). NÃO VERIFICADO.

### Breezy HR - ✅ (verificado, terceiros + teste)
- `https://{company}.breezy.hr/json` retornou array JSON (conta de teste `breezy`). Chaves: id, friendly_id, name, url, published_date, type, location, department, salary, company, locations. Doc oficial NÃO VERIFICADO.

### Personio, Teamtailor, BambooHR, Comeet, Pinpoint, JazzHR - ❓ NÃO VERIFICADO
- personio.de/support/xml-feed retornou 429. Nenhuma fonte consultada confirmou endpoints. Padrões conhecidos de memória (NÃO VERIFICADO nesta sessão): `{slug}.jobs.personio.de/xml`, Teamtailor RSS `/jobs.rss`, BambooHR `/careers/list`.

### Gupy - parcial
- Doc oficial https://developers.gupy.io: API `api.gupy.io/api/v2/jobs`, baseada em token Bearer (🔑), resposta com `results`, `totalResults`, `page`, `totalPages`; rate limit não mencionado.
- `portal.api.gupy.io/api/v1/jobs`: sem doc oficial; só relatos de scrapers terceiros (Apify) dizendo que é público, 10 resultados/página, offset, exige `jobName`. Minha requisição com `?limit=1` retornou 404 (provavelmente por faltar `jobName`; NÃO VERIFICADO). Usar seria API interna não documentada - risco ToS.
- robots.txt de portal.gupy.io: resposta não confiável. NÃO VERIFICADO.

### InHire - parcial
- Docs: https://docs.inhire.com.br/api/obter-vagas (listar vagas) - API para clientes; requisito de autenticação NÃO VERIFICADO (página retornou 404 no fetch direto). Nenhum mecanismo público de listagem em páginas de carreira confirmado.
- robots.txt de www.inhire.com.br: `*` permite tudo, bloqueia ~10 caminhos (login, cadastro, etc.); "Content-Signal" permite treino de IA/busca; Sitemap: https://www.inhire.com.br/sitemap.xml. Sem Crawl-delay.

## PARTE B - Google JobPosting (https://developers.google.com/search/docs/appearance/structured-data/job-posting)
- Obrigatórias: title, description, datePosted, hiringOrganization, jobLocation.
- Recomendadas: applicantLocationRequirements, baseSalary, directApply, employmentType, identifier, jobLocationType, validThrough. Beta: educationRequirements, experienceRequirements.
- description em HTML (p, ul, li, br; outras tags ignoradas mas inofensivas); não pode ser igual ao title.
- validThrough: ISO 8601; omitir se não expira/desconhecido.
- jobLocationType TELECOMMUTE: só 100% remoto; a descrição deve dizer isso. Com ele, applicantLocationRequirements (>=1 país) é requerido para remoto restrito a geografias; sem ele o Google usa o país do jobLocation.
- Uma página de vaga = um JobPosting; nunca em páginas de lista; marcação deve bater com o conteúdo visível; jobLocation e employmentType aceitam arrays.
- Armadilhas (@graph, vários blocos JSON-LD): NÃO consta na página; são práticas de parsing (inferência minha, a tratar no parser: varrer todos os `<script type=application/ld+json>`, desembrulhar @graph/arrays, aceitar @type como lista).

## PARTE C - Portais/agregadores

| Item | Veredito | Evidência |
|---|---|---|
| Vagas.com | ⛔/⚠ | robots.txt `*`: bloqueia /auth/, /v1/, /api/, /users/, /token/, /vagas/pesquisas (busca), /suporte, /mapa-de-carreiras/cargo/; sem bots nomeados, sem Crawl-delay; Sitemap https://www.vagas.com.br/sitemap.xml (urlset com `/vagas/v{id}/{slug}`, categorias por cargo/cidade; lastmod 2023). Página termos-de-uso: não contém cláusula sobre robôs (remete a termos completos não lidos). API oficial: NÃO VERIFICADO. |
| Jobbol | ⛔ parcial | Homepage 403 (relatado); robots.txt (relato do pedido) `*` bloqueia só /assets/php/, /curriculo/, /tmp/, /vendor/. Meu fetch de robots deu "socket hang up"; /sitemap.xml e /termos-de-uso = HTTP 403 (bloqueado). JSON-LD: NÃO VERIFICADO. |
| InHire (portal) | ✅ robots | ver acima |
| Gupy portal | ❓/🔑 | ver acima |
| Catho | ✅ robots permissivo p/ vagas, ⛔ busca | robots `*`: bloqueia /buscar/vagas/, /area-empresa/, /biblioteca, URLs com ?s=, ?q=; Googlebot/GPTBot etc. liberados; LinkedInBot bloqueado; sem Crawl-delay; Sitemap https://www.catho.com.br/sitemap-index.xml. Termos de uso: NÃO VERIFICADO. |
| InfoJobs Brasil | ❓ | robots.txt veio ilegível (binário/comprimido). NÃO VERIFICADO. |
| Indeed | ⛔ | robots.txt: /job/, /jobs/, /viewjob, /api/, /rpc/ bloqueados para `*`; bots como Scrapy, GoogleOther, omgili bloqueados totalmente. Termos: NÃO LIDOS (política esperada = proibido). |
| LinkedIn | ⛔ | robots.txt declara que o uso de robôs/automação sem permissão expressa é proibido; crawling só mediante pedido por e-mail e termos próprios; bot de treino de IA bloqueado. |
| Adzuna | 🔑 | https://developer.adzuna.com: `app_id`+`app_key` obrigatórios (cadastro). Países cobertos (br?), limites do plano gratuito e termos: NÃO VERIFICADO. Chamada sem chave = 400. |
| Jooble | 🔑 | https://jooble.org/api/about: chave obrigatória via formulário de solicitação. Endpoint/países/limites: NÃO VERIFICADO. |
| Careerjet | 🔑 | https://www.careerjet.com/partners/api: chave por site de publisher, Basic auth (chave como usuário), exige `user_ip` e `user_agent` (senão 403); `locale_code` define o país (Brasil não confirmado); page_size 1-100, page 1-10; sem limite de taxa/atribuição declarados; links via jobviewtrack.com. |
| Remotive | ✅ (restrito) | https://remotive.com/api/remote-jobs: sem chave; JSON com chaves `00-warning`, `0-legal-notice`, `job-count`, `total-job-count`, `jobs`; job: id, url, title, company_name, company_logo(_url), category, tags, job_type, publication_date, candidate_required_location, salary, description. Termos (paráfrase): linkar de volta e creditar Remotive; não republicar em Jooble/Google Jobs/LinkedIn; vagas com atraso de 24h; sugerido <= ~4 requisições/dia; proíbe coletar e-mails/cadastros; API privada paga existe. |
| Arbeitnow | ✅ | https://www.arbeitnow.com/api/job-board-api: sem chave; chave de topo `data`; job: slug, company_name, title, description, remote, url, tags, job_types, location, created_at. Foco Alemanha/Europa. Termos/limites: NÃO VERIFICADO. |
| RemoteOK | ✅ (com atribuição) | https://remoteok.com/api: array; 1o elemento = `last_updated`+`legal`; job: slug, id, epoch, date, company, company_logo, position, tags, description, location, apply_url, salary_min, salary_max, logo, url. Termos (paráfrase): link seguível de volta e crédito; acesso pode ser suspenso; logo é marca registrada. |
| Himalayas | ✅/🔑 opcional | https://himalayas.app/api: JSON, sem chave (chave grátis Bearer eleva limites: 60 req/min e 10.000/dia; sem chave limites menores); cursor, até 20 por página; endpoint por guid (200/410/404); termos: usar applicationLink intacto, link visível para Himalayas, só exibir em sites próprios, não repassar a agregadores/Google Jobs/LinkedIn, remover vagas expiradas; sem CORS (usar backend); dados atualizados diariamente. |
| We Work Remotely RSS | ⛔ (no teste) | /remote-job-rss-feed = HTTP 403 ao fetch automatizado; registrado como bloqueado. |
| Bundesagentur (Jobsuche) | ✅ | https://github.com/bundesAPI/jobsuche-api: base `https://rest.arbeitsagentur.de/jobboerse/jobsuche-service`, busca `/pc/v4/jobs` (README também cita v6), detalhe `/pc/v4/jobdetails/{base64(refnr)}`; header `X-API-Key: jobboerse-jobsuche` (chave pública publicada no README); params was, wo, umkreis, angebotsart, befristung, arbeitszeit, veroeffentlichtseit, page (de 1), size. Alemanha apenas. Termos/limites: NÃO VERIFICADO (site oficial do bund.dev com certificado expirado no fetch). |
| France Travail | 🔑 | https://francetravail.io: OAuth2 client credentials com cadastro (pelo título/tópicos da página); cotas/termos NÃO VERIFICADO. |
| EURES | ❓ | robots.txt de eures.europa.eu: `*` bloqueia /admin/, /search/, /user/*; Sitemap https://eures.europa.eu/sitemap.xml. API pública de vagas: NÃO VERIFICADO. |

## Observações
- Fontes de terceiros (TheirStack, Apify) usadas só como pista, marcadas como tal.
- Nenhum bloqueio foi contornado; 403 registrados: Jobbol (sitemap, termos), WeWorkRemotely (página do RSS).
