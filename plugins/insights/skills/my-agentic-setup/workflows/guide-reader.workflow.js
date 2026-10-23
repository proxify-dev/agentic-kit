export const meta = {
  name: 'guide-reader-laptop',
  description: 'Level 3 decided by AI readers judging the laptop evidence pack against the guide (1 reader first; 2 more + a judge when unsure or the count disagrees; a writer composes the page text)',
  phases: [
    { title: 'First read', detail: 'habit reader, sonnet' },
    { title: 'Escalate', detail: 'fresh-chat and skeptic readers (sonnet), opus judge on a split' },
    { title: 'Write', detail: 'opus writer checks every claim against the pack and writes the verdict file' },
  ],
}
// The laptop version of the call scorer that decides Level 3 from evaluation calls
// (2026-10-22): same guide, same verdicts, same cascade and the same five checks, with the evidence pack
// (evidence_pack.py) in place of a call transcript. "Not asked" becomes "not seen": the logs only see this
// laptop's Claude Code. Readers run in the person's own Claude Code and read only the guide, the pack and the
// files the pack points to. The verdict goes to a file on their machine; this script returns no quotes.
//
// args: { pack: path to evidence-l3.md, guide: path to guide-l3.md, out: path for guide-verdict.json,
//         count_l3: true|false|null (what place_level.py's count says about Level 3) }

const { pack: PACK, guide: GUIDE, out: OUT, count_l3: COUNT_L3 } = args
const EVIDENCE = { type: 'object', properties: { evidence: { type: 'string' }, way: { type: 'string', enum: ['L3_way', 'L2_way'] } }, required: ['evidence', 'way'] }
const LABEL = { type: 'object', properties: {
  verdict: { type: 'string', enum: ['L3', 'below_L3', 'not_enough_seen'] },
  confidence: { type: 'string', enum: ['high', 'medium', 'low'] },
  why: { type: 'string' }, deciding: { type: 'array', items: EVIDENCE },
  gap: { type: 'string' }, next_question: { type: 'string' },
}, required: ['verdict', 'confidence', 'why', 'deciding', 'gap', 'next_question'] }

const RULES = `Stay independent and read-only:
- Read ONLY the level guide (${GUIDE}), the evidence pack (${PACK}), and files the pack lists under "Where to look". Open nothing else: no scores, no level pages, no other JSON.
- Change nothing: no file written, edited, moved or deleted; no command that changes anything; nothing sent anywhere.
- Judge the PERSON by what THEY made and do. Something Claude Code does by default, a plugin someone else wrote, or a template they copied is not their work: the pack's "Who made it" section says which is which. An agent following a rule file is not the person checking.
- Evidence must be real: a record from the pack, their own words as the pack quotes them, or what a file you opened actually contains. Never quote secrets, keys or client data; describe them instead.`

const CHECKS = `Before you decide, check these five (each comes from a real mistake a reviewer caught on calls):
1. NOT SEEN IS NOT MISSING. The records only see this laptop's Claude Code. If an area shows nothing and the pack says it can't see it, it cannot count against them. When what is missing was never visible, and what was shown is not enough on its own, answer not_enough_seen, not below_L3.
2. APPLY THE RULINGS LITERALLY. A file an AI wrote counts when they supervise what it writes. File size and pruning do not decide Level 3. Session management, planning first, and using tools others built do not decide it either. A script or job that is not about what the agent knows is not context work.
3. IN USE, NOT JUST PRESENT. A rule file or memory folder that exists but no chat reads, or nobody changes, is a plan, not a habit. Count what the records show happening.
4. THEIR WORK, NOT THE TOOL'S. Credit only what they wrote or shaped (see "Who made it"). Claude Code's own default memory counts only when the records show them shaping it.
5. SHOW BOTH SIDES. why = what decides it, in one plain sentence; gap = what is missing or unclear (empty if nothing); next_question = the one interview question that would settle it (empty if settled).`

const LENSES = {
  habit: `Your angle: their DEFAULT HABIT. When the agent gets something wrong or lacks context in their real work, what do they do by default, and does it happen again? Look for repeated patterns in the records, not a single file or tool.`,
  fresh: `Your angle: the FRESH-CHAT TEST. If they opened a fresh chat on their real work now, what would it already know without them adding anything, how did it get there, and who put it there: them, their team, or an AI nobody supervises? Is it shaped (the right thing in the right place, kept small) or a dump the agent is told to read? Open the files under "Where to look" to see.`,
  skeptic: `Your angle: the SKEPTIC. First state to yourself the strongest case that they work the Level 2 way, then the strongest case for Level 3, then decide which the records support better. Do not let one impressive-looking file or number carry the verdict.`,
}

const labelPrompt = (lens) => `You are judging whether this person works at Level 3 (Agentic Engineer) of Proxify's agentic-engineering ladder.

Read the level guide first: ${GUIDE}. It is the only standard. Then read the evidence pack: ${PACK}. It replaces a call transcript: it was built from the files Claude Code keeps on their laptop.

There is NO required number of situations. The situations are examples of the discipline, not a checklist. Decide on the whole picture with the guide's own question: when the agent lacks something or gets something wrong, does their fix LAST (the next fresh chat already knows, without them adding anything), or do they fix it in the chat (re-type it, paste it, pick the files by hand, keep one long chat)? Read the "carried" situations and the Rulings in the guide: they say what does NOT make someone Level 3 on its own.

${LENSES[lens]}

Verdicts:
- L3: they work the Level 3 way (at least Level 3; someone clearly beyond it is still L3 here).
- below_L3: they work the Level 2 way, or lower.
- not_enough_seen: the records don't really show how they handle what the agent knows. Do not guess; this is a valid answer.

${CHECKS}

confidence: how sure you are. why: ONE plain sentence a non-engineer can follow. deciding: up to 3 pieces of evidence that settle it, each marked L3_way or L2_way (include the other side when it exists).

${RULES}`

