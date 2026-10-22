import { NextResponse } from "next/server";
import { execute, explain, rowsToObjects } from "@/lib/db";
import { BROKER_KEYS, withLegacy } from "@/lib/paper";
import { KIS_REALIZED, KIS_REALIZED_LEGACY, validRange, type RealizedRow } from "@/lib/paperRealized";

/**
 * KIS모의투자 기간 실현손익 — 시장 하나·기간 하나의 체결 매도(배치가 우리 주문 기록으로 센 실현손익·환차 몫 그대로)
 * (docs/paper.md 15장, docs/infra.md 25.1694). 미들웨어가 인증 뒤에 둔다. 색인 (broker, trade_date)
 */
export async function GET(request: Request) {
  const url = new URL(request.url);
  const market = url.searchParams.get("market");
  const from = url.searchParams.get("from");
  const to = url.searchParams.get("to");
  if (market !== "KR" && market !== "US") return NextResponse.json({ errors: ["시장은 KR 또는 US"] }, { status: 400 });
  if (!validRange(from, to)) return NextResponse.json({ errors: ["기간은 YYYY-MM-DD, 시작 ≤ 끝"] }, { status: 400 });
  try {
    const 인자 = [market === "KR" ? "KRW" : "USD", BROKER_KEYS[market], from, to as string];
    const rs = await withLegacy((q) => execute(q, 인자), KIS_REALIZED, KIS_REALIZED_LEGACY);
    return NextResponse.json({ rows: rowsToObjects<RealizedRow>(rs) });
  } catch (e) {
    return NextResponse.json({ errors: [explain(e instanceof Error ? e.message : String(e))] }, { status: 500 });
  }
}
