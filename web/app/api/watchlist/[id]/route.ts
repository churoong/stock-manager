import { NextResponse } from "next/server";
import { z } from "zod";
import { execute, rowsToObjects } from "@/lib/db";
import { issueTexts } from "@/lib/validationErrors";
import { WATCH_GONE, WATCH_PRICE_BASIS_BY_WATCH_ID, targetAboveCloseWarning, targetPriceProblem } from "@/lib/watch";

type Params = { params: Promise<{ id: string }> };

const patchSchema = z.object({
  alert_enabled: z.boolean().optional(),
  target_buy_price: z.number().positive().nullable().optional(),
});

function parseId(raw: string): number | null {
  const id = Number(raw);
  return Number.isInteger(id) && id > 0 ? id : null;
}

/** 관심 종목 알림 켜기·끄기, 목표 매수가 바꾸기 */
export async function PATCH(request: Request, { params }: Params) {
  const id = parseId((await params).id);
  if (!id) return NextResponse.json({ errors: ["잘못된 번호"] }, { status: 400 });
  const parsed = patchSchema.safeParse(await request.json().catch(() => null));
  if (!parsed.success) return NextResponse.json({ errors: issueTexts(parsed.error.issues) }, { status: 400 });
  const sets: string[] = [];
  const args: Array<number | null> = [];
  if (parsed.data.alert_enabled !== undefined) {
    sets.push("alert_enabled = ?");
    args.push(parsed.data.alert_enabled ? 1 : 0);
  }
  let 경고: string | null = null;
  try {
    if (parsed.data.target_buy_price) {
      // 단위 실수 막기 (docs/infra.md 25.360). 조회도 try 안에 둔다 — DB 가 실패하면 정리된 JSON 대신 500 페이지가 나갔다 (25.584)
      const basis = rowsToObjects<{ currency: string; last_close: number | null }>(
        await execute(WATCH_PRICE_BASIS_BY_WATCH_ID, [id]),
      )[0];
      const problem = basis ? targetPriceProblem(parsed.data.target_buy_price, basis.last_close, basis.currency) : null;
      if (problem) return NextResponse.json({ errors: [problem] }, { status: 400 });
      경고 = basis ? targetAboveCloseWarning(parsed.data.target_buy_price, basis.last_close) : null;
    }
    if (parsed.data.target_buy_price !== undefined) {
      sets.push("target_buy_price = ?");
      args.push(parsed.data.target_buy_price);
    }
    if (sets.length === 0) return NextResponse.json({ ok: true });
    const 바뀜 = await execute(`UPDATE watchlist SET ${sets.join(", ")} WHERE id = ?`, [...args, id]);
    // 다른 탭에서 이미 뺀 종목이면 "저장됨" 이 아니다 (25.814, 감사)
    if (바뀜.affectedRows === 0) return NextResponse.json({ errors: [WATCH_GONE] }, { status: 404 });
    return NextResponse.json({ ok: true, warnings: 경고 ? [경고] : [] });
  } catch (error) {
    return NextResponse.json({ errors: [error instanceof Error ? error.message : "저장 실패"] }, { status: 500 });
  }
}

export async function DELETE(_request: Request, { params }: Params) {
  const id = parseId((await params).id);
  if (!id) return NextResponse.json({ errors: ["잘못된 번호"] }, { status: 400 });
  try {
    const 지움 = await execute("DELETE FROM watchlist WHERE id = ?", [id]);
    if (지움.affectedRows === 0) return NextResponse.json({ errors: [WATCH_GONE] }, { status: 404 });
    return NextResponse.json({ ok: true });
  } catch (error) {
    return NextResponse.json({ errors: [error instanceof Error ? error.message : "삭제 실패"] }, { status: 500 });
  }
}
