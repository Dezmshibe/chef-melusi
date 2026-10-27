// Dashboard helpers: confirm dialogs and photo re-ordering (drag & drop + arrows).
(function () {
  var token = (document.querySelector('meta[name="csrf"]') || {}).content;
  document.addEventListener('submit', function (e) {
    var m = e.target.getAttribute('data-confirm');
    if (m && !window.confirm(m)) e.preventDefault();
  });
  var list = document.getElementById('photo-list');
  if (!list) return;
  var status = document.getElementById('order-status'), dragged = null;
  function save() {
    var order = Array.prototype.map.call(list.querySelectorAll(':scope > details'), function (d) { return +d.dataset.id; });
    fetch(list.dataset.url, { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': token }, body: JSON.stringify({ order: order }) })
      .then(function (r) { status.textContent = r.ok ? 'Order saved.' : 'Could not save the order. Refresh and try again.'; })
      .catch(function () { status.textContent = 'Could not save the order. Check your connection.'; });
  }
  list.addEventListener('mousedown', function (e) {
    if (e.target.classList.contains('grip')) e.target.closest('details').draggable = true;
  });
  list.addEventListener('dragstart', function (e) { dragged = e.target.closest('details'); if (dragged) { dragged.classList.add('dragging'); e.dataTransfer.effectAllowed = 'move'; e.dataTransfer.setData('text/plain', dragged.dataset.id); } });
  list.addEventListener('dragover', function (e) {
    e.preventDefault();
    var t = e.target.closest('details');
    list.querySelectorAll('.over').forEach(function (x) { x.classList.remove('over'); });
    if (t && t !== dragged) t.classList.add('over');
  });
  list.addEventListener('drop', function (e) {
    e.preventDefault();
    var t = e.target.closest('details');
    if (dragged && t && t !== dragged) { list.insertBefore(dragged, t); save(); }
  });
  list.addEventListener('dragend', function () {
    if (dragged) { dragged.classList.remove('dragging'); dragged.draggable = false; }
    list.querySelectorAll('.over').forEach(function (x) { x.classList.remove('over'); });
    dragged = null;
  });
  list.addEventListener('click', function (e) {
    var b = e.target.closest('[data-mv]');
    if (!b) return;
    e.preventDefault();
    var d = b.closest('details'), dir = +b.dataset.mv;
    if (dir < 0 && d.previousElementSibling) list.insertBefore(d, d.previousElementSibling);
    if (dir > 0 && d.nextElementSibling) list.insertBefore(d.nextElementSibling, d);
    save();
  });
})();