const judgePrompt = (readings) => `Three independent reviewers read the same evidence pack and disagreed on whether this person works at Level 3 (Agentic Engineer). Settle it.

Read the level guide first: ${GUIDE}. It is the only standard. Then read the evidence pack yourself: ${PACK}, and open the files it lists under "Where to look" if you need to. Weigh the reviewers' reasons; do not average them, and do not side with the majority by default.

${CHECKS}

Verdicts: L3 | below_L3 | not_enough_seen. why: ONE plain sentence naming what settled it. deciding: up to 3 pieces of evidence, each marked L3_way or L2_way.

${RULES}

The three readings:
${readings.map(r => `- ${r.lens}: ${r.verdict} (${r.confidence}) — ${r.why}`).join('\n')}`

const writePrompt = (final) => `You write the "Why this level" text a person reads about their own Level 3 answer, and an interviewer reads before their call. The LEVEL IS ALREADY DECIDED: ${final.verdict}${final.flagged ? ' (the readers split; flag it for the interviewer)' : ''}. Do not change it; if you think it is wrong, say so in writer_doubt.

Read the level guide (${GUIDE}) and the evidence pack (${PACK}) yourself. The readers' notes are hints, not facts: check every claim against the pack and the files it points to.
Readers' notes: ${final.why}${final.gap ? ' | gap: ' + final.gap : ''}

Write:
- reason: one or two plain sentences, addressed to the person ("you"). Lead with the strongest thing THEY do that decides it (or, for below / not enough seen, the clearest thing missing).
- gap: what is missing or unclear (empty if nothing).
- next_question: the one interview question that would settle it (empty if settled).
- evidence: 2 or 3 short lines from the pack or their files that prove the reason, each marked L3_way or L2_way. Include the other side if it exists. Never quote secrets or client data.
- writer_doubt: empty, or why the level itself may be wrong.

Checks: only Level 3 evidence (what the agent knows and how it gets it); leave out Level 4 things (hooks, pipelines, loops), planning, session management and tools others built. Credit only their own work (see "Who made it"). Plain words a non-engineer follows.

Then WRITE the result, as JSON, to ${OUT}: {"verdict", "flagged", "reads", "reason", "gap", "next_question", "evidence": [{"text", "way"}], "writer_doubt", "readings": [{"lens", "verdict", "confidence"}], "guide": "guide-l3.md"}. Use verdict "${final.verdict}", flagged ${final.flagged}, reads ${final.reads}, readings ${JSON.stringify(final.readings)}. Writing that one file is the only change you may make. Reply with only the word "written".`

phase('First read')
const first = await agent(labelPrompt('habit'), { label: 'first read (habit)', phase: 'First read', schema: LABEL, model: 'sonnet' })
if (!first) throw new Error('the first reader returned nothing')

// Escalate exactly as on calls: unsure, not enough seen, or the free count disagrees on L3-or-not
const countDisagrees = COUNT_L3 !== null && COUNT_L3 !== undefined && ((first.verdict === 'L3') !== !!COUNT_L3)
const escalate = first.confidence === 'low' || first.verdict === 'not_enough_seen' || countDisagrees
let readings = [{ lens: 'habit', ...first }], judge = null
if (escalate) {
  log(`escalating: ${first.confidence === 'low' ? 'low confidence' : first.verdict === 'not_enough_seen' ? 'not enough seen' : 'the count disagrees'}`)
  const [fresh, skep] = await parallel([
    () => agent(labelPrompt('fresh'), { label: 'fresh-chat reader', phase: 'Escalate', schema: LABEL, model: 'sonnet' }),
    () => agent(labelPrompt('skeptic'), { label: 'skeptic reader', phase: 'Escalate', schema: LABEL, model: 'sonnet' }),
  ])
  readings = [readings[0], fresh && { lens: 'fresh', ...fresh }, skep && { lens: 'skeptic', ...skep }].filter(Boolean)
  const split = new Set(readings.map(r => r.verdict)).size > 1 || readings.length < 3
  if (split) judge = await agent(judgePrompt(readings), { label: 'judge', phase: 'Escalate', schema: LABEL, model: 'opus' })
}
const decider = judge || readings[0]
const final = {
  verdict: decider.verdict, why: decider.why, gap: decider.gap, flagged: !!judge,
  reads: 1 + (escalate ? 2 : 0) + (judge ? 1 : 0),
  readings: readings.map(r => ({ lens: r.lens, verdict: r.verdict, confidence: r.confidence })),
}
phase('Write')
const wrote = await agent(writePrompt(final), { label: 'page writer', phase: 'Write', model: 'opus' })
// Only the level and how it was reached come back to the conversation; the reason and evidence stay in the file.
return { verdict: final.verdict, flagged: final.flagged, reads: final.reads, escalated: escalate,
  count_l3: COUNT_L3, written: String(wrote || '').toLowerCase().includes('written'), out: OUT }
