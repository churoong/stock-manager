import { NextResponse } from "next/server";
import { execute } from "@/lib/db";
import { issueTexts } from "@/lib/validationErrors";
import { HALT_UPSERT, haltSchema } from "@/lib/paper";

/**
 * 모의 매매 멈춤 스위치 (docs/paper.md 11장, docs/infra.md 25.1451). 켜면 다음 배치부터 새 주문(매수·매도)을 내지 않고
 * 결정 원장에 "위험 점검: 멈춤 스위치" 로 남긴다. 체결 판정·평가·검산은 그대로 돈다. 실매매로 가면 같은 스위치가 실주문을 멈춘다.
 */
export async function POST(request: Request) {
  let body: unknown;
  try {
    body = await request.json();
  } catch {
    return NextResponse.json({ errors: ["요청 본문을 읽지 못했습니다"] }, { status: 400 });
  }
  const parsed = haltSchema.safeParse(body);
  if (!parsed.success) return NextResponse.json({ errors: issueTexts(parsed.error.issues) }, { status: 400 });
  try {
    const now = new Date().toISOString();
    await execute(HALT_UPSERT, [JSON.stringify({ on: parsed.data.on, at: now, why: parsed.data.why ?? "" }), now]);
    return NextResponse.json({ ok: true, on: parsed.data.on });
  } catch (error) {
    return NextResponse.json({ errors: [error instanceof Error ? error.message : "저장 실패"] }, { status: 500 });
  }
}
