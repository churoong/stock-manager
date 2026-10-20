import { NextResponse } from "next/server";
import { execute, explain, ifMissingTable, rowsToObjects } from "@/lib/db";
import { kstToday } from "@/lib/paper";
import { DECISIONS_DONE_SQL, DECISIONS_SQL, doneWindow, mergeDecisions, validAccount, type DecisionRow } from "@/lib/paperDecisions";

/**
 * 왜 안 샀나 — 계좌 하나·시장 하나의 안 한 결정과 그 뒤 성과 (docs/desk.md 3장, docs/infra.md 25.1502). 미들웨어가 인증 뒤에 둔다.
 * 탭을 열 때만 부른다(계좌·시장마다 최근 60줄 + 그 뒤 성과가 찬 최근 60줄, 25.1578).
 */
export async function GET(request: Request) {
  const url = new URL(request.url);
  const account = url.searchParams.get("account");
  const market = url.searchParams.get("market");
  if (!validAccount(account)) return NextResponse.json({ errors: ["계좌가 올바르지 않습니다"] }, { status: 400 });
  if (market !== "KR" && market !== "US") return NextResponse.json({ errors: ["시장은 KR 또는 US"] }, { status: 400 });
  try {
    const [최근, 찬것] = await Promise.all([
      execute(DECISIONS_SQL, [account, market]).catch(ifMissingTable(null)),
      execute(DECISIONS_DONE_SQL, [account, market, ...doneWindow(kstToday())]).catch(ifMissingTable(null)),
    ]);
    const 줄 = (r: typeof 최근) => (r ? rowsToObjects<DecisionRow>(r) : []);
    return NextResponse.json({ rows: mergeDecisions(줄(최근), 줄(찬것)) });
  } catch (e) {
    return NextResponse.json({ errors: [explain(e instanceof Error ? e.message : String(e))] }, { status: 500 });
  }
}
