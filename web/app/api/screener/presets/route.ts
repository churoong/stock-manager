import { NextResponse } from "next/server";
import { z } from "zod";
import { execute, rowsToObjects } from "@/lib/db";
import { PRESET_LIST, PRESET_UPSERT, parsePreset, presetInputSchema, type PresetRow } from "@/lib/screener";
import { issueTexts } from "@/lib/validationErrors";

/** 저장한 종목 찾기 조건 (docs/screener.md 2장). 미들웨어가 인증 뒤에 둔다. */
export async function GET(request: Request) {
  const country = z.enum(["KR", "US"]).safeParse(new URL(request.url).searchParams.get("country") ?? "KR");
  if (!country.success) return NextResponse.json({ errors: ["나라는 KR·US"] }, { status: 400 });
  try {
    const rows = rowsToObjects<PresetRow>(await execute(PRESET_LIST, [country.data]));
    return NextResponse.json({
      presets: rows.map((r) => ({ id: r.id, name: r.name, country: r.country, filters: parsePreset(r), last_used_at: r.last_used_at })),
    });
  } catch (error) {
    return NextResponse.json({ errors: [error instanceof Error ? error.message : "조회 실패"] }, { status: 500 });
  }
}

export async function POST(request: Request) {
  const parsed = presetInputSchema.safeParse(await request.json().catch(() => null));
  if (!parsed.success) return NextResponse.json({ errors: issueTexts(parsed.error.issues) }, { status: 400 });
  const { name, filters } = parsed.data;
  try {
    await execute(PRESET_UPSERT, [name, filters.country, JSON.stringify(filters), new Date().toISOString()]);
    return NextResponse.json({ ok: true });
  } catch (error) {
    return NextResponse.json({ errors: [error instanceof Error ? error.message : "저장 실패"] }, { status: 500 });
  }
}
