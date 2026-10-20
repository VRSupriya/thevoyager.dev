// Build-time renderers for the interactive widgets a post can use (V2–V10, stats, timer).
// Each takes the text inside a fenced code block and returns HTML that is complete and readable without
// JavaScript. Widgets that get interactive (steps, calc, compare, timer, terminal) carry their data in a
// data-* attribute; src/scripts/widgets.ts picks them up in the browser.

import { evaluate, formatNumber } from '../lib/expr.mjs';

export const WIDGET_KINDS = ['stats', 'steps', 'flow', 'calc', 'chart', 'compare', 'timer', 'terminal', 'curve', 'quiz'];
/** Widgets that need the browser script. */
export const INTERACTIVE = new Set(['steps', 'calc', 'compare', 'timer', 'terminal']);

const esc = (s) =>
	String(s ?? '')
		.replace(/&/g, '&amp;')
		.replace(/</g, '&lt;')
		.replace(/>/g, '&gt;')
		.replace(/"/g, '&quot;');

/** Escape, then allow `code` and **bold** in short texts. */
const inline = (s) =>
	esc(s)
		.replace(/`([^`]+)`/g, '<code>$1</code>')
		.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');

const attrJson = (o) => esc(JSON.stringify(o));
const title = (t) => (t ? `<p class="w-title">${inline(t)}</p>` : '');
const errorBox = (kind, msg) =>
	`<div class="w-error" role="note">Widget "${esc(kind)}" could not be drawn: ${esc(msg)}</div>`;

function parseJson(kind, src) {
	try {
		return JSON.parse(src);
	} catch (e) {
		throw new Error(`its data is not valid JSON (${e.message})`);
	}
}

// ── G8 · stat cards ──────────────────────────────────────────────────────────
// One card per line: "702 ms | TTFT, GLM-5 at 20k tokens (was 2,334)"
function stats(src) {
	const cards = src
		.split('\n')
		.map((l) => l.trim())
		.filter(Boolean)
		.map((l) => {
			const [value, ...rest] = l.split('|');
			return `<div class="stat"><span class="stat-value">${inline(value.trim())}</span><span class="stat-label">${inline(rest.join('|').trim())}</span></div>`;
		});
	return `<div class="stats" style="--n:${Math.min(cards.length, 3)}">${cards.join('')}</div>`;
}

// ── V2 · step-through diagram ────────────────────────────────────────────────
// {"title": "...", "nodes": ["Client", "History", "Worker"], "steps": [{"node": 0, "text": "..."}]}
function steps(d) {
	const nodes = d.nodes ?? [];
	const list = d.steps ?? [];
	const chain = nodes
		.map((n, i) => `<li class="s-node" data-i="${i}">${inline(n)}</li>`)
		.join('<li class="s-arrow" aria-hidden="true">→</li>');
	const items = list
		.map((s, i) => `<li data-node="${Number(s.node) || 0}"><span class="s-num">${i + 1}</span> ${inline(s.text)}</li>`)
		.join('');
	return `<figure class="widget w-steps" data-widget="steps">${title(d.title)}<ol class="s-chain">${chain}</ol><ol class="s-list">${items}</ol></figure>`;
}

// ── V3 · animated flow ───────────────────────────────────────────────────────
// {"title": "...", "nodes": ["Prefill", "Router", "Decode"], "flows": [{"from": 2, "to": 0, "label": "KV blocks"}]}
function flow(d) {
	const nodes = d.nodes ?? [];
	const n = Math.max(nodes.length, 1);
	const W = 640;
	const H = 220;
	const gap = W / n;
	const cx = (i) => gap * i + gap / 2;
	const cy = 110;
	const boxW = Math.min(150, gap - 16);
	const boxes = nodes
		.map(
			(name, i) =>
				`<g><rect x="${cx(i) - boxW / 2}" y="${cy - 26}" width="${boxW}" height="52" rx="10" class="f-box"/><text x="${cx(i)}" y="${cy + 5}" text-anchor="middle" class="f-text">${esc(name)}</text></g>`,
		)
		.join('');
	const paths = (d.flows ?? [])
		.map((f, k) => {
			const a = Number(f.from);
			const b = Number(f.to);
			const forward = b > a;
			const x1 = cx(a) + (forward ? boxW / 2 : -boxW / 2) * 0.6;
			const x2 = cx(b) + (forward ? -boxW / 2 : boxW / 2) * 0.6;
			const lift = forward ? -62 - k * 6 : 62 + k * 6;
			const y1 = cy + (forward ? -26 : 26);
			const p = `M${x1},${y1} C${x1},${cy + lift} ${x2},${cy + lift} ${x2},${y1}`;
			const mx = (x1 + x2) / 2;
			const my = cy + lift * 0.75;
			const label = f.label ? `<text x="${mx}" y="${my + (forward ? -6 : 14)}" text-anchor="middle" class="f-label">${esc(f.label)}</text>` : '';
			const dur = 1.6 + k * 0.4;
			return `<path d="${p}" class="f-path" marker-end="url(#f-arrow)"/>${label}<circle r="5" class="f-dot" style="offset-path: path('${p}'); animation-duration: ${dur}s"/>`;
		})
		.join('');
	const svg = `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="${esc(d.title || 'Flow diagram')}"><defs><marker id="f-arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" class="f-head"/></marker></defs>${paths}${boxes}</svg>`;
	const caption = (d.flows ?? []).map((f) => `${nodes[f.from]} → ${nodes[f.to]}${f.label ? `: ${f.label}` : ''}`).join('; ');
	return `<figure class="widget w-flow">${title(d.title)}${svg}<figcaption>${inline(d.caption || caption)}</figcaption></figure>`;
}

// ── V4 · calculator ──────────────────────────────────────────────────────────
// {"title": "...", "inputs": [{"id": "events", "label": "Events per turn", "min": 1, "max": 20, "step": 1, "value": 6, "unit": ""}],
//  "outputs": [{"label": "Turns before the limit", "formula": "51200 / events", "decimals": 0, "unit": "turns"}]}
function calc(d) {
	const inputs = d.inputs ?? [];
	const vars = Object.fromEntries(inputs.map((i) => [i.id, Number(i.value)]));
	const ins = inputs
		.map(
			(i) => `<div class="c-in"><label for="calc-${esc(i.id)}">${inline(i.label)}</label>
