(function () {
  "use strict";

  var script = document.currentScript;
  if (!script || document.getElementById("latus-webchat-root")) return;

  var publicKey = (script.dataset.latusKey || "").trim();
  if (!publicKey) {
    console.error("Latus Chat Web: falta data-latus-key");
    return;
  }

  var scriptUrl = new URL(script.src, window.location.href);
  var appOrigin = scriptUrl.origin;
  var color = /^#[0-9a-f]{6}$/i.test(script.dataset.color || "")
    ? script.dataset.color
    : "#0E8DDB";
  var position = script.dataset.position === "left" ? "left" : "right";
  var side = position === "left" ? "left:20px" : "right:20px";

  var root = document.createElement("div");
  root.id = "latus-webchat-root";
  root.dataset.position = position;
  root.style.cssText = "position:fixed;z-index:2147483000;bottom:max(12px,env(safe-area-inset-bottom));" + side + ";max-width:calc(100vw - 24px);font-family:Arial,sans-serif";

  var responsiveStyle = document.createElement("style");
  responsiveStyle.textContent = [
    "@media(max-width:520px){",
    "#latus-webchat-root{left:8px!important;right:8px!important;max-width:none!important;bottom:max(8px,env(safe-area-inset-bottom))!important}",
    "#latus-webchat-root iframe{width:calc(100vw - 16px)!important;height:calc(100vh - 82px)!important;height:calc(100dvh - 82px)!important;max-height:none!important;border-radius:14px!important}",
    "#latus-webchat-root button{width:54px!important;height:54px!important;line-height:54px!important}",
    "}"
  ].join("");
  document.head.appendChild(responsiveStyle);

  var frame = document.createElement("iframe");
  frame.title = "Chat de atención";
  frame.src = appOrigin + "/chat-web/nuevo?key=" + encodeURIComponent(publicKey);
  frame.setAttribute("allow", "clipboard-write");
  frame.style.cssText = [
    "display:none", "width:min(420px,calc(100vw - 24px))", "height:min(720px,calc(100vh - 92px))", "height:min(720px,calc(100dvh - 92px))",
    "border:0", "border-radius:18px", "background:#fff", "box-shadow:0 18px 60px rgba(15,23,42,.28)",
    "margin-bottom:12px"
  ].join(";");

  var button = document.createElement("button");
  button.type = "button";
  button.setAttribute("aria-label", "Abrir chat de atención");
  button.setAttribute("aria-expanded", "false");
  button.style.cssText = [
    "display:block", position === "left" ? "margin-right:auto" : "margin-left:auto", "width:58px", "height:58px", "border:0", "border-radius:999px",
    "background:" + color, "color:#fff", "cursor:pointer", "box-shadow:0 10px 28px rgba(15,23,42,.28)",
    "font-size:26px", "line-height:58px", "text-align:center"
  ].join(";");
  button.textContent = "✦";

  button.addEventListener("click", function () {
    var opening = frame.style.display === "none";
    frame.style.display = opening ? "block" : "none";
    button.setAttribute("aria-expanded", opening ? "true" : "false");
    button.setAttribute("aria-label", opening ? "Cerrar chat de atención" : "Abrir chat de atención");
    button.textContent = opening ? "×" : "✦";
  });

  root.appendChild(frame);
  root.appendChild(button);
  document.body.appendChild(root);
})();
