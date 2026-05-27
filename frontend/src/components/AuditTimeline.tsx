import type { AuditLog } from '../types/trading';

interface Props {
  items: AuditLog[];
}

export function AuditTimeline({ items }: Props) {
  return (
    <section className="panel timeline-panel">
      <div className="panel-title">
        <h2>Audit Timeline</h2>
      </div>
      <div className="timeline">
        {items.length === 0 && <p className="muted-copy">No bot decisions yet.</p>}
        {items.map((item) => (
          <article key={item.id}>
            <time>{new Date(item.created_at).toLocaleTimeString()}</time>
            <strong>{item.event_type.replaceAll('_', ' ')}</strong>
            <span>{item.message}</span>
          </article>
        ))}
      </div>
    </section>
  );
}
