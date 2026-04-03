import React from "react";
import { Navigate, Route, Routes } from "react-router-dom";
import AppShell from "./components/AppShell";
import OrdersPage from "./pages/OrdersPage";
import OverviewPage from "./pages/OverviewPage";
import PositionsPage from "./pages/PositionsPage";
import SettingsPage from "./pages/SettingsPage";
import StrategyMonitorPage from "./pages/StrategyMonitorPage";
import TradePage from "./pages/TradePage";

export default function App() {
  return (
    <AppShell>
      <Routes>
        <Route path="/" element={<OverviewPage />} />
        <Route path="/trade" element={<TradePage />} />
        <Route path="/positions" element={<PositionsPage />} />
        <Route path="/activity" element={<OrdersPage />} />
        <Route path="/bot" element={<StrategyMonitorPage />} />
        <Route path="/settings" element={<SettingsPage />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </AppShell>
  );
}
