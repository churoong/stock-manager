import { NextResponse } from "next/server";
import { z } from "zod";
import { execute, rowsToObjects } from "@/lib/db";
import { issueTexts } from "@/lib/validationErrors";
import { WATCH_PRICE_BASIS, targetAboveCloseWarning, targetPriceProblem } from "@/lib/watch";

/**
 * 관심 종목 (docs/intraday.md). 장중 경로가 바로 읽으므로 추가하면 다음 5분 호출부터 감시한다.
 * 미들웨어가 이 경로를 인증 뒤에 두므로 여기서 다시 검사하지 않는다.
 */
const inputSchema = z.object({
  stock_id: z.number().int().positive(),
  target_buy_price: z.number().positive().nullable().optional(),
  memo: z.string().max(200).nullable().optional(),
});

export async function GET() {
  try {
    const rs = await execute(
      `SELECT w.id, w.stock_id, s.ticker, s.country, s.currency, COALESCE(s.name_ko, s.name_en, s.ticker) AS name,
              w.target_buy_price, w.alert_enabled, w.memo, w.added_at,
              (SELECT close FROM prices WHERE stock_id = w.stock_id AND close IS NOT NULL ORDER BY date DESC LIMIT 1) AS last_close,
              (SELECT date FROM prices WHERE stock_id = w.stock_id AND close IS NOT NULL ORDER BY date DESC LIMIT 1) AS last_date
       FROM watchlist w JOIN stocks s ON s.id = w.stock_id ORDER BY s.country, w.added_at DESC`,
    );
    return NextResponse.json({ watchlist: rowsToObjects(rs) });
  } catch (error) {
    return NextResponse.json({ errors: [error instanceof Error ? error.message : "조회 실패"] }, { status: 500 });
  }
}

export async function POST(request: Request) {
  const parsed = inputSchema.safeParse(await request.json().catch(() => null));
  if (!parsed.success) return NextResponse.json({ errors: issueTexts(parsed.error.issues) }, { status: 400 });
  const input = parsed.data;
  try {
    // 없는 종목 번호로 고아 행을 만들지 않는다 — 목록에도 장중 감시에도 안 보인다 (25.814, 감사)
    if (!(await execute("SELECT 1 FROM stocks WHERE id = ?", [input.stock_id])).rows.length) {
      return NextResponse.json({ errors: ["그런 종목이 없습니다"] }, { status: 400 });
    }
    // 단위 실수로 거짓 "목표 매수가 도달" 이 나가지 않게 (docs/infra.md 25.360)
    let 경고: string | null = null;
    if (input.target_buy_price) {
      const basis = rowsToObjects<{ currency: string; last_close: number | null }>(await execute(WATCH_PRICE_BASIS, [input.stock_id]))[0];
      const problem = basis ? targetPriceProblem(input.target_buy_price, basis.last_close, basis.currency) : null;
      if (problem) return NextResponse.json({ errors: [problem] }, { status: 400 });
      // 종가 이상이면 곧바로 "도달" 이 나간다 — 저장하되 알린다 (25.584)
      경고 = basis ? targetAboveCloseWarning(input.target_buy_price, basis.last_close) : null;
    }
    await execute(
      // **보내지 않은 메모를 지우지 않는다** (2026-09-21, docs/infra.md 25.74).
      // 화면의 "관심 추가" 는 `{stock_id, target_buy_price}` 만 보낸다 — 메모 칸이 없다.
      // 예전에는 `memo = excluded.memo` 라 다시 추가할 때마다 **메모가 NULL 로 덮였다.**
      // 이 경로로 메모를 넣을 길이 없으니 지우기만 하는 셈이었다.
      //
      // **목표 매수가도 "보내지 않음" 과 "비움" 을 가른다** (docs/infra.md 25.251). 예전 주석은 "화면이 매번 명시적으로
      // 보낸다" 였는데 틀렸다 — 추천 목록·종목 상세의 관심 버튼(`lib/watch.addWatch`)은 `{stock_id}` 만 보낸다.
      // 관심 목록을 못 읽은 날 이미 관심인 종목에 "등록" 이 보이고, 누르면 **저장해 둔 목표가가 NULL 로 덮여**
      // 장중 "권장 매수 구간 진입" 알림이 조용히 멈췄다. 키가 있으면(null 포함) 덮고, 없으면 그대로 둔다.
      // 다시 "관심 등록" 단추를 누르면 **알림을 켠다** (25.814, 감사) — 예전에 꺼 둔 종목은 단추가 "등록됨" 으로 바뀌어도 감시가 계속 꺼져 있었다.
      // 목표가·메모를 함께 보내는 "추가" 폼은 켜지 않는다(25.817)
      `INSERT INTO watchlist (stock_id, added_at, memo, target_buy_price, alert_enabled) VALUES (?, ?, ?, ?, 1)
       ON CONFLICT (stock_id) DO UPDATE SET memo = COALESCE(excluded.memo, watchlist.memo),
         alert_enabled = CASE WHEN ? THEN 1 ELSE watchlist.alert_enabled END,
         target_buy_price = CASE WHEN ? THEN excluded.target_buy_price ELSE watchlist.target_buy_price END`,
      [
        input.stock_id, new Date().toISOString(), input.memo ?? null, input.target_buy_price ?? null,
        // 알림을 켜는 것은 **단추만 누른 등록**({stock_id})일 때만 — 목표가·메모를 고치려고 "추가" 폼을 쓴 것은 꺼 둔 알림을 켜지 않는다 (25.817, 교차검증)
        input.target_buy_price === undefined && input.memo === undefined ? 1 : 0,
        input.target_buy_price !== undefined ? 1 : 0,
      ],
    );
    return NextResponse.json({ ok: true, warnings: 경고 ? [경고] : [] });
  } catch (error) {
    return NextResponse.json({ errors: [error instanceof Error ? error.message : "저장 실패"] }, { status: 500 });
  }
}
