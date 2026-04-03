import React, { useDeferredValue, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { formatCurrency, formatNumber, formatPercent } from "../formatters";
import { useDashboard } from "../state/DashboardContext";

const FILTER_OPTIONS = [
  { value: "all", label: "All" },
  { value: "watchlist", label: "Watchlist" },
  { value: "signals", label: "Signals" },
  { value: "eligible", label: "Eligible" },
  { value: "pinned", label: "Pinned" },
  { value: "excluded", label: "Excluded" },
];

const SORT_OPTIONS = [
  { value: "rank", label: "Rank" },
  { value: "score", label: "Score" },
  { value: "signals", label: "Signals" },
  { value: "momentum", label: "Momentum" },
];

export default function ScannerPage() {
  const {
    scannerRanked,
    scannerStatus,
    watchlist,
    operatorMode,
    sendCommand,
    commandPending,
  } = useDashboard();
  const [search, setSearch] = useState("");
  const deferredSearch = useDeferredValue(search);
  const [filter, setFilter] = useState("all");
  const [sortBy, setSortBy] = useState("rank");
  const [selectedSymbol, setSelectedSymbol] = useState("");

  const watchlistEntries = watchlist?.entries || [];
  const activeWatchlist = watchlist?.active_symbols || [];
  const watchlistBySymbol = Object.fromEntries(
    watchlistEntries.map((entry) => [entry.symbol, entry]),
  );
  const bestCandidate = scannerRanked.find((candidate) => isEligibleCandidate(candidate)) || scannerRanked[0] || null;
  const bestOpportunities = scannerRanked.filter((candidate) => isEligibleCandidate(candidate)).slice(0, 3);
  const eligibleCount = scannerRanked.filter((candidate) => isEligibleCandidate(candidate)).length;
  const excludedCount = scannerRanked.length - eligibleCount;

  const filteredCandidates = [...scannerRanked]
    .filter((candidate) => matchesCandidateSearch(candidate, deferredSearch))
    .filter((candidate) => matchesCandidateFilter(candidate, filter, activeWatchlist, watchlistBySymbol))
    .sort((left, right) => compareCandidates(left, right, sortBy));

  useEffect(() => {
    if (!filteredCandidates.length) {
      if (selectedSymbol) {
        setSelectedSymbol("");
      }
      return;
    }
    if (!filteredCandidates.some((candidate) => candidate.symbol === selectedSymbol)) {
      setSelectedSymbol(filteredCandidates[0].symbol);
    }
  }, [filteredCandidates, selectedSymbol]);

  const selectedCandidate = filteredCandidates.find((candidate) => candidate.symbol === selectedSymbol) || filteredCandidates[0] || null;
  const selectedWatchlistEntry = selectedCandidate ? watchlistBySymbol[selectedCandidate.symbol] : null;

  return (
    <div className="app-grid scanner-page">
      <section className="hero-card panel-span-2 scanner-hero">
        <div>
          <p className="eyebrow">Scanner</p>
          <h1>{bestCandidate ? `${bestCandidate.symbol} leads the board` : "Scanner is ready to rank"}</h1>
          <p className="hero-copy">
            {bestCandidate
              ? buildCandidateHeadline(bestCandidate, watchlistBySymbol[bestCandidate.symbol], activeWatchlist)
              : "Refresh the scanner to build a ranked list of the strongest paper-trading opportunities."}
          </p>
          <div className="quick-action-row">
            <button
              type="button"
              className="ghost-button"
              disabled={!operatorMode || commandPending}
              onClick={() => sendCommand("refresh_scanner")}
            >
              Refresh scanner
            </button>
            {bestCandidate && (
              <Link to={`/trade?symbol=${bestCandidate.symbol}`} className="primary-link-button">
                Open best idea
              </Link>
            )}
            <Link to="/bot" className="ghost-link-button">
              Automation controls
            </Link>
          </div>
        </div>
        <div className="hero-side-grid">
          <InfoCard label="Universe scanned" value={scannerStatus?.universe_count ? `${scannerStatus.scanned_count} of ${scannerStatus.universe_count}` : "n/a"} />
          <InfoCard label="Live watchlist" value={String(activeWatchlist.length)} />
          <InfoCard label="Eligible now" value={String(eligibleCount)} tone="positive" />
          <InfoCard label="Excluded now" value={String(excludedCount)} tone={excludedCount ? "warn" : "neutral"} />
        </div>
      </section>

      <section className="panel panel-span-2">
        <div className="section-head">
          <div>
            <h2>Browse the ranked universe</h2>
            <p className="muted">Search symbols, filter by live status, and inspect why each name is rising or being rejected.</p>
          </div>
        </div>
        <div className="scanner-toolbar">
          <label className="scanner-search-field">
            <span>Search symbols or company names</span>
            <input
              value={search}
              onChange={(event) => setSearch(event.target.value)}
              placeholder="AAPL, semiconductors, energy, SPY"
            />
          </label>
          <label className="scanner-select-field">
            <span>Sort by</span>
            <select value={sortBy} onChange={(event) => setSortBy(event.target.value)}>
              {SORT_OPTIONS.map((option) => (
                <option key={option.value} value={option.value}>
                  {option.label}
                </option>
              ))}
            </select>
          </label>
        </div>
        <div className="chip-row">
          {FILTER_OPTIONS.map((option) => (
            <button
              key={option.value}
              type="button"
              className={`chip ${filter === option.value ? "chip-active" : ""}`}
              onClick={() => setFilter(option.value)}
            >
              {option.label} ({countCandidates(scannerRanked, option.value, activeWatchlist, watchlistBySymbol)})
            </button>
          ))}
        </div>
      </section>

      {bestOpportunities.length > 0 && (
        <section className="panel panel-span-2">
          <div className="section-head">
            <div>
              <h2>Current best opportunities</h2>
              <p className="muted">These are the strongest eligible names the scanner sees right now.</p>
            </div>
          </div>
          <div className="opportunity-grid">
            {bestOpportunities.map((candidate) => {
              const watchlistEntry = watchlistBySymbol[candidate.symbol];
              const symbolEnabled = isSymbolEnabled(candidate, watchlistEntry);
              return (
                <div className="opportunity-card" key={candidate.symbol}>
                  <div className="position-summary-head">
                    <div>
                      <h3>{candidate.symbol}</h3>
                      <p className="muted">{candidate.asset?.name || "Tradable US equity"}</p>
                    </div>
                    <span className="change-pill neutral">#{candidate.rank || "-"}</span>
                  </div>
                  <div className="position-summary-grid">
                    <InfoCard label="Score" value={formatNumber(candidate.score || 0, 1)} compact />
                    <InfoCard label="Signals" value={String((candidate.signals || []).length)} compact />
                    <InfoCard label="Price" value={formatCurrency(candidate.features?.price)} compact />
                    <InfoCard label="Status" value={candidateStatusLabel(candidate, activeWatchlist, watchlistEntry)} compact />
                  </div>
                  <p className="scanner-card-copy">{summarizeCandidate(candidate)}</p>
                  <div className="button-row left-align">
                    <button
                      type="button"
                      className="ghost-button"
                      disabled={!operatorMode || commandPending}
                      onClick={() => sendCommand("pin_symbol", { symbol: candidate.symbol, pinned: !watchlistEntry?.pinned })}
                    >
                      {watchlistEntry?.pinned ? "Unpin" : "Pin"}
                    </button>
                    <button
                      type="button"
                      className="ghost-button"
                      disabled={!operatorMode || commandPending}
                      onClick={() => sendCommand("set_symbol_enabled", { symbol: candidate.symbol, enabled: !symbolEnabled })}
                    >
                      {symbolEnabled ? "Disable" : "Enable"}
                    </button>
                    <Link to={`/trade?symbol=${candidate.symbol}`} className="ghost-link-button">
                      Open ticket
                    </Link>
                  </div>
                </div>
              );
            })}
          </div>
        </section>
      )}

      <section className="panel panel-span-2">
        <div className="scanner-layout">
          <div className="table-wrap scanner-table-wrap">
            <table>
              <thead>
                <tr>
                  <th>symbol</th>
                  <th>rank</th>
                  <th>score</th>
                  <th>status</th>
                  <th>strategy fit</th>
                  <th>why / why not</th>
                  <th>actions</th>
                </tr>
              </thead>
              <tbody>
                {!filteredCandidates.length ? (
                  <tr>
                    <td colSpan={7}>
                      <div className="empty-card scanner-empty-card">
                        <h3>No candidates match this view</h3>
                        <p>Try clearing the search box or switching to a broader filter.</p>
                      </div>
                    </td>
                  </tr>
                ) : (
                  filteredCandidates.map((candidate) => {
                    const watchlistEntry = watchlistBySymbol[candidate.symbol];
                    const symbolEnabled = isSymbolEnabled(candidate, watchlistEntry);
                    return (
                      <tr
                        key={candidate.symbol}
                        className={candidate.symbol === selectedCandidate?.symbol ? "candidate-row-selected" : ""}
                        onClick={() => setSelectedSymbol(candidate.symbol)}
                      >
                        <td>
                          <div className="candidate-symbol-cell">
                            <strong>{candidate.symbol}</strong>
                            <span>{candidate.asset?.name || "Tradable US equity"}</span>
                          </div>
                        </td>
                        <td>{candidate.rank || "-"}</td>
                        <td>{formatNumber(candidate.score || 0, 1)}</td>
                        <td>
                          <span className={`status-chip status-chip-${candidateStatusTone(candidate, activeWatchlist, watchlistEntry)}`}>
                            {candidateStatusLabel(candidate, activeWatchlist, watchlistEntry)}
                          </span>
                        </td>
                        <td>{formatSignalSummary(candidate)}</td>
                        <td>{summarizeCandidate(candidate)}</td>
                        <td>
                          <div className="table-action-row">
                            <button
                              type="button"
                              className="ghost-button"
                              disabled={!operatorMode || commandPending}
                              onClick={(event) => {
                                event.stopPropagation();
                                sendCommand("pin_symbol", { symbol: candidate.symbol, pinned: !watchlistEntry?.pinned });
                              }}
                            >
                              {watchlistEntry?.pinned ? "Unpin" : "Pin"}
                            </button>
                            <button
                              type="button"
                              className="ghost-button"
                              disabled={!operatorMode || commandPending}
                              onClick={(event) => {
                                event.stopPropagation();
                                sendCommand("set_symbol_enabled", { symbol: candidate.symbol, enabled: !symbolEnabled });
                              }}
                            >
                              {symbolEnabled ? "Disable" : "Enable"}
                            </button>
                          </div>
                        </td>
                      </tr>
                    );
                  })
                )}
              </tbody>
            </table>
          </div>

          <div className="scanner-detail-card">
            {!selectedCandidate ? (
              <div className="empty-card scanner-empty-card">
                <h3>No candidate selected</h3>
                <p>Choose a ranked symbol to inspect its score, setup fit, and watchlist status.</p>
              </div>
            ) : (
              <>
                <div className="section-head">
                  <div>
                    <p className="eyebrow">Selected candidate</p>
                    <h2>{selectedCandidate.symbol}</h2>
                    <p className="muted">{selectedCandidate.asset?.name || "Tradable US equity"}</p>
                  </div>
                  <div className="chip-row">
                    <span className={`status-chip status-chip-${candidateStatusTone(selectedCandidate, activeWatchlist, selectedWatchlistEntry)}`}>
                      {candidateStatusLabel(selectedCandidate, activeWatchlist, selectedWatchlistEntry)}
                    </span>
                    <span className="change-pill neutral">#{selectedCandidate.rank || "-"}</span>
                  </div>
                </div>

                <div className="detail-card-grid scanner-detail-grid">
                  <InfoCard label="Scanner score" value={formatNumber(selectedCandidate.score || 0, 1)} />
                  <InfoCard label="Watchlist reason" value={selectedWatchlistEntry ? formatReasonLabel(selectedWatchlistEntry.watch_reason) : "Not selected"} />
                  <InfoCard label="Signals" value={String((selectedCandidate.signals || []).length)} />
                  <InfoCard label="Price" value={formatCurrency(selectedCandidate.features?.price)} />
                  <InfoCard label="Gap" value={formatPercent(selectedCandidate.features?.gap_pct)} />
                  <InfoCard label="Session return" value={formatPercent(selectedCandidate.features?.intraday_return_pct)} />
                  <InfoCard label="Relative volume" value={formatNumber(selectedCandidate.features?.relative_volume || 0, 2)} />
                  <InfoCard label="Spread" value={selectedCandidate.features?.spread_bps != null ? `${formatNumber(selectedCandidate.features?.spread_bps || 0, 1)} bps` : "n/a"} />
                </div>

                <div className="section-head scanner-subsection-head">
                  <div>
                    <h3>Score breakdown</h3>
                    <p className="muted">Each component shows how this name earned its rank.</p>
                  </div>
                </div>
                <div className="score-bar-list">
                  {sortedScoreComponents(selectedCandidate.score_components).map(([name, value]) => (
                    <div className="score-bar-row" key={name}>
                      <div className="score-bar-head">
                        <span>{formatComponentLabel(name)}</span>
                        <strong>{formatNumber(value, 1)}</strong>
                      </div>
                      <div className="score-bar-track">
                        <div className="score-bar-fill" style={{ width: `${Math.max(0, Math.min(100, Number(value) || 0))}%` }} />
                      </div>
                    </div>
                  ))}
                </div>

                <div className="scanner-detail-panels">
                  <div className="card-like scanner-note-card">
                    <h3>Why it ranks</h3>
                    <p className="muted">{buildSelectionNarrative(selectedCandidate, selectedWatchlistEntry, activeWatchlist)}</p>
                    <div className="chip-row">
                      {sortedScoreComponents(selectedCandidate.score_components).slice(0, 4).map(([name]) => (
                        <span className="change-pill neutral" key={name}>{formatComponentLabel(name)}</span>
                      ))}
                    </div>
                  </div>

                  <div className="card-like scanner-note-card">
                    <h3>Exclusions</h3>
                    {selectedCandidate.exclusion_reasons?.length ? (
                      <div className="chip-row">
                        {selectedCandidate.exclusion_reasons.map((reason) => (
                          <span className="status-chip status-chip-negative" key={reason}>{formatReasonLabel(reason)}</span>
                        ))}
                      </div>
                    ) : (
                      <p className="muted">No active exclusions. This symbol is currently eligible for watchlist routing.</p>
                    )}
                    {selectedCandidate.notes?.length ? <p className="muted scanner-note-copy">{selectedCandidate.notes.join(" · ")}</p> : null}
                  </div>
                </div>

                <div className="card-like scanner-signals-card">
                  <div className="section-head">
                    <div>
                      <h3>Strategy matches</h3>
                      <p className="muted">The live setups currently supporting or rejecting this symbol.</p>
                    </div>
                    <Link to={`/trade?symbol=${selectedCandidate.symbol}`} className="ghost-link">
                      Open trade ticket
                    </Link>
                  </div>
                  {!selectedCandidate.signals?.length ? (
                    <div className="empty-card">
                      <h3>No live strategy match yet</h3>
                      <p>This symbol can still rank well on liquidity, range, or momentum while it waits for a strategy trigger.</p>
                    </div>
                  ) : (
                    <div className="scanner-signal-list">
                      {selectedCandidate.signals.map((signal) => (
                        <div className="preview-row scanner-signal-row" key={signal.signal_key}>
                          <div>
                            <strong>{signal.strategy_name || formatComponentLabel(signal.strategy_id)}</strong>
                            <p className="muted">
                              {formatComponentLabel(signal.direction)} · {formatReasonLabel(signal.reason || "strategy_match")}
                            </p>
                          </div>
                          <div className="scanner-signal-metrics">
                            <span>{formatCurrency(signal.entry_reference_price)}</span>
                            <span>{formatCurrency(signal.stop_price)} stop</span>
                            <span>{formatCurrency(signal.target_price)} target</span>
                          </div>
                        </div>
                      ))}
                    </div>
                  )}
                </div>

                <div className="button-row left-align">
                  <button
                    type="button"
                    className="ghost-button"
                    disabled={!operatorMode || commandPending}
                    onClick={() => sendCommand("pin_symbol", { symbol: selectedCandidate.symbol, pinned: !selectedWatchlistEntry?.pinned })}
                  >
                    {selectedWatchlistEntry?.pinned ? "Unpin symbol" : "Pin symbol"}
                  </button>
                  <button
                    type="button"
                    className="ghost-button"
                    disabled={!operatorMode || commandPending}
                    onClick={() => sendCommand("set_symbol_enabled", { symbol: selectedCandidate.symbol, enabled: !isSymbolEnabled(selectedCandidate, selectedWatchlistEntry) })}
                  >
                    {isSymbolEnabled(selectedCandidate, selectedWatchlistEntry) ? "Disable symbol" : "Enable symbol"}
                  </button>
                  <Link to={`/trade?symbol=${selectedCandidate.symbol}`} className="primary-link-button">
                    Select in trade ticket
                  </Link>
                </div>
              </>
            )}
          </div>
        </div>
      </section>
    </div>
  );
}

function countCandidates(candidates, filter, activeWatchlist, watchlistBySymbol) {
  return candidates.filter((candidate) => matchesCandidateFilter(candidate, filter, activeWatchlist, watchlistBySymbol)).length;
}

function matchesCandidateSearch(candidate, query) {
  const normalized = String(query || "").trim().toLowerCase();
  if (!normalized) {
    return true;
  }
  const haystack = [candidate.symbol, candidate.asset?.name, candidate.asset?.exchange]
    .filter(Boolean)
    .join(" ")
    .toLowerCase();
  return haystack.includes(normalized);
}

function matchesCandidateFilter(candidate, filter, activeWatchlist, watchlistBySymbol) {
  const watchlistEntry = watchlistBySymbol[candidate.symbol];
  if (filter === "watchlist") {
    return activeWatchlist.includes(candidate.symbol);
  }
  if (filter === "signals") {
    return Boolean(candidate.signals?.length);
  }
  if (filter === "eligible") {
    return isEligibleCandidate(candidate);
  }
  if (filter === "pinned") {
    return Boolean(watchlistEntry?.pinned);
  }
  if (filter === "excluded") {
    return !isEligibleCandidate(candidate);
  }
  return true;
}

function compareCandidates(left, right, sortBy) {
  if (sortBy === "score") {
    return compareNumbers(right.score, left.score) || compareNumbers(left.rank, right.rank) || left.symbol.localeCompare(right.symbol);
  }
  if (sortBy === "signals") {
    return compareNumbers((right.signals || []).length, (left.signals || []).length) || compareNumbers(right.score, left.score) || left.symbol.localeCompare(right.symbol);
  }
  if (sortBy === "momentum") {
    return compareNumbers(right.features?.recent_momentum_pct, left.features?.recent_momentum_pct)
      || compareNumbers(right.features?.intraday_return_pct, left.features?.intraday_return_pct)
      || compareNumbers(right.score, left.score)
      || left.symbol.localeCompare(right.symbol);
  }
  return compareNumbers(left.rank || 999, right.rank || 999) || compareNumbers(right.score, left.score) || left.symbol.localeCompare(right.symbol);
}

function compareNumbers(left, right) {
  return Number(left || 0) - Number(right || 0);
}

function isEligibleCandidate(candidate) {
  return Boolean(candidate?.eligible) && !(candidate?.exclusion_reasons || []).length;
}

function isSymbolEnabled(candidate, watchlistEntry) {
  if (watchlistEntry?.enabled === false) {
    return false;
  }
  return !(candidate.exclusion_reasons || []).includes("disabled_override");
}

function candidateStatusLabel(candidate, activeWatchlist, watchlistEntry) {
  if (watchlistEntry?.pinned) {
    return "Pinned";
  }
  if (!isSymbolEnabled(candidate, watchlistEntry)) {
    return "Disabled";
  }
  if (activeWatchlist.includes(candidate.symbol)) {
    return "Live watchlist";
  }
  if (!isEligibleCandidate(candidate)) {
    return "Excluded";
  }
  if (candidate.signals?.length) {
    return "Strategy match";
  }
  return "Eligible";
}

function candidateStatusTone(candidate, activeWatchlist, watchlistEntry) {
  const label = candidateStatusLabel(candidate, activeWatchlist, watchlistEntry);
  if (label === "Pinned") {
    return "positive";
  }
  if (label === "Live watchlist") {
    return "info";
  }
  if (label === "Excluded" || label === "Disabled") {
    return "negative";
  }
  if (label === "Strategy match") {
    return "positive";
  }
  return "neutral";
}

function sortedScoreComponents(scoreComponents = {}) {
  return Object.entries(scoreComponents).sort((left, right) => Number(right[1] || 0) - Number(left[1] || 0));
}

function summarizeCandidate(candidate) {
  if (candidate.exclusion_reasons?.length) {
    return formatReasonLabel(candidate.exclusion_reasons[0]);
  }
  if (candidate.signals?.length) {
    return formatSignalSummary(candidate);
  }
  const topDrivers = sortedScoreComponents(candidate.score_components).slice(0, 2).map(([name]) => formatComponentLabel(name).toLowerCase());
  if (topDrivers.length) {
    return `Strong ${topDrivers.join(" + ")}`;
  }
  return "Waiting for clearer scanner edge";
}

function buildCandidateHeadline(candidate, watchlistEntry, activeWatchlist) {
  const reasons = [];
  const topDrivers = sortedScoreComponents(candidate.score_components).slice(0, 2).map(([name]) => formatComponentLabel(name).toLowerCase());
  if (candidate.signals?.length) {
    reasons.push(`${candidate.signals.length} live setup match${candidate.signals.length === 1 ? "" : "es"}`);
  }
  if (topDrivers.length) {
    reasons.push(`strong ${topDrivers.join(" and ")}`);
  }
  if (watchlistEntry?.pinned) {
    reasons.push("it is pinned into the live watchlist");
  } else if (activeWatchlist.includes(candidate.symbol)) {
    reasons.push("it is already on the live watchlist");
  }
  if (candidate.exclusion_reasons?.length) {
    reasons.push(`it is still blocked by ${formatReasonLabel(candidate.exclusion_reasons[0]).toLowerCase()}`);
  }
  return `${candidate.symbol} is the highest-ranked symbol because ${reasons.join(", ") || "its scanner score is currently strongest"}.`;
}

function buildSelectionNarrative(candidate, watchlistEntry, activeWatchlist) {
  const status = candidateStatusLabel(candidate, activeWatchlist, watchlistEntry).toLowerCase();
  const topDrivers = sortedScoreComponents(candidate.score_components).slice(0, 3).map(([name]) => formatComponentLabel(name).toLowerCase());
  const watchReason = watchlistEntry ? formatReasonLabel(watchlistEntry.watch_reason).toLowerCase() : "not selected into the live watchlist yet";
  if (candidate.exclusion_reasons?.length) {
    return `${candidate.symbol} is currently ${status}. It still scores on ${topDrivers.join(", ") || "scanner context"}, but routing is blocked by ${candidate.exclusion_reasons.map((reason) => formatReasonLabel(reason).toLowerCase()).join(", ")}.`;
  }
  return `${candidate.symbol} is currently ${status}. Its strongest drivers are ${topDrivers.join(", ") || "balanced scanner components"}, and its watchlist state is ${watchReason}.`;
}

function formatSignalSummary(candidate) {
  if (!candidate.signals?.length) {
    return "No active setup";
  }
  return candidate.signals.map((signal) => `${formatComponentLabel(signal.strategy_id)} ${formatComponentLabel(signal.direction)}`).join(" · ");
}

function formatComponentLabel(value) {
  const labels = {
    atr_pct: "ATR %",
    daily_bias: "Daily bias",
    freshness: "Freshness",
    gap: "Gap",
    intraday_return_pct: "Session return",
    liquidity: "Liquidity",
    momentum: "Momentum",
    pullback: "Pullback",
    recent_momentum_pct: "Recent momentum",
    setup: "Setup fit",
    signals: "Signals",
    spread: "Spread",
    strategy_match: "Strategy match",
    trend: "Trend",
    volatility: "Volatility",
    break: "Break",
    long: "Long",
    short: "Short",
  };
  return labels[value] || humanizeToken(value);
}

function formatReasonLabel(reason) {
  const labels = {
    above_max_price: "Above max price",
    asset_not_tradable: "Asset not tradable",
    below_min_average_volume: "Below minimum average volume",
    below_min_price: "Below minimum price",
    disabled_override: "Disabled by operator",
    dropped_from_watchlist: "Dropped from watchlist",
    leveraged_etf_excluded: "Leveraged ETF excluded",
    missing_daily_bars: "Missing daily bars",
    missing_price: "Missing price",
    missing_session_bars: "Missing session bars",
    open_position: "Open position",
    pinned_symbol: "Pinned symbol",
    spread_too_wide: "Spread too wide",
    top_ranked: "Top ranked",
    retained_buffer: "Retained in hold buffer",
  };
  if (labels[reason]) {
    return labels[reason];
  }
  if (String(reason).startsWith("scanner_error:")) {
    return `Scanner error: ${String(reason).slice("scanner_error:".length).trim()}`;
  }
  return humanizeToken(reason);
}

function humanizeToken(value) {
  return String(value || "")
    .replace(/[_-]+/g, " ")
    .replace(/\b\w/g, (character) => character.toUpperCase());
}

function InfoCard({ label, value, tone = "neutral", compact = false }) {
  return (
    <div className={`info-card tone-${tone} ${compact ? "info-card-compact" : ""}`}>
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}