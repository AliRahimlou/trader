import React from "react";

export default function PriceChart({ chart, title = "Price" }) {
  const points = chart?.points || [];
  if (!points.length) {
    return <div className="chart-empty">Chart data will appear here when prices are available.</div>;
  }

  const closes = points.map((point) => Number(point.close));
  const min = Math.min(...closes);
  const max = Math.max(...closes);
  const width = 720;
  const height = 280;
  const padding = 20;
  const range = Math.max(max - min, 0.01);
  const toX = (index) => padding + (index / Math.max(points.length - 1, 1)) * (width - padding * 2);
  const toY = (price) => height - padding - ((price - min) / range) * (height - padding * 2);

  const linePath = points
    .map((point, index) => `${index === 0 ? "M" : "L"} ${toX(index).toFixed(2)} ${toY(point.close).toFixed(2)}`)
    .join(" ");
  const areaPath = `${linePath} L ${toX(points.length - 1).toFixed(2)} ${height - padding} L ${toX(0).toFixed(2)} ${height - padding} Z`;

  const markers = (chart?.markers || []).slice(0, 8).map((marker, index) => {
    const markerTime = new Date(marker.time).getTime();
    let closestIndex = 0;
    let closestDistance = Number.POSITIVE_INFINITY;
    points.forEach((point, pointIndex) => {
      const distance = Math.abs(new Date(point.time).getTime() - markerTime);
      if (distance < closestDistance) {
        closestDistance = distance;
        closestIndex = pointIndex;
      }
    });
    return {
      ...marker,
      key: `${marker.time}-${index}`,
      x: toX(closestIndex),
      y: toY(points[closestIndex].close),
    };
  });

  return (
    <div className="chart-card">
      <div className="chart-header">
        <h3>{title}</h3>
        <span>{chart.range} view</span>
      </div>
      <svg viewBox={`0 0 ${width} ${height}`} className="price-chart" role="img" aria-label={`${title} chart`}>
        <defs>
          <linearGradient id="chart-fill" x1="0" x2="0" y1="0" y2="1">
            <stop offset="0%" stopColor="rgba(36, 99, 235, 0.28)" />
            <stop offset="100%" stopColor="rgba(36, 99, 235, 0.02)" />
          </linearGradient>
        </defs>
        <path d={areaPath} fill="url(#chart-fill)" />
        <path d={linePath} fill="none" stroke="#2563eb" strokeWidth="3" strokeLinejoin="round" strokeLinecap="round" />
        {markers.map((marker) => (
          <g key={marker.key}>
            <circle cx={marker.x} cy={marker.y} r="5" fill={marker.label === "BUY" ? "#16a34a" : "#dc2626"} />
            <text x={marker.x + 8} y={marker.y - 8} className="chart-marker-label">
              {marker.label}
            </text>
          </g>
        ))}
      </svg>
      <div className="chart-scale">
        <span>{min.toFixed(2)}</span>
        <span>{max.toFixed(2)}</span>
      </div>
    </div>
  );
}