import type { APIRoute } from 'astro';
import { getTrack } from '../../../../consts';
import { getDives, slugOf, type Dive } from '../../../../lib/posts';
import { ogPng } from '../../../../lib/og';

export async function getStaticPaths() {
	return (await getDives()).map((p) => ({ params: { track: p.data.track, slug: slugOf(p.id) }, props: p }));
}

export const GET: APIRoute = async ({ props }) => {
	const p = props as Dive;
	const png = await ogPng({ seed: p.id, colour: p.data.track, kicker: `Deep Dives · ${getTrack(p.data.track).label}`, title: p.data.title });
	return new Response(new Uint8Array(png), { headers: { 'Content-Type': 'image/png' } });
};
