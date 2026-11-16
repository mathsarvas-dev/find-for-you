# Rumo — Roadmap e ideias para priorizar

Atualizado em 2026-11-16. Reúne tudo que foi discutido, o que está pronto, o que está em andamento e as ideias que ainda dependem de decisão. Como combinado, **nenhum item abaixo pede spec**: cada ideia tem uma descrição de como funcionaria; aprovado, vai direto ao plano de implementação e à execução. Tamanho: **P** (horas), **M** (1–2 dias de agentes), **G** (vários dias).

## 1. Estado atual

| Marco | Situação |
|---|---|
| 1 Base, 2 Perfil e entrevista, 3 Coleta (Greenhouse), 4a Matching, Descoberta automática | Prontos e na `main` |
| Redesign (casca, trilha, Início, Configurações) e paleta cinza + verde | Pronto e na `main` |
| 4b Filtros por chat, consulta de gosto, teste de qualidade | Pronto e na `main` (falta **você rotular ~40 vagas** em `/jobs/qualidade` para medir o precision@10) |
| Troca do modelo do Groq (o antigo foi removido) | Feita e na `main` (commit `3ac1c69`). Preenchimento de vagas com modelo próprio e trava de prioridade do currículo: **escritos, em deploy** na `feat/marco-3b` |
| 3b Mais fontes + funil de aderência | Spec e plano prontos (45 tarefas). **Fatia 0 (verificações) concluída**; faltam as fatias 1 a 6 |
| 5 Candidatura (currículo adaptado, perguntas, PDF) | Spec e plano prontos (19 tarefas); espera o 3b (migração 0009) |

## 2. Itens para decidir (com a minha sugestão de ordem)

### A. Destravar o uso (sugiro fazer primeiro)

**R1 — Currículo que nunca bloqueia (M).** Hoje, se o Groq recusa por cota, o envio do currículo fica travado e a pessoa não avança.
- *Como funcionaria:* o envio passa a ser aceito na hora e a extração vira uma fila com retentativa automática e contagem regressiva ("tentando de novo em ~14 min"). Em paralelo, uma **opção "Continuar sem extrair"** cria um perfil provisório a partir do texto bruto do currículo: ele é dividido em trechos (por seção ou parágrafo) e vetorizado localmente, sem LLM, então a coleta, os filtros e o ranking por similaridade já funcionam. Quando a extração termina, aparece "Dados extraídos — revise": confirmar troca o perfil provisório pelo estruturado. A trilha do Início passa a aceitar "perfil provisório" como passo cumprido.
- *Decisão:* o perfil provisório deve poder gerar candidatura (marco 5)? Sugestão: não, só ranking e coleta.

**R2 — Orçamento e saúde do LLM (M).** O incidente de hoje: o preenchimento de vagas gastou 198.747 dos 200.000 tokens do dia do `gpt-oss-120b`, e o limite por minuto é de 8.000 tokens.
- *Como funcionaria:* carga de fundo (preenchimento) usa outro modelo com cota própria (**já implementado**); um medidor de cota por modelo lido dos cabeçalhos do Groq; fila de retentativas com horário previsto; painel "Sistema" em Configurações mostra o LLM (modelo existe? cota restante? próxima tentativa?) e avisa quando um modelo some; alternância automática de modelo para tarefas não críticas quando a cota acaba; opção de plano pago do Groq.
- *Decisão:* aceita usar mais de um modelo e mostrar a cota na tela?

**R3 — Operação local sem sustos (P).** O Docker reiniciou e o banco e o worker ficaram parados, o que deixou o currículo na fila sem aviso.
- *Como funcionaria:* separar as camadas do `Dockerfile` (dependências e PyTorch antes de copiar o código), porque hoje qualquer mudança de código rebaixa ~210 MB de PyTorch e o build leva vários minutos e falha com oscilação de rede; um script único para subir banco, migração, worker, API e web (e reconectar depois de reinício); API também em contêiner com `restart`; aviso na tela quando o worker está parado há mais de 2 minutos; o `.env` do Docker lido de um lugar só.

**R4 — Testes isolados do banco de desenvolvimento (P).** As suítes usam o mesmo banco que o worker de dev, o que gera falhas intermitentes e exige parar o worker.
- *Como funcionaria:* `TEST_DATABASE_URL` com um banco separado criado e migrado pelo próprio pytest.

### B. Em andamento ou já planejado

**R5 — Marco 3b: mais fontes e funil (G, em andamento).** Fatia 0 concluída. Aprovadas para implementação: conectores de ATS (Lever, Ashby, SmartRecruiters, Breezy, Recruitee, Personio, Teamtailor, BambooHR, Pinpoint), leitor de páginas de carreira (inclui JazzHR e empresas sem ATS), importar por link, feeds (Remotive, RemoteOK, Arbeitnow, Himalayas) e o funil de aderência.
- *Sugestão de ordem interna:* (1) cliente seguro e portão, (2) **funil**, (3) conectores de ATS, (4) leitor de carreira e importar por link, (5) feeds, (6) abas na tela de Fontes. Antecipar o funil resolve a cota: hoje há ~81 mil vagas esperando preenchimento por LLM, e o funil impede que vagas fora dos seus filtros cheguem ao LLM.
- *Decisão:* confirmar essa ordem ou manter a do plano.

