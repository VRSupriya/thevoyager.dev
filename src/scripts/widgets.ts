// Brings the interactive widgets to life (V2 steps, V4 calc, V6 compare, V7 timer, V8 terminal).
// Every widget is already complete without this script; this only adds the controls.
import { evaluate, formatNumber } from '../lib/expr.mjs';

const reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;

function button(label: string, cls = 'btn btn-quiet') {
	const b = document.createElement('button');
	b.type = 'button';
	b.className = cls;
	b.textContent = label;
	return b;
}

// V2 · step-through: one step at a time, the active node highlighted
for (const fig of document.querySelectorAll<HTMLElement>('[data-widget="steps"]')) {
	const items = [...fig.querySelectorAll<HTMLLIElement>('.s-list li')];
	const nodes = [...fig.querySelectorAll<HTMLLIElement>('.s-node')];
	if (!items.length) continue;
	fig.classList.add('js');
	let i = 0;
	const back = button('Back');
	const next = button('Next', 'btn');
	const pos = document.createElement('span');
	pos.className = 's-pos';
	pos.setAttribute('aria-live', 'polite');
	const controls = document.createElement('div');
	controls.className = 's-controls';
	controls.append(back, next, pos);
	fig.append(controls);
	const show = () => {
		const at = Number(items[i].dataset.node ?? 0);
		const visited = new Set(items.slice(0, i).map((li) => Number(li.dataset.node ?? 0)));
		items.forEach((li, k) => li.classList.toggle('current', k === i));
		nodes.forEach((n, k) => {
			n.classList.toggle('active', k === at);
			n.classList.toggle('done', k !== at && visited.has(k));
		});
		back.disabled = i === 0;
		next.textContent = i === items.length - 1 ? 'Start again' : 'Next';
		pos.textContent = `Step ${i + 1} of ${items.length}`;
	};
	back.addEventListener('click', () => {
		i = Math.max(0, i - 1);
		show();
	});
	next.addEventListener('click', () => {
		i = i === items.length - 1 ? 0 : i + 1;
		show();
	});
	show();
}

// V4 · calculator: sliders recompute the outputs
for (const fig of document.querySelectorAll<HTMLElement>('[data-widget="calc"]')) {
	let cfg: any;
	try {
		cfg = JSON.parse(fig.dataset.config ?? '{}');
	} catch {
		continue;
	}
	const inputs = [...fig.querySelectorAll<HTMLInputElement>('input[type="range"]')];
	const outs = [...fig.querySelectorAll<HTMLElement>('.c-out .stat-value')];
	const update = () => {
		const vars: Record<string, number> = {};
		inputs.forEach((inp, k) => {
			vars[inp.name] = Number(inp.value);
			const spec = cfg.inputs?.[k] ?? {};
			const step = String(spec.step ?? 1);
			const dec = step.includes('.') ? step.split('.')[1].length : 0;
			const o = inp.parentElement?.querySelector('output');
			if (o) o.textContent = `${formatNumber(Number(inp.value), dec)}${spec.unit ? ` ${spec.unit}` : ''}`;
		});
		(cfg.outputs ?? []).forEach((o: any, k: number) => {
			let v = NaN;
			try {
				v = evaluate(o.formula, vars);
			} catch {}
			if (outs[k]) outs[k].innerHTML = `${formatNumber(v, o.decimals ?? 0)}${o.unit ? ` <small>${o.unit}</small>` : ''}`;
		});
	};
	inputs.forEach((inp) => inp.addEventListener('input', update));
	update();
}

// V6 · before/after toggle
for (const fig of document.querySelectorAll<HTMLElement>('[data-widget="compare"]')) {
	const panels = [...fig.querySelectorAll<HTMLElement>('.cmp-panel')];
	if (panels.length < 2) continue;
	fig.classList.add('js');
	const bar = document.createElement('div');
	bar.className = 'cmp-tabs';
	bar.setAttribute('role', 'group');
	bar.setAttribute('aria-label', 'Compare');
	const buttons = panels.map((p, k) => {
		const b = button(p.querySelector('.cmp-label')?.textContent ?? `Option ${k + 1}`, '');
		b.addEventListener('click', () => select(k));
		bar.append(b);
		return b;
	});
	const select = (k: number) => {
		panels.forEach((p, j) => p.classList.toggle('current', j === k));
		buttons.forEach((b, j) => b.setAttribute('aria-pressed', String(j === k)));
	};
	fig.querySelector('.cmp-panels')?.before(bar);
	select(0);
}

// V7 · answer timer
for (const box of document.querySelectorAll<HTMLElement>('[data-widget="timer"]')) {
	const total = Number(box.dataset.minutes ?? 10) * 60;
	const out = box.querySelector<HTMLElement>('.t-time');
	if (!out) continue;
	let left = total;
	let handle: number | undefined;
	const start = button('Start', 'btn');
	const reset = button('Reset');
	box.append(start, reset);
	const render = () => {
		const m = Math.floor(left / 60);
		const s = left % 60;
		out.textContent = `${m}:${String(s).padStart(2, '0')}`;
	};
	const stop = () => {
		if (handle) clearInterval(handle);
		handle = undefined;
		start.textContent = left === 0 ? 'Done' : 'Start';
	};
	start.addEventListener('click', () => {
		if (handle) return stop();
		if (left === 0) left = total;
		start.textContent = 'Pause';
		handle = window.setInterval(() => {
			left = Math.max(0, left - 1);
			render();
			if (left === 0) {
				stop();
				out.textContent = "Time's up";
			}
		}, 1000);
	});
	reset.addEventListener('click', () => {
		stop();
		left = total;
		start.textContent = 'Start';
		render();
	});
}

// V8 · terminal: type the session out once it scrolls into view, with a replay button
for (const fig of document.querySelectorAll<HTMLElement>('[data-widget="terminal"]')) {
	const body = fig.querySelector<HTMLElement>('.tm-body');
	if (!body || reduceMotion) continue;
	const original = body.innerHTML;
	const lines = [...body.children].map((el) => ({ cls: el.className, html: el.innerHTML, text: el.textContent ?? '' }));
	const replay = button('Replay', 'tm-replay');
	fig.querySelector('.tm-bar')?.append(replay);
	let running = false;
	const play = async () => {
		if (running) return;
		running = true;
		body.innerHTML = '';
		for (const line of lines) {
			const span = document.createElement('span');
			span.className = line.cls;
			body.append(span);
			if (line.cls === 'tm-cmd') {
				const cmd = line.text.replace(/^\$\s?/, '');
				span.innerHTML = '<span class="tm-prompt">$</span> ';
				const t = document.createElement('span');
				t.className = 'tm-cursor';
				span.append(t);
				for (const ch of cmd) {
					t.textContent += ch;
					await new Promise((r) => setTimeout(r, 28));
				}
				t.classList.remove('tm-cursor');
				await new Promise((r) => setTimeout(r, 350));
			} else {
				span.innerHTML = line.html;
				await new Promise((r) => setTimeout(r, 90));
			}
			body.append('\n');
		}
		body.innerHTML = original;
		running = false;
	};
	replay.addEventListener('click', play);
	const io = new IntersectionObserver((entries) => {
		if (entries.some((e) => e.isIntersecting)) {
			io.disconnect();
			play();
		}
	});
	io.observe(fig);
}
