# Rumo — Marco 5: candidatura (currículo adaptado, perguntas, PDF)

Implementa `2026-11-11-rumo-design.md` §5.5 por inteiro, sobre o perfil canônico (marco 2), as vagas (marco 3) e o ranking (marco 4a). Herda a casca do redesign (`2026-11-13-rumo-redesign-shell-design.md`). **O envio da candidatura é sempre humano; o LLM nunca inventa fatos sobre a pessoa.**

## 1. Objetivo e critério de sucesso
De uma vaga em `/jobs`, clicar **"Preparar candidatura"** e chegar a um **kit pronto**: currículo no idioma da vaga e no formato do país (PDF + texto puro), cobertura de requisitos ("14 de 18"), carta opcional, respostas das perguntas extras e o link da vaga; depois clicar **"Apliquei"**. Pronto quando:
- toda linha escrita pela IA aponta o item do perfil de origem e passa no validador anti-invenção; o que não passa nunca é salvo como válido nem vai ao PDF;
- o que falta no perfil vira uma pergunta a você ("Tenho isso" → entra no perfil como nova versão; "Não tenho" → só aviso);
- perguntas pessoais, legais ou de remuneração nunca são respondidas pela IA;
- o PDF abre com acentos e ß corretos e tem o formato do país (§5).

Fora de escopo: foto e data de nascimento no currículo alemão (o modelo tem só texto), outros países além de EUA/Alemanha/Austrália/Brasil (os demais usam o modelo dos EUA no idioma da vaga), outros idiomas além de pt/en/de, envio automático, editor de layout, o kanban e os lembretes (marco 6), o otimizador de LinkedIn (§5.7).

