/**
 * «Коктейль Любви» (g_love): log + DB `gift_love_stock` bilan ekranni sinxronlash.
 */
(function () {
  "use strict";

  var GIFT_TITLE = "Коктейль Любви";
  var STYLE =
    "background:#e91e63;color:#fff;padding:2px 8px;border-radius:4px;font-weight:bold";
  var pendingStock = null;
  var retryTimer = null;

  function isLoveGift(type) {
    if (!type) return false;
    var t = String(type).toLowerCase();
    return t.indexOf("g_love") >= 0 || t.indexOf("love_cocktail") >= 0;
  }

  function isLoveDrink(type) {
    if (!type) return false;
    var t = String(type).toLowerCase();
    return t === "love" || t === "g_love";
  }

  function parseMsg(data) {
    if (data == null) return null;
    if (typeof data === "object") return data;
    if (typeof data !== "string") return null;
    try {
      return JSON.parse(data);
    } catch (e) {
      return null;
    }
  }

  function userLabel(u) {
    if (!u) return "(noma'lum)";
    var id = u.id || u.uid || u.userId || "";
    var name = u.name || u.username || (u.userProfile && u.userProfile.name) || "";
    return name ? name + " [" + id + "]" : "[" + id + "]";
  }

  function logLove(direction, msg) {
    var from =
      direction === "out" ? "(siz / joriy o'yinchi)" : userLabel(msg.user);
    var to =
      direction === "out"
        ? "[" + (msg.receiver_id || "?") + "]"
        : userLabel(msg.receiver);
    console.log(
      "%c" + GIFT_TITLE + "%c " + (direction === "out" ? "→ yuborildi" : "← qabul qilindi"),
      STYLE,
      "color:inherit;font-weight:normal",
      {
        sovga: msg.gift_type || "g_love",
        kimdan: from,
        kimga: to,
        narx: msg.price,
        vaqt: msg.ts ? new Date(msg.ts).toISOString() : new Date().toISOString(),
      }
    );
  }

  function readStockFromItems(items) {
    if (!items || items.g_love == null) return 0;
    return Number(items.g_love) || 0;
  }

  function pushStockToSession(n) {
    var sess = window.session;
    if (!sess || !sess.viewer || !sess.viewer.viewer) return false;
    var items = sess.viewer.viewer.items || (sess.viewer.viewer.items = {});
    if (n <= 0) {
      delete items.g_love;
    } else if (n >= 999) {
      items.g_love = 999;
    } else {
      items.g_love = n;
    }
    if (sess.viewer.isChanged && typeof sess.viewer.isChanged.emit === "function") {
      sess.viewer.isChanged.emit();
    }
    return true;
  }

  function scheduleStockRetry() {
    if (retryTimer != null) return;
    var attempts = 0;
    function tick() {
      if (pendingStock == null) {
        retryTimer = null;
        return;
      }
      if (pushStockToSession(pendingStock)) {
        refreshLoveGiftVisibility(pendingStock);
        retryTimer = null;
        return;
      }
      attempts += 1;
      if (attempts < 60) {
        retryTimer = window.setTimeout(tick, 100);
      } else {
        retryTimer = null;
      }
    }
    tick();
  }

  function applyGiftLoveStock(stock) {
    var n = Math.max(0, Number(stock) || 0);
    pendingStock = n;
    window.__giftLoveStock = n;
    document.body.setAttribute("data-g-love-stock", String(n));

    try {
      if (!pushStockToSession(n)) {
        scheduleStockRetry();
      }
    } catch (e) {
      scheduleStockRetry();
    }

    refreshLoveGiftVisibility(n);
    try {
      var sess = window.session;
      if (sess && sess.viewer && sess.viewer.isChanged && typeof sess.viewer.isChanged.emit === "function") {
        sess.viewer.isChanged.emit();
      }
    } catch (e2) {}
    console.info("[g_love] zaxira (DB):", n);
  }

  function isLoveGiftNode(el) {
    var blob = (el.innerHTML || "") + (el.getAttribute("title") || "") + (el.className || "");
    if (blob.indexOf("g_love") >= 0 || blob.indexOf("s_love") >= 0) return true;
    var t = (el.getAttribute("title") || "").toLowerCase();
    return t.indexOf("любви") >= 0 || t.indexOf("love cocktail") >= 0;
  }

  function refreshLoveGiftVisibility(stock) {
    var hide = stock <= 0;
    var nodes = document.querySelectorAll(".send-gift .item");
    for (var i = 0; i < nodes.length; i++) {
      var el = nodes[i];
      if (!isLoveGiftNode(el)) continue;
      el.style.display = hide ? "none" : "";
    }
  }

  function observeGiftPanel() {
    if (typeof MutationObserver === "undefined") return;
    var obs = new MutationObserver(function () {
      var stock = Number(document.body.getAttribute("data-g-love-stock") || "0");
      refreshLoveGiftVisibility(stock);
      if (pendingStock != null) {
        pushStockToSession(pendingStock);
      }
    });
    obs.observe(document.body, { childList: true, subtree: true });
  }

  function handleServerMessage(msg) {
    if (!msg || typeof msg !== "object") return;

    if (msg.type === "gift_love_stock") {
      applyGiftLoveStock(msg.stock);
      return;
    }

    if (msg.type === "items_get" && msg.items) {
      var stock =
        msg.gift_love_stock != null
          ? Number(msg.gift_love_stock) || 0
          : "g_love" in msg.items
            ? readStockFromItems(msg.items)
            : 0;
      applyGiftLoveStock(stock);
      return;
    }

    if (msg.type === "login" && msg.gift_love_stock != null) {
      applyGiftLoveStock(msg.gift_love_stock);
    }
  }

  function inspect(msg, direction) {
    if (!msg || typeof msg !== "object") return;
    if (direction === "in") {
      handleServerMessage(msg);
    }
    if (msg.type === "game_gift" && isLoveGift(msg.gift_type)) {
      logLove(direction, Object.assign({ sovga: msg.gift_type }, msg));
      return;
    }
    if (msg.type === "game_drink" && isLoveDrink(msg.drink_type)) {
      logLove(
        direction,
        Object.assign({ sovga: "love (game_drink)", drink_type: msg.drink_type }, msg)
      );
    }
  }

  if (!window.WebSocket) return;

  var NativeWebSocket = window.WebSocket;

  function PatchedWebSocket(url, protocols) {
    var ws =
      protocols !== undefined
        ? new NativeWebSocket(url, protocols)
        : new NativeWebSocket(url);
    ws.addEventListener("message", function (ev) {
      inspect(parseMsg(ev.data), "in");
    });
    var nativeSend = ws.send.bind(ws);
    ws.send = function (data) {
      inspect(parseMsg(data), "out");
      return nativeSend(data);
    };
    return ws;
  }

  PatchedWebSocket.prototype = NativeWebSocket.prototype;
  PatchedWebSocket.CONNECTING = NativeWebSocket.CONNECTING;
  PatchedWebSocket.OPEN = NativeWebSocket.OPEN;
  PatchedWebSocket.CLOSING = NativeWebSocket.CLOSING;
  PatchedWebSocket.CLOSED = NativeWebSocket.CLOSED;

  window.WebSocket = PatchedWebSocket;
  window.__giftLoveStock = 0;
  observeGiftPanel();
  document.body.setAttribute("data-g-love-stock", "0");
  console.info("[g_love_console] DB zaxirasi bilan sinxron — " + GIFT_TITLE);
})();
