// Site-wide settings. Import from anywhere with `import { ... } from '../consts'`.

export const SITE_NAME = 'The Voyager';
export const SITE_TAGLINE = "Exploring the frontier, building what's next!";
export const SITE_TITLE = "The Voyager — Exploring the frontier, building what's next";
export const SITE_DESCRIPTION =
	"An AI architect's journey through LLM systems, agents, cloud and data architecture: daily briefs, deep dives, builds and research toward new LLM architectures.";

// Dates on the Today page ("today", "this week") are worked out in this time zone.
export const TIME_ZONE = 'Asia/Dubai';

export const SUBSTACK_URL = 'https://voyager2050.substack.com';
export const SUBSTACK_EMBED = 'https://voyager2050.substack.com/embed';

export const LINKS = {
	github: 'https://github.com/VRSupriya',
	substack: SUBSTACK_URL,
	linkedin: '', // TODO: add your LinkedIn URL
};

export const NAV = [
	{ href: '/', label: 'Today' },
	{ href: '/brief/', label: 'Daily Brief' },
	{ href: '/growth/', label: 'Growth Plan' },
	{ href: '/writing/', label: 'Writing' },
	{ href: '/projects/', label: 'Projects' },
	{ href: '/about/', label: 'About' },
];

// Growth Plan tracks, in tab order. `day` is the weekday the track runs (0 = Sunday).
export const TRACKS = [
	{ slug: 'models-agentic', emoji: '🚀', label: 'Models & Agentic', day: 1 },
	{ slug: 'research', emoji: '🔬', label: 'Research', day: 2 },
	{ slug: 'system', emoji: '🏗️', label: 'System', day: 3 },
	{ slug: 'interview', emoji: '🎯', label: 'Interview', day: 4 },
	{ slug: 'github', emoji: '💻', label: 'GitHub', day: 5 },
	{ slug: 'build', emoji: '🛠️', label: 'Builds', day: 6 },
	{ slug: 'lab', emoji: '🧪', label: 'Lab', day: 0 },
	{ slug: 'review', emoji: '🔁', label: 'Review', day: null },
	{ slug: 'radar', emoji: '🎓', label: 'Learning Radar', day: null },
] as const;

export type TrackSlug = (typeof TRACKS)[number]['slug'];
export const TRACK_SLUGS = TRACKS.map((t) => t.slug) as [TrackSlug, ...TrackSlug[]];

export function getTrack(slug: string) {
	return TRACKS.find((t) => t.slug === slug)!;
}