**R6 — Marco 5: candidatura (G).** Currículo adaptado com regra anti-invenção, cobertura de requisitos, PDF por país, perguntas extras e carta. Plano pronto; depende do 3b só pela migração.

**R7 — Marco 4c: calibração por feedback (M–G).** Botão "recomendação certa/errada" por vaga, motivo por tipo (localidade, competências, senioridade, salário, empresa, idioma) e campo livre opcional.
- *Como funcionaria:* primeiro como sinal explícito, sem treinar modelo (por exemplo "você rejeitou 5 vagas fora da Europa: excluir?" e ajuste de pesos por tipo de erro); depois um treino leve por usuário quando houver rótulos suficientes; no SaaS, agregação entre usuários só com consentimento.
- *Decisão:* a calibração muda o ranking na hora (reversível) ou só sugere mudanças?

### C. Novas funcionalidades do design original

**R8 — Marco 6: pipeline e relatórios (G).** Kanban (salva → preparando → aplicada → entrevista → oferta / recusada / sem resposta), lembrete de follow-up, anotações, relatórios (vagas encontradas, aplicadas, respondidas; taxa de resposta por país, fonte e faixa de nota). Usa o atalho `s` (salvar), já reservado.

**R9 — Destaques no Início (P–M).** Vagas novas com nota alta aparecem no painel inicial ("3 vagas novas acima de 80"), sem notificação fora do app.

**R10 — Otimizador de perfil do LinkedIn (M–G).** Sugere como o perfil deve ficar, a partir do seu perfil canônico, sem ler o LinkedIn: um rascunho por seção, notas por quatro pontos de vista (recrutador, ATS, gestor técnico, internacional) e lista priorizada de melhorias, com a mesma regra anti-invenção.

**R11 — Vários perfis de busca nomeados (P–M).** Hoje só existe o filtro padrão. Permitir salvar e alternar filtros nomeados ("Remoto Europa", "Brasil CLT"), cada um com seus chips e consulta de gosto.

### D. Fontes adiadas (precisam de chave, decisão ou nova verificação)

**R12 (M).**
- **Adzuna, Jooble e Careerjet:** precisam de chaves suas no `.env`; sem elas ficam fora.
- **Bundesagentur (Alemanha):** deu 403 na verificação; reavaliar depois.
- **Comeet, InHire, Gupy e Workable:** só com token de cada empresa ou API não documentada; ficam de fora até haver formato público confirmado.
- **Vagas.com, Catho e InfoJobs:** só por "importar por link" (sitemap desatualizado, página de vaga bloqueada ou robots restritivo).
- **Jobbol:** bloqueado; depende de você ler os Termos de Uso.
- **Catálogos por ATS e país:** começam vazios e precisam ser preenchidos.
- **Descrições de SmartRecruiters e Breezy:** exigem uma requisição por vaga.

### E. Qualidade do ranking

**R14 (você faz, ~10 min + M).** Rotular ~40 vagas em `/jobs/qualidade`; com o resultado, ajustar pesos, definir o corte vetorial do funil e validar o desempenho em português e alemão, ainda sem teste.

### F. Produto e SaaS

**R13 (G).** Login e contas, multiusuário, cobrança, notificações por e-mail, compartilhar entre usuários o resultado da validação de tokens do catálogo, privacidade e LGPD.

### G. Dívida técnica e polimento

**R15 (P–M, em lotes).** Tudo o que está no `docs/superpowers/backlog.md`, agrupado:
- **Perfil e entrevista:** validação da justificativa e da lista de lacunas do matching, entrevista, polling sem limite.
- **Coleta:** vagas esvaziadas, orçamento de tokens por dia, aviso enganoso de "worker parado".
- **Descoberta:** teto de empresas e prioridade, rotação entre usuários.
- **Telas:** corridas na tela de vagas, rótulos e foco, estados vazios sem botão, gaveta e título do redesign.
- **Qualidade:** testes faltando no 4b e no 3b.

## 3. Ordem que eu sugiro

1. **R1, R2, R3, R4** — destravam o uso diário e a base de testes (pequenos).
2. **R5 em dois cortes:** funil + segurança + conectores de ATS primeiro; feeds e abas depois.
3. **R14** — você rotula as vagas enquanto o 3b avança.
4. **R6 (candidatura)** e **R9 (destaques)**.
5. **R7 (feedback)** e **R8 (pipeline)**.
6. **R11, R10, R12** conforme a necessidade.
7. **R13 (SaaS)** quando o uso pessoal estiver estável.
8. **R15** em lotes, entre os marcos.

## 4. Decisões que preciso de você
1. R1: o perfil provisório (sem extração) pode gerar candidatura? (sugiro que não)
2. R2: usar mais de um modelo do Groq e mostrar a cota na tela?
3. R5: antecipar o funil dentro do 3b?
4. Ordem geral: confirmar ou trocar a sugestão acima.
5. R12: você quer adicionar chaves de Adzuna, Jooble ou Careerjet?
6. R7: calibração por feedback aplicada na hora ou só como sugestão?
