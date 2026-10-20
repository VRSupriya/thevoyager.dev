// Reads the structure of a Daily Brief post from its Markdown: the stories, their slot and source site, and the
// one-liners for "In 30 seconds". Uses the same slot emoji as src/plugins/remark-voyager.mjs.

import { BRIEF_SLOTS } from '../plugins/remark-voyager.mjs';

export interface BriefStory {
	key: string;
	label: string;
	headline: string;
	/** Short line from the Telegram text, or the headline. */
	line: string;
	source?: string;
	anchor: string;
}

const LEADING_EMOJI = /^(?:[\p{Extended_Pictographic}☀-➿][️‍\p{Extended_Pictographic}]*\s*)+/u;

const slotFor = (text: string) => BRIEF_SLOTS.find((s: any) => s.emoji.some((e: string) => text.trimStart().startsWith(e)));

function domain(url: string) {
	try {
		return new URL(url).hostname.replace(/^www\./, '');
	} catch {
		return undefined;
	}
}

/** The main stories (top, models, agents, research, people, architecture), skipping "Quiet day" slots. */
export function briefStories(body = '', telegram = ''): BriefStory[] {
	const lines = telegram.split('\n').map((l) => l.trim());
	const sections = body.split(/^## /m).slice(1);
	const stories: BriefStory[] = [];
	for (const sec of sections) {
		const [headingLine, ...rest] = sec.split('\n');
		const slot: any = slotFor(headingLine);
		if (!slot?.main) continue;
		const text = rest.join('\n').trim();
		if (/^quiet day/i.test(text)) continue;
		const headline = headingLine.replace(LEADING_EMOJI, '').trim();
		const tg = lines.find((l) => slot.emoji.some((e: string) => l.startsWith(e)));
		const tgLine = tg?.replace(LEADING_EMOJI, '').trim();
		const link = text.match(/\]\((https?:\/\/[^)\s]+)\)/);
		stories.push({
			key: slot.key,
			label: slot.label,
			headline,
			line: tgLine && !/^quiet day/i.test(tgLine) ? tgLine : headline,
			source: link ? domain(link[1]) : undefined,
			anchor: `story-${slot.key}`,
		});
	}
	return stories;
}

/** Tags that say little about a topic, ignored when matching related posts. */
export const GENERIC_TAGS = new Set(['ai', 'agents', 'llm', 'llms', 'architecture', 'system-design', 'genai', 'ml', 'news', 'research']);
