# Member Briefs v3: Style Guide

Status: draft for orchestrator and Paul review, revised 2026-10-20 after the 15-member pilot (1 of 15 passed; review `C:\melange-data\member_briefs_v3_pilot\review_opus.json`, edits E1 to E12, orchestrator rulings R1 to R9), and again on 2026-10-21 after pilot 2 (reviewer A edits S2 to S7, S10, S13, S14; reviewer B edits 2, 4 to 8, 10, 11; rulings R10 to R12; where A and B differ, the stricter wording is used). Source of truth for the v3 writer prompt (`member_briefs_v3_writer_prompt.md`), the v3 review prompt (`member_briefs_v3_review_prompt.md`), the gold exemplars and coined-animal registry (`member_briefs_v3_exemplars.json`), and the mechanical checks the code must run (section 14). Where this guide states a rule "precisely", the packet builder and validator implement it exactly as written.

## 0. Authority and what changed

The template is Paul's "Member Briefing Profiles" (2026-10-20, ten archetype samples). It is authoritative for order, section names, tone, and length. Paul's rulings of 2026-10-20:

1. The old "Suggested approach" section becomes this full template block for all 533 members.
2. The template is followed loosely so each brief fits its member.
3. The brief combines biography, decision profile, personality read, and all forms of audio (short clips, hearings, floor, and long-form interviews and podcasts) into the most decisive profile the evidence allows, in language that is easy to understand.
4. Writer model: Haiku 5.5 at high effort. Reviewer: Opus.
5. Archetypes are an open set. The ten template animals are the preferred vocabulary. A new animal is allowed when none fits, with a two or three adjective descriptor and a one-line evidence justification.
6. The Basis line and Confidence level are computed in code. The model never writes them.
7. The v2 bans on "personalized persuasion" and "inferred emotional triggers" are superseded. Warning signs and Do/Don't advice are in scope, but every one must trace to public evidence in the member's packet.

Orchestrator rulings after the pilot (2026-10-20): R1 a strongly opposite slot disqualifies every animal; R2 coin trigger (section 8.2 step 5); R3 a mixed appeal gives no appeal advice, and a frame axis below most is never led with (5.7); R4 Lone Wolf, "independent", and "lone" are barred for party leaders (8.1, 8.3); R5 party names banned in any form (4); R6 pronoun openers (12); R7 cross-brief repetition threshold (12); R8 stock phrasing out of the examples, five sample meanings, seeded avoid list (7, 12); R9 shared coined animals (8.4).

What carries over from v2 (`member_narratives_v2_prompt.md`, `member_narratives_v2_portrait_prompt.md`, `revise_member_narratives_v2.py:58-87`):

