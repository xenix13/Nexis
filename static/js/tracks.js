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
    const pick = same.find((s) => s.forced === !!trackPrefs.sub_forced && s.overlay)
      || same.find((s) => s.forced === !!trackPrefs.sub_forced) || same[0];
    if (pick) return pick;
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
