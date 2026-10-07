import { NextResponse } from "next/server";
import { dispatchJob, findJob } from "@/lib/dispatch";

/**
 * 배치 수동 실행 (docs/health.md 4장). 본문 { job }. 목록에 있는 이름만 받는다.
 * 미들웨어가 인증 뒤에 둔다. 깨우기 실패는 500 이 아니라 이유를 돌려준다 — GitHub 에서 직접 돌릴 수 있게.
 */
export async function POST(request: Request) {
  let body: { job?: unknown } = {};
  try {
    body = await request.json();
  } catch {
    return NextResponse.json({ errors: ["요청을 읽지 못했습니다"] }, { status: 400 });
  }
  const job = typeof body.job === "string" ? findJob(body.job) : null;
  if (!job) {
    return NextResponse.json({ errors: ["모르는 배치 이름입니다"] }, { status: 400 });
  }
  const result = await dispatchJob(job);
  return NextResponse.json({ ...result, job: job.key, label: job.label });
}
