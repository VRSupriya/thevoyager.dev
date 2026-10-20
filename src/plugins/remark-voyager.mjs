// Turns the Markdown the routines write into The Voyager's page structure:
// - callouts: "> [!TAKEAWAYS]", "> [!MEANS]", "> [!TRY]", "> [!WATCH]", "> [!NOTE]", "> [!REVEAL] Title" (collapsible)
// - widgets: ```stats, ```steps, ```flow, ```calc, ```chart, ```compare, ```timer, ```terminal, ```curve, ```quiz
// - ```mermaid diagrams (drawn in the browser by a self-hosted script; the source shows until then)
// - Daily Brief posts: one <section> per story with its label, source chips, "Try this" boxes, and the regional
//   block as a collapsible panel; "Quiet day" slots are left out
// - Deep Dives: "The 5-minute version" as a highlighted box, "Do it" marked as the hands-on part
// - emoji at the start of headings are removed (track colours and labels replace them)
// It sets frontmatter flags the page uses: hasMermaid, widgets (list of kinds), interactive, hasCode.

import { INTERACTIVE, WIDGET_KINDS, renderWidget } from './widgets.mjs';

const LEADING_EMOJI = /^(?:[\p{Extended_Pictographic}☀-➿][️‍\p{Extended_Pictographic}]*\s*)+/u;

const SLOTS = [
	{ emoji: ['🔥'], key: 'top', label: 'Top story', main: true },
	{ emoji: ['🚀'], key: 'models', label: 'Models & features', main: true },
	{ emoji: ['🤖'], key: 'agents', label: 'Agents', main: true },
	{ emoji: ['🔬'], key: 'research', label: 'Research', main: true },
	{ emoji: ['✍️', '✍'], key: 'people', label: 'People worth following', main: true },
	{ emoji: ['🏗️', '🏗'], key: 'architecture', label: 'Architecture', main: true },
	{ emoji: ['⚡'], key: 'quick', label: 'Also worth knowing' },
	{ emoji: ['⏰'], key: 'deadlines', label: 'Deadlines & deprecations' },
	{ emoji: ['📅'], key: 'week', label: 'Week in review' },
	{ emoji: ['🌍', '🌏', '🌎'], key: 'regional', label: 'Middle East, India & events', collapsible: true },
];
export const BRIEF_SLOTS = SLOTS;

const CALLOUTS = {
	TAKEAWAYS: { cls: 'takeaways', title: 'Key takeaways' },
	MEANS: { cls: 'means', title: '' },
	TRY: { cls: 'try', title: 'Try this' },
	WATCH: { cls: 'watch', title: 'Watch out' },
	NOTE: { cls: 'note', title: 'Note' },
	TIP: { cls: 'try', title: 'Tip' },
	WARNING: { cls: 'watch', title: 'Watch out' },
	REVEAL: { cls: 'reveal', title: 'Show the answer', details: true },
};

const esc = (s) => String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');

function textOf(node) {
	if (!node) return '';
	if (node.type === 'text' || node.type === 'inlineCode') return node.value;
	return (node.children ?? []).map(textOf).join('');
}

function stripLeadingEmoji(heading) {
	const first = heading.children?.[0];
	if (first?.type === 'text') {
		first.value = first.value.replace(LEADING_EMOJI, '');
		if (!first.value) heading.children.shift();
	}
}

function slotOf(heading) {
	const t = textOf(heading).trimStart();
	return SLOTS.find((s) => s.emoji.some((e) => t.startsWith(e)));
}

const el = (tag, className, children = [], extra = {}) => ({
	type: 'voyager',
	data: { hName: tag, hProperties: { className: [].concat(className), ...extra } },
	children,
});

