import { NextResponse } from "next/server";
import { execute, explain, rowsToObjects } from "@/lib/db";
import { issueTexts } from "@/lib/validationErrors";
import { ACCOUNTS_SQL, CHALLENGER_READ, CHALLENGER_UPSERT, CHAMPION, SEPARATE_IDS, challengerOf, challengerSchema } from "@/lib/kisChallenger";

/**
 * KIS모의투자 도전자 고르기 (docs/paper.md 15.10, docs/infra.md 25.1515). 미들웨어가 인증 뒤에 둔다.
 * GET = 지금 도전자·고를 수 있는 계좌, POST = 바꾸기(null 이면 끄기). 주문은 다음 일일 배치가 낸다 — 여기서는 설정만 쓴다
 */
export async function GET() {
  try {
    const [설정, 계좌] = await Promise.all([execute(CHALLENGER_READ), execute(ACCOUNTS_SQL)]);
    const 줄 = rowsToObjects<{ value: string }>(설정)[0];
    const accounts = rowsToObjects<{ id: string; name: string | null }>(계좌).filter((a) => a.id !== CHAMPION && !SEPARATE_IDS.includes(a.id));
    return NextResponse.json({ account: challengerOf(줄?.value), champion: CHAMPION, accounts });
  } catch (e) {
    return NextResponse.json({ errors: [explain(e instanceof Error ? e.message : String(e))] }, { status: 500 });
  }
}

export async function POST(request: Request) {
  let body: unknown;
  try {
    body = await request.json();
  } catch {
    return NextResponse.json({ errors: ["요청 본문을 읽지 못했습니다"] }, { status: 400 });
  }
  const parsed = challengerSchema.safeParse(body);
  if (!parsed.success) return NextResponse.json({ errors: issueTexts(parsed.error.issues) }, { status: 400 });
  try {
    if (parsed.data.account) {
      const 있음 = rowsToObjects<{ id: string }>(await execute(ACCOUNTS_SQL)).some((a) => a.id === parsed.data.account);
      if (!있음) return NextResponse.json({ errors: [`계좌 ${parsed.data.account} 가 없습니다`] }, { status: 400 });
    }
    const now = new Date().toISOString();
    await execute(CHALLENGER_UPSERT, [JSON.stringify({ account: parsed.data.account, at: now }), now]);
    return NextResponse.json({ ok: true, account: parsed.data.account });
  } catch (e) {
    return NextResponse.json({ errors: [explain(e instanceof Error ? e.message : String(e))] }, { status: 500 });
  }
}
