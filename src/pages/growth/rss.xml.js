import rss from '@astrojs/rss';
import { SITE_NAME, getTrack } from '../../consts';
import { getGrowth, growthUrl } from '../../lib/posts';

export async function GET(context) {
	const posts = await getGrowth();
	return rss({
		title: `${SITE_NAME} · Growth Plan`,
		description: 'The Architect Growth Plan: one deep topic a day. Curated with AI.',
		site: context.site,
		items: posts.map((p) => ({
			title: `${getTrack(p.data.track).emoji} ${p.data.title}`,
			pubDate: p.data.date,
			description: p.data.summary,
			categories: [p.data.track, ...p.data.tags],
			link: growthUrl(p),
		})),
	});
}
