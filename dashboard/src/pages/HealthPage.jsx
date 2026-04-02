import React from "react";
import { useDashboard } from "../state/DashboardContext";

export default function HealthPage() {
  const { health, diagnostics } = useDashboard();

  return (
    <div className="page-grid">
      <section className="panel">
        <h3>Health</h3>
        <dl className="detail-list">
          <Detail label="Auth OK" value={String(health?.auth_ok ?? false)} />
          <Detail label="Broker Connected" value={String(health?.broker_connected ?? false)} />
          <Detail label="Market Data Connected" value={String(health?.market_data_connected ?? false)} />
          <Detail label="Reconciliation OK" value={String(health?.reconciliation_ok ?? false)} />
          <Detail label="Data Fresh" value={String(health?.data_fresh ?? false)} />
          <Detail label="Latest Bar" value={health?.latest_completed_bar_time || "n/a"} />
        </dl>
      </section>

      <section className="panel panel-span-2">
        <h3>Diagnostics</h3>
        <pre className="json-block">{JSON.stringify(diagnostics, null, 2)}</pre>
      </section>
    </div>
  );
}

function Detail({ label, value }) {
  return (
    <>
      <dt>{label}</dt>
      <dd>{value}</dd>
    </>
  );
}
