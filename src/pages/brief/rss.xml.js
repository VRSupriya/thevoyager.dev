import rss from '@astrojs/rss';
import { SITE_NAME } from '../../consts';
import { briefUrl, getBriefs } from '../../lib/posts';

export async function GET(context) {
	const posts = await getBriefs();
	return rss({
		title: `${SITE_NAME} · AI Daily Brief`,
		description: 'The AI Daily Brief: what matters in AI, cloud and systems each morning. Curated with AI.',
		site: context.site,
		items: posts.map((p) => ({
			title: p.data.title,
			pubDate: p.data.date,
			description: p.data.summary,
			categories: p.data.tags,
			link: briefUrl(p),
		})),
	});
}
