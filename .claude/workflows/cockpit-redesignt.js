export const meta = {
  name: 'cockpit-redesign',
  description: 'Modern dark redesign of the harness cockpit with crow branding, validated palette, and screenshot verification',
  phases: [
    { title: 'Design', detail: 'design system + crow branding in parallel' },
    { title: 'Review', detail: 'coherence critic gates the specs' },
    { title: 'Implement', detail: 'apply redesign to viewer.html' },
    { title: 'Verify', detail: 'seed demo run, Chromium screenshots, console errors' },
  ],
}

const ROOT = '/Users/chrismagnacca/Projects/probable-caws'
const SKILL = '/private/tmp/claude-501/bundled-skills/2.1.233/1a43e8e469ca203e681250215cf57171/dataviz'
const SCRATCH = '/private/tmp/claude-501/-Users-chrismagnacca-Projects-probable-caws/6c16a62a-4bfc-4345-8a72-6a1ed6f25ad2/scratchpad'

const CONTEXT = `CONTEXT: ${ROOT} is "probable-caws", a harness for long-running agentic coding. Its live observability cockpit is ONE self-contained page, ${ROOT}/harness/static/viewer.html (~1143 lines, inline CSS/JS, no CDN, no build step), served read-only by harness/serve.py. It renders, from an SSE event stream: a sticky Vitals strip (run state, current feature/role/attempt, segmented per-feature progress bar, cumulative cost + SVG sparkline, staleness cell), a Run Track (inline-SVG swimlane: lanes planner/generator/checks/evaluator/git, session blocks, verdict/checkpoint markers, feature bands, now-cursor), a Right Now panel (live transcript tail, liveness dot), a Feature Board rail (status chips, attempt dots, per-criterion micro-squares, per-feature cost), a Feature Forensics slide-over, and a raw Event Tail drawer. The data contract is ${ROOT}/docs/CONTRACTS.md (sections 5 and 10 describe events and the page). The page is dark-only by design and already uses CSS custom properties in :root.

THE ASK (from the project owner): a MODERN redesign with polished dark-mode theming, and a crow worked into the design — "probable caws" is a play on the crow's caw. Tasteful and information-dense: this is a cockpit a person stares at during 8-hour runs, not a marketing page.`

const DESIGN_SCHEMA = {
  type: 'object',
  required: ['summary', 'tokens_css', 'status_palette', 'swimlane_color_strategy', 'typography', 'component_specs', 'validator_report'],
  properties: {
    summary: { type: 'string' },
    tokens_css: { type: 'string', description: 'The complete new :root{} custom-property block, ready to paste' },
    status_palette: { type: 'array', items: { type: 'object', required: ['state', 'hex', 'usage'], properties: { state: { type: 'string' }, hex: { type: 'string' }, usage: { type: 'string' } } } },
    swimlane_color_strategy: { type: 'string', description: 'How Run Track blocks/bands are colored, honoring the no-cycled-categorical rule' },
    typography: { type: 'string' },
    component_specs: { type: 'array', items: { type: 'object', required: ['component', 'spec'], properties: { component: { type: 'string' }, spec: { type: 'string' } } } },
    validator_report: { type: 'string', description: 'Pasted output of the palette validator runs proving PASS' },
    notes: { type: 'string' }
  }
}

const CROW_SCHEMA = {
  type: 'object',
  required: ['summary', 'marks', 'chosen', 'favicon_data_uri', 'wordmark_spec', 'microcopy'],
  properties: {
    summary: { type: 'string' },
    marks: { type: 'array', minItems: 2, maxItems: 3, items: { type: 'object', required: ['name', 'svg', 'rationale'], properties: { name: { type: 'string' }, svg: { type: 'string', description: 'Complete inline <svg> markup, currentColor fill' }, rationale: { type: 'string' } } } },
    chosen: { type: 'string', description: 'Name of the recommended mark and why' },
    favicon_data_uri: { type: 'string', description: 'Complete <link rel="icon" href="data:image/svg+xml,..."> line' },
    wordmark_spec: { type: 'string', description: 'Header lockup: mark + "probable caws" treatment' },
    microcopy: { type: 'array', items: { type: 'object', required: ['location', 'text'], properties: { location: { type: 'string' }, text: { type: 'string' } } } }
  }
}

