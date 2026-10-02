// @ts-check

import mdx from '@astrojs/mdx';
import sitemap from '@astrojs/sitemap';
import { defineConfig, fontProviders } from 'astro/config';
import remarkMermaid from './src/plugins/remark-mermaid.mjs';

// AI-curated posts (/brief/<post>/ and /growth/<track>/<post>/) are noindex, so they
// stay out of the sitemap too. Your own writing, projects and the section pages stay in.
/** @param {string} url */
const isCuratedPost = (url) => {
	const path = new URL(url).pathname;
	return /^\/brief\/[^/]+\/$/.test(path) || /^\/growth\/[^/]+\/[^/]+\/$/.test(path);
};

// https://astro.build/config
export default defineConfig({
	site: 'https://thevoyager.dev',
	integrations: [
		mdx(),
		sitemap({ filter: (page) => !isCuratedPost(page) && !page.endsWith('/search/') }),
	],
	markdown: {
		remarkPlugins: [remarkMermaid],
		shikiConfig: {
			themes: { light: 'github-light', dark: 'github-dark' },
			defaultColor: false,
		},
	},
	fonts: [
		{
			provider: fontProviders.local(),
			name: 'Atkinson',
			cssVariable: '--font-atkinson',
			fallbacks: ['sans-serif'],
			options: {
				variants: [
					{
						src: ['./src/assets/fonts/atkinson-regular.woff'],
						weight: 400,
						style: 'normal',
						display: 'swap',
					},
					{
						src: ['./src/assets/fonts/atkinson-bold.woff'],
						weight: 700,
						style: 'normal',
						display: 'swap',
					},
				],
			},
		},
	],
});
