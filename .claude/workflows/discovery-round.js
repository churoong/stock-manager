export const meta = {
  name: 'discovery-round',
  description: 'stock-manager 발굴 루프 한 회차 — 발굴 1 → 후보마다 검증 A·B 독립 반박 → 보수적 합침 (docs/loops.md L1)',
  whenToUse: '주간 발굴 회차·사용자 지시 회차. 사전 등록·구현·기록은 이 결과를 받은 주 세션이 한다',
  phases: [
    { title: 'Discover', detail: '발굴 에이전트가 기존 12장 표와 겹치지 않는 후보 3~4개' },
    { title: 'Verify', detail: '후보마다 검증 A(데이터·시점·코드)·검증 B(통계·중복·과최적화) — 서로 결과를 보지 않는다' },
  ],
}

// 역할 에이전트 부르기 — 등록된 이름(`.claude/agents/<이름>.md`)으로 먼저, 세션이 그 정의를 아직 읽지 않았으면(세션
// 시작 뒤에 더한 역할은 목록에 없다 — 25.1751 첫 실행) 범용 에이전트가 그 정의 파일을 읽고 따르게 한다. 정의는 한 곳
const 저장소 = (args && args.repo) || '/home/user/stock-manager'
async function 역할(이름, 글, opts) {
  try {
    const r = await agent(글, { ...opts, agentType: 이름 })
    if (r !== null && r !== undefined) return r
  } catch (e) {
    log(`${이름} 이 등록돼 있지 않아 정의 파일로 부른다`)
  }
  return agent(`역할 정의 ${저장소}/.claude/agents/${이름}.md 를 먼저 읽고 그 지시(읽기 전용·출력 꼴)를 그대로 따르세요.\n\n${글}`,
    { ...opts, agentType: 'general-purpose', label: `${opts.label || 이름}*` })
}

// args: { round: 38, focus?: '운용 쪽 매도·비중', userDirected?: false, max?: 4 }
const 회차 = (args && args.round) || '다음'
const 초점 = (args && args.focus) || '운용(매도·비중·자본 배분)과 종목선정 모두'
const 최대 = Math.min((args && args.max) || 4, 4) // 1 + 2×4 = 9 에이전트 — 세션 기준 10 아래
const 지시분 = !!(args && args.userDirected)

const CANDIDATES = {
  type: 'object',
  properties: {
    candidates: {
      type: 'array',
      items: {
        type: 'object',
        properties: {
          id: { type: 'string' }, name: { type: 'string' }, kind: { type: 'string', enum: ['ic', 'strategy', 'account', 'record'] },
          summary: { type: 'string', description: '무엇을 재나·바꾸나, 근거 문헌, 무료 데이터, 계산식, 시점 근거, 가장 가까운 기존 것과 다른 점' },
        },
        required: ['id', 'name', 'kind', 'summary'],
      },
    },
  },
  required: ['candidates'],
}
const VERDICT = {
  type: 'object',
  properties: {
    verdict: { type: 'string', enum: ['pass', 'conditional', 'fail', 'hold'] },
    reasons: { type: 'string', description: '근거 file:line·문헌, 한국어' },
    conditions: { type: 'string', description: '조건부면 그 조건' },
    prereg: { type: 'string', description: '사전 등록 문장 초안(정의·비교·기준·시도 수) — 없으면 빈 글' },
  },
  required: ['verdict', 'reasons'],
}

phase('Discover')
const 발굴 = await 역할('stock-discoverer', 
  `발굴 루프 ${회차}회차. 초점: ${초점}. docs/factors.md 12장 지난 회차 표와 겹치지 않는 후보를 최대 ${최대}개 내세요.`,
  { schema: CANDIDATES, label: 'discover', phase: 'Discover' },
)
const 후보 = ((발굴 && 발굴.candidates) || []).slice(0, 최대)
if (발굴 && 발굴.candidates && 발굴.candidates.length > 최대) log(`후보 ${발굴.candidates.length}개 중 ${최대}개만 검증 — 나머지는 다음 회차`)
if (!후보.length) return { round: 회차, candidates: [], note: '발굴 결과 없음' }

const 순위 = { fail: 3, hold: 2, conditional: 1, pass: 0 }
const 결과 = await pipeline(
  후보,
  (c) => parallel([
    () => 역할('stock-verifier-data', `발굴 루프 ${회차}회차 후보를 검증 A 로 반박하세요.\n후보 ${c.id} ${c.name} (${c.kind}): ${c.summary}`,
      { schema: VERDICT, label: `A:${c.id}`, phase: 'Verify' }),
    () => 역할('stock-verifier-stats', `발굴 루프 ${회차}회차 후보를 검증 B 로 반박하세요.\n후보 ${c.id} ${c.name} (${c.kind}): ${c.summary}`,
      { schema: VERDICT, label: `B:${c.id}`, phase: 'Verify' }),
  ]),
  ([a, b], c) => {
    const 둘 = [a, b].filter(Boolean)
    // 보수적으로 합친다 — 하나라도 탈락이면 탈락, 검증 하나가 죽었으면 보류(CLAUDE.md 발굴 루프 2단계)
    let 합 = 둘.length < 2 ? 'hold' : 둘.map((v) => v.verdict).sort((x, y) => 순위[y] - 순위[x])[0]
    // 동결 규칙(factors.md 12장 머리): 사용자 지시분이 아니면 조건부는 보류
    if (합 === 'conditional' && !지시분) 합 = 'hold'
    return { ...c, A: a, B: b, verdict: 합 }
  },
)
return { round: 회차, userDirected: 지시분, results: 결과.filter(Boolean) }
