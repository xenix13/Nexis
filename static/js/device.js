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
