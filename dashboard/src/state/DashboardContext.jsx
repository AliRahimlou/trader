import React, { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";
import { fetchJson, getApiBase, postControl } from "../api";

const DashboardContext = createContext(null);

const STREAM_EVENTS = [
  "runner_start",
  "runner_error",
  "market_snapshot",
  "signal_evaluated",
  "entry_submitted",
  "entry_order_update",
  "position_open",
  "trade_closed",
  "flatten_submitted",
  "flatten_order_update",
  "audit_command_requested",
  "audit_command_applied",
  "audit_command_rejected",
  "audit_command_failed",
  "entries_pause_updated",
  "strategy_enabled_updated",
  "symbol_enabled_updated",
  "runtime_settings_updated",
  "stale_data_halt",
];

export function DashboardProvider({ children }) {
  const [overview, setOverview] = useState(null);
  const [health, setHealth] = useState(null);
  const [config, setConfig] = useState(null);
  const [strategyStatus, setStrategyStatus] = useState(null);
  const [positions, setPositions] = useState([]);
  const [orders, setOrders] = useState([]);
  const [commands, setCommands] = useState([]);
  const [events, setEvents] = useState([]);
  const [diagnostics, setDiagnostics] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [streamConnected, setStreamConnected] = useState(false);
  const [commandPending, setCommandPending] = useState(false);
  const [activeCommandType, setActiveCommandType] = useState("");
  const [commandStatus, setCommandStatus] = useState({ phase: "idle", message: "", type: "" });
  const [operatorMode, setOperatorMode] = useState(() => {
    return window.localStorage.getItem("paper-operator-mode") === "true";
  });
  const refreshTimerRef = useRef(null);
  const periodicRefreshRef = useRef(null);

  const refreshCore = useCallback(async () => {
    const [overviewData, healthData, configData, strategyData, positionsData, ordersData, commandsData] =
      await Promise.all([
        fetchJson("/api/overview"),
        fetchJson("/api/health"),
        fetchJson("/api/config"),
        fetchJson("/api/strategy-status"),
        fetchJson("/api/positions"),
        fetchJson("/api/orders"),
        fetchJson("/api/commands?limit=25"),
      ]);

    setOverview(overviewData);
    setHealth(healthData);
    setConfig(configData);
    setStrategyStatus(strategyData);
    setPositions(positionsData);
    setOrders(ordersData);
    setCommands(commandsData.items || []);
  }, []);

  const refreshEvents = useCallback(async () => {
    const eventsData = await fetchJson("/api/events?limit=200");
    setEvents(eventsData.items || []);
  }, []);

  const refreshDiagnostics = useCallback(async () => {
    const diagnosticsData = await fetchJson("/api/diagnostics");
    setDiagnostics(diagnosticsData);
  }, []);

  const refreshAll = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      await Promise.all([refreshCore(), refreshEvents(), refreshDiagnostics()]);
    } catch (err) {
      setError(err.message || String(err));
    } finally {
      setLoading(false);
    }
  }, [refreshCore, refreshDiagnostics, refreshEvents]);

  useEffect(() => {
    refreshAll();
  }, [refreshAll]);

  useEffect(() => {
    window.localStorage.setItem("paper-operator-mode", operatorMode ? "true" : "false");
  }, [operatorMode]);

  useEffect(() => {
    const source = new EventSource(`${getApiBase()}/api/events/stream`);

    const scheduleRefresh = () => {
      window.clearTimeout(refreshTimerRef.current);
      refreshTimerRef.current = window.setTimeout(() => {
        refreshCore().catch((err) => setError(err.message || String(err)));
        refreshDiagnostics().catch(() => undefined);
      }, 150);
    };

    const handleEvent = (event) => {
      try {
        const payload = JSON.parse(event.data);
        setEvents((current) => {
          const next = [payload, ...current.filter((item) => item.id !== payload.id)];
          return next.slice(0, 200);
        });
      } catch (err) {
        console.error(err);
      }
      scheduleRefresh();
    };

    const handleHeartbeat = (event) => {
      setStreamConnected(true);
      try {
        const payload = JSON.parse(event.data);
        setOverview((current) => {
          if (!current) {
            return current;
          }
          return {
            ...current,
            runner_status: {
              ...(current.runner_status || {}),
              last_heartbeat: payload.ts || current.runner_status?.last_heartbeat,
              running: payload.running ?? current.runner_status?.running,
              startup_state: payload.startup_state || current.runner_status?.startup_state,
            },
          };
        });
        setHealth((current) => {
          if (!current) {
            return current;
          }
          return {
            ...current,
            last_heartbeat: payload.ts || current.last_heartbeat,
            running: payload.running ?? current.running,
          };
        });
      } catch (err) {
        console.error(err);
      }
    };

    source.onopen = () => setStreamConnected(true);
    source.onerror = () => setStreamConnected(false);
    STREAM_EVENTS.forEach((eventName) => source.addEventListener(eventName, handleEvent));
    source.addEventListener("heartbeat", handleHeartbeat);

    periodicRefreshRef.current = window.setInterval(() => {
      refreshCore().catch((err) => setError(err.message || String(err)));
      refreshDiagnostics().catch(() => undefined);
    }, 10000);

    return () => {
      window.clearTimeout(refreshTimerRef.current);
      window.clearInterval(periodicRefreshRef.current);
      source.close();
    };
  }, [refreshCore, refreshDiagnostics]);

  const sendCommand = useCallback(
    async (commandType, payload = {}, { confirm = false } = {}) => {
      if (commandPending) {
        throw new Error("Another operator command is already in flight.");
      }
      setCommandPending(true);
      setActiveCommandType(commandType);
      setCommandStatus({
        phase: "pending",
        type: commandType,
        message: commandType === "start_runner" ? "Waiting for runner startup and reconciliation." : `Applying ${commandType}.`,
      });
      try {
        const command = await postControl(commandType, payload, confirm);
        await Promise.all([refreshCore(), refreshEvents(), refreshDiagnostics()]);
        setCommandStatus({
          phase: "success",
          type: commandType,
          message: `${commandType} applied.`,
        });
        return command;
      } catch (error) {
        setCommandStatus({
          phase: "error",
          type: commandType,
          message: error.message || String(error),
        });
        throw error;
      } finally {
        setCommandPending(false);
        setActiveCommandType("");
      }
    },
    [commandPending, refreshCore, refreshDiagnostics, refreshEvents],
  );

  const value = useMemo(
    () => ({
      overview,
      health,
      config,
      strategyStatus,
      positions,
      orders,
      commands,
      events,
      diagnostics,
      loading,
      error,
      streamConnected,
      commandPending,
      activeCommandType,
      commandStatus,
      operatorMode,
      setOperatorMode,
      refreshAll,
      sendCommand,
    }),
    [
      commands,
      config,
      diagnostics,
      error,
      events,
      health,
      loading,
      activeCommandType,
      commandStatus,
      commandPending,
      operatorMode,
      orders,
      overview,
      positions,
      refreshAll,
      sendCommand,
      streamConnected,
      strategyStatus,
    ],
  );

  return <DashboardContext.Provider value={value}>{children}</DashboardContext.Provider>;
}

export function useDashboard() {
  const context = useContext(DashboardContext);
  if (!context) {
    throw new Error("useDashboard must be used within DashboardProvider");
  }
  return context;
}
