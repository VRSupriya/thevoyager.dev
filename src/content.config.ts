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
	tags: z.array(z.string()).default([]),
	curated_with_ai: z.boolean().default(false),
};

// ☕ AI Daily Brief: src/content/brief/YYYY-MM-DD.md
const brief = defineCollection({
	loader: glob({ base: './src/content/brief', pattern: '**/*.{md,mdx}' }),
	schema: z.object(curated),
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
