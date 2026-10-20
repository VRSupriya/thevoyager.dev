import { type CollectionEntry, getCollection } from 'astro:content';
import { TIME_ZONE, getTrack as trackOf } from '../consts';
import { GENERIC_TAGS } from './brief';

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

// ---- Links between posts (P3, P4, P5, B6, B8) ----


export interface PostLinkData {
	href: string;
	title: string;
	label?: string;
	track?: string;
}

export const diveLink = (p: Dive, label?: string): PostLinkData => ({
	href: diveUrl(p),
	title: p.data.title,
	label: label ?? trackOf(p.data.track).label,
	track: p.data.track,
});

const topicTags = (tags: string[]) => tags.filter((t) => !GENERIC_TAGS.has(t.toLowerCase()));

/** Deep Dives related to a post: the ids it lists first, then the best tag overlap. */
export function relatedDives(
	post: { id?: string; data: { related?: string[]; tags?: string[] } },
	dives: Dive[],
	count = 2,
) {
	const picked: Dive[] = [];
	for (const id of post.data.related ?? []) {
		const d = dives.find((x) => x.id === id);
		if (d && d.id !== post.id && !picked.includes(d)) picked.push(d);
	}
	if (picked.length < count) {
		const mine = new Set(topicTags(post.data.tags ?? []));
		if (mine.size) {
			const scored = dives
				.filter((d) => d.id !== post.id && !picked.includes(d))
				.map((d) => ({ d, n: topicTags(d.data.tags).filter((t) => mine.has(t)).length }))
				.filter((x) => x.n > 0)
				.sort((a, b) => b.n - a.n || b.d.data.date.valueOf() - a.d.data.date.valueOf());
			for (const x of scored) {
				if (picked.length >= count) break;
				picked.push(x.d);
			}
		}
	}
	return picked.slice(0, count);
}

/** Older and newer neighbours in a newest-first list. */
export function neighbours<T extends { id: string }>(list: T[], item: T) {
	const i = list.findIndex((x) => x.id === item.id);
	return { newer: i > 0 ? list[i - 1] : undefined, older: i >= 0 && i < list.length - 1 ? list[i + 1] : undefined };
}

/** Parts of a series in reading order. */
export function seriesParts(dives: Dive[], series?: string) {
	if (!series) return [];
	return dives
		.filter((d) => d.data.series === series)
		.sort((a, b) => (a.data.part ?? 0) - (b.data.part ?? 0) || a.data.date.valueOf() - b.data.date.valueOf());
}
