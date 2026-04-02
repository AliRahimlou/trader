import React, { useMemo, useState } from "react";
import { getApiBase } from "../api";
import { useDashboard } from "../state/DashboardContext";

export default function EventLogPage() {
  const { events } = useDashboard();
  const [query, setQuery] = useState("");
  const [level, setLevel] = useState("all");

  const filtered = useMemo(() => {
    return events.filter((event) => {
      const haystack = JSON.stringify(event).toLowerCase();
      const matchesQuery = !query || haystack.includes(query.toLowerCase());
      const matchesLevel = level === "all" || event.level === level;
      return matchesQuery && matchesLevel;
    });
  }, [events, level, query]);

  return (
    <div className="page-grid">
      <section className="panel panel-span-2">
        <div className="panel-header">
          <h3>Event Log / Audit Trail</h3>
          <a className="ghost-link" href={`${getApiBase()}/api/events?limit=500`} target="_blank" rel="noreferrer">
            Export JSON
          </a>
        </div>
        <div className="filter-row">
          <input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Filter events" />
          <select value={level} onChange={(event) => setLevel(event.target.value)}>
            <option value="all">all levels</option>
            <option value="INFO">INFO</option>
            <option value="WARNING">WARNING</option>
            <option value="ERROR">ERROR</option>
            <option value="DEBUG">DEBUG</option>
          </select>
        </div>
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>ts</th>
                <th>level</th>
                <th>event</th>
                <th>symbol</th>
                <th>strategy</th>
                <th>message</th>
              </tr>
            </thead>
            <tbody>
              {filtered.map((item) => (
                <tr key={item.id}>
                  <td>{item.ts}</td>
                  <td>{item.level}</td>
                  <td>{item.event}</td>
                  <td>{item.symbol || "n/a"}</td>
                  <td>{item.strategy || "n/a"}</td>
                  <td>{item.message || "n/a"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>
    </div>
  );
}
