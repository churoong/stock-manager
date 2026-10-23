export const meta = {
  name: 'cross-verify',
  description: 'stock-manager 푸시 전 교차검증 — 커밋 범위를 렌즈 셋(돈·장부·주문 / 리포트·텔레그램·웹 / 기록·시험)으로 반박, [필수] 합계 (docs/loops.md L3)',
  whenToUse: '묶음 푸시 전. 위험 커밋(docs/loops.md L3 목록)은 [필수] 0 이어야 푸시',
  phases: [{ title: 'Verify', detail: '렌즈마다 교차검증 에이전트 하나' }],
}

// 역할 에이전트 부르기 — 등록된 이름(`.claude/agents/<이름>.md`)으로 먼저, 세션이 그 정의를 아직 읽지 않았으면(세션
// 시작 뒤에 더한 역할은 목록에 없다 — 25.1751 첫 실행) 범용 에이전트가 그 정의 파일을 읽고 따르게 한다. 정의는 한 곳
const 저장소 = (args && args.repo) || '/home/user/stock-manager'
async function 역할(이름, 글, opts) {
  // 대체 경로는 **등록 안 됨(예외)** 일 때만 — 에이전트가 null(죽음·건너뜀)을 돌려주면 그대로 돌려준다(다시 돌리면
  // 예산이 두 배가 됐다, 25.1752). 대체 경로는 범용 에이전트라 도구 제한이 없다 — 읽기 전용은 지시문으로만 지킨다.
  // 그래서 주 세션은 workflow 뒤 `git status`·`git worktree list` 가 그대로인지 본다(docs/loops.md 0장)
  try {
    return await agent(글, { ...opts, agentType: 이름 })
  } catch (e) {
    log(`${이름} 이 등록돼 있지 않아 정의 파일로 부른다`)
  }
  return agent(`역할 정의 ${저장소}/.claude/agents/${이름}.md 를 먼저 읽고 그 지시(읽기 전용·출력 꼴)를 그대로 따르세요.\n\n${글}`,
    { ...opts, agentType: 'general-purpose', label: `${opts.label || 이름}*` })
}

// args: { range?: 'origin/main..HEAD', lenses?: string[] }
const 범위 = (args && args.range) || 'origin/main..HEAD'
const 렌즈 = (args && args.lenses) || [
  '돈·장부·주문 — 가상모의투자 장부·체결 판정·위험 점검·KIS모의투자 주문 경로·맞추기에 닿는 바뀐 줄',
  '리포트·텔레그램·웹 — 일일 리포트 조립·조각 나누기·저장 본문·웹 화면에 닿는 바뀐 줄',
  '기록·시험 — docs/infra.md 25.x 가 코드와 맞는가, 되돌리기 확인에 적은 시험이 실제로 그 규칙을 잡는가, 빠진 시험',
]
const RESULT = {
  type: 'object',
  properties: {
    must: { type: 'integer', minimum: 0 },
    items: {
      type: 'array',
      items: {
        type: 'object',
        properties: {
          level: { type: 'string', enum: ['must', 'should', 'note'] },
          where: { type: 'string' }, what: { type: 'string' }, fix: { type: 'string' },
        },
        required: ['level', 'where', 'what'],
      },
    },
    ok: { type: 'string', description: '확인하고 문제없던 것' },
  },
  required: ['must', 'items'],
}

phase('Verify')
const 결과 = await parallel(렌즈.map((l, i) => () =>
  역할('stock-cross-verifier', `커밋 범위 ${범위} 를 교차검증하세요. 렌즈: ${l}. 첫 판단을 must(=[필수] 수)에 넣으세요.`,
    { schema: RESULT, label: `xv:${i + 1}`, phase: 'Verify' })))
const 살아 = 결과.filter(Boolean)
// 렌즈 하나라도 결과가 없으면 통과로 치지 않는다 — 위험 커밋 푸시 문
const 필수 = 살아.reduce((s, r) => s + (r.must || 0), 0)
return {
  range: 범위,
  must: 살아.length < 렌즈.length ? null : 필수,
  missingLenses: 렌즈.length - 살아.length,
  items: 살아.flatMap((r) => r.items || []),
  ok: 살아.map((r) => r.ok || ''),
}