- Every claim traces to a packet field and keeps that field's direction and strength.
- Independently scoped measures stay separate. Two measures may disagree; the brief may name the tension but never averages it away.
- The cross-party gate, the repeated-defensive gate, and the topic-association gate (section 11).
- The banned private-state, unsupported-inference, and measurement-jargon lists, extended in section 11.
- The leadership source-era rule and the long-form turn rule (section 6).
- No numbers, no em or en dashes, no clinical or diagnostic language, no repeated stock phrasing across briefs, no hedging (Paul's standing rules).

## 1. The brief at a glance

Rendered order. Items marked CODE are produced by code, not by the model.

| Rendered line | Source |
|---|---|
| `<Name> — The <Animal>` heading (rendered by code from the display name and `archetype.animal`) | CODE, from `archetype.animal` |
| `Archetype: <Animal>, <descriptor>` | CODE renders; model writes `animal` and `descriptor` |
| `Bottom line:` | model `bottom_line` |
| `Do` with four bullets | model `do` |
| `Don't` with three bullets | model `dont` |
| `Warning sign:` | model `warning_sign` |
| `Follow-up:` | model `follow_up` |
| `Profile`, then run-in `Personality.` | CODE adds label; model `personality` |
| run-in `How he speaks.` / `How she speaks.` / `How they speak.` | CODE adds label from pronoun; model `speaks` |
| run-in `How he decides.` / `How she decides.` / `How they decide.` | CODE adds label from pronoun; model `decides` |
| `Basis: ... Confidence: ...` | CODE only |

The model never writes the section labels, the member's title, the Basis line, or any confidence statement.

## 2. Voice

Study the template. Its voice is the target.

- Plain and concrete. A colleague who has met the member is talking to you in a hallway before the meeting.
- Second person to the reader in Bottom line, Do, Don't, Warning sign, and Follow-up ("Lead with...", "Send...").
- Third person about the member in the three Profile paragraphs.
- Short sentences. Target an average of 15 words or fewer. Hard ceiling 28 words for any sentence.
- Reading level about 8th grade. Prefer one- and two-syllable words. Code measures Flesch-Kincaid grade across the whole brief: target 8.5 or lower, fail above 10.
- State traits flat. "He is skeptical by default." Never "experts see him as", "he appears to be", "the data suggest".
- No hedging words (section 11D). Strength comes from choosing the right strength word (section 5.1), not from qualifiers.
- Practical. Every Profile paragraph should help the reader act. The template's sentences do work: "He is won over by argument, not by rapport." "Expect him to run the meeting."
- Name tensions in one plain sentence when two measures disagree: "His public words are warm, but in hearings he is one of the most combative members in the House." Do not explain the tension away.
- Contractions are fine. No exclamation marks. No rhetorical questions. No jokes about the animal.
- Sentence openers follow section 12 (R6): at most two sentences in a row may open with the member's pronoun, and no other word may open two sentences in a row.

## 3. Section rules and word bands

Word counts are hard limits checked in code. Targets are what good briefs usually hit.

| Field | Hard limit | Target | Form |
|---|---|---|---|
| `archetype.animal` | 1 to 3 words | 1 word | Title case common animal name |
| `archetype.descriptor` | 2 or 3 adjectives, at most 6 words | 2 adjectives | lower case; "x and y" or "x, y, and z" |
| `archetype.justification` | 12 to 30 words, one sentence | 18 to 25 | plain sentence naming the evidence pattern, no ids |
| `bottom_line` | 15 to 30 words, 1 or 2 sentences (thin tier: 12 to 30) | 18 to 26 | second person; the one thing to remember |
| `do` | exactly 4 items, each 3 to 15 words | 5 to 12 | verb-led imperative, no trailing period |
| `dont` | exactly 3 items, each 3 to 15 words | 4 to 10 | verb-led, no trailing period, never starts with Don't, Do not, Never, Avoid, or Not |
| `warning_sign` | 25 to 45 words, 2 or 3 sentences | 30 to 40 | cue, meaning, action (section 7) |
| `follow_up` | 15 to 35 words, 1 or 2 sentences | 18 to 28 | what to send, to whom, by when, in what form |
| `personality` | 60 to 110 words (thin tier: 35 to 110) | 70 to 95 | who this person is |
| `speaks` | 60 to 110 words (thin tier: 35 to 110) | 70 to 95 | how a conversation with them sounds and moves |
| `decides` | 60 to 110 words (thin tier: 35 to 110) | 70 to 95 | what moves them, how they commit, what to do about it |

### Bottom line

One or two sentences that would still be useful if the reader read nothing else. Lead with the single most important move, then the reason or the risk. Use the pronoun, not the name. Template model: "Come over-prepared with data and let him reach the conclusion himself. Pressure will lose him."

### Do (4) and Don't (3)

- Each bullet is one concrete action the reader can take in the room or in preparation.
- Start with a base verb: Lead, Open, Bring, Send, Ask, Keep, Let, Name, Frame, Answer, Show, Offer, Turn, Put, Give, Pair, Tie, Hold, Push, Spring, Bury, Pitch, Rush, Treat, Mistake, Crowd, Interrupt, Rely, State, Match, Take, Meet, Count, Repeat, Read, Lay, Link, Skip, Leave.
- The Don't header supplies the negation. "Push for a commitment in the room", not "Don't push for a commitment".
- No trailing period. No semicolon chains. One idea per bullet.
- A Don't must not be the mirror image of a Do. "Let his long answer run" with "Interrupt his long answers" is a reversal and fails (E9).
- No two Do bullets may carry the same idea, such as two bullets that both say to state the decision first (E9). The same holds for two Don't bullets.
- No two consecutive bullets in a list may start with the same word (R6).
- Order Do bullets in meeting order when possible: prepare, open, run the room, close.
- Every bullet cites at least one evidence id that is not a broad trait (section 9).
- At most two `PROC`-only bullets across Do and Don't together, whenever the packet has any non-`PROC` id allowed for Do or Don't (E10; section 9.3 rule 5).

### Warning sign

See section 7.

### Follow-up

What to send, to whom (member or staff), by when ("the same day", "within a day", "within a few days"), and in what form (one-page memo, written answers, letter, recap with owners and dates). Tie the form to the evidence: a member with weak follow-through gets a recap to staff; a careful, detail-minded member gets point-by-point written answers.

### Personality

Who this person is: character, role, record, and at most two biography facts. Open with the member's surname once ("Lucas is..."), then use the pronoun. Lead with the strongest, most decision-relevant traits. Fold in the observed record (cross-party work, issue range, follow-through, hearing conduct, visibility) where it agrees with or cuts against the character read. Biography comes only from `member.leadership` (as `LD.plain`), the allowed `BIO.*` items (section 4), or public sensibilities.

### How X speaks

Pick the four or five habits a visitor will notice in the first ten minutes and say what to do about them (E11). Do not list every measure. Candidates: answer length, who holds the floor, pace, pauses, pitch movement, volume, filler, defending ground (gated), word tone (warm, upbeat, forceful, emotionally charged), and the setting where the evidence comes from (House floor, interviews, hearings, long-form podcast). Template voice: "Expect him to run the meeting." End on what the reader should do in the exchange.

### How X decides

What moves them and how they commit: the appeal that fits (only when section 5.7 allows appeal advice), how outside pressure lands, how they hold a position once taken, what their follow-through record means for a yes, and any public sensibility that gives the reader honest common ground. End on a practical move. Do not repeat a move already made in the bottom line or a Do bullet.

## 4. Pronouns, names, titles, party, and place

- Use `member.pronoun` from the packet exactly: he/him/his, she/her/her, or they/them/their with plural verb agreement. Never infer gender from the name, the voice, or pitch.
- The member's surname appears once, at the start of `personality`. Nowhere else: not in bullets, not in the bottom line, not in the warning sign.
- Never print the full name, or a title: "Rep.", "Sen.", or "Senator", "Representative", "Congressman", "Congresswoman" used as a title before a name (E7).
- Party names are banned in any form, in every field, including biography (R5): Republican, Republicans, Democrat, Democrats, Democratic, GOP, and "Independent" used as a party label. "His party", "her party's" are fine. `BIO.party` is never citable; the builder sets its `may_support` to empty.
- Leadership titles: use `LD.plain`, a party-free form the builder derives from `LD.claim` with the table below. Titles without a party word are used as given (Speaker of the House, House Majority Leader, House Majority Whip, Senate Majority Leader, Senate Majority Whip). "<pron>" is filled with the member's possessive pronoun.

| `LD.claim` | `LD.plain` |
|---|---|
| House Democratic Leader | <pron> party's House leader |
| House Assistant Democratic Leader | <pron> party's assistant leader in the House |
| House Democratic Whip | <pron> party's House whip |
| House Democratic Caucus Chair | chair of <pron> party's House caucus |
| House Democratic Caucus Vice Chair | vice chair of <pron> party's House caucus |
| House Republican Conference Chair | chair of <pron> party's House conference |
| House Republican Policy Committee Chair | chair of <pron> party's House policy committee |
| National Republican Senatorial Committee Chair | chair of <pron> party's Senate campaign committee |
| Senate Democratic Leader | <pron> party's Senate leader |
| Senate Democratic Whip | <pron> party's Senate whip |
| Senate Democratic Conference Vice Chair | vice chair of <pron> party's Senate conference |
| Senate Democratic Conference Secretary | secretary of <pron> party's Senate conference |
| Senate Democratic Deputy Conference Secretary | deputy secretary of <pron> party's Senate conference |
| Senate Democratic Conference leadership | a member of <pron> party's Senate leadership |
| Senate Democratic Steering and Policy Committee Chair | chair of <pron> party's Senate steering and policy committee |
| Senate Democratic Strategic Communications Committee Chair | chair of <pron> party's Senate communications committee |
| Senate Democratic Outreach Chair | chair of <pron> party's Senate outreach effort |
| Senate Democratic Outreach Vice Chair | vice chair of <pron> party's Senate outreach effort |
| Senate Republican Conference Chair | chair of <pron> party's Senate conference |
| Senate Republican Conference Vice Chair | vice chair of <pron> party's Senate conference |
| Senate Republican Policy Committee Chair | chair of <pron> party's Senate policy committee |

  A title not in this table and containing a party word fails the packet build (fail closed) until a row is added.
- State: the state name may appear at most once, and only in `personality` (E7), and only inside a biography fact that says something else ("He has spoken about the Oklahoma City bombing..."). Never a sentence of its own about the state ("Iowa is his home state") (B10). A state name inside a named event or place ("the Oklahoma City bombing") counts as that one use.
- District: never. District numbers are spelled-out ordinals, which section 11H bans.
- `BIO.prior_career`: the builder keeps it only when it is one complete sentence of 30 words or fewer that names a job or profession held before Congress (contains "worked as", "served as", "was a", or "practiced"), contains no party word, no wiki markup (`[[`, `]]`, `{{`), and no vote or family event. Otherwise the item is dropped. The writer uses it at most once, in `personality`.

## 5. Translating evidence into plain words

### 5.1 Band strength

Every band word maps to a fixed strength. Never inflate, never reverse. Levels used by code: among the highest = +3, well above most = +2, above most = +1, middling = 0, below most = -1, well below most = -2, among the lowest = -3.

| Packet band | Allowed strength words (pick and vary) | Never |
|---|---|---|
| among the highest in the chamber | one of the most X in the House/Senate; as X as anyone there; few match him on X; nearly anyone; almost anyone | always, never, the most X ever |
| well above most | very X; much more X than most; markedly X | any top-band word: few match, few are, as X as anyone, one of the most, nearly anyone, almost anyone |
| above most | fairly X; more X than most; somewhat X | very, highly, extremely, much more |
| middling | omit; at most use as a contrast ("ordinary pace") | any strength or weakness claim |
| below most | somewhat less X than most; a little Y; a little less | very, much less, and the low-strength words: little (without "a"), the low end, rare, rarely, seldom, hardly, barely, does little, counts for little |
| well below most | much less X than most; quite Y; little; seldom; rarely | any top-band word: one of the least, few are less, almost no |
| among the lowest in the chamber | one of the least X in the House/Senate; few are less X; almost no X; little | never, not at all |

Top-band words (E1) are only for "among the highest" or "among the lowest": few match, few are, few members, few senators, few in the House, as X as anyone, as X as any, one of the most, one of the least, nearly anyone, almost anyone, nearly everyone, almost no, among the.

Low-strength words (E1) are only for "well below most" or lower: little (but "a little" is the mild form and is fine at "below most"), the low end, rare, rarely, seldom, hardly, barely, does little, counts for little.

Chamber word: House members are compared with the House; senators with the Senate. Say "in the House" or "in the Senate", never "in Congress" for a band comparison.

Never print the band labels themselves ("well above most", "among the highest in the chamber", "middling"). Code rejects them.

### 5.2 The five broad traits (`TR.*`)

These are broad, public, expert-perceived reads. State them flat and in plain words. They describe character; they are not observed behavior.

| Trait id | High end, plain words | Low end, plain words |
|---|---|---|
| `TR.E` | outgoing, energetic in a room, takes up space | reserved, quiet, keeps to himself |
| `TR.O` | curious, open to new ideas, likes to explore | prefers familiar ground, practical, set in his ways |
| `TR.C` | careful, organized, precise, thorough | loose with details, moves on quickly, detail is not his strength |
| `TR.A` | agreeable, easy to work with, gives people the benefit of the doubt | skeptical by default, hard to win over, tough, blunt |
| `TR.N` | gets worked up easily, quick to feel pressure, runs hot | hard to fluster, unbothered under pressure |

Rules:

- Never use the trait nouns (openness, conscientiousness, extraversion, agreeableness, neuroticism, emotional stability) or "Big Five".
- `TR.N` (and its plain words) may appear in `personality` only, at band strength. It never supports a Warning sign, a Do, or a Don't by itself. Reason: the project's Emotional Stability read has no clean independent validation and its cross-modal pass was gender-confounded (`big5_methods.md` sections 16 to 18). Use observed behavior for anything actionable.
- A broad trait never predicts a specific behavior on its own. "Outgoing" does not license "holds the floor"; use `BH.dominance` for that. "Careful" does not license "finishes his bills" or "a yes holds"; use `BH.diligence` for that. A trait is not a habit: never write "his habits are careful" from `TR.C`.
- A trait read never becomes a view the member holds ("wary of easy wins", "keeps a wary eye on new claims"). `TR.A` low says "skeptical by default", not what he is skeptical about.
- When a broad trait and an observed record disagree, say both and let the contrast stand: "Careful detail is not his strength, yet few members match how reliably he votes and sees his bills through."

### 5.3 Qualified language reads (`QR.*`)

How the member's own public words read. Use as color in `personality` or `speaks`, phrased as words or talk, never as character, and never as a prediction of how a conversation will run.

| Id | Plain words |
|---|---|
| `QR.breadth` | talks about a wide (narrow) range of issues |
| `QR.diligence` | talks about his work in careful, detailed terms (or not) |
| `QR.antagonism` | high band = gentle, mild words; low band = sharp words, as sharp as anyone's |
| `QR.affect` | words carry a lot of (little) emotional charge |

`QR.*` and `HA` never forecast the conversation (S7, B8). `QR` describes the member's public words; `HA` describes hearings only. Say what the words are, then give the reader a move that does not predict how the talk will run. Precisely: a sentence that carries a `QR.*` or `HA` fact must not also contain "so expect", "so the exchange", "so the talk", "so the conversation", "so the meeting", or "expect a/an ... exchange/talk/conversation/meeting" (within six words), and must not give advice about how the member hears a narrow or broad ask (section 14 check 24).

### 5.4 Observed record (`BH.*`)

| Id | What it is | Plain words | Never |
|---|---|---|---|
| `BH.coop` (gated) | share of the member's own cross-party ties and of the other-party bills he joins | works with the other party more (less) than most; builds with both parties | friendliness, compromise, moderation, a named bill, a vote, a party name; that the other party joins or backs him; a forecast that a cross-party ask will stall or will not carry him; "your side" or "your own side" (E8) |
| `BH.breadth` | distinct issue areas in his bills | his bills cover many (few) issues | curiosity, intellect; any advice about how wide to make the ask, which issue areas to list or name, or how he weighs, hears, or prefers a narrow or broad plan ("she weighs how a plan fits across several areas", "since his bills touch fewer issues, skip side topics", "lay out every issue area the plan reaches"): `causal_or_motive_inference`. Description only (S6, B8; B8's wording is the stricter and governs) |
| `BH.diligence` | roll-call participation and bill follow-through | how reliably he votes and sees bills through; follow-through | work ethic, effort, laziness, "shows up" as a character claim |
| `BH.dominance` | holds and keeps the floor in conversations | takes charge of a conversation; holds the floor once he has it | leadership, ego, Extraversion |
| `BH.wpt` | answer length in conversations | answers run short (long) | enthusiasm, energy |

### 5.5 Interaction habits (`IX.*`)

From interview transcripts across independent programs.

| Id | Definition | Plain words | Never |
|---|---|---|---|
| `IX.wpt_mean` | average answer length | short (long) answers | |
| `IX.wpt_p90` | length of his longest answers | will (won't) take a long stretch when he wants one | a topic that causes it |
| `IX.gini` | how unequal his answer lengths are | answer length varies a lot (stays even) | |
| `IX.cutoff` | transcript turns ending unfinished | trails off or gets cut off more (less) than most | interrupting others; telling the reader to finish his sentences or to help him finish (turns can end unfinished because the host cut in) (E8) |
| `IX.burst` | runs of short turns in a row | answers in quick runs of short replies | contested back-and-forth, arguing, interrupting |
| `IX.defense` and `DF` (gated) | explicit language defending his ground, repeated across programs | defends his position when challenged; pushes back | defensive personality, insecurity |
| `IX.discmark` | filler and discourse markers ("you know", "I mean") | uses a lot of (almost no) filler | nervousness |

### 5.6 Audio (`AU.*`, `SC.*`)

Descriptive delivery only. Never infer emotion, stress, health, age, gender, or intent from acoustics. "Deliberate" and "deliberately" read intent into pace and are banned (section 11B, E8).

| Id | Plain words | Never |
|---|---|---|
| `AU.pace` | talks fast (slowly); quick (unhurried) pace | words per minute, nerves, deliberate |
| `AU.pause` | long (short) pauses between phrases | hesitation, uncertainty |
| `AU.volume` | volume rises and falls a lot (stays even) | loud, shouting (it is variability, not loudness) |
| `AU.pitch` | pitch rises and falls a lot (stays flat) | emotion, excitement |
| `AU.floor` | composition label: floor_only or floor_dominant means say "on the House floor" or "in floor speeches" | comparing floor delivery with interview delivery |
| `SC.<a>_<b>` | only when the packet marks the contrast `meaningful: true`: "He talks noticeably faster on Sunday shows than on the floor" | a cause for the difference |

An `AU.*` or `SC.*` id cited for a Do or Don't must be accompanied, in the same `evidence_refs` list, by an `IX.*`, `BH.*`, or `DF` id (section 14 check 13). Jitter (`strain`) and mean pitch (`f0_mean`) are never used in prose.

### 5.7 Appeals, hearings, visibility, receptivity

**Appeals (`MA`), precisely (R3).** The packet carries `appeals.dims` (one of outcome, community, technical, partnership, mixed), `appeals.agency_percentile`, and `appeals.communion_percentile`. Agency is the results axis; communion is the people axis. An axis is "below most or lower" when its percentile is under 0.42 (the ledger's `band()` cutoff for "below most").

- Appeal advice means any advice about which kind of case to make or lead with: results, outcomes, facts, data, numbers, wins (results frame); people, community, constituents, constituent stories, human stakes, families, relationships (people frame); written technical evidence ahead of time (technical frame); a visible or shared role, something to lead or announce (partnership frame); or a combination ("bring both").
- `mixed` (neither frame leads) produces NO appeal advice anywhere. `MA` is not citable in any field (the builder sets its `may_support` to empty), and the brief carries no appeal advice. Advice about format, timing, length, detail level (from `TR.C` or `BH.diligence`), follow-up, and public-sensibility common ground is still allowed.
- Axis floor: when agency is below most or lower, no field except `dont` may tell the reader to lead with, open with, start with, put first, or build the case on the results frame. When communion is below most or lower, the same holds for the people frame. A Don't may still warn against leading with it.
- The other dims give one frame to lead with: outcome = results; community = people; technical = written evidence shared ahead; partnership = a visible shared role the member can front, plus whichever axis is not below most.
- Never: "read the room", "neither warmth nor directness", "bring both" (for mixed), copying the frame text, "best confirmed in person".

| Id | Plain words and use | Never |
|---|---|---|
| `HA` | in hearings he is one of the most (least) combative members; may support advice for formal or adversarial settings, scoped as such | anger as a private state; a claim about private meetings stated as fact; that a tactic fails in hearings when `HA` only says his tone is mild |
| `VI.observed`, `VI.role` | one of the most visible members; visible for his role | attention-seeking, loves the camera, spotlight, ambition; that a public partner helps him decide; that a bill with his name on it, a role to announce, or credit will draw him in, unless `appeals.dims` is partnership (B11) |
| `RC` | outside pressure moves him more (less) than most members; for "about the same", usually omit. `RC` carries a strength (mild, clear, outer); use the matching 5.1 words, never top-band or low-strength words unless the strength is outer. A Don't about repeating the ask or wearing the member down needs `RC` less; with `RC` more, returning to the same ask is a Do, never a Don't (S13) | percentages; "immunity"; "counts for little" or "does little" unless strength is outer; telling the reader to give up; "gets to him/her", "lands harder on", "unsettles", or any wording that reads as stress (those are `TR.N` words); always name the source: "outside pressure moves her more than most" (S3) |

### 5.8 Public sensibilities (`PS.n`)

Things the member has said in public about his own life (family, formative events, routines, inspirations). Use them as biography and as honest common ground.

- Always keep them as public statements: "He has spoken about...", "She often tells the story of..." ("often" only if two or more PS items carry the same story).
- Never turn them into private belief or motive ("he is driven by guilt about..."). Never relabel one ("roots" for a story about moving to a town to start a practice).
- Advice that uses them says "where it honestly fits" or equivalent. Never script flattery or exploitation of a family tragedy.
- Keep names of family members out unless needed; never name children.
- Use each `PS` fact once per brief. Do not repeat it across `personality` and `speaks`.

### 5.9 Leadership role (`LD`)

- State `LD.plain` once in `personality`.
- Leaders' interview and audio evidence is not split by era in the current ledger. Phrase speech claims as current practice ("In interviews he...", "As leader, she..."). Never claim a lifelong or role-independent baseline ("he has always been...").
- Long-form evidence labeled `PRE_CURRENT_LEADERSHIP_ROLE` must say so in the sentence ("before he became Speaker"). Evidence labeled `CURRENT_ROLE_CONDITIONED` must be framed as how he works in the current role.

## 6. Audio sources: what each feeds

| Source | Packet ids | Feeds `speaks` | Feeds Do/Don't | Feeds Warning sign | Notes |
|---|---|---|---|---|---|
| Short clips across settings (interviews, Sunday shows, public radio) | `AU.*`, `SC.*` | yes | only with an `IX`, `BH`, or `DF` id in the same list | as cue description only, never as the meaning | delivery description, not emotion or intent |
| House floor recordings | `AU.*` with `AU.floor` floor_only or floor_dominant | yes, labeled "on the House floor" | rarely | no | floor microphones and format differ; never compare with interviews |
| Hearing transcripts | `HA` | yes, labeled "in hearings" | yes, for formal or adversarial settings | yes, as a cue (section 7) | combativeness is a setting, not a topic |
| Interview transcripts (turn structure) | `BH.dominance`, `BH.wpt`, `IX.*`, `DF` | yes | yes | yes, primary source | the backbone of the Warning sign |
| Long-form interviews and podcasts, qualified findings | `LF.<turn_id>.<n>` with status `qualified` | yes | yes | yes | complete reviewed turns only |
| Long-form, provisional findings | `LF.<turn_id>.<n>` with status `provisional` | at most one sentence, scoped to one instance ("In one long podcast interview before he became Speaker...") | no | no | until human review promotes them |
| Long-form turn without findings | `LF.<turn_id>` | no semantic claim at all | no | no | a transcript is not a finding; never summarize it yourself |

Short identity windows are not conversational turns. A long-form turn runs from when the member takes the floor until the interviewer takes it back, however long that is.

## 7. The Warning sign recipe

**Cue-eligible items, precisely:** `DF` (when `gates.DF` is true), any non-neutral `IX.*`, `BH.wpt`, `BH.dominance`, `QR.antagonism`, `HA`, and any `LF` finding with status `qualified` whose `era` is not PRE_CONGRESS_* or PRE_CURRENT_LEADERSHIP_ROLE (section 9.2). The builder sets `warning_form` in the packet: `observed` when at least one cue-eligible item exists, else `procedural`. This holds at every evidence tier (E2b).

**Observed form** (`warning_form: observed`). Three parts, in this order.

1. **Cue.** A change the reader can see or hear, measured against one of the member's cue-eligible habits. Every cue-eligible item is already high or low for this member, so the habit itself is his normal style and cannot be the cue by itself (E2): quick runs of short replies are not a warning for a member whose `IX.burst` is high; trailing off is not a warning for a member whose `IX.cutoff` is high; defending ground is not a warning for a member with `DF`. State the usual pattern, then the change. The Warning sign must contain one of these usual-pattern markers: usually, usual, normally, normal, by default, as a rule (section 14 check 19). A change can go either way: the habit stops (a member who usually holds the floor goes quiet) or it intensifies past its usual level (answers that get even shorter than his usual clipped style).
   Choosing the cue item, precisely (S10, B7; the stricter of the two governs). Answer-length items are `BH.wpt`, `IX.wpt_mean`, `IX.wpt_p90`, and `IX.gini`. (a) When the packet has a cue-eligible item at level +3 or -3 that is not an answer-length item, the cue rests on one of those. (b) Answer length is never the cue when `IX.gini` is above most or higher. (c) Short, shorter, clipped, terse, or brief replies are never the change when `IX.burst` is above most or higher. (d) Long, longer, or stretching answers are never the change when `IX.wpt_p90` is above most or higher. (e) The Warning sign must not contradict a Do or Don't in the same brief. (f) When the member shares an animal with an exemplar, the cue rests on a different item than that exemplar's cue whenever another cue-eligible item exists (section 12, R10).
2. **Meaning.** A plain, everyday reading of the change. Sample meanings, to adapt rather than copy (R8, E5): "he has stopped weighing your ask", "you have lost him", "she is done with the point", "the talk has turned into a contest", "he is answering a different question". Plain states like "irritated", "on guard", or "losing interest" are allowed when the cue is observed. Clinical, diagnostic, or physiological words are not (section 11A). The meaning never rests on a broad trait, on acoustics alone, or on a provisional long-form finding.
3. **Action.** What the reader does next, fitted to this member's case: stop pressing, restate the ask in one sentence, concede a weak point, offer written follow-up, narrow to one next step, or return to the frame the packet allows (never "go back to the facts" for a community-first member, never any frame for a mixed appeal).

Template model: "Answers that get even shorter and colder than his usual clipped style mean he's agitated. Stop pressing and go back to the evidence."

**Procedural form** (`warning_form: procedural`, no cue-eligible item, any tier). Say what a quiet or general meeting does not tell you, then give one concrete step. Cite `PROC`, plus the trait id if one supports the expectation. Never say what his silence or quiet means: the procedural form may not contain the word "means" (section 14 check 19). Write it fresh for each member; seeded phrases such as "as a no" and "for agreement" fail (section 12).

## 8. Archetypes

### 8.1 The ten template animals and their evidence signatures

Each animal has up to six slots. A slot is satisfied when any listed alternative holds. `hi` means level +1 or more; `lo` means level -1 or less; `HI` means +2 or more; `LO` means -2 or less (the two outer bands on each side). `MA:x` holds when `x` is in `appeals.dims` (never for mixed). `PS:x|y` holds when a `PS` item has category x or y. `RC:less` / `RC:more` hold on the receptivity direction. `DF` holds when `gates.DF` is true.

Global disqualifiers (apply to EVERY animal, R1): (a) any slot that is strongly opposite; (b) an unmatched REQUIRED slot. A slot is strongly opposite when it has at least one non-neutral band alternative present and every non-neutral band alternative present sits at -2 or below for a `hi` alternative (or +2 or above for a `lo` alternative). `MA`, `PS`, `RC`, and `DF` alternatives are never "present" for this test. A slot with no non-neutral band alternative present is neither matched nor opposite. A matched slot is never strongly opposite (for example Blue Jay slot 5 matched through `DF` stays matched even when `BH.dominance` is low).

The last column lists contradictions beyond the global rules.

| Animal | Default descriptor | Slots | Extra contradictions (disqualify) | Play in one line |
|---|---|---|---|---|
| Bull | dominant and skeptical | 1 `BH.dominance` hi or `FC.assertiveness` hi; 2 `TR.A` lo or `QR.antagonism` lo; 3 `TR.C` hi or `BH.diligence` hi or `QR.diligence` hi; 4 `DF`; 5 `BH.wpt` lo or `IX.wpt_mean` lo or `IX.discmark` lo; 6 `MA:outcome` or `MA:technical` | none | facts, let him reach the conclusion, no pressure |
| Hummingbird | warm and restless | 1 `FC.warmth` hi; 2 `MA:community`; 3 `TR.E` hi or `FC.excitement` hi or `FC.gregariousness` hi; 4 `TR.N` hi or `QR.affect` hi; 5 `IX.burst` hi or `IX.cutoff` hi or `AU.pace` hi | `FC.warmth` LO; `TR.A` LO | allies, reassurance, no surprises |
| Terrier | eager and fiercely partisan | 1 `TR.E` hi; 2 `TR.C` lo or `BH.diligence` lo; 3 `IX.burst` hi or `BH.dominance` hi; 4 `MA:outcome`; 5 `BH.coop` lo; 6 `FC.excitement` hi or `FC.activity` hi | `TR.E` LO; `BH.coop` HI | ask early, keep it short, follow up through staff |
| Orca | charismatic and visionary | 1 `TR.E` hi; 2 `VI.observed` hi or `VI.role` hi; 3 `TR.O` hi; 4 `TR.C` lo or `BH.diligence` lo; 5 `FC.excitement` hi; 6 `MA:partnership` | `TR.E` LO; `VI.observed` LO | sell the big idea, then lock commitments in writing |
| Tortoise | patient and guarded | 1 `TR.E` lo (REQUIRED); 2 `TR.C` hi or `BH.diligence` hi or `QR.diligence` hi; 3 `BH.dominance` lo or `BH.wpt` lo or `IX.wpt_mean` lo; 4 `AU.pace` lo or `AU.pause` hi; 5 `VI.observed` lo or `VI.role` lo or `HA` lo (one low-profile slot) | `TR.E` hi; `BH.dominance` HI | small meeting, materials ahead, time |
| Lone Wolf | principled and independent | 1 `TR.A` lo; 2 `RC:less`; 3 `PS:formative|inspiration`; 4 `IX.wpt_mean` hi or `IX.wpt_p90` hi; 5 `QR.antagonism` lo or `HA` hi; 6 `FC.gregariousness` lo | `TR.A` HI; `BH.coop` HI; `member.leadership` is not null (R4) | principle and a real story; never "everyone else is doing it" |
| Blue Jay | loud, curious, and committed | 1 `TR.E` hi; 2 `TR.O` hi; 3 `TR.A` lo or `QR.antagonism` lo or `HA` hi; 4 `FC.assertiveness` hi or `FC.excitement` hi; 5 `BH.dominance` hi or `DF`; 6 `TR.N` hi or `QR.affect` hi | `TR.E` LO; `TR.O` LO | her language, ready for hard questions, keep your tone even |
| Owl | scholarly and ambitious | 1 `TR.O` hi; 2 `TR.C` hi or `QR.diligence` hi; 3 `IX.wpt_mean` hi or `IX.wpt_p90` hi or `BH.wpt` hi; 4 `BH.breadth` hi or `QR.breadth` hi; 5 `MA:technical`; 6 `VI.role` hi | `TR.O` LO | the mechanism and the evidence, and a chance to lead |
| Butterfly | social and hard to pin down | 1 `FC.gregariousness` hi or `FC.warmth` hi; 2 `TR.E` hi; 3 `TR.C` lo or `BH.diligence` lo; 4 `BH.coop` hi; 5 `VI.observed` hi | `TR.E` LO; `TR.C` HI together with `BH.diligence` hi | enjoy the warmth, confirm specifics through staff |
| Bear | warm and fiercely protective | 1 `MA:community`; 2 `FC.warmth` hi; 3 `QR.antagonism` lo or `HA` hi or `TR.A` lo; 4 `PS:family|formative|district`; 5 `TR.N` hi or `QR.affect` hi; 6 `AU.volume` hi | `FC.warmth` LO | lead with her people, never threaten them |

Why the Tortoise row is built this way (dry run 2026-10-20): before the REQUIRED rule, 23 of 142 Tortoise calls had an outgoing, middling, or absent `TR.E`, and the two "absence of prominence" slots (low visibility, 107 hits; mild hearing tone, 91 hits) were counted separately, so ordinary low-profile members reached three slots without being reserved. Tortoise is for reserved members; low profile alone is not reserve.

Why Lone Wolf has the `BH.coop` HI contradiction: a member near the top of the chamber for cross-party work is not working alone (pilot: Padilla). Why leaders are barred (R4): an elected party leader works through the caucus by definition, and "principled and independent" misleads the reader (pilot: Scalise).

### 8.2 Choosing (code ranks, model confirms)

Code computes `archetype_candidates` (for each animal: matched slots, the item that matched each, contradictions, disqualified) and `coin_recommended`. The writer follows `coin_recommended`; the reviewer checks the arithmetic. Precisely:

1. **Disqualify** animals with a global disqualifier (8.1) or an extra contradiction.
2. **Threshold.** Let `top` be the most matched slots among live animals. If `top` is under 3 (under 2 when `evidence_tier` is thin), no template animal is chosen: go to step 5.
3. **Tie group.** G = live animals with `matched == top`, plus live animals with `matched == top - 1` and `matched >= 4`. If G has one animal, choose it and go to step 5.
4. **Tie-break**, in order:
   a. Appeal: keep the animals in G whose `MA` slot is matched. If exactly one remains, choose it. If two or more remain, G becomes that set. If none, G is unchanged.
   b. Outer count: `outer(c)` = the number of c's matched slots that contain at least one holding band alternative at level +3 or -3. Each slot counts once; an alternative counts only if it holds (builder_gap issue 4).
   c. Let T = the animals in G with the most matched slots, keeping only those with the highest `outer`. A runner-up R (`matched == top - 1`) replaces T only when `outer(R) >= max outer(T) + 2` (E12); then T = the runners-up meeting that bar with the highest `outer`.
   d. If T has more than one animal: drop any animal in T that has an `MA` slot whose dimension is not in `appeals.dims` (for a mixed appeal, every animal with an `MA` slot). If that drops all of T, keep T unchanged (E12).
   e. Choose the first remaining animal in alphabetical order.
5. **Coin trigger (R2).** `coin_recommended.value` is true when no template animal was chosen, or when the chosen animal leaves a binding theme unexplained. An item is explained when it is a holding alternative in one of the chosen animal's matched slots. A theme is unexplained when two or more of its items sit at level +3 or -3 in the theme's direction and none of those is explained. Themes (sign = direction):

| Theme | Items (sign) | Kind |
|---|---|---|
| cooperative | `BH.coop` (+), `TR.A` (+), `QR.antagonism` (+), `FC.warmth` (+), `HA` (-) | behavior, binding |
| combative | `TR.A` (-), `QR.antagonism` (-), `HA` (+) | behavior, binding |
| expressive | `TR.E` (+), `FC.excitement` (+), `FC.gregariousness` (+), `FC.warmth` (+) | behavior, binding |
| reserved | `TR.E` (-), `FC.gregariousness` (-), `BH.dominance` (-), `FC.warmth` (-) | behavior, binding |
| careful | `TR.C` (+), `BH.diligence` (+), `QR.diligence` (+) | behavior, binding |
| loose | `TR.C` (-), `BH.diligence` (-), `QR.diligence` (-) | behavior, binding |
| wide | `BH.breadth` (+), `QR.breadth` (+), `TR.O` (+) | range, advisory |
| narrow | `BH.breadth` (-), `QR.breadth` (-), `TR.O` (-) | range, advisory |

   An unexplained behavior theme is binding: coin. An unexplained range theme is advisory only: the builder lists it in `coin_recommended.advisory_unexplained` and the writer keeps the chosen animal, but may mention the range in `personality`. Fewer than two unexplained top or bottom items in a theme is advisory.
6. **Registry reuse (R9).** When step 5 says coin, or when the chosen template animal has fewer than 3 matched slots (possible only for thin packets), the builder checks the coined-animal registry (8.4). If a registry entry's signature holds in full, and, when the coin was triggered by a binding theme, the signature contains at least one item of every theme in `binding_unexplained` (S4, B4), the builder sets `coin_recommended.registry_match = {"animal", "descriptor"}`, `coin_recommended.value = true`, and the writer must use that animal. If several match, take the entry with the most signature items, then alphabetical. If none matches under this rule, the writer coins (pilot 2: Ox was forced on Luna through a -1 issue-range item while her binding theme was combative).

Why R2 is implemented as behavior themes, not "any two top or bottom items" (the nearest rule that keeps all six gold calls): the literal reading ("two or more top- or bottom-band items unexplained, in any direction") coined 406 of 533 members in simulation, including four gold members whose template animal is right (Jeffries, Johnson, Warren, Waters). Reading it as "two or more same-direction items in any theme" holds Jeffries, Johnson, Warren, and Lucas, but coins Waters: Bear explains her sharp words and emotional charge, and leaves three top-band range items (issue range in bills, issue range in talk, curiosity). Range items describe the subject spread of a member's work; they do not change how the reader runs the meeting, which is what the animal's play encodes. Making range themes advisory holds Waters as Bear and still coins Fitzpatrick (top-band cross-party work and agreeableness, a behavior theme Owl leaves unexplained). Ratio rules (theme size against explained items) cannot hold both: Fitzpatrick 2 against 3 explained must coin, Waters 3 against 4 must not.

Example rulings: Waters matches Blue Jay on six slots and Bear on five; Bear is in the tie group (5 is at least 4 and one behind), and wins step 4a because Bear's community slot matches and Blue Jay has no appeal slot. Fitzpatrick matches Owl on five slots; Owl leaves the cooperative theme (cross-party work and agreeableness, both top-band) unexplained, so he is coined, and the registry gives Beaver. Johnson: Bull is disqualified because its careful-detail slot is strongly opposite (`TR.C` and `BH.diligence` both well below most).

Projected distribution (reference simulation of this section over the current packet builder's 533 packets, 2026-10-20, in-memory patch; the executor should rerun after implementing): Tortoise 107 (20.1%), new coin 63 (11.8%), Bull 54, Owl 46, Bear 46, registry Ox 38, Terrier 37, Blue Jay 31, Hummingbird 31, Orca 29, Butterfly 16, registry Hare 14, Lone Wolf 12, registry Beaver 9. Gold calls: Jeffries Bull, Johnson Terrier, Warren Blue Jay, Fitzpatrick coin (Beaver), Waters Bear, Lucas Tortoise. Pilot calls under the same rules: Scalise Bull, Padilla Beaver, Gluesenkamp Perez Beaver, James Orca, Neguse Hummingbird, Sánchez Hare, Grassley Bull, Lummis Tortoise, Kim Bear, Balint Bear, Ogles coin (loose theme), Boozman Tortoise, Fong Ox, Mejia Terrier, Evans Hare. These match every archetype call the pilot reviewer supported. The registry grows during the run, so Ox, Hare, Beaver, and new coins will shift as later batches reuse animals.

### 8.3 Descriptor rules

- Start from the default descriptor (or the registry descriptor). Replace any adjective the packet does not support.
- "partisan" needs `BH.coop` lo. "visionary" needs `TR.O` hi. "ambitious" needs `VI.role` hi. "restless" needs `IX.burst`, `AU.pace`, or `FC.excitement` hi. "loud" needs `AU.volume` hi or `FC.assertiveness` hi. "protective" needs a community appeal plus a family, formative, or district `PS` item; otherwise use "warm and sharp-tongued" style alternatives. "eager" needs `TR.E` hi.
- "independent" and "lone" are barred for any member with `member.leadership` not null (R4).
- "steady" and "calm" are banned (section 11A). The Tortoise default is "patient and guarded"; use "reserved and thorough" when guardedness is not supported.
- Adjectives must be plain, everyday, and not insulting: skeptical, careful, warm, quick, forceful, reserved, thorough, curious, intense, combative, practical, cooperative, wide-ranging, blunt, tenacious, independent (not for leaders), loose, narrowly focused.

### 8.4 Coining, and the shared registry (R9)

- The registry is shared by all briefs. It is seeded from `member_briefs_v3_exemplars.json` `coined_registry` (Beaver, Ox, Hare as of 2026-10-20) and grows from finished briefs. Run batches in series and rebuild the registry between batches, so a coin in batch N is reusable in batch N+1 (E4). After each assembly, restage the packets of every later batch (or re-inject `coined_animals` and `avoid_phrases`) so they carry every entry from earlier batches; the manifest and run log record the registry hash each packet saw (B2, S5).
- Registry entry: `{"animal", "descriptor", "image", "signature": [[id, cond], ...], "first_member"}`, with `cond` one of hi, lo, HI, LO, less, more, present. A signature holds when every condition holds (8.1 semantics).
- When a writer coins a new animal, the builder registers it after the batch with signature = every band id cited under that brief's `evidence_refs.archetype` whose level is +2 or more (cond HI) or -2 or less (cond LO), so an entry registered on outer items is never reused on +1 or -1 items (S4), plus `RC:less` or `RC:more` if `RC` is cited there. An entry needs at least two signature items; with fewer, the animal is used once and not registered.
- `image` is a plain everyday picture of 3 to 8 words, written by the orchestrator at registration ("builds with others"), never the justification sentence; an image outside 3 to 8 words is rejected (S5 at most eight words, B5 three to ten; the stricter bound governs).
- A new animal name already in the registry with a different signature fails: the writer must reuse the entry (when its signature holds) or choose another animal.
- Use a common, recognizable, non-insulting animal. Never: snake, rat, weasel, vulture, hyena, jackal, pig, sloth, leech, shark, donkey, elephant (party symbols), or any mythical creature.
- Check the image against the strongest items before keeping it (E4). A heron is patient and precise; never use it for a member whose work-talk is loose or whose follow-through is weak. A beaver builds with others. An ox pulls one furrow at a time. A hare covers a lot of ground and moves on before the finish.
- The justification names the evidence pattern in plain words and, for a coin, says why no template animal fits: name the unexplained theme's items, or say no template animal reaches the threshold. One sentence of 12 to 30 words. Never chamber-specific in a registry descriptor.

### 8.5 Using the animal in the brief

The animal sets the play, not the facts. Borrow the template animal's playbook only where this member's evidence supports each move (no "let him reach his own conclusion", "at her own pace", or "tie it to the party agenda" unless an item supports it). Never mention the animal outside the `archetype` object. Never write "like a bull" or extend the metaphor.

## 9. Evidence binding

### 9.1 Evidence ids

The packet builder assigns stable ids. The writer cites them; it never invents them.

| Prefix | Items |
|---|---|
| `TR.` | `TR.O`, `TR.C`, `TR.E`, `TR.A`, `TR.N` |
| `QR.` | `QR.breadth`, `QR.diligence`, `QR.antagonism`, `QR.affect` |
| `BH.` | `BH.coop`, `BH.breadth`, `BH.diligence`, `BH.dominance`, `BH.wpt` |
| `FC.` | `FC.warmth`, `FC.excitement`, `FC.activity`, `FC.gregariousness`, `FC.assertiveness` |
| `IX.` | `IX.wpt_mean`, `IX.wpt_p90`, `IX.gini`, `IX.cutoff`, `IX.burst`, `IX.defense`, `IX.discmark` |
| `AU.` | `AU.pace`, `AU.pause`, `AU.volume`, `AU.pitch`, `AU.floor` |
| `SC.` | `SC.<first>_<second>`, e.g. `SC.house_floor_sunday_show` |
| single | `DF` (repeated defensive), `MA` (appeals), `HA` (hearing antagonism), `RC` (receptivity), `LD` (leadership role, with `claim` and `plain`) |
| `VI.` | `VI.observed`, `VI.role` |
| `PS.` | `PS.1`, `PS.2`, ... in ledger order |
| `LF.` | `LF.<turn_id>` for a turn, `LF.<turn_id>.<n>` for its nth finding |
| `BIO.` | `BIO.state`, `BIO.committees`, `BIO.prior_career`, `BIO.first_elected` (when added). `BIO.party` and `BIO.district` may exist in the packet but are never citable (section 4). |
| `PROC` | procedural advice that makes no claim about the member |

Items with a middling band are flagged `neutral: true` and are not citable, except as a stated contrast.

### 9.2 Which ids may support which fields

| Id family | archetype | bottom_line | do / dont | warning_sign | follow_up | personality | speaks | decides |
|---|---|---|---|---|---|---|---|---|
| `TR.*` (not `TR.N`) | yes | with a non-TR id | with a non-TR id | procedural form only, with `PROC` | no | yes | no | yes |
| `TR.N` | yes | no | no | no | no | yes | no | no |
| `QR.antagonism` | yes | yes | yes | yes | no | yes | yes | no |
| `QR.breadth`, `QR.diligence`, `QR.affect` | yes | no | no | no | no | yes | yes | no |
| `BH.coop` | yes | yes | yes | no | yes | yes | no | yes |
| `BH.diligence` | yes | yes | yes | no | yes | yes | no | yes |
| `BH.breadth` | yes | no | no | no | no | yes | no | yes, description only (B8) |
| `BH.dominance`, `BH.wpt`, `IX.*` | yes | yes | yes | yes | no | yes | yes | yes |
| `FC.*` | yes | no | yes | no | no | yes | yes | no |
| `AU.*`, `SC.*` | yes | no | with an `IX`, `BH`, or `DF` id | cue only, with an `IX`, `BH`, `DF`, or `QR.antagonism` id | no | no | yes | no |
| `DF` | yes | yes | yes | yes | yes | yes | yes | yes |
| `MA` (not mixed) | yes | yes | yes | yes (action part) | yes | no | no | yes |
| `MA` (mixed) | no | no | no | no | no | no | no | no |
| `HA` | yes | yes | yes | yes | no | yes | yes | yes |
| `VI.*` | yes | no | public-exposure advice only | no | no | yes | no | yes |
| `RC` | no | yes | yes | no | yes | no | no | yes |
| `PS.*` | yes | no | yes | no | yes | yes | yes | yes |
| `LD`, allowed `BIO.*` | no | no | no | no | no | yes | no | yes |
| `LF` qualified finding | yes | yes | yes | yes | yes | no | yes | yes |
| `LF` provisional finding | no | no | no | no | no | no | one sentence | no |
| `LF` finding with `era` PRE_CONGRESS_* or PRE_CURRENT_LEADERSHIP_ROLE | no | no | no | no | no | yes, worded as earlier in the career | yes, worded as earlier in the career | no |

Early-era long-form findings: A long-form finding whose `era` is PRE_CONGRESS_* or PRE_CURRENT_LEADERSHIP_ROLE (from the long-form diagnostics `source_era`) may support `speaks` and `personality` only, worded as earlier in the career ("earlier in his career..."); it never supports the archetype, a Do, a Don't, or the Warning sign, and it is not a cue-eligible item (section 14 checks 9 and 19). This row overrides the qualified row; a provisional early-era finding keeps the provisional limit as well.
| `PROC` | no | no | yes | yes | yes | no | no | no |

### 9.3 Binding rules

1. `evidence_refs` has one key per field: `archetype`, `bottom_line`, `do.1` to `do.4`, `dont.1` to `dont.3`, `warning_sign`, `follow_up`, `personality`, `speaks`, `decides`. Every key has a non-empty list.
2. Every cited id exists in the packet, is not neutral, and is allowed for that field by 9.2.
3. Every Do, Don't, Warning sign, and Follow-up cites at least one id that is not `TR.*`.
4. Every factual clause in a field is supported by one of that field's cited ids, at the right direction and strength.
5. `PROC` items make no claim about the member. At most two Do or Don't bullets may cite only `PROC` (alone or with `TR.*` ids) whenever any non-`PROC` id in the packet is allowed for Do or Don't (E10). For a rich packet, at most two `PROC` citations in the whole brief. `PROC` lines are written fresh for each member.
6. Gated words (section 11G) require their gate id to be present in the packet, whether or not the field cites it.

## 10. Thin evidence

Packet tier is computed in code: `thin` when there is no interaction block, fewer than 25 interview turns, and no general audio; `rich` when an interaction block exists and general audio has 10 or more clips; otherwise `standard`.

- Say less. Use the thin-tier lower word limits. A short, true brief beats a padded one.
- Never invent a habit to fill a section. If there is no speech evidence, `speaks` uses what exists: hearing conduct, language reads, public stories (each `PS` fact once per brief).
- Never mention missing evidence in prose: no "little is known", "limited record", "we could not find", "data are thin". The Basis line carries coverage.
- Prefer procedural advice (`PROC`) over guessing, within the cap in 9.3 rule 5, and keep it neutral and useful: state the ask in one sentence, name the next step, confirm the owner and the date. Never add appeal advice to procedural lines (section 5.7).
- The Warning sign uses `warning_form` from the packet (section 7), not the tier: a thin packet with `HA` uses the observed form.
- The archetype may still be a template animal if 2 slots match without a disqualifier, unless a registry entry matches (8.2 step 6).

## 11. Banned and gated language

Matching is case-insensitive, on whole words or exact phrases. Code rejects any hit.

### 11A. Private-state and clinical words (carried over from `BANNED_PRIVATE_STATE` plus new)

unflappable, even-keeled, even keel, level-headed, unruffled, imperturbable, emotionally stable, emotional stability, volatile, erratic, neurotic, neuroticism, anxious, anxiety, unstable, stable, stability, calm, calmer, calmly, calmness, composed, composure, steady, steadiness, reactive, reactivity, emotionally reactive, emotional reactivity, high-strung, high strung, even-tempered, even tempered, emotional resilience, low-strain, paranoid, manic, depressed, depression, bipolar, narcissist, narcissistic, traumatized, trauma, hysterical, disorder, pathological, obsessive, compulsive, insecure, fragile, unhinged, meltdown, mentally, psychological, diagnosis

Note: "calm" and "steady" are banned even though the template uses them once ("Stay calm and steady", "steady and guarded"). Say "keep your tone even", "patient", "unhurried", "hard to fluster".

### 11B. Unsupported inferences (carried over from `BANNED_UNSUPPORTED` plus new)

seeks the camera, chases the camera, camera-seeking, doing the work quietly, solo operator, travels in groups, trigger topic, media-practised, media-practiced, polished answers, vocal strain, verified-solo audio, rapid-fire pace, drawn to attention, loves the camera, spotlight, attention-seeking, showboat, grandstand, ego, vain, secretly, privately, deep down, at heart, deliberate, deliberately, your own side, your side, finish his sentence, finish her sentence, finish their sentence, finish his sentences, finish her sentences, finish their sentences

### 11C. Measurement and report jargon (carried over from `BANNED_JARGON` plus new)

the ledger, ledger, packet, z-score, z score, percentile, pressure_mult, raw_text, cross_setting, headline_interviews, affective content, issue breadth, volume dynamics, pitch variation, split-half, qualifying turns, expert panel, expert panels, experts read, experts see, rated, rating, score, scores, band, tracked, covered colleagues, measured, metric, metrics, the data, our data, data show, data shows, data suggest, dataset, the record shows, evidence shows, evidence suggests, in the evidence, construct, facet, baseline, jitter, f0, egemaps, voiced segments, words per turn, floor-defense, cosponsorship share, tie share, middling, well above most, well below most, above most, below most, among the highest in the chamber, among the lowest in the chamber, openness, conscientiousness, extraversion, extroversion, agreeableness, big five, personality trait, basis, confidence level

Notes: "above most" and "below most" are banned as exact phrases; write "more than most" or "less than most". "data" and "evidence" are fine as advice words ("bring district data", "go back to the evidence"); only the meta phrases above are banned.

### 11D. Hedges

seems, seem to, appears to, apparently, perhaps, possibly, arguably, presumably, it is possible, might be, may be, could be, likely, probably, to some extent, in some ways, sort of, kind of

### 11E. Missing-data talk

no data, limited data, little data, not enough, insufficient, unknown, unclear, not available, no record of, hard to say, cannot tell, difficult to assess, thin record, sparse

### 11F. Clichés and v2 stock phrases

navigate, landscape, leverage, delve, nuanced, multifaceted, tapestry, testament, robust, synergy, stakeholders, at the end of the day, it's worth noting, it is worth noting, in today's, game-changer, double-edged sword, force to be reckoned with, wears his heart on his sleeve, wears her heart on her sleeve, close to the vest, close to his chest, close to her chest, read the room, lead with outcomes, let the credit accrue, a discount, not immunity, a discount rather than a wall, best confirmed in person, neither warmth nor directness, two registers, the interesting split, pull apart, runs on presence, not just, more than just

### 11G. Gated words

| Words | Allowed only when |
|---|---|
| defend, defends, defending, defense, defensive, push back, pushback, pushes back, pushing back, pushes harder, holds the line, holding the line, digs in, digging in | `DF` is present in the packet |
| bipartisan, across the aisle, party lines, cross-party, cross party, other party, both parties, both sides, either party, cosponsor, co-sponsor, reaches across | `BH.coop` is present in the packet |
| sets him off, sets her off, sets them off, sore spot, hot-button, off-limits, sensitive topic, sensitive subject, sensitive area, pressure point, landmine, topic to avoid, subject to avoid, topic-specific, particular topic, specific subject | a `supported_topic_associations` entry exists for that topic (none exist as of 2026-10-20) |
| trigger, triggers, triggered, triggering | never |

### 11H. Form

- No digits anywhere in model output.
- No spelled-out numbers that describe the member or the evidence (counts, shares, durations, ranks, district ordinals). Time words in reader advice are fine ("the same day", "within a few days", "the first two minutes").
- No em dash (U+2014) or en dash (U+2013). Hyphens in compound words are fine.
- No section labels, no "Basis", no "Confidence", no markdown, no quotation marks around coined phrases.

### 11I. Party names and titles (R5, E7)

Republican, Republicans, Democrat, Democrats, Democratic, GOP, and "Independent" as a party label, in any field. Titles before a name: Rep., Sen., Senator, Representative, Congressman, Congresswoman. Use `LD.plain` for leadership roles (section 4).

## 12. Anti-repetition

Within one brief:

- No idea appears in more than one of Do, Don't, Warning sign, and Follow-up unless the second use adds a new action. No two Do bullets share an idea, and no Don't reverses a Do (E9).
- Sentence openers (R6), precisely: split each prose field into sentences; the opener is the first word, lower-cased. The member's pronoun family (he, his, him / she, her, hers / they, their, them) may open at most two consecutive sentences; a third in a row fails. Any other word that opens two consecutive sentences fails. The same rule applies to the bullets of `do` and of `dont`, read as a list. Paul's template does "He uses... He frames...", which is two and passes.
- Each `PS` fact once per brief; `speaks` does not restate `personality`.

Exemplars are shape only (R10, B6, S2; where they differ, the stricter limit governs):

- The writer never reads the gold exemplar of its own member's animal. The orchestrator hands each batch only the gold exemplars whose animals differ from that member's animal (a batch with mixed animals gets, per member, the exemplars of the other animals). The two inline examples in the writer prompt (Example A is a Bull, Example B a Tortoise) stay in the prompt as shape only, and the limits below apply to them as well.
- When the member's animal equals an exemplar's animal (gold: Bull Jeffries, Terrier Johnson, Blue Jay Warren, Beaver Fitzpatrick, Bear Waters, Tortoise Lucas; inline: Bull, Tortoise): at most one Do and at most one Don't may make the same move as that exemplar (so at most two of the seven bullets); the Warning sign, Follow-up, and `decides` must each make a different move from the exemplar's; the Warning sign cue rests on a different item than the exemplar's cue whenever another cue-eligible item exists; and at least two bullets rest on a top- or bottom-band item that the exemplar never cites.
- Start from this member's two or three strongest items that the exemplar does not have.


Across the 533 briefs (code enforces, the writer helps):

- The packet carries `avoid_phrases`: up to 40 phrases that already appear in too many finished briefs, plus the seed list below. Any hit fails.
- Cross-brief repetition (R7), precisely: a six-word sequence (lower-cased words, punctuation dropped) fails a brief when it appears in more than 2 percent of finished briefs AND in at least 3 finished briefs, counting the brief under test. Sequences that fail are added to `avoid_phrases` for later batches. Proper-noun runs (two or more capitalized words, such as a named event) are exempt.
- Any brief whose `personality` starts with the same four words as more than 2 percent of finished briefs and at least 3 briefs is sent back (same thresholds as R7).
- The packet carries `coined_animals`: the shared registry (8.4). Reuse before coining.
- Do not copy wording from the packet: five or more consecutive words from any packet text fail, including the `plain` phrases, appeal frames, scope notes, finding text, and sensibility claims. Exempt: the `LD` item's `plain` title, which section 4 requires verbatim (validator: leave `LD` out of the plain-phrase n-grams). A run that carries a proper-noun phrase of two or more capitalized words, such as a named event, is exempt. Paraphrase in plain words (E6).
- Do not copy the writer prompt examples or any gold exemplar: six or more consecutive words shared with either fail (E6).

Seed `avoid_phrases` before the full run (R8, E5, pilot review copies): "the meeting has become a debate", "stop adding arguments", "make your best case before", "make your strongest point before", "the one decision you need", "keep each point short and tied", "argument matters more than pressure", "a visible role he can lead", "a visible role she can lead", "written recap within a day", "fewer specifics than you might want", "for agreement", "as a no", "pad the case with", "pad your ask with", "across the issues it touches", "whether he has the details he needs", "whether she has the details she needs", "fits how he legislates", "fits how she legislates", "stays on the issues she already names", "stays on the topics he already names", "is his home state", "is her home state", "answers run before you respond", "a weaker suit for", "reach him before his view", "reach her before her view", "hearing manner as", "lands whole", "crowd his sentences", "crowd her sentences", "gets to him more than", "gets to her more than" (B10).

The measures that appear in almost every brief are where repetition creeps in. Rotate among plain variants like these, and invent others at the same strength (section 5.1):

| Measure | Variants |
|---|---|
| `BH.diligence` top band | few match how reliably he votes and sees his bills through; one of the most reliable members at voting and seeing bills through; has one of the strongest follow-through records in the House |
| `BH.diligence` above most | shows up for votes and moves his bills more reliably than most |
| `BH.diligence` below | his follow-through on votes and bills is weaker than most; his attendance and follow-through on bills trail most members; careful follow-through is not where he shines |
| `BH.coop` top band | works with the other party as much as almost anyone; few members do more of their work across party lines |
| `BH.coop` above most | builds with both parties more than most |
| `BH.coop` low | works with the other party less than most; keeps his legislative work mostly within his own party |
| `HA` top band | in hearings he is one of the most combative members |
| `HA` above most | hearings bring out a more combative side; he questions hearing witnesses harder than most |
| `HA` low | in hearings his tone stays mild; (top band only) few members are less combative in hearings |
| `RC` more | outside pressure moves him more than most; pressure works on him more than on most members; persistence pays off with him |
| `RC` less | outside pressure moves him less than most; pressure counts for less with him than with most members; the case has to carry the weight |
| `BH.dominance` high | holds the floor once he has it; takes charge of a conversation; (top band only) few members hold the floor as firmly |

## 13. Packet contract (what the writer receives)

```
{
  "member": {
    "display_name": "...", "surname": "...", "pronoun": "he|she|they",
    "chamber": "House|Senate",
    "leadership": {"title": "...", "plain": "<party-free LD.plain>", "since_year_text": "...", "era_rule": "..."} | null,
    "bio": {"state": ..., "committees": [...], "prior_career": ..., "first_elected_text": ...} | null
  },
  "evidence_tier": "rich|standard|thin",
  "warning_form": "observed|procedural",
  "evidence": [
    {"id": "BH.dominance", "label": "holds the floor in conversations",
     "band": "well above most", "direction": "high", "strength": "clear",
     "neutral": false, "plain": "takes charge of a conversation and keeps it, much more than most",
     "scope": "...", "may_support": ["archetype", "bottom_line", "do", "dont", "warning_sign", "personality", "speaks", "decides"]},
    {"id": "LD", "claim": "House Democratic Leader", "plain": "his party's House leader", "may_support": ["personality", "decides"]},
    ...
  ],
  "gates": {"DF": true|false, "BH.coop": true|false, "topic_associations": []},
  "appeals": {"dims": ["outcome"|"community"|"technical"|"partnership"|"mixed"], "plain": "...",
              "agency_percentile": 0.0, "communion_percentile": 0.0,
              "lead_ok": {"results": true|false, "people": true|false}},
  "public_sensibilities": [{"id": "PS.1", "category": "...", "claim": "...", "date_text": "..."}],
  "longform": [{"id": "LF.<turn_id>.1", "status": "qualified|provisional", "era": "PRE_CONGRESS_*|PRE_CURRENT_LEADERSHIP_ROLE|CURRENT_ROLE_CONDITIONED|NOT_CURRENT_FORMAL_LEADERSHIP", "finding": "...", "meeting_implication": "...", "source_text": "<channel>, <year text>"}],
  "archetype_candidates": [{"animal": "Bull", "matched": 6, "slots": ["..."], "contradicted": [], "disqualified": false, "outer": 2}],
  "suggested_animal": "Bull" | null,
  "coin_recommended": {"value": true|false, "reason": "...", "binding_unexplained": {"cooperative": ["BH.coop", "TR.A"]},
                       "advisory_unexplained": {"wide": ["BH.breadth", "QR.breadth", "TR.O"]},
                       "registry_match": {"animal": "Beaver", "descriptor": "..."} | null},
  "coined_animals": [{"animal": "Beaver", "descriptor": "...", "image": "...", "signature": [["BH.coop", "hi"], ["TR.A", "hi"]]}],
  "avoid_phrases": ["..."]
}
```

- `appeals.plain` for mixed is exactly "no single frame leads; give no framing advice". `lead_ok.results` is false when agency is under 0.42; `lead_ok.people` is false when communion is under 0.42; both are false for mixed.
- `MA` has `may_support: []` when the appeal is mixed. `BIO.party` and `BIO.district`, if emitted, have `may_support: []`.
- `plain` is the builder's pre-translated phrase at the correct strength (section 5). Writers start from its meaning and write their own words: five consecutive words copied from it fail (section 12).

## 14. Mechanical checks the code runs (fail closed)

1. Output parses as JSON with exactly the schema keys; `do` has 4 strings, `dont` has 3.
2. Word counts per section 3, tier-aware.
3. Bullets: no trailing period; first word not in {Do, Don't, Never, Avoid, Not, The, A, An, He, She, They, His, Her, Their, It}.
4. No digits; no U+2014 or U+2013.
5. Section 11A to 11F and 11I lists: whole-word or phrase hits fail.
6. Section 11G gates: gated word present without its gate fails.
7. Surname appears exactly once in `personality` and nowhere else; full display name appears nowhere.
8. Pronoun consistency: no he/him/his in a she brief and vice versa; they briefs use plural verbs after "they".
9. Every `evidence_refs` key present and non-empty; every id exists in the packet, is non-neutral, and is allowed by `may_support`; every Do, Don't, Warning sign, and Follow-up cites a non-`TR` id; `TR.N` cited only under `personality` or `archetype`; `PROC` rules (9.3 rule 5).
10. Longest sentence 28 words or fewer; Flesch-Kincaid grade over all prose 10 or lower (flag above 8.5).
11. Copying: five or more consecutive words shared with any packet text (including `plain`) fail, except a run carrying a proper-noun phrase of two or more capitalized words; six or more consecutive words shared with the writer prompt examples or any gold exemplar fail; `avoid_phrases` hits fail.
12. Archetype: the animal equals `coin_recommended.registry_match.animal` when that is set; otherwise it equals `suggested_animal` when `coin_recommended.value` is false; otherwise it is a registry animal whose signature holds, or a new name not in the registry, and not a template animal. A new name already in the registry with a different signature fails.
13. AU companion: any `AU.*` or `SC.*` id cited for a Do or Don't requires an `IX.*`, `BH.*`, or `DF` id in the same list (E6).
14. Party and title: 11I words fail in any field (E6, R5). A state name (the fifty state names) outside `personality`, or more than once in it, fails.
15. Strength words (E6, E1): a top-band phrase (5.1 list) fails when no id cited for that field sits at level +3 or -3. A low-strength word (5.1 list; "a little" excluded) fails when no id cited for that field sits at level -2 or below.
16. Openers (R6): section 12 rule, per prose field and per bullet list.
17. Appeals (R3): when `appeals.dims` is mixed, `MA` cited in any field fails. Outside `dont`, the pattern "lead with", "open with", "start with", "put first", or "build the case on", followed within eight words by a results word (results, outcome, outcomes, facts, data, numbers, wins) fails when `lead_ok.results` is false, and followed by a people word (people, community, communities, constituent, constituents, stories, human, families, relationship, relationships) fails when `lead_ok.people` is false.
18. Leaders (R4): when `member.leadership` is not null, `animal` must not be Lone Wolf and `descriptor` must not contain "independent" or "lone".
19. Warning form: when `warning_form` is observed, `warning_sign` must contain a usual-pattern marker (usually, usual, normally, normal, by default, as a rule) and must cite at least one cue-eligible id (section 7). When procedural, it must cite `PROC` and must not contain the word "means". Cue words (S10, B7): fail when the warning_sign contains short, shorter, clipped, terse, or brief and `IX.burst` is above most or higher; fail when it contains long, longer, or stretch (any form) and `IX.wpt_p90` is above most or higher; fail when it contains any of those words and `IX.gini` is above most or higher; fail when it cites only answer-length items (`BH.wpt`, `IX.wpt_mean`, `IX.wpt_p90`, `IX.gini`) while the packet has a cue-eligible non-length item at level +3 or -3.
20. Cross-brief repetition (R7): section 12 thresholds, run after each batch over all finished briefs.
21. Within-brief Do duplication (E9) is a reviewer check, not mechanical; the validator flags any two Do bullets that share four or more content words (stop words removed) for the reviewer.
22. Exemplar tracking (S2, B6): for each gold exemplar and each inline writer-prompt example, count the brief's Do and Don't bullets that share three or more content words (stop words removed) with any bullet of that exemplar, plus 1 if the warning_sign shares five or more content words with that exemplar's warning_sign. A total of 3 or more flags the brief for the reviewer, who rules on `exemplar_tracking` (review prompt check 9). When the member's animal equals that exemplar's animal, a total of 2 or more flags it.
24. Conversation forecasts (S7, B8): flag for the reviewer any sentence that contains a `QR.*` or `HA` fact word (words, language, tone, sharp, sharper, gentle, mild, emotional, charge, hearing, hearings) together with "so expect", "so the exchange", "so the talk", "so the conversation", "so the meeting", or "expect a/an" followed within six words by exchange, talk, conversation, or meeting. (Check 23, the biography cap, is the executor's S12.)

## 15. Deliberate deviations from the template sample text

| Template sample | v3 rule | Why |
|---|---|---|
| "Stay calm and steady", "steady and guarded" | "calm" and "steady" banned | carried-over private-state ban; models overuse both as a stand-in for low Neuroticism |
| "highly extroverted", "low conscientiousness", "high neuroticism", "agreeableness is low" | plain words only; trait nouns banned | Paul's plain-language ruling; "neuroticism" reads clinical |
| "Her anxiety is the key to working with her" | "anxiety" banned; Neuroticism plain words in Personality only | clinical word; the Emotional Stability read has no clean validation and was gender-confounded |
| "Rep. John Doe — The Bull" | "John Doe — The Bull", rendered by code from the display name and the animal | model must not emit dashes or titles; code drops the title |
| "Basis: [N] floor speeches, [N] hearing transcripts, [date range]. Confidence: [level]." | code only | Paul's ruling |
| "drawn to attention", "Offer a visible role: a bill to lead, an event, a press moment" | attention-seeking inferences banned; a visible role may be offered only when `MA` supports partnership | visibility is exposure, not motive |
| "tie it to the party agenda", "defying party leadership" | not supported by the current packet; party names banned (R5) | no party-unity or leadership-alignment measure exists yet |
| "Make her feel safe: show who else supports it and how it helps her community" (a frame for every member) | appeal advice only when the appeal is not mixed and the axis is not below most (R3) | 89 of 533 members are mixed, 77 of them with one axis at or below 0.40 |

## 16. Run process (S14, B2, R10, R12)

1. Freeze the writer prompt, review prompt, style guide, and exemplars before a run. One pilot runs under one writer-prompt sha256; the manifest, the run log, and every review record that hash (S14, B2).
2. Per batch, hand each member only the gold exemplars whose animal differs from that member's animal (R10).
3. Haiku writes. Code runs the section 14 checks.
4. Opus runs the semantic review (review prompt, round mode) on every member and returns field-level repairs (`{field, quote, kind, repair}`) for each failing member (R12).
5. Haiku rewrites only the failing members, in repair mode (writer prompt section 13), applying those repairs. Code checks again; Opus reviews again. At most two repair rounds; a member still failing after round two is held out and listed.
6. Two independent final reviewers (Santa gate) review every member; a brief passes only when both pass it.
7. After each batch assembles: restage later packets with the updated registry and `avoid_phrases` (8.4).
