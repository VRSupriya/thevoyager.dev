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

Frontmatter schemas live in `src/content.config.ts`; tracks, links and site text in `src/consts.ts`.

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
