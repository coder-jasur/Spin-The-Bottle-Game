/**
 * YouTube iframe localhost / vaqtinchalik tunnel da "unplayable" bo'lmasligi uchun.
 * Klient videoItem.origin = location.origin — YouTube buni ko'p hollarda rad etadi.
 */
(function () {
  var host = location.hostname;
  var needsFix =
    host === "localhost" ||
    host === "127.0.0.1" ||
    host.endsWith(".trycloudflare.com");

  if (!needsFix) return;

  var EMBED_ORIGIN = "https://www.youtube.com";

  function patchYtPlayer() {
    if (!window.YT || !window.YT.Player || window.YT.Player.__originPatched) return false;
    var Orig = window.YT.Player;
    function Patched(el, opts) {
      opts = opts || {};
      opts.playerVars = opts.playerVars || {};
      var o = opts.playerVars.origin || "";
      if (!o || o.indexOf("localhost") >= 0 || o.indexOf("127.0.0.1") >= 0 || o.indexOf("trycloudflare.com") >= 0) {
        opts.playerVars.origin = EMBED_ORIGIN;
      }
      return new Orig(el, opts);
    }
    Patched.prototype = Orig.prototype;
    Patched.__originPatched = true;
    window.YT.Player = Patched;
    console.info("[youtube_embed_fix] origin →", EMBED_ORIGIN, "(was", location.origin + ")");
    return true;
  }

  var tries = 0;
  var timer = setInterval(function () {
    if (patchYtPlayer() || ++tries > 400) clearInterval(timer);
  }, 50);

  if (host === "localhost" || host === "127.0.0.1") {
    console.warn(
      "[youtube_embed_fix] Video (mv) localhost da cheklanishi mumkin. " +
        "Telegram: TELEGRAM_WEBAPP_URL (https tunnel) orqali oching yoki Audio (cz) rejimini ishlating."
    );
  }
})();
