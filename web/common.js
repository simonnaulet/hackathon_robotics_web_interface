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

// Jauge du niveau sonore dans la barre de navigation, bandeau d'alerte gardé jusqu'à l'acquittement (WS /level).
export function showLevel() {
  const nav = document.querySelector('nav');
  nav.insertAdjacentHTML('beforeend',
    '<span class="level"><meter id="level-meter" aria-label="niveau sonore autour du robot"></meter> <span id="level-text">son : …</span></span>');
  nav.insertAdjacentHTML('afterend',
    '<div class="loud" id="loud" role="alert" hidden><span id="loud-text"></span><button id="loud-ok">J\'ai vérifié sur place</button></div>');
  const $ = id => document.getElementById(id);
  let max = null;  // niveau le plus fort depuis la dernière alerte acquittée
  $('loud-ok').onclick = () => { max = null; $('loud').hidden = true; };
  const connect = () => {
    const ws = new WebSocket(`ws://${ROBOT}/level`);
    ws.onmessage = e => {
      const m = JSON.parse(e.data);
      if (m.error) { $('level-text').textContent = `son : ${m.error}`; return; }
      Object.assign($('level-meter'), { min: m.threshold - 50, max: m.threshold + 20,
        low: m.threshold - 10, high: m.threshold, optimum: m.threshold - 50, value: m.db });
      $('level-text').textContent = `${m.db.toFixed(0)} ${m.unit}` + (m.speaking ? ' (robot parle, ignoré)' : '');
      if (!m.loud) return;
      max = Math.max(max ?? -Infinity, m.db);
      $('loud-text').textContent = `Bruit fort autour du robot : jusqu'à ${max.toFixed(0)} ${m.unit} `
        + `(seuil ${m.threshold}). Allez vérifier le niveau sonore sur place.`;
      $('loud').hidden = false;
    };
    ws.onclose = () => { $('level-text').textContent = 'son : relais injoignable'; setTimeout(connect, 3000); };
  };
  connect();
}
