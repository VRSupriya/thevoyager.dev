import type { APIRoute } from 'astro';
import { getBriefs, type Brief } from '../../../lib/posts';
import { ogPng } from '../../../lib/og';

export async function getStaticPaths() {
	return (await getBriefs()).map((p) => ({ params: { slug: p.id }, props: p }));
}

export const GET: APIRoute = async ({ props }) => {
	const p = props as Brief;
	const date = p.data.date.toLocaleDateString('en-GB', { day: 'numeric', month: 'long', year: 'numeric', timeZone: 'UTC' });
	const png = await ogPng({ seed: p.id, colour: 'brief', kicker: `Daily Brief · ${date}`, title: p.data.title });
	return new Response(new Uint8Array(png), { headers: { 'Content-Type': 'image/png' } });
};
