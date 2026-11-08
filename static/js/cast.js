// Diffuser sur une TV : AirPlay (Safari, vers une Apple TV) et Chromecast (Chrome / Edge, vers un Chromecast ou
// une Google TV). Dans les deux cas c'est la TV qui va chercher la vidéo : le serveur prépare un flux qu'elle
// sait lire, à une adresse utilisable sans la session du navigateur (le temps de la lecture).
import { tr } from './i18n.js';

const SDK = 'https://www.gstatic.com/cv/js/sender/v1/cast_sender.js?loadCastFramework=1';

// ce que lit le récepteur par défaut du Chromecast (HLS en fMP4, H.264 et AAC stéréo)
export const CHROMECAST_CAPS = { video: ['h264'], audio: ['aac', 'mp3'], containers: ['mp4'], native: false };
// Apple TV : HLS natif, H.264 et HEVC, son multicanal Dolby transmis
export const AIRPLAY_CAPS = { video: ['h264', 'hevc', 'hevc10'], audio: ['aac', 'ac3', 'eac3', 'mp3'], containers: ['mp4'],
  native: false, multichannel: true };

// ------------------------------------------------------------ AirPlay
export const airplaySupported = () => !!window.WebKitPlaybackTargetAvailabilityEvent;

// onAvailable(bool) : une Apple TV est-elle à portée ; onWireless(bool) : la vidéo passe-t-elle sur la TV
export function watchAirplay(video, onAvailable, onWireless) {
  if (!airplaySupported()) return () => {};
  const a = (e) => onAvailable(e.availability === 'available');
  const w = () => onWireless(!!video.webkitCurrentPlaybackTargetIsWireless);
  video.addEventListener('webkitplaybacktargetavailabilitychanged', a);
  video.addEventListener('webkitcurrentplaybacktargetiswirelesschanged', w);
  return () => {
    video.removeEventListener('webkitplaybacktargetavailabilitychanged', a);
    video.removeEventListener('webkitcurrentplaybacktargetiswirelesschanged', w);
  };
}

// ------------------------------------------------------------ Chromecast
let sdk = null;
export const chromecastPossible = () => !!window.chrome && !/firefox|fxios/i.test(navigator.userAgent) && window.isSecureContext !== false;

// SDK de Google, chargé à l'ouverture du lecteur dans Chrome / Edge seulement -> cast.framework, ou null
export function loadCastSdk() {
  if (!chromecastPossible()) return Promise.resolve(null);
  if (sdk) return sdk;
  sdk = new Promise((resolve) => {
    const done = (ok) => {
      if (!ok || !window.cast || !window.cast.framework) { resolve(null); return; }
      try {
        window.cast.framework.CastContext.getInstance().setOptions({
          receiverApplicationId: window.chrome.cast.media.DEFAULT_MEDIA_RECEIVER_APP_ID,
          autoJoinPolicy: window.chrome.cast.AutoJoinPolicy.ORIGIN_SCOPED,
        });
        resolve(window.cast.framework);
      } catch { resolve(null); }
    };
    window.__onGCastApiAvailable = done;
    const s = document.createElement('script');
    s.src = SDK;
    s.async = true;
    s.onerror = () => resolve(null);
    document.head.append(s);
    setTimeout(() => resolve(null), 15000);
  });
  return sdk;
}

// Charge le flux préparé par le serveur sur le Chromecast connecté -> RemotePlayer et son contrôleur
export async function castLoad(framework, { url, mode, offset, title, subtitle, image }) {
  const session = framework.CastContext.getInstance().getCurrentSession();
  if (!session) throw new Error(tr('Aucun appareil de diffusion connecté'));
  const media = window.chrome.cast.media;
  const absolute = new URL(url, location.href).href;
  const info = new media.MediaInfo(absolute, mode === 'direct' ? 'video/mp4' : 'application/x-mpegurl');
  if (mode !== 'direct') {
    info.hlsSegmentFormat = media.HlsSegmentFormat.FMP4;
    info.hlsVideoSegmentFormat = media.HlsVideoSegmentFormat.FMP4;
  }
  info.streamType = media.StreamType.BUFFERED;
  const meta = new media.GenericMediaMetadata();
  meta.title = title;
  if (subtitle) meta.subtitle = subtitle;
  if (image) meta.images = [new window.chrome.cast.Image(new URL(image, location.href).href)];
  info.metadata = meta;
  const req = new media.LoadRequest(info);
  req.currentTime = (offset || 0) / 1000;
  req.autoplay = true;
  await session.loadMedia(req);
  return session.getSessionObj().receiver.friendlyName;
}

export function castDeviceName(framework) {
  const s = framework && framework.CastContext.getInstance().getCurrentSession();
  return s ? s.getSessionObj().receiver.friendlyName : '';
}
