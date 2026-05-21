/**
 * Dinamit: server `game_chat_delete_last` — qurbonning barcha chat SMS qatorlarini DOM dan olib tashlash.
 */
(function () {
  if (window.__dynamiteChatWipeHook) return;
  window.__dynamiteChatWipeHook = true;

  function wipeUserChat(userId) {
    if (!userId) return;
    var root = document.querySelector(".chat__body");
    if (!root) return;
    var id = String(userId);
    var sel =
      '.chat__message[data-user-id="' +
      id.replace(/\\/g, "\\\\").replace(/"/g, '\\"') +
      '"]';
    root.querySelectorAll(sel).forEach(function (node) {
      node.remove();
    });
  }

  function onPacket(data) {
    try {
      var msg = typeof data === "string" ? JSON.parse(data) : data;
      if (msg && msg.type === "game_chat_delete_last" && msg.user_id) {
        wipeUserChat(msg.user_id);
      }
    } catch (e) {
      /* ignore */
    }
  }

  var OrigWS = window.WebSocket;
  if (!OrigWS) return;

  window.WebSocket = function (url, protocols) {
    var ws =
      protocols !== undefined ? new OrigWS(url, protocols) : new OrigWS(url);
    ws.addEventListener("message", function (ev) {
      if (typeof ev.data === "string") onPacket(ev.data);
    });
    return ws;
  };
  window.WebSocket.prototype = OrigWS.prototype;
  Object.assign(window.WebSocket, OrigWS);
})();
