import { NextResponse } from "next/server";
import { execute, explain, rowsToObjects } from "@/lib/db";
import {
  KIS_LATEST,
  KIS_NAMES,
  KIS_ORDERS,
  KIS_TOTALS,
  KIS_TOTALS_DAYS,
  KIS_TRADE_DAYS,
  parseHoldings,
  type KisMarket,
  type KisMarketView,
  type KisOrder,
  type KisWhole,
} from "@/lib/kisPaper";
import { BROKER_KEYS } from "@/lib/paper";

/**
 * KIS모의투자 웹 보기 A — 배치가 적은 기록만 읽는다 (docs/paper.md 15.7, docs/infra.md 25.1496). 미들웨어가 인증 뒤에 둔다.
 * 0078·0080 전 DB 면 표·칸이 없다 — 그 시장은 빈 보기로.
 */
export async function GET() {
  const 오늘 = Date.now();
  const 전 = (days: number) => new Date(오늘 - days * 86_400_000).toISOString().slice(0, 10);
  let whole: KisWhole = { as_of: null, account_krw: null, perf: null, totals: [] };
  const one = async (market: KisMarket): Promise<KisMarketView> => {
    const key = BROKER_KEYS[market];
    const 빈: KisMarketView = { as_of: null, fetched_at: null, holdings: [], account: null, summary: null, diff: {}, orders: [] };
    try {
      const [latest, orders] = await Promise.all([execute(KIS_LATEST, [key]), execute(KIS_ORDERS, [key, 전(KIS_TRADE_DAYS)])]);
      const r = rowsToObjects<{ as_of: string; holdings_json: string | null; summary_json: string | null; diff_json: string | null; fetched_at: string | null }>(latest)[0];
      let summary: Record<string, unknown> | null = null;
      try {
        summary = r ? JSON.parse(r.summary_json || "null") : null;
      } catch {
        summary = null;
      }
      const holdings = parseHoldings(r?.holdings_json);
      const ids = holdings.map((h) => h.id).filter((x): x is number => x != null);
      if (ids.length) {
        const 이름 = new Map(rowsToObjects<{ id: number; name: string }>(await execute(KIS_NAMES, [JSON.stringify(ids)])).map((x) => [Number(x.id), x.name]));
        for (const h of holdings) h.name = h.id != null ? (이름.get(h.id) ?? null) : null;
      }
      let diff: Record<string, [number, number]> = {};
      try {
        diff = JSON.parse(r?.diff_json || "{}");
      } catch {
        diff = {};
      }
      if (market === "KR" && r) {
        // 계좌 전체 원화 성과·추이는 국내 배치 줄에만 있다(25.1497). 추이를 못 읽어도 보유·거래내역은 보인다(25.1499, 검토 [참고] 11)
        whole = {
          as_of: r.as_of,
          account_krw: (summary?.account_krw as KisWhole["account_krw"]) ?? null,
          perf: (summary?.perf as KisWhole["perf"]) ?? null,
          totals: [],
        };
        try {
          const totals = await execute(KIS_TOTALS, [key, 전(KIS_TOTALS_DAYS), key]);
          whole.totals = rowsToObjects<{ as_of: string; total: number | null }>(totals).map((t) => ({ as_of: t.as_of, total: t.total == null ? null : Number(t.total) }));
        } catch {
          whole.totals_error = true;
        }
      }
      return {
        as_of: r?.as_of ?? null,
        fetched_at: r?.fetched_at ?? null,
        holdings,
        account: (summary?.account as KisMarketView["account"]) ?? null,
        summary: summary as KisMarketView["summary"],
        diff,
        orders: rowsToObjects<KisOrder>(orders),
      };
    } catch (e) {
      // **표·칸이 아직 없을 때만** 빈 보기(0078·0080 전 DB) — 한도·인증 실패까지 삼키면 "아직 낸 주문이 없습니다" 로
      // 보였다(25.1637, 웹 화면 감사 [필수] 3 — 25.163 꼴. 같은 화면의 실행 품질·운용 경로는 이미 이렇게 한다)
      const m = e instanceof Error ? e.message : String(e);
      if (/no such (table|column)/i.test(m)) return 빈;
      throw e;
    }
  };
  try {
    const [KR, US] = await Promise.all([one("KR"), one("US")]);
    return NextResponse.json({ whole, markets: { KR, US } });
  } catch (e) {
    return NextResponse.json({ errors: [explain(e instanceof Error ? e.message : String(e))] }, { status: 500 });
  }
}
