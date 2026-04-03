export function formatCurrency(value, { maximumFractionDigits = 2 } = {}) {
  const amount = Number(value);
  if (!Number.isFinite(amount)) {
    return "$0.00";
  }
  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: "USD",
    maximumFractionDigits,
  }).format(amount);
}

export function formatPercent(value) {
  const amount = Number(value);
  if (!Number.isFinite(amount)) {
    return "0.00%";
  }
  return `${amount >= 0 ? "+" : ""}${amount.toFixed(2)}%`;
}

export function formatNumber(value, digits = 4) {
  const amount = Number(value);
  if (!Number.isFinite(amount)) {
    return "0";
  }
  return amount.toLocaleString("en-US", {
    maximumFractionDigits: digits,
  });
}

export function formatSignedCurrency(value) {
  const amount = Number(value);
  return `${amount >= 0 ? "+" : "-"}${formatCurrency(Math.abs(amount))}`;
}

export function formatMinutes(value) {
  const minutes = Number(value);
  if (!Number.isFinite(minutes) || minutes <= 0) {
    return "Just now";
  }
  if (minutes < 60) {
    return `${minutes}m`;
  }
  const hours = Math.floor(minutes / 60);
  const remainder = minutes % 60;
  if (hours < 24) {
    return remainder ? `${hours}h ${remainder}m` : `${hours}h`;
  }
  const days = Math.floor(hours / 24);
  const dayHours = hours % 24;
  return dayHours ? `${days}d ${dayHours}h` : `${days}d`;
}

export function botStateLabel(bot) {
  if (!bot) {
    return "Loading";
  }
  return bot.status_label || "Waiting";
}

export function pnlTone(value) {
  const amount = Number(value);
  if (!Number.isFinite(amount)) {
    return "neutral";
  }
  if (amount > 0) {
    return "positive";
  }
  if (amount < 0) {
    return "negative";
  }
  return "neutral";
}