import "server-only";
import { connection } from "next/server";
import { RecheckButton } from "@/components/RecheckButton";
import { StatusRow } from "@/components/StatusRow";
import { checkHealth, checkWorker, type HealthReport, type WorkerReport } from "@/lib/health";

/*
 * Painel "Serviços" (API e banco), vindo da antiga página inicial. Componente de
 * servidor: checa GET /health a cada requisição. Monte dentro de
 * <Suspense fallback={<HealthPanelLoading />}> (Configurações, seção Estado do sistema).
 * "Verificar de novo" (RecheckButton, cliente) refaz a checagem com router.refresh().
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
        <StatusRow label="Worker" state="loading" />
      </ul>
      <div className="panel__foot">
        <span className="skeleton skeleton--short" aria-hidden="true" />
      </div>
    </section>
  );
}

type HealthPanelProps = {
  /** Título do painel (h2); "Serviços" por padrão. */
  title?: string;
};

export async function HealthPanel({ title }: HealthPanelProps = {}) {
  await connection();
  const [report, worker] = await Promise.all([checkHealth(), checkWorker()]);
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
        <StatusRow label="Worker" state={worker.state} detail={worker.detail} />
      </ul>
      <ProblemNotice report={report} />
      <WorkerNotice worker={worker} />
      <div className="panel__foot">
        <span>
          Verificado às <span className="mono">{time}</span>
        </span>
        <RecheckButton />
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

export function WorkerNotice({ worker }: { worker: WorkerReport }) {
  if (worker.state !== "down") return null;
  return (
    <div className="notice" role="alert">
      <strong>O worker está parado.</strong>
      <p>
        Sem sinal há mais de 2 minutos: as vagas não são coletadas e as filas de currículo,
        entrevista e pontuação não andam. Suba com <code>.\scripts\dev-up.ps1</code> ou{" "}
        <code>docker compose up -d worker</code>.
      </p>
    </div>
  );
}