## 2. Decisões
- **Fatos copiados, texto escrito.** Empresa, cargo, datas, instituição, grau, certificados, idiomas e contatos são **copiados do perfil por código**, nunca gerados. O LLM só escreve (a) o resumo, (b) a redação dos marcadores de cada experiência/projeto e (c) a escolha e a ordem das habilidades (apenas nomes que existem no perfil). Isso reduz a superfície de invenção ao texto livre.
- **Linha = texto + origem.** Cada linha gerada é `{id, section, text, source_refs: [id do perfil…]}`; `source_refs` não pode ser vazio e cada id precisa existir na versão do perfil usada.
- **Validador anti-invenção (peça crítica).** Reaproveita os auxiliares do `ai_score.py` e acrescenta regras de documento. Uma linha é rejeitada quando: (1) tem `source_ref` ausente ou inexistente; (2) contém número, data, porcentagem ou valor que não aparece nos itens citados; (3) contém token técnico (maiúscula no meio, dígito, `+`, `#`, `.`), nome próprio ou sigla que não está no perfil completo nem na vaga; (4) contém e-mail, URL ou telefone que não são do perfil; (5) afirma anos de experiência ou nível que o perfil não traz. Palavras comuns são livres (permite traduzir e reformular). A comparação ignora acento e caixa. Falha: 1 retentativa devolvendo a lista de violações ao LLM; se falhar de novo, a candidatura fica em `error`, nada é salvo como válido.
- **Vaga é lida uma vez.** `extract_requirements` guarda `{must: [...], nice: [...], keywords: [...]}` (cada item ≤ 200 caracteres, até 25 no total); o texto da vaga é tratado como dado não confiável (instruções dentro dele não valem) e cortado em 6000 caracteres.
- **Seleção vetorial:** para cada requisito, os 2 itens do perfil mais próximos (os vetores de `profile_chunks` do marco 2 contra o embedding do requisito, feito pelo worker) entram como evidência no prompt de redação. Só o necessário vai ao LLM (privacidade e custo).
- **Cobertura:** um requisito é "coberto" quando pelo menos uma linha válida o cita em `covers: [req_id]` (o LLM declara; o validador confere que a linha tem o termo do requisito ou um sinônimo do perfil) — senão é "não encontrei no seu perfil". "n de N" conta obrigatórios e desejáveis separados.
- **Lacunas viram perguntas, não texto.** "Não encontrei" tem dois botões: **"Tenho isso"** (campo curto; ao confirmar, vira um item no perfil — habilidade ou nota — via `confirm_profile`, que cria nova versão e re-vetoriza só o que mudou; a candidatura é regenerada) e **"Não tenho"** (marcado como lacuna, só aviso).
- **Idioma e formato:** idioma = `job.language` (`unknown` → inglês); modelo por país da vaga: EUA→`us` (1–2 páginas, sem foto), Alemanha/Áustria/Suíça→`de` (Lebenslauf tabular; Anschreiben quando houver carta), Austrália→`au` (2–3 páginas, seção *referees* vazia por padrão, preenchida só se você digitar), Brasil→`br` (livre), outro→`us`. A pessoa pode trocar o modelo na revisão.
- **PDF por HTML + WeasyPrint** (Jinja2 + CSS por modelo, fonte embutida com acentos/ß). Nada binário vai ao banco: o PDF é gerado sob demanda a partir das linhas aprovadas, de forma determinística. A imagem do worker/API instala Pango/HarfBuzz e uma fonte; o plano confirma o build antes das demais tarefas de PDF. Versão texto puro gerada das mesmas linhas.
- **Perguntas extras:** Greenhouse devolve as perguntas em `GET /v1/boards/{token}/jobs/{id}?questions=true` (uma chamada, só quando a pessoa abre a candidatura; fonte `api`); as demais fontes: a pessoa cola o texto e cada parágrafo em branco separado vira uma pergunta (sem LLM; fonte `pasted`). Rascunho de resposta:
  - **banco de respostas** (`answer_bank`, com idioma): o LLM recebe até 50 itens e diz se algum se aplica (`bank_key` ou nada); ao reutilizar, só adapta idioma e formatação; **valores e moedas não são convertidos** (número e moeda precisam ser os do banco);
  - **perguntas abertas** ("por que nossa empresa?"): rascunho baseado no perfil + no que a vaga diz da empresa, com as mesmas linhas/validador;
  - **perguntas de categoria sensível** (autorização de trabalho, visto, pretensão salarial, deficiência, gênero, raça, antecedentes, data de nascimento, relocação): **nunca rascunhadas pela IA**; mostram "responda você" e, se existir, a sugestão do banco. A classificação é por lista de palavras-chave versionada e conservadora (na dúvida, trata como sensível).
  - respostas finais podem ser salvas no banco ("Salvar no banco").
- **Carta (opcional, botão "Gerar carta"):** mesmas linhas com origem e o mesmo validador; nome da empresa e cargo vêm da vaga; no modelo `de` vira *Anschreiben*.
- **Teto e justiça:** gerar custa 3–5 chamadas ao Groq. Limite diário configurável `application_daily_limit` (padrão 5 candidaturas geradas/dia), pausa enquanto a entrevista estiver gerando (mesmo portão do `score_matches`), timeout de 25 s por chamada, um job do worker (`prepare_applications`) com 1 candidatura por vez.
- **Privacidade:** ao Groq vão o texto da vaga, os itens relevantes do perfil e (nas perguntas) o enunciado; nunca o e-mail, o telefone ou a lista completa de pessoas/empresas do perfil além do que o item citado contém. Nenhuma ação sai do computador: o app nunca abre, preenche ou envia o formulário da empresa.
- **Perfil mudou depois de gerar:** a candidatura guarda `profile_version`; se a versão atual for outra, a tela mostra "o perfil mudou desde esta versão" com o botão "Gerar de novo" (não regenera sozinha).

