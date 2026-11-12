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
