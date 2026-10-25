// Profil du navigateur, comme browserDeviceProfile de Jellyfin : ce que cet appareil sait décoder, pour ne
// convertir que le nécessaire (lecture et téléchargements).
let capsCache = null;
export function browserCaps() {
  if (capsCache) return capsCache;
  const MS = window.ManagedMediaSource || window.MediaSource;
  const ok = (type) => {
    try { return !!(MS && MS.isTypeSupported(type)); } catch { return false; }
  };
  const video = ['h264'];
  if (ok('video/mp4; codecs="hvc1.1.6.L120.90"')) video.push('hevc');
  if (ok('video/mp4; codecs="hvc1.2.4.L120.90"')) video.push('hevc10');
  if (ok('video/mp4; codecs="av01.0.08M.08"')) video.push('av1');
  if (ok('video/mp4; codecs="av01.0.08M.10"')) video.push('av1_10');
  if (ok('video/mp4; codecs="vp09.00.40.08"')) video.push('vp9');
  const audio = ['aac', 'mp3'];
  if (ok('audio/mp4; codecs="ac-3"')) audio.push('ac3');
  if (ok('audio/mp4; codecs="ec-3"')) audio.push('eac3');
  if (ok('audio/mp4; codecs="flac"')) audio.push('flac');
  if (ok('audio/mp4; codecs="opus"')) audio.push('opus');
  const pgs = typeof Worker === 'function';
  capsCache = { video, audio, containers: ['mp4', 'mov'], pgs };
  return capsCache;
}
