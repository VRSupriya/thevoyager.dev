// V1 · cover art: a calm, abstract "orbit" picture that is unique and stable for each post.
// The same function draws it on the page (using the track's CSS colour) and in the share image (fixed colours).

function hash(s: string) {
	let h = 2166136261;
	for (let i = 0; i < s.length; i++) {
		h ^= s.charCodeAt(i);
		h = Math.imul(h, 16777619);
	}
	return h >>> 0;
}

function rng(seed: number) {
	let a = seed || 1;
	return () => {
		a = (a + 0x6d2b79f5) | 0;
		let t = Math.imul(a ^ (a >>> 15), 1 | a);
		t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
		return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
	};
}

export interface CoverColors {
	/** Main colour (orbits, planet). */
	ink: string;
	/** Soft colour (stars, faint orbits). */
	soft: string;
	/** Background; omit for a transparent picture. */
	bg?: string;
}

/** An SVG string, `w`×`h`, for the post identified by `seed` (use its id). */
export function coverSvg(seed: string, w: number, h: number, c: CoverColors) {
	const r = rng(hash(seed));
	const cx = w * (0.55 + r() * 0.35);
	const cy = h * (0.35 + r() * 0.4);
	const tilt = -35 + r() * 70;
	const rings = 2 + Math.floor(r() * 3);
	const base = Math.min(w, h) * (0.18 + r() * 0.12);
	let s = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ${w} ${h}" width="${w}" height="${h}" aria-hidden="true">`;
	if (c.bg) s += `<rect width="${w}" height="${h}" fill="${c.bg}"/>`;
	for (let i = 0; i < 26; i++) {
		const x = r() * w;
		const y = r() * h;
		const rad = 0.6 + r() * 1.6;
		s += `<circle cx="${x.toFixed(1)}" cy="${y.toFixed(1)}" r="${rad.toFixed(2)}" fill="${c.soft}" opacity="${(0.35 + r() * 0.5).toFixed(2)}"/>`;
	}
	for (let i = 0; i < rings; i++) {
		const rx = base * (1.6 + i * 0.9);
		const ry = rx * (0.3 + r() * 0.15);
		s += `<ellipse cx="${cx.toFixed(1)}" cy="${cy.toFixed(1)}" rx="${rx.toFixed(1)}" ry="${ry.toFixed(1)}" transform="rotate(${tilt.toFixed(1)} ${cx.toFixed(1)} ${cy.toFixed(1)})" fill="none" stroke="${i === 0 ? c.ink : c.soft}" stroke-width="${i === 0 ? 2 : 1.4}" opacity="${(0.85 - i * 0.18).toFixed(2)}"/>`;
		// a small body travelling on each ring
		const a = r() * Math.PI * 2;
		const t = (tilt * Math.PI) / 180;
		const px = cx + rx * Math.cos(a) * Math.cos(t) - ry * Math.sin(a) * Math.sin(t);
		const py = cy + rx * Math.cos(a) * Math.sin(t) + ry * Math.sin(a) * Math.cos(t);
		s += `<circle cx="${px.toFixed(1)}" cy="${py.toFixed(1)}" r="${(3 + r() * 4).toFixed(1)}" fill="${c.ink}"/>`;
	}
	s += `<circle cx="${cx.toFixed(1)}" cy="${cy.toFixed(1)}" r="${(base * 0.55).toFixed(1)}" fill="${c.ink}" opacity="0.9"/>`;
	s += `</svg>`;
	return s;
}

/** Fixed colours per track, for share images (dark background). */
export const OG_TRACK_COLORS: Record<string, string> = {
	'models-agentic': '#9cbcfa',
	research: '#c4b0fb',
	system: '#7fdccf',
	interview: '#f2c26b',
	github: '#c3cad6',
	build: '#f8b48a',
	lab: '#f5a3c8',
	recap: '#6fd3cf',
	radar: '#7dd3e8',
	brief: '#6fd3cf',
	own: '#6fd3cf',
};
