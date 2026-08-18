const PerformanceChart = (() => {
  function nearestPointIndex(svgX, plotLeft, plotRight, pointCount) {
    const clampedX = Math.min(plotRight, Math.max(plotLeft, svgX));
    const ratio = (clampedX - plotLeft) / (plotRight - plotLeft);
    return Math.round(ratio * (pointCount - 1));
  }

  function tooltipLeft(pointX, chartWidth, tooltipWidth) {
    const margin = 8;
    const gap = 12;
    const right = pointX + gap;
    const preferred = right + tooltipWidth <= chartWidth - margin
      ? right
      : pointX - tooltipWidth - gap;
    return Math.max(margin, Math.min(preferred, chartWidth - tooltipWidth - margin));
  }

  function returnPct(value, cost) {
    const numericCost = Number(cost);
    if (numericCost === 0) return null;
    return (Number(value) - numericCost) / numericCost * 100;
  }

  return { nearestPointIndex, tooltipLeft, returnPct };
})();

if (typeof module !== "undefined" && module.exports) {
  module.exports = PerformanceChart;
}
