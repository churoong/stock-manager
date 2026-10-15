import { NextResponse } from "next/server";
import { execute, rowsToObjects } from "@/lib/db";
import { issueTexts } from "@/lib/validationErrors";
import { DEPOSIT_INSERT, FLOW_BY_KEY, PAPER_INVESTED, depositSchema, kstToday } from "@/lib/paper";

/**
 * 모의 계좌 입금 (docs/paper.md 5장). 한 번이 모든 계좌(A·B)에 같은 금액으로 들어간다.
 * 같은 요청 키는 한 번만 들어간다 — 두 번 눌러도, 응답이 끊겨 다시 보내도 장부는 한 줄이다.
 * 응답에 **입금 뒤 총 투자금액**을 싣는다 — 화면이 확인 창에서 미리 보여 준 값과 같은지 사용자가 볼 수 있게.
 */
export async function POST(request: Request) {
  let body: unknown;
  try {
    body = await request.json();
  } catch {
    return NextResponse.json({ errors: ["요청 본문을 읽지 못했습니다"] }, { status: 400 });
  }
  const parsed = depositSchema.safeParse(body);
  if (!parsed.success) return NextResponse.json({ errors: issueTexts(parsed.error.issues) }, { status: 400 });
  const input = parsed.data;
  try {
    const rs = await execute(DEPOSIT_INSERT, [kstToday(), input.amount_krw, input.memo || null, new Date().toISOString(), input.request_key]);
    const 이미 = rs.affectedRows === 0;
    const 행 = rowsToObjects<{ id: number; amount_krw: number; kind: string }>(await execute(FLOW_BY_KEY, [input.request_key]))[0];
    if (이미 && (!행 || 행.kind !== "deposit" || Number(행.amount_krw) !== input.amount_krw)) {
      return NextResponse.json({ errors: ["같은 요청 키로 다른 기록이 있습니다 — 화면을 새로 고친 뒤 다시 넣어 주세요"] }, { status: 409 });
    }
    const invested = Number(rowsToObjects<{ invested: number }>(await execute(PAPER_INVESTED))[0]?.invested ?? 0);
    return NextResponse.json({ ok: true, id: 행?.id ?? null, already: 이미, invested });
  } catch (error) {
    return NextResponse.json({ errors: [error instanceof Error ? error.message : "저장 실패"] }, { status: 500 });
  }
}
