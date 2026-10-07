import { NextResponse } from "next/server";
import { execute } from "@/lib/db";
import { requestRecalc } from "@/lib/portfolio";

type Params = { params: Promise<{ id: string }> };

export async function DELETE(_request: Request, { params }: Params) {
  const id = Number((await params).id);
  if (!Number.isInteger(id) || id <= 0) return NextResponse.json({ errors: ["잘못된 번호"] }, { status: 400 });
  try {
    const rs = await execute("DELETE FROM dividend_receipts WHERE id = ?", [id]);
    if (rs.affectedRows === 0) return NextResponse.json({ errors: ["기록이 없습니다"] }, { status: 404 });
    const recalc = await requestRecalc();
    return NextResponse.json({ ok: true, recalc });
  } catch (error) {
    return NextResponse.json({ errors: [error instanceof Error ? error.message : "삭제 실패"] }, { status: 500 });
  }
}
