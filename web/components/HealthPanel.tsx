import { connection } from "next/server";
import { StatusRow } from "@/components/StatusRow";
import { checkHealth, type HealthReport } from "@/lib/health";

/*
 * Painel "Serviços" (API e banco), vindo da antiga página inicial. Componente de
 * servidor: checa GET /health a cada requisição. Monte dentro de
 * <Suspense fallback={<HealthPanelLoading />}> (Configurações, seção Estado do sistema).
 */

const DEFAULT_TITLE = "Serviços";

export function PanelHead({ target, title = DEFAULT_TITLE }: { target: string; title?: string }) {
  return (
    <div className="panel__head">
      <h2 id="servicos">{title}</h2>
      <span className="mono">{target}</span>
    </div>
  );
}

export function HealthPanelLoading({ title }: { title?: string } = {}) {
  return (
    <section className="panel" aria-labelledby="servicos">
      <PanelHead target="GET /health" title={title} />
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

type HealthPanelProps = {
  /** Onde "Verificar de novo" recarrega a página (a rota que monta o painel). */
  recheckHref?: string;
  /** Título do painel (h2); "Serviços" por padrão. */
  title?: string;
};

export async function HealthPanel({ recheckHref = "/settings#sistema", title }: HealthPanelProps = {}) {
  await connection();
  const report = await checkHealth();
  const time = new Intl.DateTimeFormat("pt-BR", {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  }).format(report.checkedAt);

  return (
    <section className="panel" aria-labelledby="servicos">
      <PanelHead target={`${report.apiUrl}/health`} title={title} />
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
        <a className="button" href={recheckHref}>
          Verificar de novo
        </a>
      </div>
    </section>
  );
}

export function ProblemNotice({ report }: { report: HealthReport }) {
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
