export const meta = {
  name: 'level-reader-laptop',
  description: 'The whole level (L1-L4) decided by AI readers judging one picture of the laptop evidence pack against the full level guide - the v14.2 call scorer on laptop records (1 reader first; 2 more + a bounded judge when unsure, L3+, or the count disagrees; a writer composes the page text)',
  phases: [
    { title: 'First read', detail: 'habit reader, sonnet' },
    { title: 'Escalate', detail: 'fresh-chat and skeptic readers (sonnet), bounded opus judge on a split' },
    { title: 'Write', detail: 'opus writer checks every claim against the pack and writes the verdict file' },
  ],
}
// The laptop copy of the call scorer behind substrate v14.2 (labs/v13/trials/situation-study/picture-scale.workflow.js,
// 2026-10-23, team repo): the readers build one picture - WHO CARRIES THE CONTEXT - and name the whole level from the
// full guide (guide-all.md), every L3+ gets 3 readers, and the judge is bounded (the level two readers gave stands
// unless the records show otherwise). Kept as close to it as the input allows; what differs, and why:
// - the evidence pack (evidence_pack.py) replaces the call transcript, so "the member's own words" become the records;
// - "not asked" becomes "not seen": the records only see this laptop's Claude Code;
// - the comparison level that forces a second look is place_level.py's count (on calls it was the previous version);
// - no floor: v14.2's floor (never below a survey level or a prod approval) needs data the laptop doesn't have.
//
// args: { pack: path to evidence.md, guide: path to guide-all.md, out: path for level-verdict.json,
//         count_level: 0-4 or null (place_level.py's suggested level) }

const { pack: PACK, guide: GUIDE, out: OUT, count_level: COUNT } = args
const LEVELS = ['L1', 'L2', 'L3', 'L4']
const EVIDENCE = { type: 'object', properties: { evidence: { type: 'string' }, level: { type: 'string', enum: LEVELS } }, required: ['evidence', 'level'] }
const READ = { type: 'object', properties: {
  level: { type: 'string', enum: LEVELS },
  open_above: { type: 'boolean', description: 'the records never really reach the next level up: it could be higher' },
  confidence: { type: 'string', enum: ['high', 'medium', 'low'] },
  why: { type: 'string' }, deciding: { type: 'array', items: EVIDENCE },
  gap: { type: 'string' }, next_question: { type: 'string' },
}, required: ['level', 'open_above', 'confidence', 'why', 'deciding', 'gap', 'next_question'] }

const RULES = `Stay independent and read-only:
- Read ONLY the level guide (${GUIDE}), the evidence pack (${PACK}), and files the pack lists under "Where to look". Open nothing else: no scores, no level pages, no other JSON.
- Change nothing: no file written, edited, moved or deleted; no command that changes anything; nothing sent anywhere.
- Evidence must be real: a record from the pack, their own words as the pack quotes them, or what a file you opened actually contains. Never quote secrets, keys or client data; describe them instead.`

