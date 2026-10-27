// Langue de l'interface. Le texte du code est en français et sert de clé : tr('Paramètres') renvoie la
// traduction de la langue choisie (static/js/i18n/<langue>.js), ou le français s'il n'y en a pas.
// La langue vient du compte (choisie dans Paramètres → Mon compte), gardée sur l'appareil pour s'afficher
// dès l'ouverture ; avant la connexion, c'est la langue par défaut du serveur (choisie par un administrateur).
import en from './i18n/en.js';

export const LANGUAGES = [{ id: 'fr', name: 'Français' }, { id: 'en', name: 'English' }];
const DICTS = { en };
const STORE = 'nexis.lang';

function initial() {
  let saved = null;
  try { saved = localStorage.getItem(STORE); } catch { /* stockage indisponible */ }
  const fallback = document.documentElement.dataset.defaultLang;
  return [saved, fallback].find((l) => LANGUAGES.some((x) => x.id === l)) || 'fr';
}

export let lang = initial();
document.documentElement.lang = lang;

// Format des dates et des nombres (toLocaleDateString, Intl…)
export const locale = () => (lang === 'en' ? 'en-US' : 'fr-FR');

// Un même mot français peut demander deux traductions : la clé porte alors un contexte, « bouton|Lecture »
// (affiché « Lecture » en français, « Play » en anglais).
export function tr(text, vars) {
  const dict = DICTS[lang];
  let s = (dict && dict[text]) || text.slice(text.indexOf('|') + 1);
  if (vars) s = s.replace(/\{(\w+)\}/g, (m, k) => (k in vars ? String(vars[k] ?? '') : m));
  return s;
}

// Singulier ou pluriel selon la langue (« 0 épisode » en français, « 0 episodes » en anglais).
// Les deux formes contiennent {n}.
export function plural(n, one, other, vars) {
  const single = lang === 'fr' ? Math.abs(n) < 2 : n === 1;
  return tr(single ? one : other, { n, ...vars });
}

// Langue du compte connu : si elle change l'interface, on la garde et la page est rechargée (le texte
// calculé au chargement des modules doit être refait). Renvoie true si un rechargement est lancé.
export function useLanguage(l) {
  if (!LANGUAGES.some((x) => x.id === l)) return false;
  try { localStorage.setItem(STORE, l); } catch { /* stockage indisponible */ }
  if (l === lang) return false;
  location.reload();
  return true;
}
