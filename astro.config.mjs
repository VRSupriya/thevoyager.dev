// @ts-check

import mdx from '@astrojs/mdx';
import sitemap from '@astrojs/sitemap';
import { defineConfig, fontProviders } from 'astro/config';
import remarkVoyager from './src/plugins/remark-voyager.mjs';

// https://astro.build/config
export default defineConfig({
	site: 'https://thevoyager.dev',
	integrations: [
		mdx(),
		// Daily Brief posts are noindex (D2), so they stay out of the sitemap; the Brief archive page stays in.
		sitemap({ filter: (page) => !page.endsWith('/search/') && !/\/brief\/[^/]+\/$/.test(new URL(page).pathname) }),
	],
	markdown: {
		remarkPlugins: [remarkVoyager],
		shikiConfig: {
			themes: { light: 'github-light', dark: 'github-dark' },
			defaultColor: false,
		},
	},
	fonts: [
		{
			// Headings, dates and numbers (D3): no slashed zeros, tabular figures.
			provider: fontProviders.google(),
			name: 'Inter Tight',
			cssVariable: '--font-heading',
			weights: [600, 700, 800],
			styles: ['normal'],
			subsets: ['latin'],
			fallbacks: ['system-ui', 'sans-serif'],
		},
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
