export const meta = {
  name: 'v15-cheap-practices',
  description: 'The cheap version\'s single AI pass: one agent reads the digest of what the person built and does by hand, and gives each listed practice a rung',
  phases: [{ title: 'Map', detail: 'one sonnet agent, two files in, one row per practice out, no browsing' }],
}

// args: { run: RUN_DIR, model?: 'sonnet', effort?: 'medium' }
const RUN = args.run
const S = { type: 'string' }
const ROW = { type: 'object', required: ['id', 'state', 'rung', 'could_be', 'why', 'basis', 'evidence', 'rule', 'ask'], properties: {
  id: S,
  state: { type: 'string', enum: ['seen', 'looked, not found', 'not read'] },
  rung: { type: 'string', enum: ['L1', 'L2', 'L3', 'L4', ''] },
  could_be: { type: 'string', enum: ['L2', 'L3', 'L4', ''] },
  why: S, basis: S,
  evidence: { type: 'array', items: { type: 'object', required: ['source', 'detail'], properties: { source: S, detail: S } } },
  rule: S, ask: S } }
const SCHEMA = { type: 'object', required: ['rows'], properties: { rows: { type: 'array', items: ROW } } }

const brief = `You give each agentic-engineering practice listed in cheap-practices.md a rung for the person who owns this laptop (never write
their name). Read exactly two files, each once, whole, and nothing else (no other file, no shell):
${RUN}/cheap-practices.md (the practices, their ladders, how a rung is read) and ${RUN}/cheap-digest.md (every artifact
they built, with authorship and whether it ran in the 30 days, and what they do by hand, with counts).

For EACH practice listed there, one row:
- rung: the highest rung whose ladder text the digest's evidence matches. L2 = they do it by hand (the "by hand" counts
  at the end of the digest); L3 = a setup of theirs does it (their files, skills, agents, standing rules); L4 = they built
  something that does it and changes how the agent runs (their hook, program, plugin, gateway), with whatever that
  practice's own L4 text asks for (often a test or eval: the digest's eval folders say which ran and how the runner
  keeps the agent out). Use only artifacts marked as theirs; installed things others wrote are L1 use.
- state: 'seen' with a rung; 'looked, not found' when the digest has the sources that would show it and they show
  nothing; 'not read' when nothing in the digest covers it.
- could_be: a higher rung the evidence points to but doesn't prove ('' otherwise).
- why: one or two plain sentences for their page, with the number and its base from the digest. No level words.
  Use the industry's words: session, not chat; sub-agent, not helper; compaction;
  worktree; evals; sandbox; headless run (claude -p, Agent SDK); tool calls they rejected. Never name what we
  read or how: no 'digest', 'detector', 'probe', 'pointer', 'rung'; say 'your files' or 'your logs'.
- basis: the artifact id in [brackets] (or the by-hand line) the rung rests on.
- evidence: 1-3 items {source: artifact id or digest section, detail: the exact number or phrase}.
- rule: what you matched, in one line (e.g. "hook:memory ran 254 chats + eval with runs, sandboxed -> L4 text").
- ask: a question for the call when the digest can't settle it, else ''.
Be exact: copy numbers from the digest; never invent one. Return one row per listed practice: as many as the first line of cheap-practices.md says, no more.`

phase('Map')
const out = await agent(brief, { label: 'map:practices', phase: 'Map', schema: SCHEMA, model: args.model || 'sonnet',
                                 effort: args.effort || 'medium' })
return out
