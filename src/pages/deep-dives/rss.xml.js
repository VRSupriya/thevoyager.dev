import rss from '@astrojs/rss';
import { SITE_NAME, getTrack } from '../../consts';
import { getDives, diveUrl } from '../../lib/posts';

export async function GET(context) {
	const posts = await getDives();
	return rss({
		title: `${SITE_NAME} · Deep Dives`,
		description: 'Deep Dives: one topic a day, explained properly. Curated with AI.',
		site: context.site,
		items: posts.map((p) => ({
			title: `${getTrack(p.data.track).emoji} ${p.data.title}`,
			pubDate: p.data.date,
			description: p.data.summary,
			categories: [p.data.track, ...p.data.tags],
			link: diveUrl(p),
		})),
	});
}
