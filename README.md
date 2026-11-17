# Find for You

Ferramenta que eu criei para procurar vaga. Eu descrevo minha experiência uma vez, o sistema transforma isso em embeddings, busca vagas em várias fontes, mostra as que mais combinam comigo e me ajuda a montar a candidatura. Quem envia é sempre eu.

O nome do código é Rumo, por isso você vai ver `rumo_core` e `rumo` no banco e nas variáveis.

> **English summary.** Find for You is a job-search tool I built around a vectorized profile of my own experience. It collects jobs from ATS boards and public feeds, filters them in SQL, ranks them with local multilingual embeddings (bge-m3 and pgvector) and asks an LLM for a score with strengths and gaps. For applications, a validator rejects any generated resume line that can't be traced back to my profile, so the model can rephrase and translate but not invent. Stack: Python 3.12, FastAPI, SQLAlchemy, Alembic, PostgreSQL 16 with pgvector, Next.js, TypeScript and Docker Compose. It is a personal project under active development.

Feito por Matheus Mosconi Sarvas: [LinkedIn](https://www.linkedin.com/in/matheus-mosconi-sarvas/) · [portfólio](https://mathsarvas-dev.github.io/mathsarvas-dev/)

---

## Por que fiz

Procurar vaga fora do Brasil dá muito trabalho: abrir dezenas de páginas de carreira, ler descrições longas, adaptar o currículo para cada país e lembrar o que já enviei. Busca por palavra-chave também erra bastante. Ela não percebe que "desenvolvedor full stack" e "software engineer, TypeScript" são quase a mesma vaga, e não entende "sem presencial".

Por isso dividi o trabalho assim:

- O que dá para decidir com regra (país, remoto, salário, senioridade) é filtro no SQL, sem IA.
- O que é "parecido ou não" fica por conta dos embeddings, que rodam na minha máquina e não custam nada.
- Só as vagas que sobram chegam ao LLM, que dá uma nota e explica. Assim o uso da API gratuita dura mais.

## Como funciona

```
web (Next.js) ──▶ api (FastAPI) ──▶ PostgreSQL 16 + pgvector
                                         ▲
                  worker (Python) ───────┘
                    ├─▶ LLM (Groq, atrás de uma interface que dá para trocar)
                    └─▶ bge-m3 local (embeddings de 1024 dimensões)
```

| Pasta | O que tem |
|---|---|
| `web/` | Interface: onboarding, entrevista, vagas com filtros e chat, fontes, candidaturas, configurações |
| `api/` | FastAPI. Todo dado tem `user_id` desde o começo, para aceitar mais de um usuário no futuro |
| `worker/` | Tarefas em segundo plano: coleta, descoberta de empresas, embeddings, notas por IA e preparo de candidaturas |
| `rumo_core/` | Código compartilhado: modelos, perfil, embeddings, LLM, fontes, matching e candidatura |
| `alembic/` | 14 migrações do banco |

### Perfil e embeddings

Eu envio o currículo (PDF ou DOCX), o texto é lido localmente e uma IA devolve um JSON validado por schema: dados básicos, resumo, experiências, formação, habilidades, idiomas, projetos e notas. Nada é vetorizado antes de eu revisar e confirmar.

Depois vem uma entrevista curta, com uma pergunta por vez, sobre o que ficou faltando, como uma conquista sem número ou um período sem descrição. Cada resposta vira uma sugestão de alteração que eu aceito ou ajusto.

O perfil é dividido em trechos, um por experiência, formação, projeto e assim por diante, e cada trecho tem um hash. Quando o perfil muda, só os trechos alterados são vetorizados de novo. Cada confirmação cria uma nova versão do perfil.

Os embeddings usam o **BAAI/bge-m3**, que é multilíngue e tem 1024 dimensões, rodando localmente com sentence-transformers. A versão do modelo fica salva junto de cada vetor. Os números de desempenho em CPU estão em [`docs/benchmarks.md`](docs/benchmarks.md).

### Coleta de vagas

Cada fonte é um conector em `rumo_core/sources/`:

- ATS: Greenhouse, Lever, Ashby, SmartRecruiters, Breezy, Recruitee, Personio, Teamtailor, BambooHR e Pinpoint
- Feeds públicos: Remotive, RemoteOK, Arbeitnow e Himalayas
- Páginas de carreira com dados estruturados (JSON-LD) e importação por link

Alguns cuidados que tomei:

- LinkedIn e Indeed não são coletados automaticamente, porque os termos de uso proíbem. Para eles só existe importação por link. A política de cada site está em `policy.json`, com a data da verificação.
- As requisições passam por um cliente HTTP próprio (`sources/http.py`): só http e https nas portas 80 e 443, só endereços públicos, redirecionamento apenas dentro do mesmo domínio, limite de tamanho e de tempo, intervalo mínimo por host e leitura do `robots.txt`.
- Se uma fonte falha, as outras continuam, e a coleta tenta de novo com espera crescente.
- O sistema descobre novas empresas a partir de um catálogo e só adiciona quando confirma que o quadro de vagas existe.
- O conteúdo bruto de cada vaga fica guardado, então dá para reprocessar sem coletar de novo. Vagas repetidas são unificadas.

### Matching

1. **Filtros no SQL:** país, modelo de trabalho, salário, senioridade, idioma e departamento. A vaga que não passa é guardada só como um registro mínimo, sem descrição e sem vetor, e nunca chega ao LLM. A versão em Python desse filtro é testada contra a consulta SQL para garantir o mesmo resultado.
2. **Similaridade:** a nota da vaga é a média dos três maiores cossenos entre o vetor da vaga e os trechos do meu perfil. Se eu escrevi um gosto de busca, ele entra com um peso configurável. Tudo isso é calculado dentro do PostgreSQL.
3. **Corte opcional:** vagas abaixo de uma nota mínima ficam guardadas, mas não gastam LLM.
4. **LLM:** as melhores vagas recebem uma nota de 0 a 100, com justificativa, pontos fortes e lacunas. O resultado fica em cache por vaga e versão do perfil, e existe um limite diário.

Também dá para filtrar por conversa. Se eu escrevo "remoto na Europa, sem presencial", o LLM devolve filtros estruturados que aparecem como chips editáveis, sincronizados com os filtros manuais. Restrições, inclusive negações, viram filtros no SQL. Só o "gosto" que sobra vira consulta vetorial, porque embeddings erram com negação.

Há ainda uma tela de teste de qualidade: eu marco vagas como boas ou ruins e o sistema calcula quantas das 10 primeiras eram boas (precision@10).

### Candidatura

Para uma vaga, o sistema extrai os requisitos, escolhe no perfil o que é mais relevante, reescreve no idioma da vaga e gera o PDF.

A regra principal é não inventar nada. Cada frase gerada aponta para o item do perfil de onde veio, e um validador (`rumo_core/applications/validator.py`) rejeita a frase se ela:

- cita um item que não existe naquela versão do perfil;
- traz número, data ou mês que não está no item citado;
- usa um termo técnico ou nome próprio que não está no perfil;
- traz e-mail, link ou telefone que não é meu;
- afirma anos de experiência ou nível sem base no perfil.

O validador não usa banco nem LLM, então é fácil de testar. O modelo pode reformular, traduzir e priorizar, mas não criar fatos.

Também mostra a cobertura dos requisitos (por exemplo, "14 de 18") separando o que eu tenho mas não estava no perfil do que eu realmente não tenho, e guarda respostas prontas para perguntas comuns de formulário.

### Outros recursos

- Kanban das candidaturas, de "salva" até "oferta" ou "recusada".
- Sugestões para o meu perfil do LinkedIn, calculadas a partir do meu perfil canônico, sem ler nada do LinkedIn e com a mesma regra de não inventar.
- Camada de LLM trocável. Hoje usa Groq, com controle de ritmo, medidor de cota por modelo, um modelo separado para tarefas de fundo e um de reserva para quando a cota acaba.
- Painel de saúde com os batimentos do worker e aviso quando ele para.

## Decisões

- **Embeddings locais:** custo zero e meu perfil não sai da máquina para ser vetorizado. Em troca, é mais lento em CPU, e medi isso.
- **Filtro antes da IA:** é barato, previsível e fácil de testar.
- **`user_id` em tudo:** hoje uso sozinho, mas o banco já está pronto para mais pessoas.
- **Toda resposta do LLM passa por schema:** se vier inválida, tenta mais uma vez, e se falhar o estado vira `erro` e nada é gravado como válido.
- **Decisões por escrito:** specs e planos de cada etapa em [`docs/superpowers/`](docs/superpowers), com o [roadmap](docs/superpowers/roadmap.md).

## Stack

Python 3.12 · FastAPI · SQLAlchemy 2 · Alembic · Pydantic · PostgreSQL 16 com pgvector · sentence-transformers (bge-m3) · WeasyPrint · Next.js · TypeScript · Vitest · Docker Compose · Groq

## Como rodar

### Precisa de

- Docker e Docker Compose
- Node.js 20 ou mais novo
- Uma chave da API do Groq (o plano gratuito serve): <https://console.groq.com>
- Cerca de 3 GB de memória livre para o worker e 2,3 GB de disco para o modelo de embeddings, que é baixado na primeira execução

### Passo a passo

```bash
git clone https://github.com/mathsarvas-dev/find-for-you.git
cd find-for-you

# configuração
cp .env.example .env
# abra o .env e preencha GROQ_API_KEY

# banco, migrações, worker e API
docker compose up -d db
docker compose up -d migrate
docker compose up -d worker api
# a primeira build do worker demora, porque instala o PyTorch (versão CPU)

# interface
cd web
npm install
npm run dev
```

Abra <http://localhost:3000>. A API fica em <http://localhost:8000>, com a documentação em `/docs`. No Windows, o script `scripts/dev-up.ps1` sobe tudo e espera o banco ficar pronto.

### Primeiro uso

1. Na tela inicial, envie o currículo ou escreva um resumo sobre você.
2. Revise o perfil que o sistema extraiu e confirme.
3. Responda à entrevista.
4. Em Fontes, escolha as fontes e as empresas que quer acompanhar. O worker coleta de tempos em tempos.
5. Em Vagas, use os filtros ou o chat e abra uma vaga para ver a nota, a justificativa, os pontos fortes e as lacunas.

### Configuração

Todas as variáveis estão em [`.env.example`](.env.example). As principais:

| Variável | Para que serve |
|---|---|
| `DATABASE_URL` | Conexão com o PostgreSQL |
| `GROQ_API_KEY` | Chave do LLM (fica só no `.env`, que não vai para o git) |
| `GROQ_MODEL`, `GROQ_FILL_MODEL`, `GROQ_FALLBACK_MODEL` | Modelo principal, modelo das tarefas de fundo e modelo de reserva |
| `GROQ_MAX_PER_MINUTE` | Limite de chamadas por minuto, para respeitar o plano gratuito |
| `COLLECT_INTERVAL_HOURS` | Intervalo entre coletas de cada empresa |
| `MATCH_TOP_N`, `MATCH_DAILY_LIMIT` | Quantas vagas vão ao LLM e o limite por dia |
| `CORS_ORIGINS` | Origens que podem chamar a API |

### Testes

Com o banco rodando:

```bash
pip install -e ".[dev,embeddings]"
pytest        # usa um banco separado, o rumo_test

cd web && npm test
```

As respostas reais de cada fonte estão salvas em `tests/fixtures/`, então os testes dos conectores não usam a internet. O LLM é simulado nos testes. O resultado da verificação de cada fonte está em [`tests/fixtures/VERIFICATION.md`](tests/fixtures/VERIFICATION.md).

## Estrutura

```
api/            rotas FastAPI
worker/         tarefas em segundo plano e agendador
rumo_core/
  profile/      schema do perfil, trechos e versões
  interview/    entrevista e sugestões de alteração
  embeddings/   bge-m3
  llm/          provedor, controle de ritmo, cota e reserva
  sources/      conectores, feeds, páginas de carreira, cliente HTTP e funil
  discovery/    catálogo e validação de empresas
  matching/     filtros, ranking, nota por IA, filtros por chat e teste de qualidade
  applications/ currículo adaptado, validador, PDF e perguntas
  linkedin/     sugestões para o perfil do LinkedIn
alembic/        migrações
web/            interface Next.js
docs/           benchmarks, specs, planos e roadmap
tests/          testes e fixtures
```

## Situação

É um projeto pessoal e ainda está em desenvolvimento. Já funcionam: base, perfil e entrevista, coleta, matching, filtros por chat, descoberta de empresas, pipeline e as sugestões para o LinkedIn. Estou trabalhando em mais fontes, na saúde e cota do LLM e no acabamento da candidatura. O que falta e o que ainda está em aberto está no [roadmap](docs/superpowers/roadmap.md).

A interface e a documentação estão em português do Brasil, e o sistema trabalha com vagas em português, inglês e alemão.
