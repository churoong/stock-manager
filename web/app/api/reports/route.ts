import { NextResponse } from "next/server";
import { execute, explain, rowsToObjects } from "@/lib/db";
import { issueTexts } from "@/lib/validationErrors";
import {
  LATEST_REPORT, REPORT_BY_DATE, REPORT_HISTORY, REPORT_ITEMS, SESSIONS_AFTER, isLatestReport, missedReports, parseItems, parseWarnings, reportQuerySchema,
  type HistoryRow, type ItemRow, type ReportRow,
} from "@/lib/reports";

/**
 * 일일 리포트 조회 (docs/reports.md). ?market=KR|US&date=YYYY-MM-DD (date 없으면 최근).
 * 미들웨어가 인증 뒤에 둔다. 표가 아직 없으면(마이그레이션 전) 빈 결과에 이유를 붙인다.
 */
export async function GET(request: Request) {
  const url = new URL(request.url);
  const parsed = reportQuerySchema.safeParse({
    market: url.searchParams.get("market") ?? undefined,
    date: url.searchParams.get("date") ?? undefined,
  });
  if (!parsed.success) {
    return NextResponse.json({ errors: issueTexts(parsed.error.issues) }, { status: 400 });
  }
  const { market, date } = parsed.data;

  try {
    const [reportRs, historyRs] = await Promise.all([
      execute(date ? REPORT_BY_DATE : LATEST_REPORT, date ? [market, date] : [market]),
      execute(REPORT_HISTORY, [market]),
    ]);
    const report = rowsToObjects<ReportRow>(reportRs)[0] ?? null;
    const history = rowsToObjects<HistoryRow>(historyRs);
    const items = report ? parseItems(rowsToObjects<ItemRow>(await execute(REPORT_ITEMS, [report.id]))) : [];
    // **가장 새 리포트일 때만** 빠진 수를 센다 — 날짜를 골라 본 옛 리포트에 "그 뒤로 없다" 고 하면 거짓이다 (25.235).
    // 달력을 못 읽으면 모른다(null)로 둔다. 배치가 멈춘 것은 무응답 감시(docs/health.md)가 따로 알린다
    let missed: number | null = null;
    if (report && (!date || isLatestReport(report, history))) {
      try {
        missed = missedReports(
          rowsToObjects<{ date: string; open_utc: string | null }>(
            await execute(SESSIONS_AFTER, [market, report.trade_date]),
          ),
        );
      } catch (error) {
        console.warn("거래일 달력을 읽지 못해 빠진 리포트를 세지 않았습니다", error);
      }
    }
    const notes: string[] = [];
    if (!report) {
      notes.push(
        history.length === 0
          ? "저장된 리포트가 아직 없습니다. 2026-09-17 이후의 일일 배치부터 저장됩니다"
          : "그 날짜의 리포트가 없습니다 (휴장일이거나 배치가 돌지 않은 날)",
      );
    }
    return NextResponse.json({
      report: report ? { ...report, warnings: parseWarnings(report.warnings_json) } : null,
      missed_reports: missed,
      items,
      history,
      notes,
    });
  } catch (error) {
    return NextResponse.json(
      { error: explain(error instanceof Error ? error.message : "리포트를 읽지 못했습니다") },
      { status: 500 },
    );
  }
}
