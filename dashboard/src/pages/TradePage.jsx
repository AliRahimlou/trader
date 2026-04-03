import React, { useEffect, useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import PriceChart from "../components/PriceChart";
import TradePreviewModal from "../components/TradePreviewModal";
import { fetchJson } from "../api";
import { botStateLabel, formatCurrency, formatNumber, formatPercent, pnlTone } from "../formatters";
import { useDashboard } from "../state/DashboardContext";

const RANGE_OPTIONS = ["1D", "1W", "1M"];
const QUICK_AMOUNTS = [50, 100, 500];

export default function TradePage() {
  const { overview, sendCommand, commandPending, refreshAll, watchlist: liveWatchlist, scannerRanked } = useDashboard();
  const [searchParams, setSearchParams] = useSearchParams();
  const configuredSymbol = overview?.runner_status?.symbol || "SPY";
  const selectedSymbol = (searchParams.get("symbol") || configuredSymbol).toUpperCase();
  const [chartRange, setChartRange] = useState("1D");
  const [tradeContext, setTradeContext] = useState(null);
  const [amount, setAmount] = useState("100");
  const [symbolInput, setSymbolInput] = useState(configuredSymbol);
  const [preview, setPreview] = useState(null);
  const [previewPending, setPreviewPending] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [recentSymbols, setRecentSymbols] = useState(() => {
    const stored = window.localStorage.getItem("paper-recent-symbols");
    if (!stored) {
      return [];
    }
    try {
      return JSON.parse(stored);
    } catch {
      return [];
    }
  });
  const activeWatchlist = liveWatchlist?.active_symbols || [];
  const rankedSymbols = scannerRanked.slice(0, 8).map((candidate) => candidate.symbol);
  const pickerSymbols = Array.from(
    new Set([selectedSymbol, ...activeWatchlist, ...rankedSymbols, ...recentSymbols, configuredSymbol].filter(Boolean)),
  ).slice(0, 12);

  useEffect(() => {
    setSymbolInput(selectedSymbol);
  }, [selectedSymbol]);

  useEffect(() => {
    if (!selectedSymbol) {
      return;
    }
    setRecentSymbols((current) => Array.from(new Set([selectedSymbol, ...current])).slice(0, 8));
  }, [selectedSymbol]);

  useEffect(() => {
    window.localStorage.setItem("paper-recent-symbols", JSON.stringify(recentSymbols));
  }, [recentSymbols]);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError("");
    fetchJson(`/api/trade/context?symbol=${encodeURIComponent(selectedSymbol)}&chart_range=${chartRange}`)
      .then((payload) => {
        if (!cancelled) {
          setTradeContext(payload);
        }
      })
      .catch((requestError) => {
        if (!cancelled) {
          setError(requestError.message || String(requestError));
        }
      })
      .finally(() => {
        if (!cancelled) {
          setLoading(false);
        }
      });
    return () => {
      cancelled = true;
    };
  }, [chartRange, selectedSymbol]);

  const account = tradeContext?.account || {};
  const position = tradeContext?.position || null;
  const quote = tradeContext?.quote || {};
  const canSubmit = tradeContext?.manual_trading_enabled;
  const amountNumber = Number(amount) || 0;
  const quickAmounts = useMemo(() => {
    const dynamicMax = position ? Number(position.market_value || 0) : Number(account.buying_power || 0);
    return [...QUICK_AMOUNTS, Math.max(0, Math.floor(dynamicMax))].filter((value, index, values) => value > 0 && values.indexOf(value) === index);
  }, [account.buying_power, position]);

  const requestPreview = async (side) => {
    setPreviewPending(true);
    setError("");
    try {
      const payload = await fetchJson("/api/trade/preview", {
        method: "POST",
        body: JSON.stringify({
          symbol: selectedSymbol,
          side,
          amount_dollars: amountNumber,
        }),
      });
      setPreview(payload);
    } catch (requestError) {
      setError(requestError.message || String(requestError));
    } finally {
      setPreviewPending(false);
    }
  };

  const submitPreview = async () => {
    if (!preview) {
      return;
    }
    setPreviewPending(true);
    try {
      const result = await sendCommand(
        "manual_trade",
        {
          symbol: preview.symbol,
          side: preview.side,
          amount_dollars: preview.amount_dollars,
        },
        { confirm: true },
      );
      setMessage(`${preview.side === "buy" ? "Buy" : "Sell"} order sent for ${preview.symbol}. Status: ${result.result?.status || "submitted"}.`);
      setPreview(null);
      await refreshAll();
      const context = await fetchJson(`/api/trade/context?symbol=${encodeURIComponent(selectedSymbol)}&chart_range=${chartRange}`);
      setTradeContext(context);
    } catch (requestError) {
      setError(requestError.message || String(requestError));
    } finally {
      setPreviewPending(false);
    }
  };

  const loadSymbol = () => {
    const symbol = symbolInput.trim().toUpperCase();
    if (!symbol) {
      return;
    }
    setSearchParams({ symbol });
  };

  return (
    <div className="app-grid trade-grid">
      <section className="hero-card trade-hero">
        <div>
          <div className="eyebrow-row">
            <span className="paper-badge">Paper trading</span>
            <span className={`change-pill ${pnlTone(quote.absolute_change)}`}>{formatPercent(quote.percent_change)}</span>
          </div>
          <h1>{selectedSymbol}</h1>
          <p className="hero-price">{formatCurrency(quote.last_price)}</p>
          <p className="hero-copy">
            {botStateLabel(tradeContext?.bot)}. {tradeContext?.bot?.status_reason || "Checking the latest market move."}
          </p>
        </div>
        <div className="symbol-search-card">
          <label>
            <span>Open a symbol</span>
            <div className="symbol-search-row">
              <input value={symbolInput} onChange={(event) => setSymbolInput(event.target.value.toUpperCase())} placeholder="SPY" />
              <button type="button" className="ghost-button" onClick={loadSymbol}>
                Open
              </button>
            </div>
          </label>
          <div className="chip-row">
            {pickerSymbols.map((symbol) => (
              <button key={symbol} type="button" className={`chip ${symbol === selectedSymbol ? "chip-active" : ""}`} onClick={() => setSearchParams({ symbol })}>
                {symbol}
              </button>
            ))}
          </div>
        </div>
      </section>

      <section className="panel chart-panel panel-span-2">
        <div className="section-head">
          <div>
            <h2>Price chart</h2>
            <p className="muted">Recent price action with buy/sell markers when available.</p>
          </div>
          <div className="segmented-control">
            {RANGE_OPTIONS.map((option) => (
              <button key={option} type="button" className={chartRange === option ? "segmented-active" : ""} onClick={() => setChartRange(option)}>
                {option}
              </button>
            ))}
          </div>
        </div>
        {loading ? <div className="chart-empty">Loading chart...</div> : <PriceChart chart={tradeContext?.chart} title={`${selectedSymbol} price`} />}
      </section>

      <section className="panel trade-ticket-panel">
        <div className="section-head">
          <div>
            <h2>Trade ticket</h2>
            <p className="muted">Choose how much money you want to put to work.</p>
          </div>
          <span className="inline-paper-pill">Paper only</span>
        </div>

        <label>
          <span>Amount to invest</span>
          <input value={amount} onChange={(event) => setAmount(event.target.value)} placeholder="100" inputMode="decimal" />
          <small className="helper-text">Enter a dollar amount. We estimate shares for you before anything is sent.</small>
        </label>

        <div className="chip-row amount-row">
          {quickAmounts.map((value) => (
            <button key={value} type="button" className="chip" onClick={() => setAmount(String(value))}>
              {value === quickAmounts[quickAmounts.length - 1] && value > 1000 ? "Max" : formatCurrency(value, { maximumFractionDigits: 0 })}
            </button>
          ))}
        </div>

        <div className="ticket-summary">
          <TicketMetric label="Estimated shares" value={formatNumber(amountNumber / Math.max(Number(quote.last_price || 0), 0.0001), 6)} />
          <TicketMetric label="Buying power" value={formatCurrency(account.buying_power)} />
          <TicketMetric label="Current position" value={position ? formatNumber(position.qty, 6) : "No position"} />
        </div>

        {tradeContext?.manual_trading_reason && <div className="inline-banner warn">{tradeContext.manual_trading_reason}</div>}
        {tradeContext?.manual_trade_warning && <div className="inline-banner warn">{tradeContext.manual_trade_warning}</div>}
        {message && <div className="inline-banner success">{message}</div>}
        {error && <div className="inline-banner error">{error}</div>}

        <div className="button-stack">
          <button type="button" className="primary-button buy-button" disabled={!canSubmit || previewPending || amountNumber <= 0} onClick={() => requestPreview("buy")}>
            Preview buy
          </button>
          <button type="button" className="danger-button" disabled={!canSubmit || previewPending || amountNumber <= 0 || !position} onClick={() => requestPreview("sell")}>
            Preview sell
          </button>
          <button
            type="button"
            className="ghost-button"
            disabled={commandPending || !position}
            onClick={() => sendCommand("close_symbol", { symbol: selectedSymbol }, { confirm: true })}
          >
            Exit current position now
          </button>
        </div>
      </section>

      <section className="panel position-panel">
        <div className="section-head">
          <div>
            <h2>Position details</h2>
            <p className="muted">See what is open right now and how it is performing.</p>
          </div>
        </div>
        {!tradeContext?.position_summary ? (
          <div className="empty-card">
            <h3>No open position</h3>
            <p>You are flat on {selectedSymbol}. Preview a buy to open a paper position when manual trading is available.</p>
          </div>
        ) : (
          <div className="detail-card-grid">
            <InfoCard label="Entry price" value={formatCurrency(tradeContext.position_summary.entry_price)} />
            <InfoCard label="Current price" value={formatCurrency(tradeContext.position_summary.current_price)} />
            <InfoCard label="Unrealized PnL" value={formatCurrency(tradeContext.position_summary.unrealized_pnl)} tone={pnlTone(tradeContext.position_summary.unrealized_pnl)} />
            <InfoCard label="Realized PnL" value={formatCurrency(tradeContext.position_summary.realized_pnl)} tone={pnlTone(tradeContext.position_summary.realized_pnl)} />
            <InfoCard label="Time in trade" value={tradeContext.position_summary.time_in_trade_minutes ? `${tradeContext.position_summary.time_in_trade_minutes} min` : "n/a"} />
            <InfoCard label="Stop / target" value={tradeContext.position_summary.stop_price ? `${formatCurrency(tradeContext.position_summary.stop_price)} / ${formatCurrency(tradeContext.position_summary.target_price)}` : "No active stop or target"} />
          </div>
        )}
      </section>

      <section className="panel panel-span-2">
        <details className="advanced-details">
          <summary>Advanced trade details</summary>
          <pre className="json-block">{JSON.stringify(tradeContext, null, 2)}</pre>
        </details>
      </section>

      <TradePreviewModal preview={preview} pending={previewPending} onCancel={() => setPreview(null)} onConfirm={submitPreview} />
    </div>
  );
}

function TicketMetric({ label, value }) {
  return (
    <div className="ticket-metric">
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}

function InfoCard({ label, value, tone = "neutral" }) {
  return (
    <div className={`info-card tone-${tone}`}>
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}