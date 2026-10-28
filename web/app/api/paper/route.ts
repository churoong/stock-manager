import { NextResponse } from "next/server";
import { execute, rowsToObjects } from "@/lib/db";
import {
  PAPER_ACCOUNTS,
  PAPER_ACCOUNTS_LEGACY,
  PAPER_CASH,
  PAPER_FILLS,
  PAPER_FLOWS,
  PAPER_FX_LATEST,
  PAPER_HISTORY,
  PAPER_INVESTED,
  PAPER_LATEST_DAILY,
  PAPER_LATEST_ATTR,
  PAPER_MEETINGS_LATEST,
  PAPER_DOSSIERS_LATEST,
  PAPER_OPEN_BUYS,
  PAPER_OPEN_BUYS_LEGACY,
  PAPER_OPEN_ORDERS,
  PAPER_OPEN_ORDERS_LEGACY,
  PAPER_STOCK_NAMES,
  PAPER_TWR,
  HALT_READ,
  SHADOW_READ,
  haltOn,
  shadowLedgers,
  REAL_LOOKBACK_DAYS,
  REAL_TWR,
  accountView,
  compareReal,
  freshFx,
  kstToday,
  reservedByAccount,
  withLegacy,
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
      withLegacy((q) => execute(q), PAPER_ACCOUNTS, PAPER_ACCOUNTS_LEGACY),
      execute(PAPER_INVESTED),
      execute(PAPER_CASH),
      withLegacy((q) => execute(q), PAPER_OPEN_BUYS, PAPER_OPEN_BUYS_LEGACY),
      execute(PAPER_FX_LATEST),
      execute(PAPER_LATEST_DAILY),
      execute(PAPER_FLOWS),
      execute(PAPER_FILLS),
      withLegacy((q) => execute(q), PAPER_OPEN_ORDERS, PAPER_OPEN_ORDERS_LEGACY),
    ]);
    const 시작 = new Date(Date.now() - HISTORY_DAYS * 86_400_000).toISOString().slice(0, 10);
    const history = rowsToObjects(await execute(PAPER_HISTORY, [시작]));
    const 입금 = Number(rowsToObjects<{ invested: number }>(invested)[0]?.invested ?? 0);
    const 현금 = Object.fromEntries(rowsToObjects<{ account_id: string; cash_krw: number }>(cash).map((r) => [r.account_id, Number(r.cash_krw)]));
    // 취소 판정과 같은 환율 — 묵은 환율이면 달러 예약을 모름으로(25.1486)
    const 환율 = freshFx(rowsToObjects<{ rate: number; date: string }>(fx)[0], kstToday());
    const 예약 = reservedByAccount(rowsToObjects<OpenBuy>(buys), 환율);
    const 마지막 = rowsToObjects<DailyRow & { positions_json: string; unknown_json: string | null; attribution_json: string | null }>(latest);
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
    // 성과 분해는 적힌 가장 최근 행에서 — 그날 평가 행에 아직 없으면 그 앞 행(기준일을 붙여 보인다, 25.1602)
    const 분해행 = rowsToObjects<{ account_id: string; as_of: string; attribution_json: string }>(await execute(PAPER_LATEST_ATTR));
    const 성과분해 = (id: string) => {
      const r = 분해행.find((x) => x.account_id === id);
      if (!r) return null;
      try {
        return { ...JSON.parse(r.attribution_json), as_of: r.as_of };
      } catch {
        return null;
      }
    };
    const views = rowsToObjects<{
      id: string; name: string; sell_rule: string; rule_version: number;
      // 계좌 규칙 칸 (migrations/0075, 25.1458) — 화면의 규칙 글·계좌 비교가 읽는다
      entry_rule: string | null; top_n: number | null; selection: string | null; charter_json?: string | null;
    }>(accounts).map((a) => ({
      ...a,
      view: accountView(a.id, 입금, 현금[a.id] ?? 입금, 예약[a.id] === undefined ? 0 : 예약[a.id], 마지막.find((d) => d.account_id === a.id)),
      positions: (보유들[a.id] ?? []).map((p) => ({ ...p, ...(이름[Number(p.stock_id)] ?? {}) })),
      unknown: JSON.parse(마지막.find((d) => d.account_id === a.id)?.unknown_json || "[]"),
      attribution: 성과분해(a.id),
    }));
    // 실제 투자와 같은 기간 비교 (25.1448) — 실제 표는 읽기만. 못 읽으면 비교만 빠진다
    let realCompare = null;
    try {
      const 모의: Record<string, Array<[string, number]>> = {};
      for (const r of rowsToObjects<{ account_id: string; as_of: string; twr_index: number }>(await execute(PAPER_TWR))) {
        (모의[r.account_id] ??= []).push([r.as_of, Number(r.twr_index)]);
      }
      const 처음 = Object.values(모의).map((v) => v[0]?.[0]).filter(Boolean).sort()[0];
      if (처음) {
        const 부터 = new Date(Date.parse(`${처음}T00:00:00Z`) - REAL_LOOKBACK_DAYS * 86_400_000).toISOString().slice(0, 10);
        const 실제 = rowsToObjects<{ date: string; twr_index: number }>(await execute(REAL_TWR, [부터])).map(
          (r) => [r.date, Number(r.twr_index)] as [string, number],
        );
        realCompare = compareReal(모의, 실제);
      }
    } catch {
      realCompare = { error: "실제 포트폴리오 기록을 읽지 못해 비교를 싣지 않았습니다" };
    }
    // 투자위원회 최근 회의록·조사 자료 (docs/company.md, 25.1469) — 0076 전 DB 에는 표가 없다. 못 읽으면 회의록만 빠진다
    let meetings: unknown[] = [];
    let dossiers: unknown[] = [];
    try {
      meetings = rowsToObjects(await execute(PAPER_MEETINGS_LATEST));
      dossiers = rowsToObjects(await execute(PAPER_DOSSIERS_LATEST));
    } catch {
      meetings = [];
      dossiers = [];
    }
    // KIS모의투자는 따로 읽는다 — /api/paper/kis (docs/paper.md 15.7, 25.1496)
    let halted = false;
    try {
      halted = haltOn((await execute(HALT_READ)).rows[0]?.[0]);
    } catch {
      halted = false; // 못 읽으면 꺼진 것으로 보인다 — 배치는 제 읽기로 판정한다
    }
    // 단타·스윙 그림자 장부 (25.1875) — 곁다리. 못 읽으면 칸만 빠진다
    let shadow: ReturnType<typeof shadowLedgers> | null = null;
    try {
      shadow = shadowLedgers(rowsToObjects<{ key: string; value: string | null }>(await execute(SHADOW_READ)));
    } catch {
      shadow = null;
    }
    return NextResponse.json({
      halted,
      shadow,
      realCompare,
      meetings,
      dossiers,
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
