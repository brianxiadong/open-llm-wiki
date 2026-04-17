(function () {
  'use strict';

  function getCsrfToken() {
    var meta = document.querySelector('meta[name="csrf-token"]');
    return meta ? meta.getAttribute('content') : '';
  }

  function isUnsafeMethod(method) {
    return ['POST', 'PUT', 'PATCH', 'DELETE'].indexOf(String(method || 'GET').toUpperCase()) >= 0;
  }

  function isSameOrigin(url) {
    try {
      var target = new URL(url || window.location.href, window.location.href);
      return target.origin === window.location.origin;
    } catch (_e) {
      return true;
    }
  }

  if (window.fetch) {
    var originalFetch = window.fetch.bind(window);
    window.fetch = function (input, init) {
      init = init || {};
      var method = init.method || (input && input.method) || 'GET';
      var url = typeof input === 'string' ? input : (input && input.url) || window.location.href;
      if (isUnsafeMethod(method) && isSameOrigin(url)) {
        var headers = new Headers(init.headers || (input && input.headers) || {});
        if (!headers.has('X-CSRFToken')) {
          var token = getCsrfToken();
          if (token) headers.set('X-CSRFToken', token);
        }
        init.headers = headers;
      }
      return originalFetch(input, init);
    };
  }

  if (window.XMLHttpRequest) {
    var originalOpen = XMLHttpRequest.prototype.open;
    var originalSend = XMLHttpRequest.prototype.send;
    XMLHttpRequest.prototype.open = function (method, url) {
      this.__csrfMethod = method;
      this.__csrfUrl = url;
      return originalOpen.apply(this, arguments);
    };
    XMLHttpRequest.prototype.send = function () {
      if (isUnsafeMethod(this.__csrfMethod) && isSameOrigin(this.__csrfUrl)) {
        var token = getCsrfToken();
        if (token) this.setRequestHeader('X-CSRFToken', token);
      }
      return originalSend.apply(this, arguments);
    };
  }

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

  /* --- Flash message auto-dismiss (4s) --- */
  function dismissToast(el) {
    el.style.transition = 'opacity 0.4s ease, transform 0.4s ease';
    el.style.opacity = '0';
    el.style.transform = 'translateY(-6px)';
    setTimeout(function () { if (el.parentNode) el.parentNode.removeChild(el); }, 420);
  }
  document.querySelectorAll('.flash-toast').forEach(function (el) {
    var btn = el.querySelector('.flash-close');
    if (btn) btn.addEventListener('click', function () { dismissToast(el); });
    setTimeout(function () { dismissToast(el); }, 4000);
  });

  /* --- Global showToast(msg, type) — replaces alert() --- */
  window.showToast = function (msg, type) {
    type = type || 'info';
    var iconMap = { success: 'check-circle', error: 'alert-circle', warning: 'alert-triangle', info: 'info' };
    var stack = document.querySelector('.flash-stack');
    if (!stack) {
      stack = document.createElement('div');
      stack.className = 'flash-stack';
      var main = document.querySelector('main.main-content');
      if (main) main.insertBefore(stack, main.firstChild);
      else document.body.insertBefore(stack, document.body.firstChild);
    }
    var el = document.createElement('div');
    el.className = 'flash-toast flash-' + type;
    el.setAttribute('role', 'alert');
    el.innerHTML = '<i data-lucide="' + (iconMap[type] || 'info') + '"></i><span>' +
      msg.replace(/</g, '&lt;') + '</span>' +
      '<button class="flash-close" aria-label="关闭">&times;</button>';
    stack.appendChild(el);
    if (typeof lucide !== 'undefined') lucide.createIcons({ nodes: [el] });
    var closeBtn = el.querySelector('.flash-close');
    if (closeBtn) closeBtn.addEventListener('click', function () { dismissToast(el); });
    setTimeout(function () { dismissToast(el); }, 5000);
  };

  /* --- Delete confirmation dialogs --- */
  document.querySelectorAll('[data-confirm]').forEach(function (form) {
    form.addEventListener('submit', function (e) {
      if (!confirm(form.dataset.confirm)) {
        e.preventDefault();
      }
    });
  });

  document.querySelectorAll('form').forEach(function (form) {
    var method = (form.getAttribute('method') || 'GET').toUpperCase();
    if (!isUnsafeMethod(method)) return;
    if (!isSameOrigin(form.getAttribute('action') || window.location.href)) return;
    if (form.querySelector('input[name="csrf_token"]')) return;
    var token = getCsrfToken();
    if (!token) return;
    var input = document.createElement('input');
    input.type = 'hidden';
    input.name = 'csrf_token';
    input.value = token;
    form.appendChild(input);
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