const CRIT_SCHEMA = {
  type: 'object',
  required: ['verdict_summary', 'amendments'],
  properties: {
    verdict_summary: { type: 'string' },
    amendments: { type: 'array', items: { type: 'object', required: ['target', 'severity', 'issue', 'fix'], properties: { target: { type: 'string', enum: ['system', 'crow', 'both'] }, severity: { type: 'string', enum: ['high', 'medium', 'low'] }, issue: { type: 'string' }, fix: { type: 'string' } } } }
  }
}

const IMPL_SCHEMA = {
  type: 'object',
  required: ['summary', 'changes', 'tests_output', 'js_syntax_check', 'line_count', 'deviations'],
  properties: {
    summary: { type: 'string' },
    changes: { type: 'array', items: { type: 'string' } },
    tests_output: { type: 'string' },
    js_syntax_check: { type: 'string' },
    line_count: { type: 'integer' },
    deviations: { type: 'array', items: { type: 'string' } }
  }
}

const VERIFY_SCHEMA = {
  type: 'object',
  required: ['summary', 'server_ok', 'console_errors', 'screenshots', 'issues'],
  properties: {
    summary: { type: 'string' },
    server_ok: { type: 'boolean' },
    console_errors: { type: 'array', items: { type: 'string' } },
    screenshots: { type: 'array', items: { type: 'string' }, description: 'Absolute PNG paths' },
    issues: { type: 'array', items: { type: 'string' }, description: 'Visual/functional problems noticed while driving the page' }
  }
}

phase('Design')
const designs = await parallel([
  () => agent(`${CONTEXT}

You are the DESIGN-SYSTEM DESIGNER. Produce a complete modern dark design system for this cockpit as a spec an implementer can apply mechanically. Read the current ${ROOT}/harness/static/viewer.html first (its :root tokens and component classes are your anchor points — design NEW values for the SAME structural roles, adding tokens where needed), and read these method files: ${SKILL}/references/palette.md, ${SKILL}/references/marks-and-anatomy.md, ${SKILL}/references/anti-patterns.md.

Hard rules from the dataviz method (non-negotiable):
- Dark theme is designed as its own palette against the dark surface, not a flip. Single dark theme, tokenized (light could be added later).
- VALIDATE with the runnable validator, do not eyeball: node ${SKILL}/scripts/validate_palette.js "<hex,hex,...>" --mode dark (run it via Bash; iterate hexes until PASS; paste the final output into validator_report). Validate BOTH the status palette (done/failed/blocked/building/todo as used side-by-side in the progress segbar and feature board) and any categorical set you keep.
- Categorical hues in fixed order, never cycled — the current page hashes a hue per feature id (up to 25 features), which violates this. Redesign the Run Track coloring: recommended direction is status/role-driven color with identity carried by band labels and block text, but you decide and justify in swimlane_color_strategy.
- Status colors are reserved for state and ship with more than color (icon/label/shape). Text wears text tokens, never series colors. Thin marks; 4px rounded data-ends; 2px lines; recessive grids/borders.
- Contrast: body text vs surfaces must pass (the validator reports it); muted ink is for de-emphasis, not for load-bearing values.

Modernization direction: contemporary dashboard idiom — layered surfaces with subtle elevation, tighter typographic hierarchy (a display face is unnecessary; system stack + ui-monospace for data is right), refined chips/badges, calm borders, restrained accent usage, focus/hover states, consistent 4/8px spacing rhythm, subtle motion only where it carries meaning (pulsing building state, growing open block). Specify per component: vitals strip (treat as stat tiles per marks-and-anatomy hero-number guidance), segbar, Run Track (lanes, blocks, markers, bands, cursor, zoom buttons), Right Now (transcript styling, tool-use chips, liveness), Feature Board rows + criterion micro-grid, Forensics slide-over, Event drawer, tooltips, scrollbars, empty state. Leave room in the header spec for a crow mark + wordmark (a sibling agent designs it).`,
    { label: 'design:system', phase: 'Design', schema: DESIGN_SCHEMA, effort: 'high' }),

  () => agent(`${CONTEXT}

You are the CROW BRAND DESIGNER. "probable caws" puns on the crow's caw — work a crow into the cockpit's identity, tastefully. Deliverables:
1. Two or three candidate crow marks as complete inline <svg> markup: fill="currentColor", clean single-path (or very few paths) silhouette, viewBox tight around the artwork, legible at 16px and handsome at 24-48px. Aim for a smart, geometric corvid — perched profile or head-with-beak; think modern logomark, not clip-art or emoji. Craft the path coordinates carefully (you cannot render — so keep geometry simple enough to be confident: bold silhouette, no fine interior detail) and sanity-check every coordinate lies inside the viewBox.
2. A favicon: the strongest mark as a data:image/svg+xml URI in a complete <link rel="icon" ...> tag (URL-encode properly; give it an explicit dark-appropriate fill since favicons don't inherit currentColor).
3. wordmark_spec: header lockup — mark + "probable caws" (lowercase suits the repo name), with size/weight/spacing/color guidance and how the run-state pill sits beside it.
4. microcopy: at most 4 restrained caw/corvid touches (e.g. empty state, run-complete state, page <title>). No pun overload — one smile per screen, never at the cost of clarity. The staleness cell and error states stay strictly literal.
Choose your best mark in 'chosen' with rationale.`,
    { label: 'design:crow', phase: 'Design', schema: CROW_SCHEMA, effort: 'high' }),
])

