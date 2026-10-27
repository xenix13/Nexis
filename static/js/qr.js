import { tr } from './i18n.js';
// QR code dessiné en SVG (bibliothèque qrcode-generator, MIT, fournie dans static/vendor/qrcode)
export async function qrSvg(text, size = 200) {
  const { default: qrcode } = await import('/vendor/qrcode/qrcode.mjs');
  const qr = qrcode(0, 'M');
  qr.addData(text);
  qr.make();
  const n = qr.getModuleCount();
  const ns = 'http://www.w3.org/2000/svg';
  const svg = document.createElementNS(ns, 'svg');
  svg.setAttribute('viewBox', `-2 -2 ${n + 4} ${n + 4}`);
  svg.setAttribute('width', size);
  svg.setAttribute('height', size);
  svg.setAttribute('role', 'img');
  svg.setAttribute('aria-label', tr('QR code à scanner avec l’application d’authentification'));
  svg.classList.add('qr');
  const bg = document.createElementNS(ns, 'rect');
  Object.entries({ x: -2, y: -2, width: n + 4, height: n + 4, fill: '#fff' }).forEach(([k, v]) => bg.setAttribute(k, v));
  svg.append(bg);
  let d = '';
  for (let y = 0; y < n; y += 1) for (let x = 0; x < n; x += 1) if (qr.isDark(y, x)) d += `M${x} ${y}h1v1h-1z`;
  const path = document.createElementNS(ns, 'path');
  path.setAttribute('d', d);
  path.setAttribute('fill', '#000');
  svg.append(path);
  return svg;
}
