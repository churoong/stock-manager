"use client";

import { useEffect, useState } from "react";
import EquityChart from "@/components/EquityChart";
import { bodyOf, readJson } from "@/lib/http";
import { qualityLine, qualitySeries, slipSeries, type QualityRow } from "@/lib/kisQuality";

/**
 * KIS모의투자 실행 품질 (docs/paper.md 15.8, docs/infra.md 25.1504) — 가상 체결 판정이 얼마나 낙관적인가.
 * 값은 배치가 날마다 적은 28일 창 비교 요약 그대로다.
 */
const MARKETS: Array<["KR" | "US", string]> = [
  ["KR", "국내"],
  ["US", "미국"],
];

export default function KisQuality() {
  const [data, setData] = useState<Record<"KR" | "US", QualityRow[]> | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    void (async () => {
      const r = await readJson<Record<"KR" | "US", QualityRow[]>>("/api/kis/quality");
      const body = bodyOf<Record<"KR" | "US", QualityRow[]>>(r);
      if (body.errors?.length) setError(body.errors.join(" · "));
      else if (!Array.isArray(body.KR) || !Array.isArray(body.US)) setError("실행 품질을 읽지 못했습니다(응답 꼴이 다름 — 새로 고침해 주세요)");
      else setData({ KR: body.KR, US: body.US });
    })();
  }, []);

  return (
    <section className="rounded-xl border border-slate-200 bg-white p-4 dark:border-slate-800 dark:bg-slate-900">
      <h2 className="text-base font-semibold">실행 품질 — 가상 성적의 할인율</h2>
      <p className="mt-1 text-xs text-slate-500">
        H 의 주문을 KIS모의투자에 그대로 내고, 가상모의투자의 체결 판정과 견줍니다. <b>할인율</b> = 가상이 찼다고 한 주문 가운데 KIS 에서도 찬 비율 — 100% 면 가상
        성적을 그대로 믿어도 되고, 낮을수록 실전에서는 덜 나옵니다. 가격 차이는 불리한 쪽이 +(가상이 낙관). 일치율이 80% 아래로 내려가거나 가격 차이가 +0.5% 를 넘는 첫날 텔레그램으로
        알립니다(문턱은 첫 값 — 한 달 쌓이면 다시 정합니다).
      </p>
      {error ? <p className="mt-2 text-sm text-red-600">{error}</p> : null}
      {!data && !error ? <p className="mt-2 text-sm text-slate-500">불러오는 중…</p> : null}
      {data
        ? MARKETS.map(([m, label]) => {
            const rows = data[m];
            const 일치 = qualitySeries(rows);
            const 가격 = slipSeries(rows);
            return (
              <div key={m} className="mt-3">
                <h3 className="text-sm font-medium">{label}</h3>
                <p className="text-sm">{qualityLine(rows[rows.length - 1])}</p>
                {일치.length && 일치[0].points.length > 1 ? <EquityChart series={일치} height={180} format="pct" /> : null}
                {가격.length && 가격[0].points.length > 1 ? <EquityChart series={가격} height={140} format="pct" /> : null}
              </div>
            );
          })
        : null}
    </section>
  );
}