## 3. Dados (migração 0009, depois da 0007 do 4b e da 0008 do 3b)
- `applications` (UserScopedMixin + TimestampMixin): `id, job_id FK jobs ON DELETE CASCADE, profile_version int, job_content_hash str(64), state str(16) ('draft'|'generating'|'ready'|'error'|'applied'), locale str(8), template str(8), requirements JSONB, coverage JSONB, error text NULL, attempts int default 0, applied_at timestamptz NULL`. Único `(user_id, job_id)`; índice `(user_id, state)`.
- `application_documents`: `id, application_id FK ON DELETE CASCADE, kind str(16) ('resume'|'cover_letter'), lines JSONB, status str(16) ('valid'|'needs_fix'), version int` (a nova geração substitui o documento do mesmo `kind`; `needs_fix` quando uma edição manual falha no validador).
- `application_questions`: `id, application_id FK ON DELETE CASCADE, position int, source str(8) ('api'|'pasted'), label text, kind str(16) ('text'|'choice'|'bool'|'file'), options JSONB, required bool, sensitive bool, answer text NULL, answer_state str(8) ('empty'|'draft'|'edited'|'final'), bank_key str(64) NULL`.
- `answer_bank`: nova coluna `lang str(8) NOT NULL DEFAULT 'pt'` (os itens existentes seguem válidos).
- `/overview` ganha `applications: {total, ready, applied}`; o passo 7 da trilha ("Candidatar-se") deixa de ser "em breve": atual quando há ≥ 1 vaga avaliada e nenhuma candidatura `ready/applied`; concluído com ≥ 1 `applied`. Sidebar ganha o item **"Candidaturas"** (lista simples).

## 4. Componentes
- `rumo_core/applications/`: `requirements.py` (`extract_requirements`), `select.py` (evidências por requisito), `draft.py` (`draft_resume`, `draft_letter`; copia fatos, pede texto), `validator.py` (`validate_lines`, o §2), `coverage.py`, `questions.py` (`parse_pasted`, `fetch_greenhouse_questions`, `is_sensitive`, `draft_answer`), `render.py` (HTML por modelo, texto puro, `render_pdf`), `templates/{us,de,au,br}.html` e `static/*.css`.
- `rumo_core/profile/service.py`: reaproveita `confirm_profile` para "Tenho isso" (a decisão de onde o item entra — `Skill` se for nome de ferramenta/habilidade, senão `Note` — fica em `applications/gaps.py`).
- `worker/prepare_applications.py`: seleciona candidaturas `generating` com `FOR UPDATE SKIP LOCKED`, commit antes de chamadas de rede, tentativa máxima 3, backoff por 429; estados no padrão do `score_matches`.
- API `api/applications.py` (tudo escopado por `user_id`): `POST /applications {job_id}` (idempotente), `GET /applications`, `GET /applications/{id}`, `POST /applications/{id}/generate` (409 se gerando, 429 no teto), `PUT /applications/{id}/lines/{line_id}`, `POST /applications/{id}/gaps/{req_id}/resolve {action, text?}`, `GET /applications/{id}/resume.pdf|resume.txt|cover-letter.pdf|cover-letter.txt`, `POST /applications/{id}/cover-letter`, `POST /applications/{id}/questions` (colar), `PUT /applications/{id}/questions/{qid}`, `POST /applications/{id}/questions/{qid}/draft`, `POST /applications/{id}/apply` e `/unapply`. PDF só é servido com todas as linhas `valid`.
- Web: botão "Preparar candidatura" no painel da vaga e a tecla `p` (reservada no 4a); página `/candidaturas` (lista) e `/candidaturas/[id]` com a trilha horizontal **Ler a vaga → Currículo → Perguntas → Kit**; no passo "Currículo": cobertura, linhas editáveis com o chip da origem, lacunas com "Tenho isso"/"Não tenho"; no "Kit": baixar PDF, copiar texto, respostas copiáveis, link da vaga e **"Apliquei"**. Estados vazios e de erro com um botão ("Gerar de novo", "Tentar de novo").

