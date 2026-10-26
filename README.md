# The Voyager

*Exploring the frontier, building what's next!* · https://thevoyager.dev

An Astro site on Cloudflare Pages. Cloudflare's Git integration builds every push
(`npm run build`, output `dist`): `main` goes live, and other branches get a preview URL.

## Sections

| Section | Content | Files |
|---|---|---|
| Today (`/`) | Today's brief + deep dive, this week's strip, newsletter | `src/pages/index.astro` |
| ☕ Daily Brief (`/brief/`) | Routine 1, curated with AI | `src/content/brief/YYYY-MM-DD.md` |
| 🔭 Deep Dives (`/deep-dives/<track>/`) | Routine 2, curated with AI | `src/content/dives/<track>/YYYY-MM-DD.md` |
| ✍️ Writing (`/writing/`) | Your own posts (`draft: true` stays unpublished) | `src/content/writing/*.md` |
| 🛰️ Projects (`/projects/`) | Finished builds | `src/content/projects/*.md` |

Frontmatter schemas live in `src/content.config.ts` (fixes after publishing go in `corrections:`, shown on the post and on `/corrections/`); tracks, links and site text in `src/consts.ts`.

Posts with `curated_with_ai: true` get the "Curated with AI" badge, a
`noindex, follow` robots tag, and are left out of the sitemap. They never get the
newsletter box; Writing posts do.

Diagrams: use a ```` ```mermaid ```` code block. The Mermaid script loads only on pages that have one.

RSS: `/brief/rss.xml`, `/deep-dives/rss.xml`, `/writing/rss.xml`. Search: `/search/` (Pagefind, built after `astro build`).

## Commands

| Command | Action |
|---|---|
| `npm install` | Install dependencies |
| `npm run dev` | Dev server at `localhost:4321` (shows drafts; search needs a build) |
| `npm run build` | Build to `dist/` and index it for search |
| `npm run preview` | Preview the build locally |

`config/voice.md` is the writing voice both routines follow.

## Routine data

| Path | Purpose |
|---|---|
| `feeds/sources.yml`, `people.yml`, `youtube.yml` | What the Daily Brief routine reads each morning |
| `scripts/fetch_feeds.py` | Fetches all feeds in parallel → `candidates.json` (`pip install -r scripts/requirements.txt`, then `python3 scripts/fetch_feeds.py --since 36h --out /tmp/candidates.json`) |
| `data/brief-history.json` | Stories already covered, so the brief never repeats one |
| `data/newcomers-seen.json` | First day each new model, repo, tool or release was listed (for 🆕 New on the radar) |
| `data/concept-history.json` | Daily count of sites mentioning each architecture concept (for 📈 rising topics; concepts listed in `feeds/priorities.yml`) |
| `data/claims/DATE.json` | The independent verifier's claims ledger for each brief (from 2026-10-27): every main-item claim with its source quote and verdict. `check_brief.py` enforces verification, freshness, the vendor cap, 14-day repeats and the advisory fields from it |
| `security_watch` (in candidates.json) | Security incidents and advisories in the window, whatever their score, plus OSV advisories for the packages listed under `osv:` in `feeds/sources.yml`. The security scout reviews it every run |
| `data/deadlines.json` | Verified model retirements and compliance dates (for the ⏰ calendar, refreshed on Mondays) |
| `config/brief-playbook.md` | The Daily Brief routine's full instructions (the scheduled task just points here) |
| `scripts/check_brief.py` | Format and rule check for a brief: six main items, ⚡ quick hits, ⏰ on Mondays, 📅 on Sundays |
| `config/dives-playbook.md` | The Deep Dives routine's full instructions, including the fixed 14-section outline for Wednesday system posts |
| `scripts/check_dive.py` | Format and rule check for a Deep Dive: front matter, takeaways, visuals, widget JSON; for system posts every required section, both diagrams, the components table, ADRs, cost calculator and minimum length |
