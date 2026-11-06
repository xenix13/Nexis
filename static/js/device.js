// Réglages propres à cet appareil (ce navigateur, cette TV), gardés dans le navigateur et prioritaires sur
// ceux du profil : qualité de lecture, taille des sous-titres (une TV se regarde de plus loin), animations,
// navigation à la télécommande.
const KEY = 'nexis-device';

export function device() {
  try {
    const v = JSON.parse(localStorage.getItem(KEY) || '{}');
    return v && typeof v === 'object' ? v : {};
  } catch { return {}; }
}

export function setDevice(patch) {
  const d = { ...device(), ...patch };
  for (const k of Object.keys(d)) if (d[k] === '' || d[k] == null) delete d[k];
  try { localStorage.setItem(KEY, JSON.stringify(d)); } catch { /* stockage indisponible : réglage pour cette session */ }
  applyDevice();
  return d;
}

// préférences du profil, corrigées par celles de l'appareil
export function deviceOverrides() {
  const d = device();
  const o = {};
  if (d.quality) o.quality = d.quality;
  if (d.sub_size) o.sub_size = Number(d.sub_size);
  return o;
}

export function applyDevice() {
  const d = device();
  const r = document.documentElement;
  r.classList.toggle('reduce-motion', d.motion === 'reduced');
  if (d.remote === 'on') r.classList.add('remote');
  else if (d.remote === 'off') r.classList.remove('remote');
}

// Identifiant de cet appareil (télécommande) : tiré au hasard une fois, gardé dans le navigateur
export function deviceId() {
  try {
    let id = localStorage.getItem('nexis-device-id');
    if (!id) {
      id = Array.from(crypto.getRandomValues(new Uint8Array(12)), (b) => b.toString(16).padStart(2, '0')).join('');
      localStorage.setItem('nexis-device-id', id);
    }
    return id;
  } catch { return 'session' + Math.random().toString(16).slice(2, 14); }
}

// Nom de l'appareil montré dans la télécommande : choisi dans Cet appareil, sinon navigateur et système
export function deviceName() {
  const set = device().name;
  if (set) return set;
  const ua = navigator.userAgent;
  const browser = /edg\//i.test(ua) ? 'Edge' : /firefox|fxios/i.test(ua) ? 'Firefox' : /chrome|crios/i.test(ua) ? 'Chrome'
    : /safari/i.test(ua) ? 'Safari' : 'Navigateur';
  const os = /android/i.test(ua) ? (/mobile/i.test(ua) ? 'Android' : 'Android (tablette)') : /iphone/i.test(ua) ? 'iPhone'
    : /ipad/i.test(ua) || (navigator.platform === 'MacIntel' && navigator.maxTouchPoints > 1) ? 'iPad'
      : /windows/i.test(ua) ? 'Windows' : /mac os/i.test(ua) ? 'Mac' : /linux|cros/i.test(ua) ? 'Linux' : '';
  return os ? `${browser} · ${os}` : browser;
}
