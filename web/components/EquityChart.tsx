"use client";

import { useEffect, useRef } from "react";

/**
 * 자본곡선 차트 (lightweight-charts 5.2.1, Apache-2.0).
 *
 * 백테스트 결과를 나란히 겹쳐 본다. 시작 1.0 이 기준이고, 선 하나가 전략 하나다.
 * PriceChart 와 같은 라이선스 규칙: attributionLogo 를 켜고 아래에도 표기를 둔다. 끄면 안 된다.
 *
 * 값은 배치가 저장한 backtest_curves 를 그대로 그린다. 화면에서 보정하거나 이어 붙이지 않는다.
 */

export interface Series {
  label: string;
  color: string;
  points: Array<{ date: string; value: number }>;
}

/** ratio: 시작 1.0 대비 배수(1.234). pct: 비율을 %로(−12.3%). 낙폭 차트가 pct 다 */
export default function EquityChart({ series, height = 260, format = "ratio" }: { series: Series[]; height?: number; format?: "ratio" | "pct" }) {
  const box = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const el = box.current;
    if (!el || series.every((s) => s.points.length === 0)) return;
    let disposed = false;
    let cleanup = () => {};

    // 서버 렌더링 때 window 를 건드리지 않게 브라우저에서만 불러온다
    import("lightweight-charts").then(({ createChart, LineSeries }) => {
      if (disposed) return;
      const dark = window.matchMedia("(prefers-color-scheme: dark)").matches;
      const chart = createChart(el, {
        height,
        autoSize: true,
        layout: {
          background: { color: "transparent" },
          textColor: dark ? "#cbd5e1" : "#334155",
          attributionLogo: true,
        },
        grid: {
          vertLines: { color: dark ? "#1e293b" : "#f1f5f9" },
          horzLines: { color: dark ? "#1e293b" : "#f1f5f9" },
        },
        // 시작 1.0 대비 배수다. 1.234 처럼 세 자리면 충분하다. 낙폭은 %
        localization: { priceFormatter: (p: number) => (format === "pct" ? `${(p * 100).toFixed(1)}%` : p.toFixed(3)) },
        rightPriceScale: { borderVisible: false },
        timeScale: { borderVisible: false },
      });

      for (const s of series) {
        if (s.points.length === 0) continue;
        const line = chart.addSeries(LineSeries, {
          color: s.color,
          lineWidth: 2,
          title: s.label,
          priceFormat: format === "pct" ? { type: "price", precision: 4, minMove: 0.0001 } : { type: "price", precision: 3, minMove: 0.001 },
        });
        line.setData(s.points.map((p) => ({ time: p.date, value: p.value })));
      }
      chart.timeScale().fitContent();
      cleanup = () => chart.remove();
    });

    return () => {
      disposed = true;
      cleanup();
    };
  }, [series, height, format]);

  return (
    <div>
      <div ref={box} style={{ height }} className="w-full" />
      <p className="mt-1 text-[10px] text-slate-400">
        Charts by{" "}
        <a href="https://www.tradingview.com/" target="_blank" rel="noopener noreferrer" className="underline">
          TradingView Lightweight Charts™
        </a>{" "}
        · Copyright (c) TradingView, Inc.
      </p>
    </div>
  );
}
