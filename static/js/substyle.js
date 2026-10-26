// Apparence des sous-titres texte (préférences de chaque utilisateur), appliquée par variables CSS
// au lecteur et à l'aperçu des Paramètres. Les sous-titres ASS rendus par libass gardent leur propre style.
export const SUB_FONTS = {
  sans: ['Standard', 'Inter, system-ui, sans-serif'],
  display: ['Arrondie', 'Outfit, Inter, sans-serif'],
  serif: ['À empattements', 'Georgia, "Times New Roman", serif'],
  mono: ['Machine à écrire', '"SFMono-Regular", Consolas, "Liberation Mono", monospace'],
};
export const SUB_BGS = { box: ['Bandeau semi-transparent', 'rgba(0, 0, 0, 0.55)'], solid: ['Bandeau opaque', 'rgba(0, 0, 0, 0.92)'],
  none: ['Aucun', 'transparent'] };
export const SUB_EDGES = {
  shadow: ['Ombre', '0 1px 2px #000, 0 0 6px rgba(0, 0, 0, 0.7)'],
  outline: ['Contour', '-1.5px -1.5px 0 #000, 1.5px -1.5px 0 #000, -1.5px 1.5px 0 #000, 1.5px 1.5px 0 #000, 0 0 4px #000'],
  none: ['Aucun', 'none'],
};
export const SUB_POSITIONS = { bottom: ['En bas', '9%'], high: ['Un peu plus haut', '16%'] };
export const SUB_COLORS = ['#ffffff', '#fff4b0', '#ffd23f', '#9be7ff', '#b6f5c0'];

export function applySubStyle(el, prefs) {
  const pick = (table, key, fallback) => (table[prefs[key]] || table[fallback])[1];
  el.style.setProperty('--sub-font', pick(SUB_FONTS, 'sub_font', 'sans'));
  el.style.setProperty('--sub-bg', pick(SUB_BGS, 'sub_bg', 'box'));
  el.style.setProperty('--sub-shadow', pick(SUB_EDGES, 'sub_edge', 'shadow'));
  el.style.setProperty('--sub-bottom', pick(SUB_POSITIONS, 'sub_position', 'bottom'));
  el.style.setProperty('--sub-color', prefs.sub_color || '#ffffff');
}
