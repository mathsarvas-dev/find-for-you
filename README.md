# Find for You

Ferramenta de busca de vagas guiada por um **perfil vetorizado**. Você descreve quem é, o sistema transforma esse perfil em embeddings, coleta vagas de várias fontes, filtra, ordena por similaridade, pede a uma IA uma nota com justificativa e prepara a candidatura. **O envio é sempre seu.**

O nome interno do código é **Rumo** (os pacotes `rumo_core`, o banco e as variáveis ainda usam esse nome).

> **English summary.** Find for You is a personal job-hunting tool built around a vectorized candidate profile. It collects jobs from ATS boards and public feeds, filters them with a hard-filter funnel, ranks them by local multilingual embeddings (bge-m3 + pgvector), asks an LLM for a 0 to 100 score with rationale, strengths and gaps, and prepares tailored applications. A validator rejects any generated resume line that cannot be traced back to the candidate's own profile, so the LLM can rephrase and translate but never invent facts. Stack: Python 3.12, FastAPI, SQLAlchemy and Alembic, PostgreSQL 16 with pgvector, Next.js and TypeScript, Docker Compose. Status: working personal project, in active development.

Projeto de Matheus Mosconi Sarvas ([LinkedIn](https://www.linkedin.com/in/matheus-mosconi-sarvas/) · [portfólio](https://mathsarvas-dev.github.io/mathsarvas-dev/)).

---

## Por que existe

Procurar vaga internacional dá trabalho repetitivo: abrir dezenas de páginas de carreira, ler descrições longas, adaptar o currículo ao idioma e ao formato de cada país e acompanhar o que foi enviado. Busca por palavra-chave também erra muito: ela não entende que "desenvolvedor full stack" e "software engineer, TypeScript" são parecidos e não entende negação ("sem presencial").

A ideia do projeto é separar o que é **regra** do que é **julgamento**:

- o que dá para decidir com regra (país, modelo remoto, salário, senioridade) é decidido por **filtro estruturado no SQL**, sem IA;
- o que é parecido ou não (a vaga combina com o seu histórico?) é decidido por **similaridade de vetores**, localmente e sem custo;
- só as vagas que sobrevivem aos dois passos chegam ao **LLM**, que explica a nota. Isso protege o limite gratuito da API e deixa o sistema barato.

## Como funciona

```
web (Next.js) ──▶ api (FastAPI) ──▶ PostgreSQL 16 + pgvector
                                         ▲
                  worker (Python) ───────┘
                    ├─▶ LLM (Groq, atrás de uma interface trocável)
                    └─▶ bge-m3 local (embeddings de 1024 dimensões)
```

| Parte | Função |
|---|---|
| `web/` | Interface: onboarding, entrevista, lista de vagas com filtros e chat, fontes, pipeline de candidaturas, configurações |
| `api/` | FastAPI. Regras de negócio; todo dado carrega `user_id` desde o primeiro dia, para virar multiusuário depois |
| `worker/` | Processo em segundo plano com tarefas agendadas: coleta, descoberta de empresas, vetorização, preenchimento por LLM, nota por IA e preparo de candidaturas |
| `rumo_core/` | Biblioteca compartilhada: modelos, perfil, embeddings, LLM, fontes, matching, candidatura |
| `alembic/` | 14 migrações versionadas do banco |

### 1. Perfil canônico e embeddings

O currículo (PDF ou DOCX) é lido localmente e uma IA devolve um JSON validado por schema (Pydantic): dados básicos, resumo, experiências, formação, habilidades, idiomas, certificações, projetos e notas. **Nada é vetorizado antes de você revisar e confirmar.**

Depois a **entrevista** faz uma pergunta por vez (cerca de 8 a 12) sobre as lacunas que o sistema detectou, como uma conquista sem número ou um período sem descrição. Cada resposta vira uma sugestão de alteração do perfil, que você aceita ou ajusta, e um trecho vetorizado.

O perfil é quebrado em **trechos determinísticos**, um por unidade de significado: resumo, uma experiência, uma formação, o bloco de habilidades e idiomas, um projeto, uma nota. Cada trecho tem hash do conteúdo. Quando o perfil muda, só os trechos alterados são vetorizados de novo; os outros reaproveitam o vetor. O perfil é **versionado** a cada confirmação.

Embeddings: **BAAI/bge-m3**, multilíngue, 1024 dimensões, rodando local (sentence-transformers). A versão do modelo fica registrada em cada vetor, então trocar de modelo mostra exatamente o que precisa ser recalculado. Medição de desempenho em CPU em [`docs/benchmarks.md`](docs/benchmarks.md).

### 2. Coleta de vagas

Cada fonte é um conector em `rumo_core/sources/`:

- **ATS:** Greenhouse, Lever, Ashby, SmartRecruiters, Breezy, Recruitee, Personio, Teamtailor, BambooHR, Pinpoint;
- **Feeds públicos:** Remotive, RemoteOK, Arbeitnow, Himalayas;
- **Páginas de carreira** (JSON-LD `JobPosting`) e **importar por link**.

Cuidados com as fontes:

- **Respeito aos termos de uso.** Portais como LinkedIn e Indeed ficam bloqueados para coleta automática por política registrada em `policy.json`, com a data e a evidência da verificação; neles só se importa por link colado por você.
- **Cliente HTTP seguro** (`sources/http.py`): só http/https nas portas 80 e 443, todo endereço resolvido precisa ser público, o IP checado é o mesmo usado na conexão (evita DNS rebinding), redirecionamentos só dentro do mesmo domínio, limite de tamanho e de tempo, limite diário e intervalo mínimo por host, leitura do `robots.txt`.
- **Fontes isoladas:** a falha de um conector não afeta os outros; há retentativa com espera crescente.
- **Descoberta automática de empresas:** o sistema testa candidatos de um catálogo por ATS e valida se o quadro de vagas existe antes de adicioná-lo.
- **Conteúdo bruto guardado**, o que permite renormalizar sem coletar de novo, e **deduplicação** por impressão digital da vaga.

### 3. Funil de matching

1. **Filtros duros no SQL** (país, modelo de trabalho, salário, senioridade, idioma, departamento). Quando a vaga reprova, ela é guardada só como "stub" (sem descrição nem vetor) e nunca vai ao LLM. A decisão em Python é testada contra a consulta SQL para garantir que as duas dão o mesmo resultado.
2. **Similaridade vetorial.** A nota da vaga é a média dos 3 maiores cossenos normalizados entre o vetor da vaga e os trechos do perfil; se há uma consulta de gosto, entra uma soma ponderada com pesos configuráveis. Tudo dentro de uma subconsulta SQL (LATERAL), sem trazer vetores para o Python.
3. **Corte vetorial opcional:** vagas abaixo do seu corte ficam guardadas, mas não consomem LLM.
4. **LLM nas melhores vagas:** nota de 0 a 100, justificativa, pontos fortes e lacunas, com cache por (vaga, versão do perfil) e limite diário.

**Filtros por conversa.** Você escreve "quero remoto na Europa, sem presencial" e o LLM devolve filtros estruturados que aparecem como chips editáveis, sincronizados com os filtros manuais. Restrições, inclusive negações, viram filtros duros. **Só o "gosto" restante vira consulta vetorial**, porque embeddings falham com negação. Existe também uma tela de **teste de qualidade**: você rotula vagas como boas ou ruins e o sistema calcula a precisão das 10 primeiras (precision@10).

### 4. Candidatura sem invenção

Para uma vaga escolhida, o sistema extrai os requisitos, seleciona no perfil o que é mais relevante, reescreve no idioma da vaga e gera o PDF por um template.

A regra central é a **anti-invenção** (`rumo_core/applications/validator.py`): cada linha gerada cita os itens do perfil de origem, e um validador **puro** (sem banco e sem LLM) rejeita a linha se ela:

- cita um item que não existe naquela versão do perfil;
- traz número, data, porcentagem ou mês que não está no item citado;
- usa termo técnico ou nome próprio que não está no perfil;
- traz e-mail, link ou telefone que não é o seu;
- afirma anos de experiência ou nível sem base no perfil.

O LLM pode reformular, traduzir e priorizar, mas não criar fatos. Há também a **cobertura de requisitos** ("14 de 18"), separando o que "você tem, mas não está no perfil" do que "você não tem", e um banco de respostas reutilizáveis para perguntas de formulário.

### 5. Pipeline e demais recursos

- Kanban de candidaturas (salva → preparando → aplicada → entrevista → oferta / recusada / sem resposta).
- **Otimizador de perfil do LinkedIn:** sugere como o perfil deve ficar a partir do seu perfil canônico, sem ler nem coletar nada do LinkedIn, com a mesma regra anti-invenção.
- **Camada de LLM trocável:** hoje Groq, com controle de ritmo, medidor de cota por modelo, modelo separado para tarefas de fundo e modelo de reserva para quando a cota acaba.
- **Saúde do sistema:** batimentos do worker e aviso quando ele para.

## Decisões de projeto

- **Embeddings locais em vez de API:** custo zero e sem enviar o perfil a terceiros para vetorizar. O custo é desempenho em CPU, medido e documentado.
- **Filtro estruturado antes de qualquer IA:** barato, previsível e testável. O LLM só vê o que sobrou.
- **`user_id` em tudo desde o início:** o uso hoje é pessoal, mas o modelo de dados já aceita vários usuários.
- **Validação em vez de confiança:** toda saída de LLM passa por schema (Pydantic), com uma retentativa; se falhar, o estado é `erro` e nada é gravado como válido.
- **Documentação das decisões no repositório:** specs e planos por marco em [`docs/superpowers/`](docs/superpowers) e o [roadmap](docs/superpowers/roadmap.md).

## Stack

Python 3.12 · FastAPI · SQLAlchemy 2 · Alembic · Pydantic · PostgreSQL 16 + pgvector · sentence-transformers (bge-m3) · WeasyPrint (PDF) · Next.js (App Router) · TypeScript · Vitest · Docker Compose · Groq (LLM)

## Como rodar

### Requisitos

- Docker e Docker Compose
- Node.js 20 ou superior (para a interface)
- Uma chave da API do Groq (plano gratuito serve): <https://console.groq.com>
- Cerca de 3 GB de memória livre para o worker e 2,3 GB de disco para o modelo de embeddings (baixado na primeira execução)

### Passo a passo

```bash
git clone https://github.com/mathsarvas-dev/find-for-you.git
cd find-for-you

# 1. configuração
cp .env.example .env
# edite o .env e preencha GROQ_API_KEY

# 2. banco, migrações, worker e API
docker compose up -d db
docker compose up -d migrate
docker compose up -d worker api
# a primeira build do worker é lenta: instala o PyTorch (CPU)

# 3. interface
cd web
npm install
npm run dev
```

Abra <http://localhost:3000>. A API fica em <http://localhost:8000> (documentação interativa em `/docs`).

No Windows, `scripts/dev-up.ps1` sobe a pilha inteira e espera o banco ficar saudável.

### Primeiro uso

1. Em **Início**, envie o currículo (ou escreva um resumo sobre você).
2. Revise o perfil extraído e confirme.
3. Responda à entrevista.
4. Em **Fontes**, ative as fontes e as empresas que quer acompanhar. O worker coleta a cada algumas horas.
5. Em **Vagas**, use os filtros ou o chat e abra uma vaga para ver a nota, a justificativa, os pontos fortes e as lacunas.

### Configuração

As variáveis estão documentadas em [`.env.example`](.env.example). As principais:

| Variável | Para que serve |
|---|---|
| `DATABASE_URL` | Conexão com o PostgreSQL |
| `GROQ_API_KEY` | Chave do LLM (nunca vai para o git) |
| `GROQ_MODEL`, `GROQ_FILL_MODEL`, `GROQ_FALLBACK_MODEL` | Modelo principal, modelo das tarefas de fundo e reserva |
| `GROQ_MAX_PER_MINUTE` | Controle de ritmo para respeitar o plano gratuito |
| `COLLECT_INTERVAL_HOURS` | Intervalo entre coletas de cada empresa |
| `MATCH_TOP_N`, `MATCH_DAILY_LIMIT` | Quantas vagas vão ao LLM e o teto diário |
| `CORS_ORIGINS` | Origens autorizadas a chamar a API |

### Testes

```bash
pip install -e ".[dev,embeddings]"
pytest                      # usa o banco rumo_test, criado e migrado pela suíte

cd web && npm test          # Vitest + Testing Library
```

As respostas reais de cada fonte estão salvas como fixtures em `tests/fixtures/`, então os testes dos conectores não usam a internet. O LLM é simulado nos testes unitários. O resultado da verificação de cada fonte está em [`tests/fixtures/VERIFICATION.md`](tests/fixtures/VERIFICATION.md).

## Estrutura

```
api/            rotas FastAPI
worker/         tarefas em segundo plano e agendador
rumo_core/
  profile/      schema canônico, trechos, versionamento
  interview/    entrevista e sugestões de alteração
  embeddings/   bge-m3
  llm/          provedor, controle de ritmo, cota, reserva
  sources/      conectores de ATS, feeds, páginas de carreira, cliente HTTP seguro, funil
  discovery/    catálogo e validação de empresas
  matching/     filtros, ranking, nota por IA, filtros por chat, teste de qualidade
  applications/ currículo adaptado, validador anti-invenção, PDF, perguntas
  linkedin/     otimizador de perfil
alembic/        migrações
web/            interface Next.js
docs/           benchmarks, specs, planos e roadmap
tests/          testes e fixtures
```

## Status

Projeto pessoal em desenvolvimento ativo. Prontos: base, perfil e entrevista, coleta, matching, filtros por chat, descoberta de empresas, pipeline e otimizador de LinkedIn. Em andamento: mais fontes, saúde e cota do LLM e acabamento da candidatura. O que está planejado e as decisões em aberto estão no [roadmap](docs/superpowers/roadmap.md).

Os textos da interface e a documentação estão em português do Brasil, com suporte a vagas em português, inglês e alemão.
