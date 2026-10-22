import { NextResponse } from "next/server";
import { execute, explain, rowsToObjects } from "@/lib/db";
import { withLegacy } from "@/lib/paper";
import { validAccount } from "@/lib/paperDecisions";
import { PAPER_REALIZED, PAPER_REALIZED_LEGACY, validRange, type RealizedRow } from "@/lib/paperRealized";

/**
 * 가상모의투자 기간 실현손익 — 계좌 하나·기간 하나의 매도(배치가 적은 실현손익·환차 몫 그대로) (docs/paper.md 6장,
 * docs/infra.md 25.1693). 미들웨어가 인증 뒤에 둔다. 탭을 열거나 기간을 바꿀 때만 부른다 — 색인 (account_id, fill_date)
 */
export async function GET(request: Request) {
  const url = new URL(request.url);
  const account = url.searchParams.get("account");
  const from = url.searchParams.get("from");
  const to = url.searchParams.get("to");
  if (!validAccount(account)) return NextResponse.json({ errors: ["계좌가 올바르지 않습니다"] }, { status: 400 });
  if (!validRange(from, to)) return NextResponse.json({ errors: ["기간은 YYYY-MM-DD, 시작 ≤ 끝"] }, { status: 400 });
  try {
    const rs = await withLegacy((q) => execute(q, [account, from, to as string]), PAPER_REALIZED, PAPER_REALIZED_LEGACY);
    return NextResponse.json({ rows: rowsToObjects<RealizedRow>(rs) });
  } catch (e) {
    return NextResponse.json({ errors: [explain(e instanceof Error ? e.message : String(e))] }, { status: 500 });
  }
}
