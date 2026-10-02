// Turns ```mermaid code blocks into <pre class="mermaid"> so the diagram can be drawn in
// the browser, and flags the page (hasMermaid) so the Mermaid script loads only where needed.
// Works for both .md and .mdx files.

function walk(node, visit) {
	if (!node.children) return;
	node.children.forEach((child, i) => {
		visit(child, i, node);
		walk(child, visit);
	});
}

export default function remarkMermaid() {
	return (tree, file) => {
		let found = false;
		walk(tree, (node, index, parent) => {
			if (node.type === 'code' && node.lang === 'mermaid') {
				parent.children[index] = {
					type: 'mermaid',
					data: {
						hName: 'pre',
						hProperties: { className: ['mermaid'] },
						hChildren: [{ type: 'text', value: node.value }],
					},
				};
				found = true;
			}
		});
		if (found && file.data.astro?.frontmatter) {
			file.data.astro.frontmatter.hasMermaid = true;
		}
	};
}
