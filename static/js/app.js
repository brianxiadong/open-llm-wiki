(function () {
  'use strict';

  /* --- Convert lucide-* classes to data-lucide and render SVG icons --- */
  document.querySelectorAll('[class*="lucide-"]').forEach(function (el) {
    var match = Array.from(el.classList).find(function (c) { return c.indexOf('lucide-') === 0; });
    if (match) {
      el.setAttribute('data-lucide', match.substring(7));
      el.classList.remove(match);
    }
  });
  if (typeof lucide !== 'undefined') {
    lucide.createIcons();
  }

  /* --- Slug auto-generation for repo creation --- */
  var nameInput = document.getElementById('name');
  var slugInput = document.getElementById('slug');
  if (nameInput && slugInput) {
    var manualSlug = false;
    slugInput.addEventListener('input', function () { manualSlug = !!slugInput.value; });

    nameInput.addEventListener('input', function () {
      if (manualSlug) return;
      slugInput.value = nameInput.value
        .toLowerCase()
        .replace(/[\u4e00-\u9fa5]/g, function (ch) { return ch; })
        .replace(/[^a-z0-9\u4e00-\u9fa5]+/g, '-')
        .replace(/^-|-$/g, '');
    });
  }

  /* --- Flash message auto-dismiss --- */
  document.querySelectorAll('.flash-toast').forEach(function (el) {
    var btn = el.querySelector('.flash-close');
    if (btn) {
      btn.addEventListener('click', function () { el.remove(); });
    }
    setTimeout(function () {
      el.style.transition = 'opacity 0.5s ease';
      el.style.opacity = '0';
      setTimeout(function () { el.remove(); }, 500);
    }, 6000);
  });

  /* --- Delete confirmation dialogs --- */
  document.querySelectorAll('[data-confirm]').forEach(function (form) {
    form.addEventListener('submit', function (e) {
      if (!confirm(form.dataset.confirm)) {
        e.preventDefault();
      }
    });
  });

  /* --- SSE listener for ingest progress (reusable) --- */
  window.connectSSE = function (url, handlers) {
    var source = new EventSource(url);
    Object.keys(handlers).forEach(function (evt) {
      source.addEventListener(evt, function (e) {
        handlers[evt](JSON.parse(e.data));
      });
    });
    source.onerror = function () {
      if (handlers.error) handlers.error();
      source.close();
    };
    return source;
  };
})();
