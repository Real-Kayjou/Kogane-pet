/* Chill Pet local companion — intentionally sends no page text, URL, account,
   comments, history, clicks, or media data. Only a tiny playback-state cue. */
const ENDPOINT = 'http://127.0.0.1:8765/v1/event';
let last = '';
function send(event, extra = {}) {
  const key = event + JSON.stringify(extra);
  if (key === last) return;
  last = key;
  fetch(ENDPOINT, {method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({source: 'brave', event, ...extra})}).catch(() => {});
}
function video() { return document.querySelector('video'); }
function bind() {
  const v = video();
  if (!v || v.dataset.chillPetBound) return;
  v.dataset.chillPetBound = '1';
  const music = location.hostname === 'music.youtube.com';
  v.addEventListener('play', () => send(music ? 'youtube_music' : 'youtube_play', {playing: true}));
  v.addEventListener('pause', () => send('youtube_pause', {playing: false}));
  document.addEventListener('fullscreenchange', () => send('youtube_fullscreen', {fullscreen: !!document.fullscreenElement}));
  send(music ? 'youtube_music' : 'youtube_browse', {playing: !v.paused, fullscreen: !!document.fullscreenElement});
}
new MutationObserver(bind).observe(document.documentElement, {childList: true, subtree: true});
bind();
// A Like is only polled after the user selects the visible "Like current
// YouTube video" command in the pet's menu. No automatic liking is performed.
setInterval(() => fetch('http://127.0.0.1:8765/v1/command').then(r => r.json()).then(c => {
  if (!c.like) return;
  const button = [...document.querySelectorAll('like-button-view-model button, ytd-toggle-button-renderer button')]
    .find(b => /like/i.test(b.getAttribute('aria-label') || b.title || ''));
  if (button && button.getAttribute('aria-pressed') !== 'true') { button.click(); send('youtube_liked'); }
}).catch(() => {}), 1100);
