import { z } from "zod";
import { issueTexts } from "@/lib/validationErrors";
import { NextResponse } from "next/server";
import { batch, execute, rowsToObjects } from "@/lib/db";
import { ALERT_TOTALS, readUpdateStatements } from "@/lib/alerts";
import { CLOSE_GRACE_MINUTES } from "@/lib/intraday";

/**
 * 알림 센터 (docs/intraday.md). 최근 알림과 오늘 감시 대상·세션을 보여 준다.
 * 미들웨어가 이 경로를 인증 뒤에 두므로 여기서 다시 검사하지 않는다.
 */
export async function GET() {
  // **"못 읽었다" 와 "없다" 를 가른다** (2026-09-21, docs/infra.md 25.74).
  // 호출 기록이 비면 화면이 "호출 기록 없음" 이라고 적는데, 그것은 "외부 크론이 안 부른다"
  // 는 뜻으로 읽힌다(todo-user 2번이 바로 그 진단이다). 질의가 실패했을 때도 같은 글자가
  // 나오면 **못 읽은 것을 안 불린 것으로** 잘못 읽는다.
  let heartbeatsError: string | null = null;
  try {
    const [alerts, targets, sessions, heartbeats, totals] = await Promise.all([
      execute(
        `SELECT a.id, a.market, a.trade_date, a.trigger_type, a.message, a.data, a.created_at, a.sent_at, s.ticker, a.stock_id,
                COALESCE(a.is_read, 0) AS is_read
         FROM alerts a JOIN stocks s ON s.id = a.stock_id ORDER BY a.created_at DESC LIMIT 200`, // = ALERT_LIST_LIMIT (SQL 에 보간하지 않는다 — appSql 검사)
      ),
      execute("SELECT market, COUNT(*) AS n, MAX(built_at) AS built_at FROM monitor_targets GROUP BY market"),
      execute(
        // **닫는 시각도 함께 준다** (docs/infra.md 25.144). 화면이 "지금 정규장인가" 를
        // 알아야 장중 크론이 멈춘 것을 말할 수 있다. 세 MIN 은 같은 행에서 온다 —
        // 한 시장 안에서 나중 세션은 여는 시각도 닫는 시각도 나중이다
        `SELECT market, MIN(date) AS next_date, MIN(open_utc) AS next_open, MIN(close_utc) AS next_close
         FROM market_sessions WHERE close_utc >= ? GROUP BY market`,
        // **폐장 뒤 유예까지 같은 세션으로 본다** (docs/infra.md 25.279) — 장중 경로의 `activeSession` 과 같은 규칙.
        // 예전에는 폐장 시각이 지나면 곧장 다음 세션을 골라, 15:30~15:55 KST 에 장중 크론이 멈춰도 화면은 "장 밖" 으로 조용했다
        [new Date(Date.now() - CLOSE_GRACE_MINUTES * 60_000).toISOString()],
      ),
      execute("SELECT market, called_at, outcome, calls_today, day FROM cron_heartbeats WHERE job = 'intraday'").catch(
        (error: unknown) => {
          heartbeatsError = error instanceof Error ? error.message : "호출 기록을 읽지 못했습니다";
          return { columns: [], rows: [], affectedRows: 0 };
        },
      ),
      // 목록 상한과 상관없는 전체·안 읽은 수 (docs/infra.md 25.800)
      // 못 읽으면 null — 전체 수를 모르면 화면이 목록 안에서 센다. 이 한 줄 때문에 알림 목록 전체가 500 이 되지 않게 (25.805, 교차검증)
      execute(ALERT_TOTALS).catch(() => null),
    ]);
    return NextResponse.json({
      alerts: rowsToObjects(alerts),
      targets: rowsToObjects(targets),
      sessions: rowsToObjects(sessions),
      heartbeats: rowsToObjects(heartbeats),
      heartbeats_error: heartbeatsError,
      totals: totals ? (rowsToObjects<{ total: number; unread: number }>(totals)[0] ?? null) : null,
      // 이 응답을 만든 시각 — 화면은 10분 캐시(25.879)로 다시 그리므로 장중 감시 판정을 **읽은 시각** 기준으로 한다 (25.917)
      server_time: new Date().toISOString(),
    });
  } catch (error) {
    const message = error instanceof Error ? error.message : "조회 실패";
    if (/no such table/i.test(message)) {
      return NextResponse.json({ alerts: [], targets: [], sessions: [], heartbeats: [], notes: ["알림 표가 아직 없습니다. 일일 배치가 한 번 돌면 만들어집니다"] });
    }
    return NextResponse.json({ errors: [message] }, { status: 500 });
  }
}

/**
 * 읽음 처리. 본문 { ids?: number[], read?: boolean }. ids 가 비면 안 읽은 전부.
 * 사용자 입력이라 배치는 건드리지 않는다 (docs/intraday.md 7장).
 */
const alertsPatchSchema = z.object({
  ids: z.array(z.number().int().positive()).max(200).optional(),
  read: z.boolean().optional(),
});

export async function PATCH(request: Request) {
  let body: { ids?: unknown; read?: unknown } = {};
  try {
    body = await request.json();
  } catch {
    return NextResponse.json({ errors: ["요청을 읽지 못했습니다"] }, { status: 400 });
  }
  // **본문을 검사한다** (docs/infra.md 25.312). 예전에는 숫자가 아닌 id 를 조용히 버렸고, 그래서 빈 목록이 되면
  // "ids 가 비면 안 읽은 전부" 경로로 떨어졌다 — `{"ids":["123"]}` 한 건이 **모든 알림**을 읽음으로 바꿨다.
  // `read: "false"` 는 거짓으로 읽혀 전부를 안 읽음으로 되돌렸다. 전부는 **빈 목록이나 ids 없음**으로만 뜻한다
  // (화면의 "모두 읽음" 은 25.584 뒤로 **보이는 것의 id** 를 보낸다 — 빈 목록 경로는 API 로만 남았다). 원소가 하나라도 틀리면 400 이다
  const parsed = alertsPatchSchema.safeParse(body);
  if (!parsed.success) {
    return NextResponse.json({ errors: issueTexts(parsed.error.issues) }, { status: 400 });
  }
  const ids = parsed.data.ids ?? [];
  const read = parsed.data.read ?? true;
  // D1 은 질의당 파라미터 100개라 나눠 보낸다 (docs/infra.md 25.5)
  const statements = readUpdateStatements(ids, read);
  try {
    const results = await batch(statements);
    const updated = results.reduce((sum, rs) => sum + (rs.affectedRows ?? 0), 0);
    return NextResponse.json({ ok: true, updated });
  } catch (error) {
    return NextResponse.json({ errors: [error instanceof Error ? error.message : "갱신 실패"] }, { status: 500 });
  }
}