const CHECKS = `HOW TO DECIDE. A level is one cohesive picture of how the person works. It is never a count of things they have or did, and never one example ticked.

First build the picture from the records:
 a) A fresh chat on their real work tomorrow, with only the task typed: what does the agent already know, and where does that come from?
 b) Who decided where that lives, and do they keep it right (fix it when it is wrong, keep it small, put each rule where it applies)?
 c) What do they still carry by hand in each chat: re-explaining, pasting, pointing at files, deciding what to keep?
 d) How do they run the work itself: do they shape it before the agent acts, steer it, check what comes back and own it?
Then name the level from the guide's ladder and borders:
- Level 1: uses agents on real work, and mostly takes what comes back.
- Level 2: works with discipline (shapes, steers, checks, manages chats on purpose), but CARRIES THE CONTEXT: what the agent knows is what they bring to each chat. They may have rules files and skills of their own.
- Level 3: has ENGINEERED THE CONTEXT: the agent gets what it needs from where they put it, and the records show why it lives there and that they keep it right (the owner: "setting up context, for themselves or the team, is L3").
- Level 4: also builds what changes how the agent runs, and proves it with evals the agent can't edit.

Watch for these (each is a mistake a reviewer caught):
1. FILES AND SKILLS ARE NOT THE LINE; WHO CARRIES THE CONTEXT IS. A CLAUDE.md, rules files or skills on top of someone who still carries everything else is Level 2; the guide's border has a strong Level 2 with twenty skills. Equally, a small setup that really carries the context is Level 3.
2. NO SINGLE EXAMPLE IS THE TEST. Any of the guide's Level 3 abilities can show the picture: rules in the right place, depth behind pointers, corrections that land in the file, memory or a knowledge base with a shape they chose (its content may be general best practices they follow: owner ruling 10), notes the next session reads on its own, context set up for the team. Judge how they hang together.
3. NOT SEEN IS NOT MISSING, AND NEVER A RAISE. The records only see this laptop's Claude Code (the pack's last section says what they cannot show). Something they cannot see never lowers a level; and nothing RAISES a level by not being seen: a level needs its own picture in the records.
4. IF THE RECORDS DON'T SETTLE IT, give the level they do show, say in gap what is missing, and put the one interview question that would settle it in next_question. open_above = true when the records never reach the next level's picture.
5. THEIR OWN WORK, IN USE. Credit only what they wrote or shaped: the pack's "Who made it" section says which is which; Claude Code's defaults, someone else's plugin or a copied template are not theirs. A file that exists but no chat reads or nobody changes is a plan, not a habit. An agent following a rule file is not the person checking.
6. The guide's rulings and its "not in the map" list apply as written. Where they use agents doesn't matter: side projects count like client work.`

const LENSES = {
  habit: `Your angle: their DEFAULT HABIT - what they do by default when the agent gets something wrong or lacks context, and what they have built around it. Repeated patterns in the records, not one file or one number.`,
  fresh: `Your angle: the FRESH-CHAT TEST - if they opened a fresh chat on their real work now, what would it already know and do without them adding anything, who put it there, and what runs without them typing? Open the files under "Where to look" to see.`,
  skeptic: `Your angle: the SKEPTIC - state the strongest case for the level BELOW your first instinct and for the level ABOVE it, then decide. One impressive-looking file or number never carries a level.`,
}

const readPrompt = (lens) => `You decide the level this person works at on Proxify's agentic-engineering ladder: Level 1 (Agentic Explorer), Level 2 (Agentic Practitioner), Level 3 (Agentic Engineer) or Level 4 (Agentic Architect).

Read the level guide first: ${GUIDE}. It is the only standard. Then read the evidence pack: ${PACK}. It replaces a call transcript: it was built from the files Claude Code keeps on their laptop. Decide on the whole picture.

${LENSES[lens]}

${CHECKS}

level: the level whose picture the records show. open_above: true when the records never really reach the next level's picture. confidence: how sure you are. why: ONE plain sentence a non-engineer can follow. deciding: up to 3 pieces of evidence that settle it, each tagged with the level it shows (include the other side when it exists). gap: what is missing or unclear. next_question: the one interview question that would settle it.

${RULES}`

const judgePrompt = (readings) => `Three independent readers read the same evidence pack and disagreed on the level (Level 1 to Level 4 of Proxify's agentic-engineering ladder). Settle it.

Read the level guide first: ${GUIDE}. Then read the evidence pack yourself: ${PACK}, and open the files it lists under "Where to look" if you need to. Weigh the readers' reasons.

HOW TO SETTLE: decide on the picture, not the vote. The level at least two readers gave stands unless the records show otherwise: going ABOVE two readers needs records showing that level's picture; going BELOW them needs records showing what they still carry by hand. With three different answers, start from the middle one.

${CHECKS}

Answer with the same fields: level, open_above, confidence, why (ONE plain sentence naming what settled it), deciding (evidence tagged with the level each shows), gap, next_question.

${RULES}

The three readings:
${readings.map(r => `- ${r.lens}: ${r.level}${r.open_above ? ' (could be higher)' : ''} (${r.confidence}) - ${r.why}`).join('\n')}`

