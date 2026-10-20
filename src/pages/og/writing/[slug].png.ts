import type { APIRoute } from 'astro';
import { getWriting, type Writing } from '../../../lib/posts';
import { ogPng } from '../../../lib/og';

export async function getStaticPaths() {
	return (await getWriting()).map((p) => ({ params: { slug: p.id }, props: p }));
}

export const GET: APIRoute = async ({ props }) => {
	const p = props as Writing;
	const png = await ogPng({ seed: p.id, colour: 'own', kicker: 'Writing', title: p.data.title });
	return new Response(new Uint8Array(png), { headers: { 'Content-Type': 'image/png' } });
};