## 5. Fluxo
1. "Preparar candidatura" → `POST /applications` (cria `draft`, ou abre a existente) → `generate` → `generating`; a tela acompanha por polling (como `/jobs`).
2. Worker: requisitos → evidências → rascunho do currículo → validador (até 2 tentativas) → cobertura → `ready` (ou `error` com motivo legível).
3. Revisão: você edita linhas (cada edição passa pelo validador; falhou → `needs_fix`, a linha mostra o motivo, o PDF fica bloqueado), resolve lacunas, escolhe o modelo, gera a carta (opcional).
4. Perguntas: abre as da vaga (Greenhouse) ou cola; rascunha as permitidas; você edita e marca como final.
5. Kit e "Apliquei" → `applied` (reversível com `unapply`); nenhum envio.

## 6. Erros e resiliência
- JSON inválido do LLM: 1 retentativa; depois `error`, sem gravar documento válido. Groq 429/timeout: `generating` com backoff; teto diário esgotado: mensagem clara e o botão fica desabilitado com o motivo.
- Vaga fechada ou mudou (`content_hash` diferente) depois de gerar: aviso, sem regenerar sozinho.
- Fonte não-Greenhouse ou falha da API de perguntas: cai para "colar as perguntas".
- WeasyPrint falha: 500 evitado (erro `PdfUnavailable` → 503 em português); a versão texto continua disponível.
- Texto da vaga e do perfil sempre tratados como texto; nada vira HTML sem escape (Jinja2 com autoescape).

## 7. Testes
- **Validador (cobertura pesada):** linha sem origem, origem inexistente, número/data/porcentagem inventado, habilidade/ferramenta não existente no perfil, nome de empresa ou instituição estranho, e-mail/URL/telefone de fora, "10 anos de…" sem base, tradução legítima passando (pt→en/de), acento/caixa, unicode, tentativa de injetar texto de vaga ("ignore as regras"), retentativa com violações e erro final.
- `draft_resume`: fatos copiados do perfil (nunca do LLM); LLM falso que tenta alterar empresa/data é ignorado.
- Cobertura: contagem "n de N", requisito citado por linha inválida não conta.
- Lacunas: "Tenho isso" cria nova versão do perfil (re-vetoriza só o novo item), regenera; "Não tenho" não altera o perfil.
- Perguntas: parser de texto colado, Greenhouse (fixture real salva), sensíveis nunca rascunhadas (lista), reuso do banco sem converter moeda/número, bancos de outro usuário invisíveis.
- Render: HTML por modelo (campos, ordem, ausência de foto), texto puro, PDF abre (primeiros bytes `%PDF`, texto extraível com acentos/ß), PDF bloqueado com linha `needs_fix`.
- Worker: estados, tentativas, teto diário, portão da entrevista, `SKIP LOCKED`, falha de rede.
- API: escopo por usuário, idempotência de `POST /applications`, 409/429, `apply`/`unapply`, perfil mudado (aviso), vaga inexistente de outro usuário (404).
- Web: fluxo da revisão (edição → validador → bloqueio de PDF), lacunas, perguntas coladas, kit e "Apliquei", trilha, estados de erro; verificação visual em 375, 768 e desktop, claro e escuro; PDFs conferidos abrindo um de cada modelo.

## 8. Riscos
- **Invenção por tradução:** o LLM traduz e "melhora". Mitigação: fatos copiados por código, validador por tokens (números/técnicos/nomes), 2 tentativas e bloqueio.
- **Validador estrito demais** (rejeita texto bom): a pessoa vê o motivo, edita ou adiciona o fato ao perfil; os limites ficam em constantes testadas.
- **Imagem Docker maior e build do WeasyPrint no Windows/Docker:** confirmado no começo do plano; se falhar, a decisão volta à pessoa (Chromium é o plano B, mais pesado).
- **Cobertura declarada pelo LLM:** conferida pelo validador (termo ou sinônimo do perfil); ainda é heurística e é apresentada como tal.
- **Respostas sensíveis:** lista conservadora; na dúvida, "responda você".
- **Quota do Groq:** teto diário e o portão da entrevista; a fila anda 1 por vez.
- **Conflito de arquivos com o 4b** em `web/app/jobs/page.tsx`: o 4b executa primeiro; o 5 parte da versão mesclada e a migração é a 0009 (a 0008 é do marco 3b, que executa antes do 5).