<div class="c-row"><input id="calc-${esc(i.id)}" type="range" name="${esc(i.id)}" min="${Number(i.min)}" max="${Number(i.max)}" step="${Number(i.step ?? 1)}" value="${Number(i.value)}"><output for="calc-${esc(i.id)}">${formatNumber(Number(i.value), decimalsOf(i.step))}${i.unit ? ` ${esc(i.unit)}` : ''}</output></div></div>`,
		)
		.join('');
	const outs = (d.outputs ?? [])
		.map((o) => {
			let v;
			try {
				v = evaluate(o.formula, vars);
			} catch {
				v = NaN;
			}
			return `<div class="stat c-out"><span class="stat-value">${formatNumber(v, o.decimals ?? 0)}${o.unit ? ` <small>${esc(o.unit)}</small>` : ''}</span><span class="stat-label">${inline(o.label)}</span></div>`;
		})
		.join('');
	const note = d.note ? `<p class="w-note">${inline(d.note)}</p>` : '';
	return `<figure class="widget w-calc" data-widget="calc" data-config="${attrJson(d)}">${title(d.title)}<div class="c-inputs">${ins}</div><div class="stats" style="--n:${Math.min((d.outputs ?? []).length, 3)}">${outs}</div>${note}</figure>`;
}
const decimalsOf = (step) => {
	const s = String(step ?? 1);
	return s.includes('.') ? s.split('.')[1].length : 0;
};

// ── V5 · chart ───────────────────────────────────────────────────────────────
// {"title": "...", "type": "line" | "bar", "x": ["0", "100", "1000"], "series": [{"name": "Recall", "values": [73.6, 52, 27.4]}],
//  "unit": "%", "yLabel": "Recall", "xLabel": "Irrelevant skills installed", "note": "Source: ..."}
function niceMax(v) {
	if (v <= 0) return 1;
	const p = 10 ** Math.floor(Math.log10(v));
	const m = v / p;
	return (m <= 1 ? 1 : m <= 2 ? 2 : m <= 2.5 ? 2.5 : m <= 5 ? 5 : 10) * p;
}
function chart(d) {
	const xs = d.x ?? [];
	const series = d.series ?? [];
	const W = 640;
	const H = 300;
	const L = 56;
	const R = 16;
	const T = 16;
	const B = 48;
	const all = series.flatMap((s) => s.values ?? []).filter((v) => Number.isFinite(v));
	const minV = Math.min(0, ...all);
	const maxV = niceMax(Math.max(...all, 0));
	const y = (v) => T + (H - T - B) * (1 - (v - minV) / (maxV - minV || 1));
	const unit = d.unit ?? '';
	const ticks = [0, 0.25, 0.5, 0.75, 1].map((f) => minV + (maxV - minV) * f);
	const grid = ticks
		.map((t) => `<line x1="${L}" x2="${W - R}" y1="${y(t)}" y2="${y(t)}" class="ch-grid"/><text x="${L - 8}" y="${y(t) + 4}" text-anchor="end" class="ch-tick">${formatNumber(t, Number.isInteger(t) ? 0 : 1)}${esc(unit)}</text>`)
		.join('');
	const plotW = W - L - R;
	let marks = '';
	let xlabels = '';
	if (d.type === 'bar') {
		const group = plotW / Math.max(xs.length, 1);
		const bw = Math.min(48, (group - 12) / Math.max(series.length, 1));
		xs.forEach((label, i) => {
			const gx = L + group * i + group / 2;
			series.forEach((s, k) => {
				const v = s.values?.[i];
				if (!Number.isFinite(v)) return;
				const x = gx - (bw * series.length) / 2 + bw * k;
				marks += `<rect x="${x}" y="${Math.min(y(v), y(0))}" width="${bw - 3}" height="${Math.abs(y(0) - y(v))}" rx="3" class="ch-s${k}"><title>${esc(s.name)}, ${esc(label)}: ${formatNumber(v, Number.isInteger(v) ? 0 : 1)}${esc(unit)}</title></rect>`;
			});
			xlabels += `<text x="${gx}" y="${H - B + 20}" text-anchor="middle" class="ch-tick">${esc(label)}</text>`;
		});
	} else {
		const x = (i) => L + (xs.length > 1 ? (plotW * i) / (xs.length - 1) : plotW / 2);
		series.forEach((s, k) => {
			const pts = (s.values ?? []).map((v, i) => (Number.isFinite(v) ? [x(i), y(v), v, i] : null)).filter(Boolean);
			marks += `<polyline points="${pts.map((p) => `${p[0]},${p[1]}`).join(' ')}" class="ch-line ch-s${k}"/>`;
			marks += pts
				.map((p) => `<circle cx="${p[0]}" cy="${p[1]}" r="5" class="ch-dot ch-s${k}" tabindex="0"><title>${esc(s.name)}, ${esc(xs[p[3]])}: ${formatNumber(p[2], Number.isInteger(p[2]) ? 0 : 1)}${esc(unit)}</title></circle>`)
				.join('');
			const last = pts[pts.length - 1];
			if (last && series.length > 1) marks += `<text x="${last[0] - 6}" y="${last[1] - 10}" text-anchor="end" class="ch-name ch-t${k}">${esc(s.name)}</text>`;
		});
		xlabels = xs.map((label, i) => `<text x="${x(i)}" y="${H - B + 20}" text-anchor="middle" class="ch-tick">${esc(label)}</text>`).join('');
	}
	const axisLabels =
		(d.xLabel ? `<text x="${L + plotW / 2}" y="${H - 6}" text-anchor="middle" class="ch-axis">${esc(d.xLabel)}</text>` : '') +
		(d.yLabel ? `<text x="14" y="${T + (H - T - B) / 2}" text-anchor="middle" transform="rotate(-90 14 ${T + (H - T - B) / 2})" class="ch-axis">${esc(d.yLabel)}</text>` : '');
	const legend =
		series.length > 1 && d.type === 'bar'
			? `<p class="ch-legend">${series.map((s, k) => `<span><i class="ch-s${k}"></i>${esc(s.name)}</span>`).join('')}</p>`
			: '';
	const table = `<table class="sr-only"><thead><tr><th>${esc(d.xLabel || '')}</th>${series.map((s) => `<th>${esc(s.name)}</th>`).join('')}</tr></thead><tbody>${xs
		.map((label, i) => `<tr><td>${esc(label)}</td>${series.map((s) => `<td>${s.values?.[i] ?? ''}${esc(unit)}</td>`).join('')}</tr>`)
		.join('')}</tbody></table>`;
	const note = d.note ? `<figcaption>${inline(d.note)}</figcaption>` : '';
	return `<figure class="widget w-chart">${title(d.title)}<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="${esc(d.title || 'Chart')}">${grid}${marks}${xlabels}${axisLabels}</svg>${legend}${table}${note}</figure>`;
}

// ── V6 · before/after ────────────────────────────────────────────────────────
// {"title": "...", "options": [{"label": "Without a key", "text": "..."}, {"label": "With a key", "text": "..."}]}
function compare(d) {
	const opts = d.options ?? [];
	const panels = opts
		.map(
			(o, i) =>
				`<div class="cmp-panel" data-i="${i}"><p class="cmp-label">${inline(o.label)}</p>${String(o.text ?? '')
					.split(/\n{2,}/)
					.map((para) => `<p>${inline(para).replace(/\n/g, '<br>')}</p>`)
					.join('')}</div>`,
		)
		.join('');
	return `<figure class="widget w-compare" data-widget="compare">${title(d.title)}<div class="cmp-panels">${panels}</div></figure>`;
}

// ── V7 · answer timer ────────────────────────────────────────────────────────
// {"minutes": 10, "label": "Answer out loud before you read on"}
function timer(d) {
	const min = Number(d.minutes) || 10;
	return `<div class="widget w-timer" data-widget="timer" data-minutes="${min}"><span class="t-label">${inline(d.label || 'Answer out loud before you read on')}</span><span class="t-time" aria-live="polite">${min}:00</span></div>`;
}

// ── V8 · terminal ────────────────────────────────────────────────────────────
// Plain text: lines starting with "$ " are commands, the rest is output.
function terminal(src) {
	const lines = src.replace(/\n+$/, '').split('\n');
	const html = lines
		.map((l) => (l.startsWith('$ ') ? `<span class="tm-cmd"><span class="tm-prompt">$</span> ${esc(l.slice(2))}</span>` : `<span class="tm-out">${esc(l)}</span>`))
		.join('\n');
	return `<figure class="widget w-terminal" data-widget="terminal"><div class="tm-bar"><span class="tm-dots" aria-hidden="true"><i></i><i></i><i></i></span><span class="tm-name">terminal</span></div><pre class="tm-body">${html}</pre></figure>`;
}

// ── V9 · training curves ─────────────────────────────────────────────────────
// {"title": "...", "xLabel": "Step", "yLabel": "Loss", "series": [{"name": "Healthy", "points": [[0, 4.2], [500, 2.1]]},
//  {"name": "No warmup", "points": [[0, 4.2], [200, 6.0]], "bug": true}]}
let curveId = 0;
function curve(d) {
	const series = d.series ?? [];
	const pts = series.flatMap((s) => s.points ?? []);
	const W = 640;
	const H = 300;
	const L = 56;
	const R = 16;
	const T = 16;
	const B = 44;
	const maxX = Math.max(...pts.map((p) => p[0]), 1);
	const maxY = niceMax(Math.max(...pts.map((p) => p[1]), 0));
	const x = (v) => L + (W - L - R) * (v / maxX);
	const y = (v) => T + (H - T - B) * (1 - v / maxY);
	const grid = [0, 0.25, 0.5, 0.75, 1]
		.map((f) => `<line x1="${L}" x2="${W - R}" y1="${y(maxY * f)}" y2="${y(maxY * f)}" class="ch-grid"/><text x="${L - 8}" y="${y(maxY * f) + 4}" text-anchor="end" class="ch-tick">${formatNumber(maxY * f, 1)}</text>`)
		.join('');
	const hasBug = series.some((s) => s.bug);
	const id = `curve-${++curveId}`;
	const lines = series
		.map((s, k) => {
			const p = (s.points ?? []).map(([a, b]) => `${x(a)},${y(b)}`).join(' ');
			return `<polyline points="${p}" class="cv-line ${s.bug ? 'cv-bug' : `ch-s${k}`}" pathLength="1"><title>${esc(s.name)}</title></polyline>`;
		})
		.join('');
	const xl = `<text x="${L + (W - L - R) / 2}" y="${H - 6}" text-anchor="middle" class="ch-axis">${esc(d.xLabel || 'Step')}</text><text x="${L}" y="${H - B + 18}" class="ch-tick">0</text><text x="${W - R}" y="${H - B + 18}" text-anchor="end" class="ch-tick">${formatNumber(maxX)}</text>`;
	const legend = `<p class="ch-legend">${series.map((s, k) => `<span class="${s.bug ? 'lg-bug' : ''}"><i class="${s.bug ? 'cv-bug-key' : `ch-s${k}`}"></i>${esc(s.name)}</span>`).join('')}</p>`;
	const toggle = hasBug
		? `<input type="checkbox" id="${id}" class="cv-toggle"><label for="${id}" class="cv-switch">Switch the bug on</label>`
		: '';
	return `<figure class="widget w-curve">${title(d.title)}${toggle}<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="${esc(d.title || 'Training curve')}">${grid}${lines}${xl}</svg>${legend}${d.note ? `<figcaption>${inline(d.note)}</figcaption>` : ''}</figure>`;
}

// ── V10 · self-check ─────────────────────────────────────────────────────────
// {"questions": [{"q": "...", "a": "..."}]}
function quiz(d) {
	const qs = (d.questions ?? [])
		.map((q, i) => `<details class="qz"><summary><span class="qz-n">${i + 1}</span> ${inline(q.q)}</summary><div class="qz-a">${inline(q.a)}</div></details>`)
		.join('');
	return `<section class="widget w-quiz" aria-label="Self-check"><p class="w-title">${inline(d.title || 'Check yourself')}</p>${qs}</section>`;
}

const JSON_KINDS = { steps, flow, calc, chart, compare, timer, curve, quiz };

/** Render a widget code block. Returns HTML; errors become a visible note instead of breaking the build. */
export function renderWidget(kind, src) {
	try {
		if (kind === 'stats') return stats(src);
		if (kind === 'terminal') return terminal(src);
		const fn = JSON_KINDS[kind];
		if (!fn) throw new Error('unknown widget');
		return fn(parseJson(kind, src));
	} catch (e) {
		return errorBox(kind, e.message);
	}
}
