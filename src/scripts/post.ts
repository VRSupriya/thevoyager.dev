// Small enhancements for every post: reading progress bar (G10) and copy buttons on code blocks (G6).

const bar = document.querySelector<HTMLElement>('.progress');
const article = document.querySelector<HTMLElement>('article.post');
if (bar && article) {
	let ticking = false;
	const update = () => {
		const rect = article.getBoundingClientRect();
		const total = rect.height - window.innerHeight;
		const p = total > 0 ? Math.min(1, Math.max(0, -rect.top / total)) : 1;
		bar.style.setProperty('--p', p.toFixed(3));
		ticking = false;
	};
	window.addEventListener(
		'scroll',
		() => {
			if (!ticking) {
				ticking = true;
				requestAnimationFrame(update);
			}
		},
		{ passive: true },
	);
	update();
}

for (const pre of document.querySelectorAll<HTMLPreElement>('.content pre.astro-code')) {
	const btn = document.createElement('button');
	btn.type = 'button';
	btn.className = 'copy-btn';
	btn.textContent = 'Copy';
	btn.setAttribute('aria-label', 'Copy code');
	btn.addEventListener('click', async () => {
		const code = pre.querySelector('code')?.innerText ?? pre.innerText;
		try {
			await navigator.clipboard.writeText(code.replace(/\n$/, ''));
			btn.textContent = 'Copied';
		} catch {
			btn.textContent = 'Press Ctrl+C';
		}
		setTimeout(() => (btn.textContent = 'Copy'), 1800);
	});
	pre.appendChild(btn);
}
