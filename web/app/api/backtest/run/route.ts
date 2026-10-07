import { NextResponse } from "next/server";
import { requestBacktest, runInputSchema } from "@/lib/backtest";
import { issueTexts } from "@/lib/validationErrors";

/**
 * 백테스트 실행 요청 (docs/backtest.md 8장).
 *
 * 웹은 계산하지 않고 GitHub Actions 를 깨우기만 한다. 결과는 배치가 DB 에 넣고
 * 화면은 batch_runs 를 폴링해 상태만 본다.
 *
 * 깨우기가 실패해도 500 을 내지 않는다. 사용자는 GitHub 에서 직접 돌릴 수 있고,
 * 이유를 알아야 그렇게 할 수 있다. 실패 이유를 그대로 돌려준다.
 */
export async function POST(request: Request) {
  let body: unknown;
  try {
    body = await request.json();
  } catch {
    return NextResponse.json({ errors: ["요청을 읽지 못했습니다"] }, { status: 400 });
  }

  const parsed = runInputSchema.safeParse(body);
  if (!parsed.success) {
    return NextResponse.json({ errors: issueTexts(parsed.error.issues) }, { status: 400 });
  }

  const result = await requestBacktest(parsed.data);
  return NextResponse.json({ ...result, input: parsed.data });
}