const writePrompt = (final) => `You write the "Why this level" text a person reads about their own level, and an interviewer reads before their call. The LEVEL IS ALREADY DECIDED: ${final.level}${final.open_above ? ' (the records never reach the level above)' : ''}${final.flagged ? ' - the readers split; flag it for the interviewer' : ''}. Do not change it; if you think it is wrong, say so in writer_doubt.

Read the level guide (${GUIDE}) and the evidence pack (${PACK}) yourself. The readers' notes are hints, not facts: check every claim against the pack and the files it points to.
Readers' notes: ${final.why}${final.gap ? ' | gap: ' + final.gap : ''}

Write:
- reason: one or two plain sentences, addressed to the person ("you"). Lead with the picture: in a fresh chat on their work, what does the agent already know and who put it there, or what do they still carry by hand. Name the strongest thing THEY do that puts them at this level, and say why not the level above (not shown, or not visible in these records).
- gap: what is missing or unclear (empty if nothing).
- next_question: the one interview question that would settle the level above.
- evidence: 2 or 3 short lines from the pack or their files that prove the reason, each tagged with the level it shows. Include the other side if it exists. Never quote secrets or client data.
- writer_doubt: EMPTY unless you believe the decided level itself is wrong; then say which level and why in one sentence.

Checks - reviewers rejected earlier pages for each: never leave out their STRONGEST evidence (anything they built themselves: a tool, a skill, a script, an agent setup); credit only their own work (see "Who made it"); plans are not done; side projects count like client work. Plain words a non-engineer follows.

Then WRITE the result, as JSON, to ${OUT}: {"level", "open_above", "flagged", "reads", "count_level", "reason", "gap", "next_question", "evidence": [{"text", "level"}], "writer_doubt", "readings": [{"lens", "level", "open_above", "confidence"}], "guide": "guide-all.md", "scorer": "v14.2 picture"}. Use level "${final.level}", open_above ${final.open_above}, flagged ${final.flagged}, reads ${final.reads}, count_level ${JSON.stringify(COUNT ?? null)}, readings ${JSON.stringify(final.readings)}. Writing that one file is the only change you may make. Reply with only the word "written".`

const lvl = (s) => Number(String(s || 'L1').slice(1))

phase('First read')
const first = await agent(readPrompt('habit'), { label: 'first read (habit)', phase: 'First read', schema: READ, model: 'sonnet' })
if (!first) throw new Error('the first reader returned nothing')

// Escalate as v14.2 does on calls: unsure, any L3+, or the comparison level disagrees (here: the count)
const countDisagrees = COUNT !== null && COUNT !== undefined && lvl(first.level) !== Number(COUNT)
const escalate = first.confidence === 'low' || lvl(first.level) >= 3 || countDisagrees
let readings = [{ lens: 'habit', ...first }], judge = null
if (escalate) {
  log(`escalating: ${first.confidence === 'low' ? 'low confidence' : lvl(first.level) >= 3 ? 'L3+ gets 3 readers' : 'the count disagrees'}`)
  const [fresh, skep] = await parallel([
    () => agent(readPrompt('fresh'), { label: 'fresh-chat reader', phase: 'Escalate', schema: READ, model: 'sonnet' }),
    () => agent(readPrompt('skeptic'), { label: 'skeptic reader', phase: 'Escalate', schema: READ, model: 'sonnet' }),
  ])
  readings = [readings[0], fresh && { lens: 'fresh', ...fresh }, skep && { lens: 'skeptic', ...skep }].filter(x => x && x.level)
  const split = new Set(readings.map(r => r.level)).size > 1 || readings.length < 3
  if (split) judge = await agent(judgePrompt(readings), { label: 'judge (bounded)', phase: 'Escalate', schema: READ, model: 'opus' })
}
const decider = judge || readings[0]
const final = {
  level: decider.level, open_above: !!decider.open_above, why: decider.why, gap: decider.gap, flagged: !!judge,
  reads: 1 + (escalate ? 2 : 0) + (judge ? 1 : 0),
  readings: readings.map(r => ({ lens: r.lens, level: r.level, open_above: !!r.open_above, confidence: r.confidence })),
}
phase('Write')
const wrote = await agent(writePrompt(final), { label: 'page writer', phase: 'Write', model: 'opus' })
// Only the level and how it was reached come back to the conversation; the reason and evidence stay in the file.
return { level: final.level, open_above: final.open_above, flagged: final.flagged, reads: final.reads, escalated: escalate,
  count_level: COUNT ?? null, readings: final.readings, written: String(wrote || '').toLowerCase().includes('written'), out: OUT }
