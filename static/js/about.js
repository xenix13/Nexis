// « À propos » : version, sources des données (attribution exigée par TMDB et JustWatch) et licences des
// composants tiers fournis avec Noxa.
import { h, modal } from './ui.js';
import { tr } from './i18n.js';

const link = (href, text) => h('a', { href, target: '_blank', rel: 'noopener noreferrer' }, text);

// Mention imposée par les conditions de l'API TMDB, reprise sous les catalogues (Découvrir)
export const dataCredit = () => h('p.muted.small.data-credit',
  tr('Données et images : '), link('https://www.themoviedb.org/', 'TMDB'),
  tr('. Ce produit utilise l’API TMDB mais n’est ni approuvé ni certifié par TMDB. Disponibilités sur les plateformes : '),
  link('https://www.justwatch.com/', 'JustWatch'), '.');

const COMPONENTS = [
  ['hls.js 1.5.15', 'Apache 2.0', 'https://github.com/video-dev/hls.js'],
  ['JASSUB / libass', tr('MIT (libass : ISC ; FriBidi : LGPL 2.1 ; FreeType : FTL ; HarfBuzz : MIT)'), 'https://github.com/ThaUnknown/jassub'],
  ['libpgs', 'MIT', 'https://github.com/Arcus92/libpgs-js'],
  ['qrcode-generator 2.0.4', 'MIT', 'https://github.com/kazuhikoarase/qrcode-generator'],
  ['Inter, Outfit, Liberation Sans', 'SIL Open Font License 1.1', 'https://openfontlicense.org/'],
  ['FFmpeg', tr('LGPL / GPL (installé séparément sur le serveur)'), 'https://ffmpeg.org/legal.html'],
];

export function aboutDialog(version) {
  return modal(tr('À propos de Noxa'), h('div.about',
    h('p', h('strong', `Noxa ${version || ''}`), ' — ', tr('serveur multimédia auto-hébergé.')),
    h('p.small', tr('Logiciel libre sous licence MIT : '), link('https://github.com/xenix13/Noxa', 'github.com/xenix13/Noxa'), '.'),
    h('h3.sub', tr('Sources des données')),
    dataCredit(),
    h('p.small', tr('Bandes-annonces : YouTube (lecteur intégré, chargé seulement à la demande).')),
    h('h3.sub', tr('Composants tiers')),
    h('ul.small', COMPONENTS.map(([name, licence, url]) => h('li', link(url, name), ` — ${licence}`))),
    h('p.small.muted', tr('Les textes des licences sont fournis avec Noxa (dossiers static/vendor et static/fonts).')),
    h('h3.sub', tr('Usage')),
    h('p.small', tr('Noxa lit uniquement les fichiers et les sources que vous lui indiquez. Utilisez-le avec des contenus dont vous détenez les droits, dans le cadre privé du foyer.'))),
  [{ label: tr('Fermer'), value: null }]);
}