const [system, crow] = designs
if (!system || !crow) { throw new Error('a design agent failed: system=' + !!system + ' crow=' + !!crow) }
log('both designs in — critic gate next')

phase('Review')
const critique = await agent(`${CONTEXT}

You are the COHERENCE CRITIC gating two design specs before implementation. Read ${ROOT}/harness/static/viewer.html (what exists), ${SKILL}/references/anti-patterns.md and ${SKILL}/references/color-formula.md (the rules). Then audit the two specs below for: dataviz non-negotiable violations (cycled categorical, status-color reuse, text in series colors, contrast, missing validator PASS evidence — if the validator_report does not show a clean PASS for the status set on the dark surface, that is a HIGH amendment requiring re-run hexes you supply yourself by running node ${SKILL}/scripts/validate_palette.js), internal conflicts between the two specs (crow lockup vs header spec, accent colors fighting status colors), implementability against the real DOM/JS (ids, SVG text labels on blocks, the segbar, attempt dots), SVG-mark risks (coordinates outside viewBox, overcomplex paths, favicon URI encoding errors — decode and check it), and dark-cockpit legibility (9-10px SVG text needs adequate ink contrast). Emit concrete amendments the implementer applies verbatim; severity high = must fix.

DESIGN SYSTEM SPEC:\n${JSON.stringify(system, null, 1)}\n\nCROW SPEC:\n${JSON.stringify(crow, null, 1)}`,
  { label: 'critic:coherence', phase: 'Review', schema: CRIT_SCHEMA, effort: 'high' })

log('critic: ' + (critique ? critique.amendments.length + ' amendments' : 'FAILED'))