// ── callouts ────────────────────────────────────────────────────────────────
function calloutFrom(node) {
	const para = node.children?.[0];
	if (para?.type !== 'paragraph') return null;
	const first = para.children?.[0];
	if (first?.type !== 'text') return null;
	const m = first.value.match(/^\[!([A-Z]+)\][ \t]*([^\n]*)\n?/);
	if (!m || !CALLOUTS[m[1]]) return null;
	const kind = CALLOUTS[m[1]];
	first.value = first.value.slice(m[0].length);
	if (!first.value) para.children.shift();
	if (!para.children.length) node.children.shift();
	const titleText = m[2].trim() || kind.title;
	if (kind.details) {
		return el('details', ['callout', 'callout-reveal'], [el('summary', 'callout-title', [{ type: 'text', value: titleText }]), el('div', 'callout-body', node.children)]);
	}
	const kids = titleText ? [el('p', 'callout-title', [{ type: 'text', value: titleText }]), ...node.children] : node.children;
	return el('aside', ['callout', `callout-${kind.cls}`], kids);
}

// "… sentence. **Try this:** do X." → paragraph + Try this callout
function splitTryThis(parent) {
	const out = [];
	for (const node of parent.children) {
		if (node.type === 'paragraph') {
			const i = node.children.findIndex((c) => c.type === 'strong' && /^try this:?$/i.test(textOf(c).trim()));
			if (i !== -1) {
				const before = node.children.slice(0, i);
				const after = node.children.slice(i + 1);
				if (after[0]?.type === 'text') {
					after[0].value = after[0].value.replace(/^\s*:?\s*/, '');
					after[0].value = after[0].value.charAt(0).toUpperCase() + after[0].value.slice(1);
				}
				if (before.some((c) => textOf(c).trim())) out.push({ ...node, children: before });
				out.push(el('aside', ['callout', 'callout-try'], [el('p', 'callout-title', [{ type: 'text', value: 'Try this' }]), { type: 'paragraph', children: after }]));
				continue;
			}
		}
		out.push(node);
	}
	parent.children = out;
}

// Trailing "[Source](…) · [Policy API](…)" → source chips
function domainOf(url) {
	try {
		return new URL(url).hostname.replace(/^www\./, '');
	} catch {
		return '';
	}
}
const isSep = (c) => c.type === 'text' && /^[\s·|,]*$/.test(c.value);
function extractSources(parent) {
	const out = [];
	for (const node of parent.children) {
		if (node.type !== 'paragraph') {
			out.push(node);
			continue;
		}
		const kids = node.children;
		let i = kids.length;
		while (i > 0 && (kids[i - 1].type === 'link' || isSep(kids[i - 1]))) i--;
		const tail = kids.slice(i);
		const links = tail.filter((c) => c.type === 'link');
		if (!links.length) {
			out.push(node);
			continue;
		}
		let head = kids.slice(0, i);
		// The tail must start on its own line, or be the whole paragraph.
		const last = head[head.length - 1];
		if (head.length && !(last.type === 'text' && /\n\s*$/.test(last.value)) && !(last.type === 'break')) {
			out.push(node);
			continue;
		}
		if (last?.type === 'text') last.value = last.value.replace(/\s+$/, '');
		head = head.filter((c) => !(c.type === 'text' && !c.value));
		if (head.some((c) => textOf(c).trim())) out.push({ ...node, children: head });
		out.push(
			el(
				'div',
				'sources',
				links.map((l) => {
					const label = textOf(l).trim();
					const d = domainOf(l.url);
					const text = /^(source|link|paper|repo)$/i.test(label) || !label ? d : `${d} · ${label}`;
					return { type: 'link', url: l.url, title: null, data: { hProperties: { className: ['source-chip'], rel: 'noopener' } }, children: [{ type: 'text', value: text }] };
				}),
			),
		);
	}
	parent.children = out;
}

