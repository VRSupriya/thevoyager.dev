import { type CollectionEntry, getCollection } from 'astro:content';
import { TIME_ZONE } from '../consts';

export type Brief = CollectionEntry<'brief'>;
export type Dive = CollectionEntry<'dives'>;
export type Writing = CollectionEntry<'writing'>;
export type Project = CollectionEntry<'projects'>;

const newestFirst = (a: { data: { date: Date } }, b: { data: { date: Date } }) =>
	b.data.date.valueOf() - a.data.date.valueOf();

export async function getBriefs() {
	return (await getCollection('brief')).sort(newestFirst);
}

export async function getDives(track?: string) {
	const all = await getCollection('dives', (p) => !track || p.data.track === track);
	return all.sort(newestFirst);
}

// Drafts are visible while you write (`npm run dev`) and never in the built site.
export async function getWriting() {
	const all = await getCollection('writing', (p) => import.meta.env.DEV || !p.data.draft);
	return all.sort(newestFirst);
}

export async function getProjects() {
	return (await getCollection('projects')).sort(newestFirst);
}

// The last part of a file id, e.g. "system/2026-10-01" -> "2026-10-01".
const slugOf = (id: string) => id.split('/').pop()!;

export const briefUrl = (p: Brief) => `/brief/${p.id}/`;
export const diveUrl = (p: Dive) => `/deep-dives/${p.data.track}/${slugOf(p.id)}/`;
export const writingUrl = (p: Writing) => `/writing/${p.id}/`;
export const projectUrl = (p: Project) => `/projects/${p.id}/`;
export { slugOf };

// Frontmatter dates like 2026-10-01 are parsed as UTC midnight, so read them back in UTC.
export const dayKey = (date: Date) => date.toISOString().slice(0, 10);

// Today's date (YYYY-MM-DD) in the site's time zone, at build time.
export function todayKey(now = new Date()) {
	return new Intl.DateTimeFormat('en-CA', {
		timeZone: TIME_ZONE,
		year: 'numeric',
		month: '2-digit',
		day: '2-digit',
	}).format(now);
}

const formatMinutes = (m: number) => (m >= 60 ? `${+(m / 60).toFixed(1)} h` : `${m} min`);

// "10 min read · 35 min hands-on" when the post has an exercise, otherwise just the time.
export function diveTime(data: { time_minutes: number; read_minutes?: number }) {
	const { time_minutes: total, read_minutes: read } = data;
	if (read && total - read >= 10) return `${read} min read · ${formatMinutes(total - read)} hands-on`;
	return formatMinutes(read ?? total);
}

export function readingMinutes(body = '') {
	const words = body.trim().split(/\s+/).filter(Boolean).length;
	return Math.max(1, Math.round(words / 220));
}

// True when the post has a ```mermaid code block, so the diagram script is only loaded there.
export const hasMermaid = (body = '') => /^\s*(```|~~~)\s*mermaid\b/m.test(body);
