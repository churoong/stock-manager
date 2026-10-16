import { NextResponse } from "next/server";
import { execute, rowsToObjects } from "@/lib/db";
import { issueTexts } from "@/lib/validationErrors";
import {
  CANCEL_INSERT,
  FLOW_BY_ID,
  FLOW_BY_KEY,
  PAPER_LEDGER_VERSION,
  PAPER_CASH,
  PAPER_FX_LATEST,
  PAPER_INVESTED,
  PAPER_OPEN_BUYS,
  PAPER_OPEN_BUYS_LEGACY,
  cancelBlock,
  cancelSchema,
  freshFx,
  kstToday,
  reservedByAccount,
  withLegacy,
  type OpenBuy,
} from "@/lib/paper";

type Params = { params: Promise<{ id: string }> };

/**
 * 입금 취소 (docs/paper.md 5장). 지우지 않고 **취소 행**(음수, 원래 행을 가리킴)을 더한다. 한 입금은 한 번만 취소된다(유일).
 * 그 돈으로 이미 사서 어느 계좌든 (현금 − 미체결 매수 예약)이 모자라면 막는다.
 */
export async function POST(request: Request, { params }: Params) {
  const id = Number((await params).id);
  if (!Number.isInteger(id) || id <= 0) return NextResponse.json({ errors: ["잘못된 번호"] }, { status: 400 });
  let body: unknown;
  try {
    body = await request.json();
  } catch {
    return NextResponse.json({ errors: ["요청 본문을 읽지 못했습니다"] }, { status: 400 });
  }
  const parsed = cancelSchema.safeParse(body);
  if (!parsed.success) return NextResponse.json({ errors: issueTexts(parsed.error.issues) }, { status: 400 });
  try {
    // 판(version)을 먼저 읽는다 — 판정에 쓴 현금이 이 판보다 새것이어도 넣을 때 판이 같으면 그 사이 바뀐 것이 없다
    const 판 = rowsToObjects<{ n: number; s: number; f: number }>(await execute(PAPER_LEDGER_VERSION))[0];
    const [flow, cash, buys, fx] = await Promise.all([
      execute(FLOW_BY_ID, [id]),
      execute(PAPER_CASH),
      withLegacy((q) => execute(q), PAPER_OPEN_BUYS, PAPER_OPEN_BUYS_LEGACY),
      execute(PAPER_FX_LATEST),
    ]);
    const 행 = rowsToObjects<{ amount_krw: number; kind: string; cancelled: number }>(flow)[0];
    const 환율 = freshFx(rowsToObjects<{ rate: number; date: string }>(fx)[0], kstToday());
    const 예약 = reservedByAccount(rowsToObjects<OpenBuy>(buys), 환율);
    const 여유 = Object.fromEntries(
      rowsToObjects<{ account_id: string; cash_krw: number }>(cash).map((r) => {
        const 잡힘 = 예약[r.account_id];
        return [r.account_id, 잡힘 === null ? null : Number(r.cash_krw) - (잡힘 ?? 0)];
      }),
    );
    const 막음 = cancelBlock(행 ? { ...행, amount_krw: Number(행.amount_krw) } : undefined, 여유);
    if (막음) return NextResponse.json({ errors: [막음] }, { status: 409 });
    const 금액 = Number(행!.amount_krw);
    const rs = await execute(CANCEL_INSERT, [
      kstToday(), -금액, id, `입금 #${id} 취소`, new Date().toISOString(), parsed.data.request_key,
      Number(판?.n ?? 0), Number(판?.s ?? 0), Number(판?.f ?? 0),
    ]);
    if (rs.affectedRows === 0) {
      // 같은 키가 이미 들어갔으면(두 번 누름) 성공, 아니면 그 사이 장부가 바뀐 것 — 넣지 않았다
      const 같은키 = rowsToObjects(await execute(FLOW_BY_KEY, [parsed.data.request_key]))[0];
      if (!같은키) return NextResponse.json({ errors: ["그 사이 다른 입금·취소·체결이 들어왔습니다 — 다시 눌러 주세요"] }, { status: 409 });
    }
    const invested = Number(rowsToObjects<{ invested: number }>(await execute(PAPER_INVESTED))[0]?.invested ?? 0);
    return NextResponse.json({ ok: true, already: rs.affectedRows === 0, invested });
  } catch (error) {
    return NextResponse.json({ errors: [error instanceof Error ? error.message : "취소 실패"] }, { status: 500 });
  }
}
