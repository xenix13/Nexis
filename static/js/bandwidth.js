// Débit entre cet appareil et le serveur, pour la qualité automatique : quelques mégaoctets téléchargés, et
// la mesure gardée 10 minutes (la lecture suivante démarre sans attendre).
const KEY = 'nexis-bandwidth';
const KEEP = 10 * 60 * 1000;
const MAX_MS = 2500;      // une mesure ne retarde jamais le démarrage de plus de 2,5 s
const MAX_BYTES = 24 << 20;

export function saveBandwidth(kbps) {
  try { sessionStorage.setItem(KEY, JSON.stringify({ kbps, at: Date.now() })); } catch { /* stockage indisponible */ }
}

function cached() {
  try {
    const v = JSON.parse(sessionStorage.getItem(KEY) || 'null');
    return v && Date.now() - v.at < KEEP ? v.kbps : null;
  } catch { return null; }
}

// -> kbit/s, ou 0 si la mesure échoue (le serveur se sert alors de la dernière mesure de cet appareil)
export async function measureBandwidth() {
  const hit = cached();
  if (hit) return hit;
  const ctl = new AbortController();
  let got = 0, t1 = 0;
  const timer = setTimeout(() => ctl.abort(), MAX_MS + 2000);
  try {
    const res = await fetch(`/api/bandwidth?size=${MAX_BYTES}`, { cache: 'no-store', credentials: 'same-origin', signal: ctl.signal });
    if (!res.ok || !res.body) return 0;
    const reader = res.body.getReader();
    t1 = performance.now(); // depuis l'arrivée des en-têtes : le temps de réponse du serveur ne compte pas
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      got += value.byteLength;
      if (performance.now() - t1 > MAX_MS) { ctl.abort(); break; }
    }
  } catch { /* coupé au bout du temps imparti : on garde ce qui est arrivé */ } finally { clearTimeout(timer); }
  const ms = performance.now() - t1;
  if (!t1 || got < 256 * 1024 || ms <= 0) return 0;
  const kbps = Math.round((got * 8) / ms);
  saveBandwidth(kbps);
  return kbps;
}
