// A tiny, safe arithmetic evaluator for calculator widgets.
// Supports numbers, variables, + - * / ^ %, parentheses and a few functions. No other code can run.

const FUNCS = {
	min: Math.min,
	max: Math.max,
	floor: Math.floor,
	ceil: Math.ceil,
	round: Math.round,
	abs: Math.abs,
	sqrt: Math.sqrt,
	log: Math.log,
	log2: Math.log2,
	log10: Math.log10,
	exp: Math.exp,
};

function tokenize(src) {
	const tokens = [];
	const re = /\s*(\d+(?:\.\d+)?(?:e[+-]?\d+)?|[A-Za-z_][A-Za-z0-9_]*|[-+*/^%(),])/gy;
	let m;
	let pos = 0;
	while (pos < src.length) {
		re.lastIndex = pos;
		m = re.exec(src);
		if (!m) {
			if (/^\s*$/.test(src.slice(pos))) break;
			throw new Error(`Bad formula near "${src.slice(pos, pos + 10)}"`);
		}
		tokens.push(m[1]);
		pos = re.lastIndex;
	}
	return tokens;
}

/** Evaluate `formula` with `vars` (an object of numbers). Throws on anything it doesn't understand. */
export function evaluate(formula, vars = {}) {
	const t = tokenize(String(formula));
	let i = 0;
	const peek = () => t[i];
	const next = () => t[i++];
	const expect = (s) => {
		if (next() !== s) throw new Error(`Expected "${s}"`);
	};

	function primary() {
		const tok = next();
		if (tok === undefined) throw new Error('Unexpected end of formula');
		if (tok === '(') {
			const v = expr();
			expect(')');
			return v;
		}
		if (tok === '-') return -primary();
		if (tok === '+') return primary();
		if (/^\d/.test(tok)) return Number(tok);
		if (/^[A-Za-z_]/.test(tok)) {
			if (peek() === '(') {
				const fn = FUNCS[tok];
				if (!fn) throw new Error(`Unknown function ${tok}`);
				next();
				const args = [];
				if (peek() !== ')') {
					args.push(expr());
					while (peek() === ',') {
						next();
						args.push(expr());
					}
				}
				expect(')');
				return fn(...args);
			}
			if (!(tok in vars)) throw new Error(`Unknown value ${tok}`);
			return Number(vars[tok]);
		}
		throw new Error(`Unexpected "${tok}"`);
	}
	function power() {
		const base = primary();
		if (peek() === '^') {
			next();
			return base ** power();
		}
		return base;
	}
	function term() {
		let v = power();
		while (peek() === '*' || peek() === '/' || peek() === '%') {
			const op = next();
			const r = power();
			v = op === '*' ? v * r : op === '/' ? v / r : v % r;
		}
		return v;
	}
	function expr() {
		let v = term();
		while (peek() === '+' || peek() === '-') {
			const op = next();
			const r = term();
			v = op === '+' ? v + r : v - r;
		}
		return v;
	}
	const v = expr();
	if (i !== t.length) throw new Error(`Unexpected "${t[i]}"`);
	return v;
}

/** Format a number for display: fixed decimals, thousands separators. */
export function formatNumber(n, decimals = 0) {
	if (!Number.isFinite(n)) return '—';
	return n.toLocaleString('en-GB', { minimumFractionDigits: decimals, maximumFractionDigits: decimals });
}
