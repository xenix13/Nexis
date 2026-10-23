// Choix des pistes audio / sous-titres : choix retenu pour le film ou la série, sinon préférences générales.

export function pickAudio(audio, prefs, trackPrefs) {
  if (!audio || !audio.length) return null;
  const want = (trackPrefs && trackPrefs.audio_lang) || prefs.audio_lang;
  return (want && audio.find((a) => a.lang === want)) || audio.find((a) => a.default) || audio[0];
}

export function pickSubtitle(subs, audio, prefs, trackPrefs) {
  if (!subs || !subs.length) return null;
  if (trackPrefs && trackPrefs.sub_lang) {
    if (trackPrefs.sub_lang === 'off') return null;
    const same = subs.filter((s) => s.lang === trackPrefs.sub_lang);
    // « forcés » ne sert que si l'audio est dans la même langue (sinon on n'aurait que les panneaux)
    const forced = !!trackPrefs.sub_forced && !!(audio && audio.lang === trackPrefs.sub_lang);
    const pick = same.find((s) => s.forced === forced && s.overlay)
      || same.find((s) => s.forced === forced) || same[0];
    if (pick) return fixForced(pick, audio, subs);
  }
  if (prefs.sub_mode === 'never') return null;
  const lang = prefs.sub_lang;
  const inLang = subs.filter((s) => !lang || s.lang === lang);
  const best = inLang.find((s) => !s.forced && s.overlay) || inLang.find((s) => !s.forced) || inLang[0];
  if (prefs.sub_mode === 'always') return best || null;
  // « foreign » : sous-titres seulement si l'audio n'est pas dans la langue de l'utilisateur
  if (audio && prefs.audio_lang && audio.lang && audio.lang !== prefs.audio_lang) return best || null;
  return subs.find((s) => s.forced && audio && s.lang === audio.lang) || null;
}

export function trackPayload(audio, sub) {
  return { audio_lang: audio ? audio.lang || '' : undefined, sub_lang: sub ? sub.lang || '' : 'off', sub_forced: !!(sub && sub.forced) };
}

// Pistes « forcées » : elles ne contiennent que les panneaux, inscriptions et chansons traduits, pour qui
// regarde DÉJÀ dans cette langue (VF d'un anime : les panneaux japonais restent à traduire). Avec un autre
// audio, elles semblent vides : on les cache quand une piste complète existe, sauf si l'audio s'y prête.
export function isSignsOnly(s, subs) {
  return !!s.forced && subs.some((x) => !x.forced && x.lang === s.lang);
}

export function menuSubs(subs, audio) {
  return (subs || []).filter((s) => !isSignsOnly(s, subs) || (audio && audio.lang && audio.lang === s.lang));
}

export const SIGNS_HINT = 'panneaux et chansons seulement';

// Une piste forcée choisie avec un audio d'une autre langue -> la piste complète de la même langue
export function fixForced(sub, audio, subs) {
  if (!sub || !isSignsOnly(sub, subs) || (audio && audio.lang === sub.lang)) return sub;
  return subs.find((x) => !x.forced && x.lang === sub.lang && x.overlay) || subs.find((x) => !x.forced && x.lang === sub.lang) || sub;
}
