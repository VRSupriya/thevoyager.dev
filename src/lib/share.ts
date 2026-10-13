// Builds the text people copy or send when they share a post.
//
// One shape for every platform: title, hook line, "•" points, the blog link at the bottom, then hashtags.
// A post can carry its own text in the `share:` front-matter field (written by the routines; the older
// `linkedin:` name still works). Posts without one get a draft built from their summary and Telegram lines.
// Plain text only: LinkedIn, WhatsApp and Substack do not render markdown.

const ACRONYMS: Record<string, string> = {
	ai: 'AI', llm: 'LLM', llms: 'LLMs', mcp: 'MCP', rag: 'RAG', gpu: 'GPU', api: 'API', sdk: 'SDK', moe: 'MoE',
	ml: 'ML', a2a: 'A2A', nlp: 'NLP', gitlab: 'GitLab', github: 'GitHub', openai: 'OpenAI', devops: 'DevOps',
};
const MONTHS = ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September', 'October', 'November', 'December'];

export interface ShareInput {
	title: string;
	summary: string;
	tags?: string[];
	/** Absolute address of the post. */
	url: string;
	/** AI-curated posts (Brief, Deep Dives) or the author's own writing and projects. */
	kind: 'brief' | 'dive' | 'own';
	date?: Date;
	/** Text written for sharing (front matter `share:` or `linkedin:`). */
	written?: string;
	/** The `telegram:` text, used to build points when nothing was written. */
	telegram?: string;
}

export interface ShareText {
	/** Full text for LinkedIn, WhatsApp, Substack, email. */
	long: string;
	/** Short text for X (the link is added by X, so it is not in this text). */
	short: string;
	/** Short text with the link, for Bluesky and similar. */
	shortWithLink: string;
	title: string;
	url: string;
}

export function hashtags(tags: string[] = [], limit = 4): string {
	const out: string[] = [];
	for (const t of ['AI', ...tags]) {
		const parts = String(t).split(/[^A-Za-z0-9]+/).filter(Boolean);
		const tag = '#' + parts.map((w) => ACRONYMS[w.toLowerCase()] ?? w[0].toUpperCase() + w.slice(1)).join('');
		if (tag.length > 1 && !out.some((o) => o.toLowerCase() === tag.toLowerCase())) out.push(tag);
	}
	return out.slice(0, limit).join(' ');
}

function firstSentence(text: string, limit = 220): string {
	const t = text.replace(/\s+/g, ' ').trim();
	const m = t.match(/^(.+?[.!?])(\s|$)/);
	const s = m ? m[1] : t;
	return s.length <= limit ? s : s.slice(0, limit - 1).trimEnd() + '…';
}

const noEmoji = (line: string) => line.trim().replace(/^[^\p{L}\p{N}\s"“'(\[]+\s*/u, '');

function splitTags(draft: string): [string, string] {
	const lines = draft.trimEnd().split('\n');
	const last = lines[lines.length - 1]?.trim() ?? '';
	if (last && last.split(/\s+/).every((w) => w.startsWith('#'))) return [lines.slice(0, -1).join('\n').trimEnd(), last];
	return [draft.trimEnd(), ''];
}

function clip(text: string, max: number): string {
	return text.length <= max ? text : text.slice(0, Math.max(0, max - 1)).trimEnd() + '…';
}

export function buildShare(i: ShareInput): ShareText {
	const written = (i.written ?? '').replace(/\*\*|__/g, '').trim();
	let body: string;
	let tags: string;
	let title: string;

	if (written) {
		[body, tags] = splitTags(written);
		title = body.split('\n')[0].trim() || i.title;
		if (!tags) tags = hashtags(i.tags);
	} else {
		const lines = (i.telegram ?? '').split('\n').map((l) => l.trim()).filter(Boolean);
		const items = lines.slice(1).filter((l) => !l.startsWith('⏱'));
		let points: string[] = [];
		if (i.kind === 'brief') {
			const d = i.date;
			const when = d ? `${d.getUTCDate()} ${MONTHS[d.getUTCMonth()]} ${d.getUTCFullYear()}` : '';
			title = `AI Daily Brief${when ? ', ' + when : ''}`;
			points = items.map((l) => '• ' + noEmoji(l));
		} else if (i.kind === 'dive') {
			title = i.title;
			points = items.map((l) => '• ' + firstSentence(noEmoji(l)));
		} else {
			title = i.title;
		}
		const hook = firstSentence(i.summary, 300);
		body = [title, hook, points.join('\n')].filter(Boolean).join('\n\n');
		tags = hashtags(i.tags);
	}

	const tail = i.kind === 'own' ? 'Read the full post:' : 'Full post and sources:';
	const long = [body, `${tail}\n${i.url}`, tags].filter(Boolean).join('\n\n');

	// X counts every link as 23 characters and adds it itself; keep the rest within what is left.
	const shortTags = tags.split(' ').slice(0, 2).join(' ');
	const room = 280 - 23 - 2 - (shortTags ? shortTags.length + 2 : 0);
	const paras = body.split(/\n\s*\n/).map((x) => x.trim()).filter(Boolean);
	const hookLine = paras[1] && !paras[1].startsWith('•') ? paras[1] : '';
	const withHook = hookLine ? `${title}\n\n${hookLine}` : title;
	const shortTitle = clip(withHook.length <= room ? withHook : title, room);
	const short = [shortTitle, shortTags].filter(Boolean).join('\n\n');
	const shortWithLink = [clip(title, 300 - i.url.length - 4 - (shortTags.length + 2)), i.url, shortTags].filter(Boolean).join('\n\n');

	return { long, short, shortWithLink, title, url: i.url };
}
