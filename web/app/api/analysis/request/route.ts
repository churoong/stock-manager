import { NextResponse } from "next/server";
import { z } from "zod";
import { requestAnalysis } from "@/lib/analysis";
import { execute, rowsToObjects } from "@/lib/db";
import { dispatchJob } from "@/lib/dispatch";
import { issueTexts } from "@/lib/validationErrors";

/**
 * "지금 분석" (docs/analysis.md 8장, docs/infra.md 25.1018). 관심 종목에 넣고 `analyze-stock.yml` 을 깨운다.
 * 웹은 계산하지 않는다 — 깨우기만 한다. 결과는 작업이 `stock_verdicts` 에 두고 텔레그램·알림 센터로 알린다.
 * 미들웨어가 이 경로를 인증 뒤에 두므로 여기서 다시 검사하지 않는다.
 */
const inputSchema = z.object({ stock_id: z.number().int().positive() });

export async function POST(request: Request) {
  const parsed = inputSchema.safeParse(await request.json().catch(() => null));
  if (!parsed.success) return NextResponse.json({ errors: issueTexts(parsed.error.issues) }, { status: 400 });
  try {
    const out = await requestAnalysis(
      async (sql, args = []) => rowsToObjects(await execute(sql, args)),
      parsed.data.stock_id, new Date(), (job) => dispatchJob(job),
    );
    if (!out.ok) return NextResponse.json({ errors: [out.error] }, { status: out.status });
    return NextResponse.json({ ok: true, state: out.state });
  } catch (error) {
    return NextResponse.json({ errors: [error instanceof Error ? error.message : "요청에 실패했습니다"] }, { status: 500 });
  }
}
