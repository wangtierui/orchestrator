/* ui/err.js —— 页面侧异常捕获（经典脚本，任意页面可直接 <script src> 引入）
 *
 * 目的：冒烟探针读 window.__psErr，页面报错时能拿到消息与首行栈，
 * 而不是只有 has=false。壳与 13 个独立页共用同一份实现。
 */
(function () {
  if (window.__psErrInstalled) return;
  window.__psErrInstalled = true;
  window.__psErr = window.__psErr || null;

  function rec(msg) {
    if (!window.__psErr) window.__psErr = String(msg).slice(0, 400);
  }
  window.addEventListener("error", function (e) {
    rec(e.message || (e.error && e.error.message) || e.type);
  });
  window.addEventListener("unhandledrejection", function (e) {
    var r = e.reason;
    rec("unhandled: " + ((r && r.message) || r));
  });
  window.__psErrClear = function () { window.__psErr = null; };
})();
