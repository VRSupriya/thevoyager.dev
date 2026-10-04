import rss from '@astrojs/rss';
import { SITE_NAME } from '../../consts';
import { getWriting, writingUrl } from '../../lib/posts';

export async function GET(context) {
	const posts = await getWriting();
	return rss({
		title: `${SITE_NAME} · Writing`,
		description: 'Original writing on LLM systems, agents, cloud and data architecture.',
		site: context.site,
		items: posts.map((p) => ({
			title: p.data.title,
			pubDate: p.data.date,
			description: p.data.summary,
			categories: p.data.tags,
			link: writingUrl(p),
		})),
	});
}
