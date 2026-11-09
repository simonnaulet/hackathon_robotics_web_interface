export const ROBOT = new URLSearchParams(location.search).get('robot') || '10.0.1.41:8081';
export const FRONT = '/aima/hal/sensor/rgb_head_front_center/rgb_image/compressed';

export const videoError = reason =>
  `impossible de récupérer le flux video${reason ? ' : ' + reason : ''}`;

// Affiche un flux du relais dans <img>, `onError(texte|null)` pour l'erreur courante. Renvoie une fonction d'arrêt.
export function playVideo(topic, img, onError) {
  const ws = new WebSocket(`ws://${ROBOT}/video?topic=${encodeURIComponent(topic)}`);
  let stopped = false;
  ws.onmessage = e => {
    if (typeof e.data === 'string') return onError(videoError(JSON.parse(e.data).error));
    URL.revokeObjectURL(img.src);
    img.src = URL.createObjectURL(e.data);
    onError(null);
  };
  ws.onclose = e => stopped || onError(videoError(
    e.code === 1006 ? `relais injoignable (ws://${ROBOT})` : `connexion fermée (code ${e.code})`));
  return () => { stopped = true; ws.close(); img.removeAttribute('src'); };
}
