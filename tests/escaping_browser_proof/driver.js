// Exercises each page as an operator would, then records what executed.
window.addEventListener('load', function () {
  function fireAll() {
    Array.prototype.forEach.call(document.querySelectorAll('*'), function (el) {
      try {
        el.dispatchEvent(new MouseEvent('mouseover', { bubbles: false }));
        el.dispatchEvent(new FocusEvent('focus'));
      } catch (e) {}
    });
  }
  function click(sel) { var n = document.querySelector(sel); if (n) n.click(); return !!n; }
  function count(sel) { return document.querySelectorAll(sel).length; }
  var r = { actions: {}, panels: {} };
  var path = location.pathname;
  var steps = [];
  if (path === '/kanban') {
    steps.push(function () {
      var card = Array.prototype.filter.call(document.querySelectorAll('.leadCard'), function (c) {
        return (c.getAttribute('data-search') || '').indexOf('SINTHREAD') !== -1;
      })[0];
      var button = card && card.querySelector('[data-lead-wa-btn="1"]');
      r.actions.waButton = !!button;
      if (button) button.click();
    });
    steps.push(function () { r.panels.waPicker = count('button[data-thread-id]'); });
  }
  if (path === '/whatsapp/inbox') {
    steps.push(function () { r.actions.rowMenu = click('.wa-chat-row-menu-btn'); });
    steps.push(function () { r.actions.link = click('[data-wa-row-action="link"]'); });
    steps.push(function () { r.panels.leadPicker = count('.wa-utility-list .wa-utility-item'); });
  }
  if (path.indexOf('/whatsapp/thread/') === 0) {
    steps.push(function () { r.actions.rowMenu = click('.wa-chat-row-menu-btn'); });
    steps.push(function () { r.actions.link = click('[data-wa-row-action="link"]'); });
    steps.push(function () { r.panels.leadPicker = count('.wa-utility-list .wa-utility-item'); });
    steps.push(function () { r.actions.msgMenu = click('.wa-msg-menu-toggle'); });
    steps.push(function () { r.actions.forward = click('[data-wa-menu-action="forward"]'); });
    steps.push(function () { r.panels.forwardPicker = count('.wa-utility-list .wa-utility-item'); });
  }
  if (path === '/control') {
    steps.push(function () {
      r.panels.paths = count('#paths-tbody a');
      r.panels.messages = count('#msgs-tbody tr, #messages-tbody tr');
      r.panels.threads = count('#threads-tbody tr');
      Array.prototype.forEach.call(document.querySelectorAll('#paths-tbody a'), function (a) { a.click(); });
    });
  }
  if (path === '/table' && window.openFilters) { steps.push(function () { window.openFilters(); }); }
  if (path === '/agencias') {
    steps.push(function () {
      Array.prototype.forEach.call(document.querySelectorAll('.agModalOverlay'), function (m) {
        window.openAgenciaEdit(parseInt(m.id.replace('ag-modal-', ''), 10));
      });
    });
  }
  function finish() {
    fireAll();
    setTimeout(function () {
      var all = Array.prototype.slice.call(document.querySelectorAll('*'));
      r.pwn = Array.from(new Set(window.__pwn)).sort();
      r.dom = {
        injectedImg: count('img[src="x"],img[src="X"]'),
        injectedScript: Array.prototype.filter.call(document.scripts, function (s) {
          return /__pwn\.push/i.test(s.textContent) && s.id !== '__driver' && s.id !== '__seed'
            && s.id !== 'zones-data';
        }).length,
        handlers: all.filter(function (e) {
          return Array.prototype.some.call(e.attributes, function (a) {
            return a.name.indexOf('on') === 0 && /__pwn/i.test(a.value);
          });
        }).length,
      };
      r.origin = location.origin;
      var pre = document.createElement('pre');
      pre.id = '__result';
      pre.textContent = JSON.stringify(r);
      document.documentElement.appendChild(pre);
    }, 500);
  }
  function next(i) {
    if (i >= steps.length) return finish();
    fireAll();
    try { steps[i](); } catch (e) { r.actions['error' + i] = String(e); }
    setTimeout(function () { next(i + 1); }, 900);
  }
  setTimeout(function () { next(0); }, 1200);
});
