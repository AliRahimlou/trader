import type { Candidate } from '../types/trading';

interface Props {
  candidates: Candidate[];
}

export function RankedStocksTable({ candidates }: Props) {
  return (
    <section className="panel table-panel">
      <div className="panel-title">
        <h2>Ranked Stocks</h2>
      </div>
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th>Ticker</th>
              <th>Signal</th>
              <th>Confidence</th>
              <th>Entry</th>
              <th>Stop</th>
              <th>Target</th>
              <th>Shares</th>
              <th>Allowed</th>
            </tr>
          </thead>
          <tbody>
            {candidates.length === 0 && (
              <tr>
                <td colSpan={8}>No rankings yet.</td>
              </tr>
            )}
            {candidates.map((candidate) => (
              <tr key={`${candidate.ticker}-${candidate.id ?? candidate.final_score}`}>
                <td className="ticker">{candidate.ticker}</td>
                <td>{candidate.direction}</td>
                <td>{Math.round(candidate.confidence * 100)}%</td>
                <td>{candidate.entry_trigger ? `$${candidate.entry_trigger.toFixed(2)}` : '-'}</td>
                <td>{candidate.stop_loss ? `$${candidate.stop_loss.toFixed(2)}` : '-'}</td>
                <td>{candidate.take_profit ? `$${candidate.take_profit.toFixed(2)}` : '-'}</td>
                <td>{candidate.suggested_position_size}</td>
                <td>
                  <span className={candidate.allowed ? 'pill ok' : 'pill muted'}>
                    {candidate.allowed ? 'Yes' : 'No'}
                  </span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {candidates[0] && (
        <div className="candidate-reason">
          <strong>{candidates[0].ticker}</strong>
          <span>{candidates[0].reason}</span>
        </div>
      )}
    </section>
  );
}
