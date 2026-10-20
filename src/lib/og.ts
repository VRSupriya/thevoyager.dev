// G9 / V1 · share images (1200×630) for each post, drawn at build time with satori, then turned into PNG with sharp.
import fs from 'node:fs/promises';
import path from 'node:path';
import satori from 'satori';
import sharp from 'sharp';
import { coverSvg, OG_TRACK_COLORS } from './cover';

const fontDir = path.resolve('src/assets/fonts');
let fonts: Promise<{ name: string; data: Buffer; weight: 400 | 700; style: 'normal' }[]> | undefined;
const loadFonts = () =>
	(fonts ??= Promise.all([
		fs.readFile(path.join(fontDir, 'atkinson-regular.woff')).then((data) => ({ name: 'Atkinson', data, weight: 400 as const, style: 'normal' as const })),
		fs.readFile(path.join(fontDir, 'atkinson-bold.woff')).then((data) => ({ name: 'Atkinson', data, weight: 700 as const, style: 'normal' as const })),
	]));

// A tiny element helper, so this file needs no JSX.
const h = (type: string, style: Record<string, unknown>, children?: unknown) => ({ type, props: { style, children } });

export interface OgInput {
	seed: string;
	/** Colour key: a track slug, "brief" or "own". */
	colour: string;
	kicker: string;
	title: string;
	footer?: string;
}

export async function ogPng({ seed, colour, kicker, title, footer = 'thevoyager.dev' }: OgInput) {
	const ink = OG_TRACK_COLORS[colour] ?? OG_TRACK_COLORS.brief;
	const art = coverSvg(seed, 560, 630, { ink, soft: '#a3adc2' });
	const size = title.length > 90 ? 50 : title.length > 60 ? 58 : 66;
	const tree = h(
		'div',
		{ width: 1200, height: 630, display: 'flex', background: '#0b1020', fontFamily: 'Atkinson', position: 'relative' },
		[
			h('img', { position: 'absolute', right: 0, top: 0, width: 560, height: 630, opacity: 0.9 }, undefined),
			h(
				'div',
				{ display: 'flex', flexDirection: 'column', justifyContent: 'space-between', padding: '72px 80px', width: 860, height: 630 },
				[
					h('div', { display: 'flex', flexDirection: 'column' }, [
						h('div', { width: 84, height: 6, borderRadius: 3, background: ink, marginBottom: 28 }),
						h('div', { fontSize: 30, fontWeight: 700, color: ink, letterSpacing: 2, textTransform: 'uppercase' }, kicker),
						h('div', { fontSize: size, fontWeight: 700, color: '#e2e7f2', lineHeight: 1.12, marginTop: 22 }, title),
					]),
					h('div', { display: 'flex', fontSize: 28, color: '#a3adc2' }, `The Voyager · ${footer}`),
				],
			),
		],
	);
	// satori needs the picture as an <img> with a data URL.
	(tree.props.children as any)[0].props.src = `data:image/svg+xml;base64,${Buffer.from(art).toString('base64')}`;
	const svg = await satori(tree as any, { width: 1200, height: 630, fonts: await loadFonts() });
	return sharp(Buffer.from(svg)).png({ compressionLevel: 9 }).toBuffer();
}
