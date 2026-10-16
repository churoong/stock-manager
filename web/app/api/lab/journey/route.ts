import { NextResponse } from "next/server";
import { execute, explain, ifMissingTable, rowsToObjects } from "@/lib/db";
import {
  JOURNEY_BROKER_SQL,
  JOURNEY_DECISIONS_SQL,
  JOURNEY_ORDERS_SQL,
  JOURNEY_SIGNALS_SQL,
  JOURNEY_STOCK_SQL,
  type JourneyRaw,
} from "@/lib/lab";

/**
 * 종목 여정 — 한 종목의 추천 → 결정 → 주문 → 가상 체결 → KIS 주문 (docs/desk.md 5장, docs/infra.md 25.1506). 미들웨어가 인증 뒤에 둔다.
 * `?ticker=005930` 또는 `?stock=123`. 같은 티커가 여럿이면 고를 목록(시장·상태 함께, 활성 먼저). 원장마다 종목·날짜 색인으로 최근 120줄까지 —
 * 자를 날은 화면이 `journeyCut` 으로 맞춘다.
 */
const TICKER = /^[A-Za-z0-9.\-]{1,12}$/;

export async function GET(request: Request) {
  const url = new URL(request.url);
  const ticker = url.searchParams.get("ticker")?.trim() ?? "";
  const stockParam = url.searchParams.get("stock");
  try {
    type Stock = { id: number; ticker: string; country: string; market: string; status: string; name: string };
    let stock: Stock | null = null;
    if (stockParam != null) {
      if (!/^\d{1,9}$/.test(stockParam)) return NextResponse.json({ errors: ["종목 번호가 올바르지 않습니다"] }, { status: 400 });
      const r = rowsToObjects<Stock>(
        await execute("SELECT id, ticker, country, market, status, COALESCE(name_ko, name_en, ticker) AS name FROM stocks WHERE id = ?", [Number(stockParam)]),
      );
      stock = r[0] ?? null;
    } else {
      if (!TICKER.test(ticker)) return NextResponse.json({ errors: ["티커를 넣어 주세요(영문·숫자 12자까지)"] }, { status: 400 });
      const r = rowsToObjects<Stock>(await execute(JOURNEY_STOCK_SQL, [ticker.toUpperCase()]));
      if (r.length > 1) return NextResponse.json({ choices: r });
      stock = r[0] ?? null;
    }
    if (!stock) return NextResponse.json({ stock: null });
    const 읽기 = async <T,>(sql: string): Promise<T[]> => {
      const res = await execute(sql, [stock!.id]).catch(ifMissingTable(null));
      return res ? rowsToObjects<T>(res) : [];
    };
    const [signals, decisions, orders, broker] = await Promise.all([
      읽기<JourneyRaw["signals"][number]>(JOURNEY_SIGNALS_SQL),
      읽기<JourneyRaw["decisions"][number]>(JOURNEY_DECISIONS_SQL),
      읽기<JourneyRaw["orders"][number]>(JOURNEY_ORDERS_SQL),
      읽기<JourneyRaw["broker"][number]>(JOURNEY_BROKER_SQL),
    ]);
    return NextResponse.json({ stock, raw: { signals, decisions, orders, broker } satisfies JourneyRaw });
  } catch (e) {
    return NextResponse.json({ errors: [explain(e instanceof Error ? e.message : String(e))] }, { status: 500 });
  }
}
