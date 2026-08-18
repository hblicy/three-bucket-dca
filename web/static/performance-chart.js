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

  return { nearestPointIndex, tooltipLeft };
})();

if (typeof module !== "undefined" && module.exports) {
  module.exports = PerformanceChart;
}
