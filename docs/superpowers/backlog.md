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
