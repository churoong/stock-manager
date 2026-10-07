import { NextResponse } from "next/server";
import { execute } from "@/lib/db";
import { PRESET_DELETE, PRESET_TOUCH } from "@/lib/screener";

type Params = { params: Promise<{ id: string }> };

function parseId(raw: string): number | null {
  const id = Number(raw);
  return Number.isInteger(id) && id > 0 ? id : null;
}

/** 썼다는 표시. 어느 조건을 자주 쓰는지 남긴다 */
export async function PATCH(_request: Request, { params }: Params) {
  const id = parseId((await params).id);
  if (!id) return NextResponse.json({ errors: ["잘못된 번호"] }, { status: 400 });
  try {
    await execute(PRESET_TOUCH, [new Date().toISOString(), id]);
    return NextResponse.json({ ok: true });
  } catch (error) {
    return NextResponse.json({ errors: [error instanceof Error ? error.message : "저장 실패"] }, { status: 500 });
  }
}

export async function DELETE(_request: Request, { params }: Params) {
  const id = parseId((await params).id);
  if (!id) return NextResponse.json({ errors: ["잘못된 번호"] }, { status: 400 });
  try {
    await execute(PRESET_DELETE, [id]);
    return NextResponse.json({ ok: true });
  } catch (error) {
    return NextResponse.json({ errors: [error instanceof Error ? error.message : "삭제 실패"] }, { status: 500 });
  }
}
