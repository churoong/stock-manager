"use client";

import { useEffect, useRef } from "react";

/**
 * 가격 차트 (lightweight-charts 5.2.1, Apache-2.0).
 *
 * 라이선스가 TradingView 표기와 링크를 요구한다. attributionLogo(차트 안 로고 링크)를 켜 두고
 * 차트 아래에도 문구를 둔다. 로고를 끄면 안 된다.
 *
 * 겹쳐 그리는 선은 배치가 저장한 값뿐이다: 매수 구간 위·아래, 목표가, 손절가, 내 평균 단가.
 */

export interface Bar {
  date: string;
  open: number | null;
  high: number | null;
  low: number | null;
  close: number;
  volume: number | null;
}

export interface Overlay {
  price: number;
  label: string;
  kind: "zone" | "target" | "stop" | "cost";
}

const COLORS = {
  zone: "#2563eb",
  target: "#16a34a",
  stop: "#dc2626",
  cost: "#9333ea",
};

export default function PriceChart({
  bars,
  overlays,
  currency,
  height = 320,
}: {
  bars: Bar[];
  overlays: Overlay[];
  currency: string;
  height?: number;
}) {
  const box = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const el = box.current;
    if (!el || bars.length === 0) return;
    let disposed = false;
    let cleanup = () => {};

    // 서버 렌더링 때 window 를 건드리지 않게 브라우저에서만 불러온다
    import("lightweight-charts").then(({ createChart, CandlestickSeries, HistogramSeries, LineStyle }) => {
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
        localization: {
          priceFormatter: (p: number) =>
            currency === "KRW" ? Math.round(p).toLocaleString("ko-KR") : p.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 }),
        },
        rightPriceScale: { borderVisible: false },
        timeScale: { borderVisible: false },
      });

      // 국내 관례: 오르면 빨강, 내리면 파랑
      const candles = chart.addSeries(CandlestickSeries, {
        upColor: "#dc2626",
        downColor: "#2563eb",
        borderVisible: false,
        wickUpColor: "#dc2626",
        wickDownColor: "#2563eb",
        // 원화는 원 단위, 달러는 센트 단위
        priceFormat: currency === "KRW" ? { type: "price", precision: 0, minMove: 1 } : { type: "price", precision: 2, minMove: 0.01 },
      });
      // 시가·고가·저가가 빈 날(일부 소스)은 종가로 채워 막대 대신 점으로 보이게 한다. 값을 지어내는 것이 아니라 그리기만
      candles.setData(
        bars.map((b) => ({
          time: b.date,
          open: b.open ?? b.close,
          high: b.high ?? b.close,
          low: b.low ?? b.close,
          close: b.close,
        })),
      );

      const volume = chart.addSeries(HistogramSeries, { priceScaleId: "", priceFormat: { type: "volume" } });
      volume.priceScale().applyOptions({ scaleMargins: { top: 0.8, bottom: 0 } });
      volume.setData(
        bars
          .filter((b) => b.volume !== null)
          .map((b) => ({ time: b.date, value: b.volume as number, color: dark ? "#334155" : "#cbd5e1" })),
      );

      for (const o of overlays) {
        candles.createPriceLine({
          price: o.price,
          color: COLORS[o.kind],
          lineWidth: 1,
          lineStyle: o.kind === "zone" ? LineStyle.Dashed : LineStyle.Solid,
          axisLabelVisible: true,
          title: o.label,
        });
      }
      chart.timeScale().fitContent();
      cleanup = () => chart.remove();
    });

    return () => {
      disposed = true;
      cleanup();
    };
  }, [bars, overlays, currency, height]);

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
