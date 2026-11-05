export const meta = {
  name: 'discovery-round',
  description: 'stock-manager 발굴 루프 한 회차 — 성과판정 → 연구기획(질문) → 후보 팀 → 실험설계 → 검증 A·B → 보수적 합침 (docs/loops.md L1)',
  whenToUse: '주간 발굴 회차·사용자 지시 회차. 사전 등록·구현·기록은 이 결과를 받은 주 세션이 한다',
  phases: [
    { title: 'Judge', detail: '성과판정팀이 결과가 난 지난 후보를 사전 등록 기준으로 판정하고 교훈을 낸다' },
    { title: 'Plan', detail: '연구기획팀이 교훈·성과 기록 요약에서 이번 회차 질문 2~3개를 정해 팀에 맡긴다' },
    { title: 'Discover', detail: '질문을 받은 팀(종목선정·매매규칙·포트폴리오·위험·데이터)만 질문마다 후보 0~2개' },
    { title: 'Triage', detail: '후보가 상한보다 많으면 연구기획팀이 겹침을 걸러 순서를 정한다' },
    { title: 'Design', detail: '실험설계팀이 고른 후보를 사전 등록 문장·리플레이 명세로' },
    { title: 'Verify', detail: '후보마다 데이터검증팀·계량검증팀 — 서로 결과를 보지 않는다' },
  ],
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

// 연구개발본부(25.2140 — 2026-11-04 사용자 지시 "연구개발실쪽을 좀 더 세분화" → 주 세션이 낸 이상안 "그 이상안으로 진행해줘").
// 분야 분류표가 아니라 깔때기 단계로 나눈다: 결과 판정 → 질문 → 후보 → 실험 설계 → 검증. 분야는 팀 안의 점검표
const 팀목록 = [
  { key: 'selection', 이름: 'stock-rnd-selection', 부서: '종목선정팀' },
  { key: 'trading', 이름: 'stock-rnd-trading', 부서: '매매규칙팀' },
  { key: 'portfolio', 이름: 'stock-rnd-portfolio', 부서: '포트폴리오·위험팀' },
  { key: 'data', 이름: 'stock-rnd-data', 부서: '데이터팀' },
]

// args: { round: 38, evidence?: '성과 기록 요약(수치·금액 없음)', questions?: [{id,question,teams}], userDirected?: false,
//         max?: 3, teams?: ['selection', 'portfolio'] }
const 회차 = (args && args.round) || '다음'
const 증거 = (args && args.evidence) || ''
// 검증에 올리는 후보 수 — **시도 수가 예산이다**(후보마다 등록 시도 수 N 이 늘어 판정판 DSR 이 모두를 깎는다). 3 을 넘기지 않는다
const 최대 = Math.min((args && args.max) || 3, 3)
const 지시분 = !!(args && args.userDirected)
const 쓸팀 = args && Array.isArray(args.teams) && args.teams.length
  ? 팀목록.filter((t) => args.teams.includes(t.key)) : 팀목록

const JUDGE = {
  type: 'object',
  properties: {
    judgments: { type: 'array', items: { type: 'object', properties: {
      id: { type: 'string' }, verdict: { type: 'string', enum: ['keep', 'off', 'drop', 'wait'] },
      reason: { type: 'string' }, lesson: { type: 'string' } }, required: ['id', 'verdict', 'reason'] } },
  },
  required: ['judgments'],
}
const QUESTIONS = {
  type: 'object',
  properties: {
    questions: { type: 'array', items: { type: 'object', properties: {
      id: { type: 'string' }, question: { type: 'string' }, why: { type: 'string' },
      teams: { type: 'array', items: { type: 'string', enum: ['selection', 'trading', 'portfolio', 'data'] } },
      parts: { type: 'string', description: '팀 점검표에서 먼저 볼 파트' } }, required: ['id', 'question', 'teams'] } },
  },
  required: ['questions'],
}
const CANDIDATES = {
  type: 'object',
  properties: {
    candidates: {
      type: 'array',
      items: {
        type: 'object',
        properties: {
          id: { type: 'string' }, name: { type: 'string' }, question: { type: 'string', description: '답하는 질문 id' },
          kind: { type: 'string', enum: ['ic', 'strategy', 'account', 'record'] },
          summary: { type: 'string', description: '무엇을 재나·바꾸나, 근거 문헌, 무료 데이터, 계산식, 시점 근거, 판정 방법, 가장 가까운 기존 것과 다른 점' },
        },
        required: ['id', 'name', 'kind', 'summary'],
      },
    },
  },
  required: ['candidates'],
}
const TRIAGE = {
  type: 'object',
  properties: {
    selected: { type: 'array', items: { type: 'string' }, description: '검증에 올릴 후보 id, 앞이 먼저' },
    merged: { type: 'array', items: { type: 'object', properties: { into: { type: 'string' }, from: { type: 'string' }, why: { type: 'string' } }, required: ['into', 'from', 'why'] } },
    dropped: { type: 'array', items: { type: 'object', properties: { id: { type: 'string' }, why: { type: 'string' } }, required: ['id', 'why'] } },
  },
  required: ['selected'],
}
const DESIGN = {
  type: 'object',
  properties: {
    specs: { type: 'array', items: { type: 'object', properties: {
      id: { type: 'string' }, prereg: { type: 'string' }, variant: { type: 'string' }, trials_added: { type: 'string' },
      stage: { type: 'string' }, read_budget: { type: 'string' }, when: { type: 'string' } }, required: ['id', 'prereg', 'stage'] } },
  },
  required: ['specs'],
}
const VERDICT = {
  type: 'object',
  properties: {
    verdict: { type: 'string', enum: ['pass', 'conditional', 'fail', 'hold'] },
    reasons: { type: 'string', description: '근거 file:line·문헌, 한국어' },
    conditions: { type: 'string', description: '조건부면 그 조건' },
    prereg: { type: 'string', description: '실험설계팀 사전 등록 문장의 고칠 점(없으면 빈 글)' },
  },
  required: ['verdict', 'reasons'],
}

// ⑤→① 성과판정 — 회차 맨 앞. 교훈이 기획의 질문이 된다. 죽어도 회차는 이어 간다(판정은 주 세션이 다시 본다)
phase('Judge')
const 판정 = await 역할('stock-rnd-judge',
  `발굴 루프 ${회차}회차 시작 — 결과가 난 지난 후보를 사전 등록 기준으로 판정하고 교훈을 내세요.` + (증거 ? `\n성과 기록 요약(주 세션):\n${증거}` : ''),
  { schema: JUDGE, label: '성과판정팀', phase: 'Judge' })
const 판정들 = (판정 && 판정.judgments) || []

// ① 질문 — 사용자 지시분이면 넘긴 질문을 그대로
phase('Plan')
let 질문 = (args && Array.isArray(args.questions) && args.questions.length) ? args.questions : null
let 기획죽음 = false
if (!질문) {
  const 교훈 = 판정들.map((j) => `- ${j.id} ${j.verdict}: ${j.lesson || j.reason}`).join('\n')
  const 기획 = await 역할('stock-rnd-planner',
    `할 일 A — 발굴 루프 ${회차}회차 질문 2~3개를 정하세요. 맡길 수 있는 팀: ${쓸팀.map((t) => `${t.key}(${t.부서})`).join(', ')}.\n` +
    `성과판정팀 교훈:\n${교훈 || '(없음)'}\n` + (증거 ? `성과 기록 요약(주 세션):\n${증거}` : '성과 기록 요약: (없음 — 문서에서 뽑으세요)'),
    { schema: QUESTIONS, label: '연구기획팀', phase: 'Plan' })
  질문 = (기획 && 기획.questions) || []
  기획죽음 = !기획
}
// 질문은 셋까지 — 넘친 질문은 결과의 questionsTrimmed 로(소리 없는 자르기 금지, loops.md 0장, 25.2141)
const 잘린질문 = 질문.slice(3)
질문 = 질문.slice(0, 3)
if (잘린질문.length) log(`질문 ${잘린질문.length}개를 셋 넘어 뺐다 — 결과의 questionsTrimmed`)
// 기획이 죽었으면 모든 팀이 영역에서 1~2개(공통 규칙의 "질문이 없으면") — 질문 0 이면 그 회차는 후보를 내지 않는다
const 팀질문 = (t) => 질문.filter((q) => (q.teams || []).includes(t.key))
const 부를팀 = 기획죽음 ? 쓸팀 : 쓸팀.filter((t) => 팀질문(t).length)
if (!기획죽음 && !부를팀.length) {
  return { round: 회차, judgments: 판정들, judgeMissing: !판정, questions: 질문, questionsTrimmed: 잘린질문, teams: [], deadTeams: [],
    results: [], skipped: [], note: '이번 회차 질문 없음 — 후보를 내지 않았다' }
}

// ② 후보 — 질문을 받은 팀만
phase('Discover')
const 팀결과 = await parallel(부를팀.map((t) => () => {
  const qs = 팀질문(t)
  const 글 = qs.length
    ? `발굴 루프 ${회차}회차 — ${t.부서}. 다음 질문에 답하는 후보를 질문마다 0~2개 내세요(답할 것이 없으면 0개). id 는 "${t.key}-번호".\n` +
      qs.map((q) => `- ${q.id}: ${q.question}${q.parts ? ` (먼저 볼 파트: ${q.parts})` : ''}${q.why ? ` — 근거: ${q.why}` : ''}`).join('\n')
    : `발굴 루프 ${회차}회차 — ${t.부서}. 기획 질문 없음(기획 결과 없음). 맡은 영역에서 지난 회차 표와 겹치지 않는 후보 1~2개. id 는 "${t.key}-번호".`
  return 역할(t.이름, 글, { schema: CANDIDATES, label: t.부서, phase: 'Discover' })
}))
const 죽은팀 = 부를팀.filter((t, i) => !팀결과[i]).map((t) => t.부서)
let 모음 = []
const 팀넘침 = [] // 팀 상한에 걸린 후보 — 버리지 않고 skipped 로(25.2141)
const 넣기 = (c, team) => {
  // 두 팀이 같은 id 를 내면 뒤의 것에 팀 열쇠를 붙인다 — 같은 후보가 두 번 검증에 오르지 않게(25.2141)
  const id = 모음.some((x) => x.id === c.id) ? `${team.key}:${c.id}` : c.id
  모음.push({ ...c, id, team: team.부서 })
}
부를팀.forEach((t, i) => {
  // 팀마다 질문 수 × 2 까지(질문 없으면 2)
  const 상한 = Math.max(팀질문(t).length, 1) * 2
  const 낸것 = (팀결과[i] && 팀결과[i].candidates) || []
  for (const c of 낸것.slice(0, 상한)) 넣기(c, t)
  for (const c of 낸것.slice(상한)) 팀넘침.push({ ...c, team: t.부서, why: `팀 상한 ${상한}` })
})
if (팀넘침.length) log(`팀 상한에 걸린 후보 ${팀넘침.length}개 — 결과의 skipped`)
if (죽은팀.length) log(`결과 없는 팀: ${죽은팀.join('·')} — 결과의 deadTeams`)
// 팀이 모두 죽은 것과 후보 0 을 가른다(25.1752). 모두 죽었으면 통합 발굴(예비)을 한 번
if (죽은팀.length === 부를팀.length) {
  const 예비 = await 역할('stock-discoverer',
    `발굴 루프 ${회차}회차(팀 결과 없음 — 통합 예비). docs/factors.md 12장 지난 회차 표와 겹치지 않는 후보를 최대 ${최대}개 내세요.` +
    (질문.length ? `\n질문:\n${질문.map((q) => `- ${q.id}: ${q.question}`).join('\n')}` : ''),
    { schema: CANDIDATES, label: '연구개발본부', phase: 'Discover' })
  if (!예비) return { round: 회차, error: '후보 팀·통합 예비 모두 결과 없음 — 다시 돌린다', deadTeams: 죽은팀, questions: 질문, questionsTrimmed: 잘린질문, results: [], skipped: 팀넘침 }
  // 예비는 검증 상한까지만 — 넘친 것은 skipped(거름을 붙이지 않아 예산이 늘지 않는다, 25.2141)
  const 예비후보 = 예비.candidates || []
  모음 = 예비후보.slice(0, 최대).map((c) => ({ ...c, team: '연구개발본부' }))
  for (const c of 예비후보.slice(최대)) 팀넘침.push({ ...c, team: '연구개발본부', why: `예비 상한 ${최대}` })
}
if (!모음.length) return { round: 회차, judgments: 판정들, questions: 질문, questionsTrimmed: 잘린질문, results: [], deadTeams: 죽은팀, skipped: 팀넘침, note: '발굴 결과 없음(후보 0)' }

// 걸러 고르기 — 상한보다 많을 때만. 죽거나 아는 후보가 없으면 팀 순서(선정 → 매매 → 포트폴리오·위험 → 데이터)대로
phase('Triage')
const 거름 = 모음.length > 최대
  ? await 역할('stock-rnd-planner',
    `할 일 B — 발굴 루프 ${회차}회차 후보 ${모음.length}개입니다. 겹침을 거르고 검증에 올릴 순서를 정하세요(최대 ${최대}개).\n` +
    `질문:\n${질문.map((q) => `- ${q.id}: ${q.question}`).join('\n') || '(없음)'}\n후보:\n` +
    모음.map((c) => `- ${c.id} [${c.team}${c.question ? `·${c.question}` : ''}] ${c.name} (${c.kind}): ${c.summary}`).join('\n'),
    { schema: TRIAGE, label: '연구기획팀:거름', phase: 'Triage' })
  : null
const 아는id = new Set(모음.map((c) => c.id))
const 고름 = ((거름 && 거름.selected) || []).filter((id, i, a) => 아는id.has(id) && a.indexOf(id) === i)
const 거름없음 = !거름 || !고름.length
if (모음.length > 최대 && 거름없음) log('연구기획팀 거름 결과가 없거나 아는 후보가 없다 — 팀 순서대로 고른다')
const 순서 = 거름없음 ? 모음.map((c) => c.id) : 고름
const 후보 = 순서.slice(0, 최대).map((id) => 모음.find((c) => c.id === id))
const 넘침 = 모음.filter((c) => !후보.includes(c)).filter((c) => 거름없음 || !((거름.dropped || []).some((d) => d.id === c.id)
  || (거름.merged || []).some((m) => m.from === c.id)))
if (넘침.length) log(`검증에 못 오른 후보 ${넘침.length}개 — 결과의 skipped 로 다음 회차`)

// ③ 실험 설계 — 고른 후보 한꺼번에. 죽으면 명세 없이 검증(검증이 사전 등록 초안을 본다 — 예전 길)
phase('Design')
const 설계 = await 역할('stock-rnd-design',
  `발굴 루프 ${회차}회차 검증에 오를 후보 ${후보.length}개의 사전 등록 문장·리플레이 명세를 내세요.\n` +
  후보.map((c) => `- ${c.id} [${c.team}] ${c.name} (${c.kind}): ${c.summary}`).join('\n'),
  { schema: DESIGN, label: '실험설계팀', phase: 'Design' })
const 명세 = (id) => ((설계 && 설계.specs) || []).find((s) => s.id === id) || null
const 명세글 = (s) => s ? `\n실험설계팀 명세 — 사전 등록: ${s.prereg} / 변형: ${s.variant || '-'} / 더하는 시도 수: ${s.trials_added || '-'} / 단계: ${s.stage} / 읽기: ${s.read_budget || '-'}` : '\n실험설계팀 명세 없음 — 사전 등록 초안을 직접 보세요'

const 순위 = { fail: 3, hold: 2, conditional: 1, pass: 0 }
const 결과 = await pipeline(
  후보,
  (c) => parallel([
    () => 역할('stock-verifier-data', `발굴 루프 ${회차}회차 후보를 검증 A 로 반박하세요.\n후보 ${c.id} [${c.team}] ${c.name} (${c.kind}): ${c.summary}${명세글(명세(c.id))}`,
      { schema: VERDICT, label: `데이터검증팀:${c.id}`, phase: 'Verify' }),
    () => 역할('stock-verifier-stats', `발굴 루프 ${회차}회차 후보를 검증 B 로 반박하세요.\n후보 ${c.id} [${c.team}] ${c.name} (${c.kind}): ${c.summary}${명세글(명세(c.id))}`,
      { schema: VERDICT, label: `계량검증팀:${c.id}`, phase: 'Verify' }),
  ]),
  ([a, b], c) => {
    const 둘 = [a, b].filter(Boolean)
    // 보수적으로 합친다 — 하나라도 탈락이면 탈락, 검증 하나가 죽었으면 보류(CLAUDE.md 발굴 루프 2단계)
    let 합 = 둘.length < 2 ? 'hold' : 둘.map((v) => v.verdict).sort((x, y) => 순위[y] - 순위[x])[0]
    // 동결 규칙(factors.md 12장 머리): 사용자 지시분이 아니면 조건부는 보류
    if (합 === 'conditional' && !지시분) 합 = 'hold'
    return { ...c, spec: 명세(c.id), A: a, B: b, verdict: 합 }
  },
)
return {
  round: 회차, userDirected: 지시분, judgments: 판정들, judgeMissing: !판정, questions: 질문, plannerMissing: 기획죽음,
  teams: 부를팀.map((t) => t.부서), deadTeams: 죽은팀,
  triage: 모음.length <= 최대 ? { none: '상한 안' } : 거름없음 ? { fallback: '팀 순서' } : { merged: 거름.merged || [], dropped: 거름.dropped || [] },
  questionsTrimmed: 잘린질문, designMissing: !설계, results: 결과.filter(Boolean), skipped: [...넘침, ...팀넘침],
}
