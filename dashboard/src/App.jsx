import React from "react";
import { Navigate, Route, Routes } from "react-router-dom";
import AppShell from "./components/AppShell";
import ControlsPage from "./pages/ControlsPage";
import EventLogPage from "./pages/EventLogPage";
import HealthPage from "./pages/HealthPage";
import OrdersPage from "./pages/OrdersPage";
import OverviewPage from "./pages/OverviewPage";
import PositionsPage from "./pages/PositionsPage";
import SettingsPage from "./pages/SettingsPage";
import StrategyMonitorPage from "./pages/StrategyMonitorPage";

export default function App() {
  return (
    <AppShell>
      <Routes>
        <Route path="/" element={<OverviewPage />} />
        <Route path="/strategy" element={<StrategyMonitorPage />} />
        <Route path="/orders" element={<OrdersPage />} />
        <Route path="/positions" element={<PositionsPage />} />
        <Route path="/events" element={<EventLogPage />} />
        <Route path="/controls" element={<ControlsPage />} />
        <Route path="/settings" element={<SettingsPage />} />
        <Route path="/health" element={<HealthPage />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </AppShell>
  );
}
