import { NextResponse } from "next/server";
import { execute, rowsToObjects } from "@/lib/db";
import {
  PAPER_ACCOUNTS,
  PAPER_CASH,
  PAPER_FILLS,
  PAPER_FLOWS,
  PAPER_FX_LATEST,
  PAPER_HISTORY,
  PAPER_INVESTED,
  PAPER_LATEST_DAILY,
  PAPER_OPEN_BUYS,
  PAPER_OPEN_ORDERS,
  PAPER_STOCK_NAMES,
  accountView,
  reservedByAccount,
  type DailyRow,
  type OpenBuy,
} from "@/lib/paper";

/** 그래프에 싣는 기간 — 하루 한 행이라 400행이면 1년 남짓 */
const HISTORY_DAYS = 400;

/**
 * 모의 투자 계좌 화면 (docs/paper.md 6장). 총 입금액·현금은 지금 장부로, 보유 평가는 배치가 마지막으로 잰 값으로.
 * 미들웨어가 이 경로를 인증 뒤에 둔다.
 */
export async function GET() {
  try {
    const [accounts, invested, cash, buys, fx, latest, flows, fills, orders] = await Promise.all([
      execute(PAPER_ACCOUNTS),
      execute(PAPER_INVESTED),
      execute(PAPER_CASH),
      execute(PAPER_OPEN_BUYS),
      execute(PAPER_FX_LATEST),
      execute(PAPER_LATEST_DAILY),
      execute(PAPER_FLOWS),
      execute(PAPER_FILLS),
      execute(PAPER_OPEN_ORDERS),
    ]);
    const 시작 = new Date(Date.now() - HISTORY_DAYS * 86_400_000).toISOString().slice(0, 10);
    const history = rowsToObjects(await execute(PAPER_HISTORY, [시작]));
    const 입금 = Number(rowsToObjects<{ invested: number }>(invested)[0]?.invested ?? 0);
    const 현금 = Object.fromEntries(rowsToObjects<{ account_id: string; cash_krw: number }>(cash).map((r) => [r.account_id, Number(r.cash_krw)]));
    const 환율 = rowsToObjects<{ rate: number }>(fx)[0]?.rate ?? null;
    const 예약 = reservedByAccount(rowsToObjects<OpenBuy>(buys), 환율 == null ? null : Number(환율));
    const 마지막 = rowsToObjects<DailyRow & { positions_json: string; unknown_json: string | null }>(latest);
    // 보유 이름 — 마지막 평가의 보유 목록에 든 종목만
    const 보유들 = Object.fromEntries(
      마지막.map((d) => [d.account_id, JSON.parse(d.positions_json || "[]") as Array<Record<string, unknown>>]),
    );
    const ids = [...new Set(Object.values(보유들).flat().map((p) => Number(p.stock_id)))];
    const 이름 = ids.length
      ? Object.fromEntries(
          rowsToObjects<{ id: number; ticker: string; name: string }>(await execute(PAPER_STOCK_NAMES, [JSON.stringify(ids)])).map(
            (r) => [Number(r.id), r],
          ),
        )
      : {};
    const views = rowsToObjects<{ id: string; name: string; sell_rule: string; rule_version: number }>(accounts).map((a) => ({
      ...a,
      view: accountView(a.id, 입금, 현금[a.id] ?? 입금, 예약[a.id] === undefined ? 0 : 예약[a.id], 마지막.find((d) => d.account_id === a.id)),
      positions: (보유들[a.id] ?? []).map((p) => ({ ...p, ...(이름[Number(p.stock_id)] ?? {}) })),
      unknown: JSON.parse(마지막.find((d) => d.account_id === a.id)?.unknown_json || "[]"),
    }));
    return NextResponse.json({
      invested: 입금,
      accounts: views,
      flows: rowsToObjects(flows),
      fills: rowsToObjects(fills),
      orders: rowsToObjects(orders),
      history,
    });
  } catch (error) {
    return NextResponse.json({ errors: [error instanceof Error ? error.message : "조회 실패"] }, { status: 500 });
  }
}
