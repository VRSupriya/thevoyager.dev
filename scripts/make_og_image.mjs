// Builds public/og-default.png (1200x630), the preview card shown when a page is shared.
// Run: node scripts/make_og_image.mjs
import sharp from 'sharp';

const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="630" viewBox="0 0 1200 630">
  <defs>
    <radialGradient id="glow" cx="82%" cy="12%" r="60%">
      <stop offset="0" stop-color="#12313a"/><stop offset="1" stop-color="#0b1020"/>
    </radialGradient>
  </defs>
  <rect width="1200" height="630" fill="url(#glow)"/>
  <g fill="#dfe4f0" opacity="0.55">
    <circle cx="930" cy="92" r="2.4"/><circle cx="1010" cy="170" r="1.8"/><circle cx="1110" cy="86" r="2.2"/>
    <circle cx="1070" cy="250" r="1.6"/><circle cx="880" cy="210" r="1.6"/><circle cx="1150" cy="190" r="1.4"/>
    <circle cx="980" cy="40" r="1.4"/><circle cx="800" cy="70" r="1.6"/>
  </g>
  <circle cx="1010" cy="470" r="150" fill="none" stroke="#6fd3cf" stroke-width="3" opacity="0.5"/>
  <circle cx="1010" cy="470" r="96" fill="none" stroke="#6fd3cf" stroke-width="2" opacity="0.3"/>
  <circle cx="1118" cy="372" r="11" fill="#6fd3cf"/>
  <rect x="80" y="236" width="84" height="6" rx="3" fill="#6fd3cf"/>
  <text x="80" y="340" font-family="DejaVu Sans, sans-serif" font-weight="700" font-size="84" fill="#dfe4f0">The Voyager</text>
  <text x="80" y="408" font-family="DejaVu Sans, sans-serif" font-size="34" fill="#9aa4ba">Exploring the frontier, building what's next</text>
  <text x="80" y="548" font-family="DejaVu Sans, sans-serif" font-size="28" fill="#6fd3cf">Daily AI brief · Deep dives · Builds</text>
  <text x="80" y="590" font-family="DejaVu Sans, sans-serif" font-size="24" fill="#9aa4ba">thevoyager.dev</text>
</svg>`;

await sharp(Buffer.from(svg)).png().toFile('public/og-default.png');
console.log('wrote public/og-default.png');