// ── Daily Brief structure ───────────────────────────────────────────────────
function structureBrief(tree) {
	const out = [];
	let current = null;
	let n = 0;
	const close = () => {
		if (!current) return;
		const { slot, heading, body } = current;
		const quiet = body.length && body.every((b) => /^quiet day/i.test(textOf(b).trim()));
		if (!quiet || !slot.main) {
			if (slot.collapsible) {
				const count = body.reduce((k, b) => k + (b.type === 'list' ? b.children.length : 0), 0);
				out.push(
					el('details', ['brief-extra', `slot-${slot.key}`], [el('summary', 'extra-summary', [{ type: 'text', value: `${slot.label}${count ? ` · ${count}` : ''}` }]), el('div', 'extra-body', body)], { id: `story-${slot.key}` }),
				);
			} else if (slot.main) {
				n++;
				stripLeadingEmoji(heading);
				out.push(el('section', ['story', `slot-${slot.key}`], [el('p', 'story-label', [{ type: 'text', value: `${n} · ${slot.label}` }]), heading, ...body], { id: `story-${slot.key}` }));
			} else {
				stripLeadingEmoji(heading);
				out.push(el('section', ['brief-extra', `slot-${slot.key}`], [heading, ...body], { id: `story-${slot.key}` }));
			}
		}
		current = null;
	};
	for (const node of tree.children) {
		const slot = node.type === 'heading' && node.depth === 2 ? slotOf(node) : null;
		if (slot) {
			close();
			current = { slot, heading: node, body: [] };
		} else if (node.type === 'thematicBreak' || (node.type === 'heading' && node.depth <= 2)) {
			close();
			out.push(node);
		} else if (current) {
			current.body.push(node);
		} else {
			out.push(node);
		}
	}
	close();
	tree.children = out;
}

// ── Deep Dive structure ─────────────────────────────────────────────────────
function structureDive(tree) {
	const out = [];
	let box = null;
	for (const node of tree.children) {
		if (node.type === 'heading' && node.depth === 2) {
			box = null;
			const t = textOf(node).trim().toLowerCase();
			if (t.startsWith('the 5-minute version') || t.startsWith('the five-minute version')) {
				box = el('div', 'five-min', [node]);
				out.push(box);
				continue;
			}
			if (t.startsWith('do it')) {
				node.data = { ...(node.data ?? {}), hProperties: { className: ['do-it'] } };
			}
		}
		if (box) box.children.push(node);
		else out.push(node);
	}
	tree.children = out;
}

// ── tree walk ───────────────────────────────────────────────────────────────
function walk(node, fn) {
	if (!node.children) return;
	for (let i = 0; i < node.children.length; i++) {
		const replaced = fn(node.children[i], i, node);
		if (replaced) node.children[i] = replaced;
		walk(node.children[i], fn);
	}
}

export default function remarkVoyager() {
	return (tree, file) => {
		const path = String(file.path ?? file.history?.[0] ?? '');
		const isBrief = /[/\\]content[/\\]brief[/\\]/.test(path);
		const isDive = /[/\\]content[/\\]dives[/\\]/.test(path);
		const fm = file.data?.astro?.frontmatter ?? {};
		const widgets = new Set();
		let hasMermaid = false;
		let hasCode = false;

		walk(tree, (node, i, parent) => {
			if (node.type === 'code') {
				const lang = (node.lang ?? '').toLowerCase();
				if (lang === 'mermaid') {
					hasMermaid = true;
					return { type: 'html', value: `<div class="mermaid-wrap"><pre class="mermaid">${esc(node.value)}</pre></div>` };
				}
				if (WIDGET_KINDS.includes(lang)) {
					widgets.add(lang);
					return { type: 'html', value: renderWidget(lang, node.value) };
				}
				hasCode = true;
				return null;
			}
			if (node.type === 'blockquote') return calloutFrom(node);
			if (node.type === 'heading' && (isDive || node.depth > 2)) stripLeadingEmoji(node);
			return null;
		});

		if (isBrief) {
			walk(tree, (node) => {
				if (node.children && node.type !== 'paragraph') {
					splitTryThis(node);
					extractSources(node);
				}
				return null;
			});
			splitTryThis(tree);
			extractSources(tree);
			structureBrief(tree);
		}
		if (isDive) structureDive(tree);

		fm.hasMermaid = hasMermaid;
		fm.widgets = [...widgets];
		fm.interactive = [...widgets].some((w) => INTERACTIVE.has(w));
		fm.hasCode = hasCode;
	};
}
