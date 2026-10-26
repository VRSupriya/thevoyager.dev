import { defineCollection } from 'astro:content';
import { glob } from 'astro/loaders';
import { z } from 'astro/zod';
import { TRACK_SLUGS } from './consts';

// Fields shared by the two AI-curated collections (written by the routines).
const curated = {
	title: z.string(),
	date: z.coerce.date(),
	summary: z.string(),
	telegram: z.string().optional(),
	// Text people copy when they share the post: title, hook, points and hashtags. The site adds the link.
	share: z.string().optional(),
	// Older name for `share`; still accepted.
	linkedin: z.string().optional(),
	tags: z.array(z.string()).default([]),
	curated_with_ai: z.boolean().default(false),
	// Ids of related Deep Dives, e.g. "system/2026-10-14" (C8). Shown as "Go deeper" / "Related".
	related: z.array(z.string()).default([]),
	// Fixes made after publishing. Shown on the post and listed on /corrections/.
	corrections: z.array(z.object({ date: z.coerce.date(), text: z.string() })).default([]),
};

// ☕ AI Daily Brief: src/content/brief/YYYY-MM-DD.md
const brief = defineCollection({
	loader: glob({ base: './src/content/brief', pattern: '**/*.{md,mdx}' }),
	schema: z.object({
		...curated,
		// For the author only: sent privately on Telegram, never shown on the site.
		telegram_private: z.string().optional(),
	}),
});

// 🔭 Deep Dives: src/content/dives/<track>/YYYY-MM-DD.md
const dives = defineCollection({
	loader: glob({ base: './src/content/dives', pattern: '**/*.{md,mdx}' }),
	schema: z.object({
		...curated,
		track: z.enum(TRACK_SLUGS),
		week: z.number().int(),
		phase: z.string(),
		time_minutes: z.number().int(),
		read_minutes: z.number().int().optional(),
		// Three short points shown under the title and on the home card (C5).
		takeaways: z.array(z.string()).default([]),
		// A series the post belongs to, e.g. "Lab · Step 1", and its part number (P3).
		series: z.string().optional(),
		part: z.number().int().optional(),
		// For the author only: sent privately on Telegram, never shown on the site.
		telegram_private: z.string().optional(),
	}),
});

// ✍️ Your own posts. `draft: true` posts show in `npm run dev` but are never published.
const writing = defineCollection({
	loader: glob({ base: './src/content/writing', pattern: '**/*.{md,mdx}' }),
	schema: z.object({
		title: z.string(),
		date: z.coerce.date(),
		summary: z.string(),
		tags: z.array(z.string()).default([]),
		draft: z.boolean().default(false),
	}),
});

// Finished builds.
const projects = defineCollection({
	loader: glob({ base: './src/content/projects', pattern: '**/*.{md,mdx}' }),
	schema: z.object({
		title: z.string(),
		date: z.coerce.date(),
		summary: z.string(),
		repo: z.string().url().optional(),
		tags: z.array(z.string()).default([]),
	}),
});

export const collections = { brief, dives, writing, projects };
