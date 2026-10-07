import { NextResponse } from "next/server";
import { execute, explain } from "@/lib/db";

/**
 * DB 가 살아 있는가 (docs/infra.md 24절).
 *
 * 화면 맨 위 띠가 이것 하나만 부른다. 2026-09-18 에 월 한도로 계정이 막혔을 때
 * **화면은 메뉴만 보이고 아무 설명이 없었다.** 데이터가 안 보이는 이유를 화면이 말해야 한다.
 *
 * 질의는 표를 읽지 않는 `SELECT 1` 이다. 막혔는지 보려고 예산을 쓰지 않는다.
 */
export const dynamic = "force-dynamic";

export async function GET() {
  try {
    await execute("SELECT 1");
    return NextResponse.json({ ok: true });
  } catch (error) {
    const raw = error instanceof Error ? error.message : String(error);
    return NextResponse.json({ ok: false, reason: explain(raw) });
  }
}
