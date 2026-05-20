/**
 * YouTube iframe API — Telegram WebApp / localhost / tunnel da video ishlashi.
 */
(function () {
  var EMBED_ORIGIN = "https://www.youtube.com";
  window.__SPIN_YT_EMBED_ORIGIN = EMBED_ORIGIN;

  function badOrigin(o) {
    if (!o || typeof o !== "string") return true;
    var lo = o.toLowerCase();
    return (
      lo.indexOf("localhost") >= 0 ||
      lo.indexOf("127.0.0.1") >= 0 ||
      lo.indexOf("trycloudflare.com") >= 0 ||
      lo.indexOf("ngrok") >= 0 ||
      lo.indexOf("file:") >= 0
    );
  }

  function ensurePlayerState() {
    if (!window.YT) return;
    if (!window.YT.PlayerState) {
      window.YT.PlayerState = {
        UNSTARTED: -1,
        ENDED: 0,
        PLAYING: 1,
        PAUSED: 2,
        BUFFERING: 3,
        CUED: 5,
      };
    }
  }

  function patchYtPlayer() {
    if (!window.YT || !window.YT.Player || window.YT.Player.__originPatched) return false;
    ensurePlayerState();
    var Orig = window.YT.Player;
    function Patched(el, opts) {
      opts = opts || {};
      opts.playerVars = opts.playerVars || {};
      if (badOrigin(opts.playerVars.origin)) {
        opts.playerVars.origin = EMBED_ORIGIN;
      }
      delete opts.playerVars.host;
      return new Orig(el, opts);
    }
    Patched.prototype = Orig.prototype;
    try {
      Object.keys(Orig).forEach(function (k) {
        Patched[k] = Orig[k];
      });
    } catch (e) {}
    Patched.__originPatched = true;
    window.YT.Player = Patched;
    console.info("[youtube_embed_fix] YT.Player ready, origin=", EMBED_ORIGIN);
    return true;
  }

  function onApiReady() {
    ensurePlayerState();
    patchYtPlayer();
  }

  var prevReady = window.onYouTubeIframeAPIReady;
  if (!prevReady || !prevReady.__ytFixWrapped) {
    window.onYouTubeIframeAPIReady = function () {
      onApiReady();
      if (typeof prevReady === "function" && !prevReady.__ytFixWrapped) prevReady();
    };
    window.onYouTubeIframeAPIReady.__ytFixWrapped = true;
  }

  if (!document.querySelector('script[src*="youtube.com/iframe_api"]')) {
    var tag = document.createElement("script");
    tag.src = "https://www.youtube.com/iframe_api";
    tag.async = true;
    (document.head || document.documentElement).appendChild(tag);
  }

  var tries = 0;
  var timer = setInterval(function () {
    if (patchYtPlayer() || ++tries > 400) clearInterval(timer);
  }, 50);
})();
