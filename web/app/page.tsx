import { Suspense } from "react";
import Link from "next/link";
import { connection } from "next/server";
import { StatusRow } from "@/components/StatusRow";
import { checkHealth, type HealthReport } from "@/lib/health";

export default function StatusPage() {
  return (
    <main className="page">
      <div className="page-head">
        <h1>Estado do sistema</h1>
        <p>O Rumo precisa da API e do banco de dados para buscar e guardar vagas.</p>
      </div>
      <Suspense fallback={<HealthPanelLoading />}>
        <HealthPanel />
      </Suspense>
      <section className="next-step" aria-labelledby="proximo-passo">
        <p>
          <strong id="proximo-passo">Perfil</strong>: envie seu currículo, confira o que foi lido e
          confirme. É a base para comparar vagas.
        </p>
        <Link className="button" href="/onboarding">
          Montar meu perfil
        </Link>
      </section>
      <section className="next-step next-step--follow" aria-labelledby="fontes">
        <p>
          <strong id="fontes">Fontes</strong>: cadastre as empresas que o Rumo vigia e veja as
          vagas coletadas.
        </p>
        <Link className="button button--secondary" href="/sources">
          Abrir fontes
        </Link>
      </section>
      <section className="next-step next-step--follow" aria-labelledby="vagas">
        <p>
          <strong id="vagas">Vagas</strong>: veja as vagas coletadas ordenadas pela nota contra o seu
          perfil, filtre e leia a avaliação da IA.
        </p>
        <Link className="button button--secondary" href="/jobs">
          Abrir vagas
        </Link>
      </section>
    </main>
  );
}

function PanelHead({ target }: { target: string }) {
  return (
    <div className="panel__head">
      <h2 id="servicos">Serviços</h2>
      <span className="mono">{target}</span>
    </div>
  );
}

function HealthPanelLoading() {
  return (
    <section className="panel" aria-labelledby="servicos">
      <PanelHead target="GET /health" />
      <ul className="status-list">
        <StatusRow label="API" state="loading" />
        <StatusRow label="Banco de dados" state="loading" />
      </ul>
      <div className="panel__foot">
        <span className="skeleton skeleton--short" aria-hidden="true" />
      </div>
    </section>
  );
}

async function HealthPanel() {
  await connection();
  const report = await checkHealth();
  const time = new Intl.DateTimeFormat("pt-BR", {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  }).format(report.checkedAt);

  return (
    <section className="panel" aria-labelledby="servicos">
      <PanelHead target={`${report.apiUrl}/health`} />
      <ul className="status-list">
        <StatusRow label="API" state={report.api} detail={report.apiDetail} />
        <StatusRow label="Banco de dados" state={report.db} detail={report.dbDetail} />
      </ul>
      <ProblemNotice report={report} />
      <div className="panel__foot">
        <span>
          Verificado às <span className="mono">{time}</span>
        </span>
        {/* Recarga completa de propósito: refaz a checagem no servidor. */}
        {/* eslint-disable-next-line @next/next/no-html-link-for-pages */}
        <a className="button" href="/">
          Verificar de novo
        </a>
      </div>
    </section>
  );
}

function ProblemNotice({ report }: { report: HealthReport }) {
  switch (report.problem) {
    case null:
      return null;
    case "api-unreachable":
      return (
        <div className="notice" role="alert">
          <strong>A API não respondeu.</strong>
          <p>
            Tentamos <code>{report.apiUrl}/health</code> e não houve resposta. Sem a API, o Rumo
            não lê nem grava nada.
          </p>
          <p>
            Suba a API com <code>uvicorn api.main:app --port 8000</code> (ou{" "}
            <code>docker compose up -d api</code>) e verifique de novo.
          </p>
        </div>
      );
    case "db-down":
      return (
        <div className="notice" role="alert">
          <strong>A API está no ar, mas não alcança o banco.</strong>
          <p>
            Confira se o Postgres está de pé com <code>docker compose up -d db</code> e verifique
            de novo. Ele pode levar alguns segundos para aceitar conexões.
          </p>
        </div>
      );
    case "api-unexpected":
      return (
        <div className="notice" role="alert">
          <strong>A API respondeu de um jeito inesperado (HTTP {report.httpStatus}).</strong>
          <p>
            Veja o log da API para entender o erro e verifique de novo. Se <code>API_URL</code>{" "}
            aponta para outro serviço, corrija o endereço.
          </p>
        </div>
      );
  }
}
