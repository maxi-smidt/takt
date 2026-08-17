# TAKT UI

TAKT UI is the base component system shared by TAKT's two React apps: the
offline Pi timer and the Fleet Registry admin portal. It is sharp-edged and
high-contrast — a dark ground, no drop shadows except a modal's, uppercase
tracked labels for chrome, and monospace reserved for technical values
(run times, revisions, protocol numbers). Nothing decorative: every surface
exists to be read quickly on a Pi touchscreen or scanned across a fleet of
devices.

## Two surfaces, one contract

This is not a single-brand system with one accent — it is a **contract**
(`webui/src/shared/ui/tokens.css`, the `--ui-*` custom properties) that two
different surfaces map their own palette onto:

| | Fleet Registry portal | Pi timer app |
|---|---|---|
| Ground | near-black green (`#08100f`) | near-black blue (`#080e13`) |
| Accent | `#5ceaad` | `#2ba8e0` |
| Radius | `0px` — fully sharp | `2px` — barely rounded |
| Control height | `46px` | `44px` (touch target) |

Every page in this project is rendered with the **Fleet Registry** mapping
(`theme-fleet.css`, linked after `styles.css` on every page) because that's
the surface with the richer set of screens today. The Pi timer maps the
identical component classes onto its own blue-black palette instead — same
markup, same `--ui-*` names, different values. `theme.json` records both.

## How to use this

- Components read `--ui-*` custom properties exclusively — never
  `--blue`/`--green`/`--panel`/... directly, and never a raw hex. To adopt
  this system on a new surface, write one small adapter block mapping your
  palette onto the contract (see `webui/src/fleet/styles.css` or
  `webui/src/styles.css`, both append theirs at the very end of the file).
- Build with the `.takt-*` classes shown on the component pages rather than
  inventing parallel ones — every class is namespaced `takt-` specifically
  so it can never collide with either app's existing hand-rolled CSS.
- `styles.css` in this project is **generated** — run
  `node scripts/build_design_bundle.mjs` from the repo root after any change
  to `webui/src/shared/ui/tokens.css` or `ui.css`, then re-upload. It is a
  straight concatenation of those two real files, so this hosted system can
  never drift from what actually ships.
- The real, typed React components are TypeScript, not the plain HTML this
  project shows — see `webui/src/shared/ui/*.tsx`. `Dialog`, `Select` and
  `Checkbox` are built on Radix UI Primitives for accessibility (focus trap,
  keyboard, ARIA); everything you see here is the styling layer on top.

## Do

- Take every color, radius and type value from the `--ui-*` variables.
- Keep interactive elements' `:focus-visible` on the accent
  (`outline: 2px solid var(--ui-accent)`), never the browser default.
- Prefer the existing tone scale (`neutral`/`accent`/`success`/`warning`/`danger`)
  over inventing a new one.

## Don't

- Don't reference `--blue`, `--green`, `--panel`, or any app-specific
  variable from inside `webui/src/shared/ui` — that breaks the surface that
  didn't define it.
- Don't add border-radius, drop shadows, or softness beyond what's already
  here — the sharpness is deliberate, not an oversight.
- Don't hand-edit `styles.css` in this project — edit the source files and
  regenerate.

## Files

- `styles.css` — generated token + component layer. Link it from every page.
- `theme-fleet.css` — the real Fleet Registry palette mapping, hand-authored,
  linked after `styles.css` on every page in this project.
- `theme.json` — the contract and both surfaces' real values, machine-readable.
- `thumbnail.html` — the project cover.
- `foundations/color.html` — the nine `--ui-*` color roles.
- `foundations/type.html` — the type scale, monospace and tracking.
- `components/buttons.html` — `.takt-btn` variants, sizes and states.
- `components/forms.html` — text field, select trigger, checkbox.
- `components/dialog.html` — the modal shell (static; the real component is
  a Radix Dialog).
- `components/feedback.html` — badges and callouts.
