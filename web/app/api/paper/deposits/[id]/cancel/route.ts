import { NextResponse } from "next/server";
import { execute, rowsToObjects } from "@/lib/db";
import { issueTexts } from "@/lib/validationErrors";
import {
  CANCEL_INSERT,
  FLOW_BY_ID,
  PAPER_CASH,
  PAPER_FX_LATEST,
  PAPER_INVESTED,
  PAPER_OPEN_BUYS,
  cancelBlock,
  cancelSchema,
  kstToday,
  reservedByAccount,
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
    const [flow, cash, buys, fx] = await Promise.all([
      execute(FLOW_BY_ID, [id]),
      execute(PAPER_CASH),
      execute(PAPER_OPEN_BUYS),
      execute(PAPER_FX_LATEST),
    ]);
    const 행 = rowsToObjects<{ amount_krw: number; kind: string; cancelled: number }>(flow)[0];
    const 환율 = rowsToObjects<{ rate: number }>(fx)[0]?.rate ?? null;
    const 예약 = reservedByAccount(rowsToObjects<OpenBuy>(buys), 환율 == null ? null : Number(환율));
    const 여유 = Object.fromEntries(
      rowsToObjects<{ account_id: string; cash_krw: number }>(cash).map((r) => {
        const 잡힘 = 예약[r.account_id];
        return [r.account_id, 잡힘 === null ? null : Number(r.cash_krw) - (잡힘 ?? 0)];
      }),
    );
    const 막음 = cancelBlock(행 ? { ...행, amount_krw: Number(행.amount_krw) } : undefined, 여유);
    if (막음) return NextResponse.json({ errors: [막음] }, { status: 409 });
    const 금액 = Number(행!.amount_krw);
    const rs = await execute(CANCEL_INSERT, [kstToday(), -금액, id, `입금 #${id} 취소`, new Date().toISOString(), parsed.data.request_key]);
    const invested = Number(rowsToObjects<{ invested: number }>(await execute(PAPER_INVESTED))[0]?.invested ?? 0);
    return NextResponse.json({ ok: true, already: rs.affectedRows === 0, invested });
  } catch (error) {
    return NextResponse.json({ errors: [error instanceof Error ? error.message : "취소 실패"] }, { status: 500 });
  }
}
