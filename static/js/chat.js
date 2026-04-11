(function () {
  'use strict';

  var cfg = window.__chatConfig || {};
  var messages = document.getElementById('chat-messages');
  var welcome = document.getElementById('chat-welcome');
  var form = document.getElementById('chat-form');
  var input = document.getElementById('chat-input');
  var submitBtn = document.getElementById('chat-submit');
  var isLoading = false;

  /* ── Auto-resize textarea ─────────────────────────────── */
  input.addEventListener('input', function () {
    this.style.height = 'auto';
    this.style.height = Math.min(this.scrollHeight, 160) + 'px';
  });

  /* ── Enter to submit, Shift+Enter for newline ─────────── */
  input.addEventListener('keydown', function (e) {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      form.dispatchEvent(new Event('submit'));
    }
  });

  /* ── Suggestion buttons ───────────────────────────────── */
  document.querySelectorAll('.chat-suggestion').forEach(function (btn) {
    btn.addEventListener('click', function () {
      input.value = btn.dataset.q;
      form.dispatchEvent(new Event('submit'));
    });
  });

  /* ── Upload drag-and-drop ─────────────────────────────── */
  var dropArea = document.getElementById('upload-drop-area');
  var fileInput = document.getElementById('upload-file-input');
  var pending = document.getElementById('upload-pending');
  var filenameEl = document.getElementById('upload-filename');

  if (dropArea && fileInput) {
    ['dragenter', 'dragover'].forEach(function (evt) {
      dropArea.addEventListener(evt, function (e) {
        e.preventDefault();
        dropArea.classList.add('kb-upload-active');
      });
    });
    ['dragleave', 'drop'].forEach(function (evt) {
      dropArea.addEventListener(evt, function (e) {
        e.preventDefault();
        dropArea.classList.remove('kb-upload-active');
      });
    });
    dropArea.addEventListener('drop', function (e) {
      if (e.dataTransfer.files.length) {
        fileInput.files = e.dataTransfer.files;
        showPending(e.dataTransfer.files[0].name);
      }
    });
    dropArea.addEventListener('click', function (e) {
      // Once a file is pending, don't re-open picker from label clicks
      if (pending && !pending.hidden) {
        e.preventDefault();
        return;
      }
      fileInput.click();
    });
    fileInput.addEventListener('change', function () {
      if (fileInput.files.length) showPending(fileInput.files[0].name);
    });
  }

  function showPending(name) {
    if (pending && filenameEl) {
      filenameEl.textContent = name;
      pending.hidden = false;
      // Make the drop area non-clickable visually
      if (dropArea) dropArea.style.cursor = 'default';
    }
  }

  function resetUpload() {
    if (pending) pending.hidden = true;
    if (filenameEl) filenameEl.textContent = '';
    if (fileInput) fileInput.value = '';
    if (dropArea) dropArea.style.cursor = '';
  }

  /* AJAX upload with progress feedback */
  var uploadForm = document.getElementById('upload-form') || (dropArea ? dropArea.closest('form') : null);
  if (uploadForm) {
    uploadForm.addEventListener('submit', function (e) {
      e.preventDefault();
      if (!fileInput || !fileInput.files.length) return;

      var btn = uploadForm.querySelector('.kb-upload-submit');
      var origHtml = btn ? btn.innerHTML : '';
      if (btn) {
        btn.disabled = true;
        btn.innerHTML = '<span class="icon-spin" style="display:inline-block">⏳</span> 上传中…';
      }
      if (dropArea) { dropArea.style.opacity = '0.5'; dropArea.style.pointerEvents = 'none'; }

      var fd = new FormData();
      fd.append('file', fileInput.files[0]);

      var xhr = new XMLHttpRequest();
      xhr.open('POST', uploadForm.action, true);
      xhr.setRequestHeader('X-Requested-With', 'XMLHttpRequest');

      xhr.upload.addEventListener('progress', function (ev) {
        if (ev.lengthComputable && btn) {
          var pct = Math.round(ev.loaded / ev.total * 100);
          btn.innerHTML = '<span class="icon-spin" style="display:inline-block">⏳</span> 上传 ' + pct + '%';
        }
      });

      xhr.onload = function () {
        if (xhr.status >= 200 && xhr.status < 300) {
          if (btn) btn.innerHTML = '✓ 上传成功，处理中…';
          setTimeout(function () { window.location.reload(); }, 1200);
        } else {
          var errMsg = '上传失败';
          try { var j = JSON.parse(xhr.responseText); errMsg = j.error || errMsg; } catch(e) {}
          resetUpload();
          if (btn) { btn.innerHTML = origHtml; btn.disabled = false; }
          if (dropArea) { dropArea.style.opacity = '1'; dropArea.style.pointerEvents = 'auto'; }
          alert(errMsg);
        }
      };

      xhr.onerror = function () {
        if (btn) { btn.innerHTML = origHtml; btn.disabled = false; }
        if (dropArea) { dropArea.style.opacity = '1'; dropArea.style.pointerEvents = 'auto'; }
        alert('上传失败，请检查网络');
      };

      xhr.send(fd);
    });
  }

  /* ── Chat message rendering ───────────────────────────── */

  function addUserMessage(text) {
    if (welcome) welcome.hidden = true;
    var div = document.createElement('div');
    div.className = 'chat-msg chat-msg-user';
    div.innerHTML = '<div class="chat-msg-avatar"><i class="lucide-user" aria-hidden="true"></i></div>' +
      '<div class="chat-msg-body"><div class="chat-msg-content">' + escapeHtml(text) + '</div></div>';
    messages.appendChild(div);
    initIcons(div);
    scrollToBottom();
  }

  function addAIMessage(html, refs, markdown) {
    var div = document.createElement('div');
    div.className = 'chat-msg chat-msg-ai';

    var refHtml = '';
    if (refs && refs.length) {
      refHtml = '<div class="chat-refs"><span class="chat-refs-label">' +
        '<i class="lucide-book-marked" aria-hidden="true"></i> 参考</span>';
      refs.forEach(function (r) {
        refHtml += '<a href="' + r.url + '" class="chat-ref-link">' + escapeHtml(r.title) + '</a>';
      });
      refHtml += '</div>';
    }

    var saveBtn = '';
    if (markdown) {
      saveBtn = '<div class="chat-msg-actions">' +
        '<button class="chat-action-btn chat-save-btn" title="保存为 Wiki 页面">' +
        '<i class="lucide-bookmark-plus" aria-hidden="true"></i> 保存为页面</button></div>';
    }

    div.innerHTML = '<div class="chat-msg-avatar chat-msg-avatar-ai">' +
      '<i class="lucide-bot" aria-hidden="true"></i></div>' +
      '<div class="chat-msg-body">' +
      '<div class="chat-msg-content rendered-content">' + html + '</div>' +
      refHtml + saveBtn + '</div>';

    messages.appendChild(div);
    initIcons(div);

    var saveBtnEl = div.querySelector('.chat-save-btn');
    if (saveBtnEl && markdown) {
      saveBtnEl.addEventListener('click', function () { saveAsPage(markdown, saveBtnEl); });
    }

    scrollToBottom();
  }

  function addLoadingMessage() {
    var div = document.createElement('div');
    div.className = 'chat-msg chat-msg-ai chat-msg-loading';
    div.id = 'chat-loading';
    div.innerHTML = '<div class="chat-msg-avatar chat-msg-avatar-ai">' +
      '<i class="lucide-bot" aria-hidden="true"></i></div>' +
      '<div class="chat-msg-body"><div class="chat-msg-content">' +
      '<span class="chat-typing"><span></span><span></span><span></span></span>' +
      '</div></div>';
    messages.appendChild(div);
    initIcons(div);
    scrollToBottom();
  }

  function removeLoadingMessage() {
    var el = document.getElementById('chat-loading');
    if (el) el.remove();
  }

  function addErrorMessage(text) {
    var div = document.createElement('div');
    div.className = 'chat-msg chat-msg-ai chat-msg-error';
    div.innerHTML = '<div class="chat-msg-avatar chat-msg-avatar-ai">' +
      '<i class="lucide-bot" aria-hidden="true"></i></div>' +
      '<div class="chat-msg-body"><div class="chat-msg-content chat-error-text">' +
      '<i class="lucide-alert-circle" aria-hidden="true"></i> ' + escapeHtml(text) + '</div></div>';
    messages.appendChild(div);
    initIcons(div);
    scrollToBottom();
  }

  /* ── Send query ───────────────────────────────────────── */

  form.addEventListener('submit', function (e) {
    e.preventDefault();
    var q = input.value.trim();
    if (!q || isLoading) return;

    addUserMessage(q);
    input.value = '';
    input.style.height = 'auto';
    isLoading = true;
    submitBtn.disabled = true;

    addLoadingMessage();

    fetch(cfg.queryUrl, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ q: q })
    })
      .then(function (r) { return r.json(); })
      .then(function (data) {
        removeLoadingMessage();
        if (data.error) {
          addErrorMessage(data.error);
        } else {
          addAIMessage(data.html || '', data.references || [], data.markdown || '');
        }
      })
      .catch(function () {
        removeLoadingMessage();
        addErrorMessage('查询失败，请稍后重试');
      })
      .finally(function () {
        isLoading = false;
        submitBtn.disabled = false;
        input.focus();
      });
  });

  /* ── Save answer as wiki page ─────────────────────────── */

  function saveAsPage(markdown, btn) {
    var lastUserMsg = '';
    var userMsgs = messages.querySelectorAll('.chat-msg-user .chat-msg-content');
    if (userMsgs.length) lastUserMsg = userMsgs[userMsgs.length - 1].textContent;

    var formData = new FormData();
    formData.append('content', markdown);
    formData.append('query', lastUserMsg);

    btn.disabled = true;
    btn.innerHTML = '<i class="lucide-loader-2 icon-spin" aria-hidden="true"></i> 保存中…';
    initIcons(btn);

    fetch(cfg.saveUrl, { method: 'POST', body: formData })
      .then(function (r) {
        if (r.redirected) {
          btn.innerHTML = '<i class="lucide-check" aria-hidden="true"></i> 已保存';
          initIcons(btn);
          setTimeout(function () { window.location.reload(); }, 800);
        } else {
          btn.innerHTML = '<i class="lucide-bookmark-plus" aria-hidden="true"></i> 保存为页面';
          initIcons(btn);
          btn.disabled = false;
        }
      })
      .catch(function () {
        btn.innerHTML = '<i class="lucide-bookmark-plus" aria-hidden="true"></i> 保存为页面';
        initIcons(btn);
        btn.disabled = false;
      });
  }

  /* ── Helpers ───────────────────────────────────────────── */

  function scrollToBottom() {
    messages.scrollTop = messages.scrollHeight;
  }

  function escapeHtml(str) {
    var d = document.createElement('div');
    d.appendChild(document.createTextNode(str));
    return d.innerHTML;
  }

  function initIcons(root) {
    if (typeof lucide === 'undefined') return;
    root.querySelectorAll('[class*="lucide-"]').forEach(function (el) {
      var match = Array.from(el.classList).find(function (c) { return c.indexOf('lucide-') === 0; });
      if (match) {
        el.setAttribute('data-lucide', match.substring(7));
        el.classList.remove(match);
      }
    });
    lucide.createIcons({ nodes: root.querySelectorAll('[data-lucide]') });
  }

  /* ── Sidebar task progress polling ──────────────────────── */
  (function pollSidebarTasks() {
    var items = document.querySelectorAll('.kb-source-item[data-task-id]');
    if (!items.length) return;

    function tick() {
      var active = document.querySelectorAll('.kb-source-item[data-task-id]');
      if (!active.length) return;

      active.forEach(function (li) {
        var tid = li.dataset.taskId;
        if (!tid || tid === '0') return;
        fetch('/api/tasks/' + tid + '/status')
          .then(function (r) { return r.json(); })
          .then(function (d) {
            var badge = li.querySelector('.kb-status-badge');
            if (d.status === 'running') {
              if (badge) {
                badge.textContent = d.progress + '%';
                badge.title = d.progress_msg || '';
                badge.className = 'kb-status-badge kb-status-running';
              } else {
                var span = document.createElement('span');
                span.className = 'kb-status-badge kb-status-running';
                span.textContent = d.progress + '%';
                span.title = d.progress_msg || '';
                li.appendChild(span);
              }
            } else if (d.status === 'done') {
              if (badge) badge.remove();
              li.removeAttribute('data-task-id');
              if (!li.querySelector('.kb-status-dot')) {
                var dot = document.createElement('span');
                dot.className = 'kb-status-dot kb-status-done';
                dot.title = '已摄入';
                li.appendChild(dot);
              }
            } else if (d.status === 'failed') {
              if (badge) {
                badge.textContent = '失败';
                badge.className = 'kb-status-badge kb-status-failed';
                badge.title = d.progress_msg || '处理失败';
              }
              li.removeAttribute('data-task-id');
            }
          })
          .catch(function () {});
      });

      if (document.querySelectorAll('.kb-source-item[data-task-id]').length) {
        setTimeout(tick, 2000);
      } else {
        setTimeout(function () { window.location.reload(); }, 1500);
      }
    }

    setTimeout(tick, 1500);
  })();
})();