phase('Implement')
const impl = await agent(`${CONTEXT}

You are the IMPLEMENTER. Apply the design system + crow branding below to ${ROOT}/harness/static/viewer.html, honoring every critic amendment (they override the specs where they conflict). Read the whole current file first.

HARD CONSTRAINTS:
- The page must remain ONE self-contained file: inline CSS/JS, no CDN, no external requests, no build step.
- Preserve ALL functionality: the SSE client, reducer, offsets, every panel's behavior, ids/hooks used by JS, the empty state, forensics, lightbox, event drawer, zoom buttons, visibilitychange handling, DOM caps. Restructure markup/CSS freely; change JS only where the design demands it (e.g. block/segbar color logic per the swimlane_color_strategy, status classes, header lockup, title/favicon, microcopy strings).
- Add the favicon link and the chosen crow mark per the wordmark_spec. Include ONLY the chosen mark in the page.
- Keep it tight: target <= ~1500 lines. Delete replaced styles fully — no dead CSS.
- After editing, verify: (1) extract the inline <script> body to a temp file and run node --check on it (syntax gate — browser globals are fine, it only parses); (2) cd ${ROOT} && python3 -m unittest tests.test_serve -q must stay green; (3) grep that no removed CSS custom property is still referenced (every var(--x) resolves to a defined token).
- Do not touch any file other than harness/static/viewer.html.

DESIGN SYSTEM SPEC:\n${JSON.stringify(system, null, 1)}\n\nCROW SPEC:\n${JSON.stringify(crow, null, 1)}\n\nCRITIC AMENDMENTS (authoritative):\n${JSON.stringify(critique ? critique.amendments : [], null, 1)}`,
  { label: 'implement:viewer', phase: 'Implement', schema: IMPL_SCHEMA, effort: 'high' })

if (!impl) { throw new Error('implementation agent failed') }
log('implemented: ' + impl.line_count + ' lines — verification next')

phase('Verify')
const verify = await agent(`${CONTEXT}

You are the VERIFIER. The redesigned page is now in ${ROOT}/harness/static/viewer.html. Prove it works and capture screenshots for human review.

1. Playwright: if ${ROOT}/eval/node_modules/playwright is missing, run: cd ${ROOT}/eval && npm install && npx playwright install chromium (this is the project's intended tooling; it needs network — if installs fail, report and stop).
2. Seed a demo run at ${SCRATCH}/demo-root/ mimicking the real layout (read ${ROOT}/docs/CONTRACTS.md section 5 for exact schemas): logs/events.jsonl telling a believable story — run_start (data: {config: <echo of ${ROOT}/config.json>, claude_version: "2.1.233", auth_mode: "subscription", prompt: "A habit tracker with charts"}), a planner session, then features F001-F008 moving through feature_selected/session_start/session_end/precheck/app_boot/eval_verdict/feature_done cycles: 4 done, 1 failed mid-retry (2 attempts with eval_verdict fail), 1 blocked (escalation), 1 building NOW (session_start with data.session_dir matching a real dir you create under demo-root/logs/sessions/ containing prompt.md and a transcript.jsonl with ~15 realistic claude stream-json lines), 1 todo; plus one git_rollback and one budget_warn. logs/ledger.jsonl rows consistent with those sessions (rising cumulative_cost_usd). state/features.json with all 8 features, criteria with mixed last_verdicts, feedback on the failed one. state/verdicts/ + state/feedback/ for the failed feature. Timestamps: spread over the last 90 minutes ending "now" so the staleness cell reads fresh and the Run Track spans real time.
3. Serve: cd ${ROOT} && python3 -m harness serve --root ${SCRATCH}/demo-root --port 8791 (background; kill when done).
4. Drive with a playwright script (node, from ${ROOT}/eval): capture page console errors and failed requests (report ALL; empty list expected); wait for the app to render (vitals visible); screenshot to ${SCRATCH}/shots/: 01-full.png (full page 1600x1000), 02-vitals.png (header+vitals closeup), 03-runtrack.png, 04-featureboard.png, 05-forensics.png (click the failed feature's row first, wait for slide-over), 06-eventdrawer.png (open the drawer first). Also build ${SCRATCH}/shots/crow-sheet.html yourself rendering the page's crow mark (extract the inline svg from viewer.html) at 16/24/48/96px on the page's background color, and screenshot it as 07-crow.png.
5. Report server_ok, every console error verbatim, all screenshot paths, and any visual/functional issues you noticed while driving (overlaps, invisible text, broken layout, missing data).
Do not modify any file under ${ROOT} except eval/node_modules via npm.`,
  { label: 'verify:screens', phase: 'Verify', schema: VERIFY_SCHEMA, effort: 'high' })

return { system_summary: system.summary, swimlane: system.swimlane_color_strategy, crow_chosen: crow.chosen, microcopy: crow.microcopy, amendments: critique ? critique.amendments : [], impl, verify }