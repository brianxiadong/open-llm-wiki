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
          if (window.showToast) showToast(errMsg, 'error'); else alert(errMsg);
        }
      };

      xhr.onerror = function () {
        if (btn) { btn.innerHTML = origHtml; btn.disabled = false; }
        if (dropArea) { dropArea.style.opacity = '1'; dropArea.style.pointerEvents = 'auto'; }
        if (window.showToast) showToast('上传失败，请检查网络', 'error'); else alert('上传失败，请检查网络');
      };

      xhr.send(fd);
    });
  }

  /* ── Chat message rendering ───────────────────────────── */

  function addUserMessage(text, isRestore) {
    if (welcome) welcome.hidden = true;
    var div = document.createElement('div');
    div.className = 'chat-msg chat-msg-user';
    div.innerHTML = '<div class="chat-msg-avatar"><i class="lucide-user" aria-hidden="true"></i></div>' +
      '<div class="chat-msg-body"><div class="chat-msg-content">' + escapeHtml(text) + '</div></div>';
    messages.appendChild(div);
    initIcons(div);
    if (!isRestore) scrollToBottom();
  }

  function addAIMessage(html, refs, markdown, wikiSources, qdrantSources, confidence, wikiEvidence, chunkEvidence, evidenceSummary, isRestore) {
    var div = document.createElement('div');
    div.className = 'chat-msg chat-msg-ai';

    // -- Confidence badge -------------------------------------------------
    var confHtml = '';
    if (confidence && confidence.level) {
      var levelLabel = { high: '高置信度', medium: '中置信度', low: '低置信度' }[confidence.level] || confidence.level;
      var reasons = confidence.reasons || [];
      var reasonsHtml = reasons.length
        ? '<div class="confidence-reasons hidden">' + reasons.map(function(r) {
            return '<span class="confidence-reason-item">' + escapeHtml(r) + '</span>';
          }).join('') + '</div>'
        : '';
      confHtml = '<div class="confidence-badge confidence-' + confidence.level + '" data-expanded="false">' +
        '<i class="lucide-shield-check" aria-hidden="true"></i>' +
        '<span>' + escapeHtml(levelLabel) + '</span>' +
        (reasons.length ? '<button class="confidence-toggle" type="button" aria-label="查看原因">▾</button>' : '') +
        reasonsHtml + '</div>';
      if (confidence.level === 'low') {
        confHtml = '<div class="low-confidence-warning">' +
          '<i class="lucide-alert-triangle" aria-hidden="true"></i> ' +
          '此回答证据有限，请谨慎参考</div>' + confHtml;
      }
    }

    // -- Wiki evidence panel ----------------------------------------------
    var wikiEvHtml = '';
    if (wikiEvidence && wikiEvidence.length) {
      wikiEvHtml = '<div class="evidence-panel">' +
        '<div class="evidence-panel-header"><i class="lucide-book-open" aria-hidden="true"></i> LLM Wiki 结构证据 (' + wikiEvidence.length + ')</div>' +
        '<div class="evidence-items">';
      wikiEvidence.forEach(function(e) {
        wikiEvHtml += '<div class="evidence-item evidence-wiki">' +
          '<span class="badge badge-' + escapeHtml(e.type || 'concept') + '">' + escapeHtml(e.type || '') + '</span> ' +
          '<a href="' + escapeHtml(e.url || '#') + '">' + escapeHtml(e.title || e.filename || '') + '</a>' +
          '<span class="evidence-reason">' + escapeHtml(e.reason || '') + '</span>' +
          '</div>';
      });
      wikiEvHtml += '</div></div>';
    }

    // -- Chunk evidence panel ---------------------------------------------
    var chunkEvHtml = '';
    if (chunkEvidence && chunkEvidence.length) {
      chunkEvHtml = '<div class="evidence-panel">' +
        '<div class="evidence-panel-header"><i class="lucide-file-search" aria-hidden="true"></i> 原文片段证据 (' + chunkEvidence.length + ')</div>' +
        '<div class="evidence-items">';
      chunkEvidence.forEach(function(e) {
        var scorePct = e.score ? Math.round(e.score * 100) : 0;
        chunkEvHtml += '<div class="evidence-item evidence-chunk">' +
          '<a href="' + escapeHtml(e.url || '#') + '" class="evidence-chunk-title">' + escapeHtml(e.title || e.filename || '') + '</a>' +
          (e.heading ? '<span class="evidence-heading">§ ' + escapeHtml(e.heading) + '</span>' : '') +
          '<div class="evidence-snippet">' + escapeHtml((e.snippet || '').substring(0, 150)) + '</div>' +
          '<span class="evidence-score">' + scorePct + '%</span>' +
          '</div>';
      });
      chunkEvHtml += '</div></div>';
    }

    // -- Legacy source panel (fallback if no new evidence) ----------------
    var sourceHtml = '';
    if (!wikiEvidence || !wikiEvidence.length) {
      var hasWiki = wikiSources && wikiSources.length;
      var hasQdrant = qdrantSources && qdrantSources.length;
      if (hasWiki || hasQdrant) {
        sourceHtml = '<div class="chat-sources">';
        sourceHtml += '<div class="chat-sources-header">' +
          '<i class="lucide-git-branch" aria-hidden="true"></i> 溯源</div>';
        sourceHtml += '<div class="chat-sources-body">';

        if (hasWiki) {
          sourceHtml += '<div class="chat-source-channel">' +
            '<span class="chat-source-tag chat-source-tag-wiki">' +
            '<i class="lucide-book-open" aria-hidden="true"></i> LLM Wiki</span>';
          wikiSources.forEach(function(r) {
            sourceHtml += '<a href="' + r.url + '" class="chat-source-link">' +
              escapeHtml(r.title) + '</a>';
          });
          sourceHtml += '</div>';
        }

        if (hasQdrant) {
          sourceHtml += '<div class="chat-source-channel">' +
            '<span class="chat-source-tag chat-source-tag-qdrant">' +
            '<i class="lucide-database" aria-hidden="true"></i> 向量检索</span>';
          qdrantSources.forEach(function(r) {
            var inWiki = hasWiki && wikiSources.some(function(w) { return w.filename === r.filename; });
            sourceHtml += '<a href="' + r.url + '" class="chat-source-link' +
              (inWiki ? ' chat-source-link-overlap" title="与 LLM Wiki 通道重合"' : '"') +
              '>' + escapeHtml(r.title) + (inWiki ? ' ↑' : '') + '</a>';
          });
          sourceHtml += '</div>';
        }

        sourceHtml += '</div></div>';
      }
    }

    var saveBtn = '';
    if (markdown) {
      saveBtn = '<div class="chat-msg-actions">' +
        '<button class="chat-action-btn chat-copy-btn" title="复制回答">' +
        '<i class="lucide-copy" aria-hidden="true"></i> 复制</button>' +
        '<button class="chat-action-btn chat-save-btn" title="保存为 Wiki 页面">' +
        '<i class="lucide-bookmark-plus" aria-hidden="true"></i> 保存为页面</button></div>';
    }

    div.innerHTML = '<div class="chat-msg-avatar chat-msg-avatar-ai">' +
      '<i class="lucide-bot" aria-hidden="true"></i></div>' +
      '<div class="chat-msg-body">' +
      confHtml +
      '<div class="chat-msg-content rendered-content">' + html + '</div>' +
      wikiEvHtml + chunkEvHtml + sourceHtml + saveBtn + '</div>';

    messages.appendChild(div);
    initIcons(div);

    // confidence toggle
    var badge = div.querySelector('.confidence-badge');
    var toggle = badge && badge.querySelector('.confidence-toggle');
    if (toggle) {
      toggle.addEventListener('click', function(ev) {
        ev.stopPropagation();
        var reasons = badge.querySelector('.confidence-reasons');
        if (reasons) {
          var expanded = badge.dataset.expanded === 'true';
          reasons.classList.toggle('hidden', expanded);
          badge.dataset.expanded = expanded ? 'false' : 'true';
          toggle.textContent = expanded ? '▾' : '▴';
        }
      });
    }

    var copyBtnEl = div.querySelector('.chat-copy-btn');
    if (copyBtnEl && markdown) {
      copyBtnEl.addEventListener('click', function () {
        navigator.clipboard.writeText(markdown).then(function () {
          copyBtnEl.innerHTML = '<i class="lucide-check" aria-hidden="true"></i> 已复制';
          initIcons(copyBtnEl);
          setTimeout(function () {
            copyBtnEl.innerHTML = '<i class="lucide-copy" aria-hidden="true"></i> 复制';
            initIcons(copyBtnEl);
          }, 2000);
        }).catch(function () {
          if (window.showToast) showToast('复制失败，请手动选择文本', 'warning');
        });
      });
    }

    var saveBtnEl = div.querySelector('.chat-save-btn');
    if (saveBtnEl && markdown) {
      saveBtnEl.addEventListener('click', function () { saveAsPage(markdown, saveBtnEl); });
    }

    if (!isRestore) scrollToBottom();
  }

  function addLoadingMessage() {
    var div = document.createElement('div');
    div.className = 'chat-msg chat-msg-ai chat-msg-loading';
    div.id = 'chat-loading';
    div.innerHTML = '<div class="chat-msg-avatar chat-msg-avatar-ai">' +
      '<i class="lucide-bot" aria-hidden="true"></i></div>' +
      '<div class="chat-msg-body"><div class="chat-msg-content">' +
      '<span class="chat-typing"><span></span><span></span><span></span></span>' +
      '<span class="chat-wait-hint" hidden></span>' +
      '</div></div>';
    messages.appendChild(div);
    initIcons(div);
    scrollToBottom();

    // Show elapsed seconds after 5s
    var elapsed = 0;
    var waitHint = div.querySelector('.chat-wait-hint');
    var timer = setInterval(function () {
      elapsed++;
      if (elapsed >= 5 && waitHint) {
        waitHint.hidden = false;
        waitHint.textContent = ' 正在思考… 已等待 ' + elapsed + 's';
      }
    }, 1000);
    div._waitTimer = timer;
  }

  function removeLoadingMessage() {
    var el = document.getElementById('chat-loading');
    if (el) {
      if (el._waitTimer) clearInterval(el._waitTimer);
      el.remove();
    }
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

  /* ── Session Management ──────────────────────────────────── */

  var SESSION_KEY = null;  // 当前激活的 session key
  var sessionBar = document.getElementById('chat-session-bar');
  var sessionList = document.getElementById('chat-session-list');
  var newSessionBtn = document.getElementById('new-session-btn');

  function renderSessionTabs(sessions, activeKey) {
    sessionList.innerHTML = '';
    if (!sessions || !sessions.length) {
      sessionList.innerHTML = '<span style="color:var(--pico-muted-color);font-size:0.82rem;padding:0 0.25rem;">暂无历史</span>';
      return;
    }
    sessions.forEach(function(s) {
      var tab = document.createElement('div');
      tab.className = 'chat-session-tab' + (s.key === activeKey ? ' active' : '');
      tab.dataset.key = s.key;
      tab.innerHTML =
        '<span class="session-title" title="' + escapeHtml(s.title) + '">' + escapeHtml(s.title) + '</span>' +
        '<button class="session-del" title="删除">✕</button>';
      tab.querySelector('.session-del').addEventListener('click', function(e) {
        e.stopPropagation();
        deleteSession(s.key);
      });
      tab.addEventListener('click', function() {
        switchSession(s.key, sessions, tab);
      });
      tab.querySelector('.session-title').addEventListener('dblclick', function(e) {
        e.stopPropagation();
        var newTitle = prompt('重命名对话', s.title);
        if (newTitle && newTitle.trim()) renameSession(s.key, newTitle.trim());
      });
      sessionList.appendChild(tab);
    });
    // 滚动到激活 tab
    var activeTab = sessionList.querySelector('.chat-session-tab.active');
    if (activeTab) activeTab.scrollIntoView({ block: 'nearest', inline: 'nearest' });
  }

  function switchSession(key, sessions, clickedTab) {
    if (SESSION_KEY === key) return;
    SESSION_KEY = key;
    // 更新 active 样式
    sessionList.querySelectorAll('.chat-session-tab').forEach(function(t) {
      t.classList.toggle('active', t.dataset.key === key);
    });
    restoreChatMessages(key);
  }

  function loadSessionList(activateKey) {
    if (!cfg.listSessionsUrl) return;
    fetch(cfg.listSessionsUrl)
      .then(function(r) { return r.json(); })
      .then(function(data) {
        var sessions = data.sessions || [];
        var activeKey = activateKey || SESSION_KEY;
        // 如果没有任何会话且没有指定 key，自动新建
        if (!sessions.length && !activeKey) {
          createNewSession();
          return;
        }
        // 没有 active key 则用最新的
        if (!activeKey && sessions.length) activeKey = sessions[0].key;
        renderSessionTabs(sessions, activeKey);
        if (activeKey !== SESSION_KEY) {
          SESSION_KEY = activeKey;
          restoreChatMessages(activeKey);
        }
      })
      .catch(function() {
        sessionList.innerHTML = '<span style="color:var(--pico-muted-color);font-size:0.82rem;">—</span>';
      });
  }

  function createNewSession() {
    if (!cfg.newSessionUrl) return;
    fetch(cfg.newSessionUrl, { method: 'POST', headers: {'Content-Type':'application/json'} })
      .then(function(r) { return r.json(); })
      .then(function(data) {
        if (data.ok) {
          SESSION_KEY = data.key;
          // 清空聊天界面
          if (messages) messages.innerHTML = '';
          if (welcome) welcome.style.display = '';
          loadSessionList(data.key);
        }
      });
  }

  function deleteSession(key) {
    if (!confirm('确认删除此对话？')) return;
    var url = cfg.deleteSessionBaseUrl + encodeURIComponent(key) + '/delete';
    fetch(url, { method: 'POST' })
      .then(function() {
        if (SESSION_KEY === key) {
          SESSION_KEY = null;
          if (messages) messages.innerHTML = '';
          if (welcome) welcome.style.display = '';
        }
        loadSessionList(null);
      });
  }

  function renameSession(key, title) {
    var url = cfg.deleteSessionBaseUrl + encodeURIComponent(key) + '/rename';
    fetch(url, {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({title: title})
    }).then(function() { loadSessionList(SESSION_KEY); });
  }

  function restoreChatMessages(key) {
    if (!cfg.getSessionUrl || !key) return;
    fetch(cfg.getSessionUrl + '?key=' + encodeURIComponent(key))
      .then(function(r) { return r.json(); })
      .then(function(data) {
        var msgs = data.messages || [];
        if (!msgs.length) {
          if (messages) messages.innerHTML = '';
          if (welcome) welcome.style.display = '';
          return;
        }
        if (welcome) welcome.style.display = 'none';
        if (messages) messages.innerHTML = '';
        // 渲染历史消息（简单文本展示，不含证据面板）
        msgs.forEach(function(m) {
          if (m.role === 'user') {
            addUserMessage(m.content, true);
          } else if (m.role === 'assistant') {
            var safeHtml = '<p>' + escapeHtml(m.content).replace(/\n/g, '<br>') + '</p>';
            addAIMessage(safeHtml, [], m.content, [], [], null, null, null, '', true);
          }
        });
        if (messages) messages.scrollTop = messages.scrollHeight;
      });
  }

  if (newSessionBtn) {
    newSessionBtn.addEventListener('click', createNewSession);
  }

  // 清空会话按钮改为清空当前 session 消息
  var clearSessionBtn = document.getElementById('clear-session-btn');
  if (clearSessionBtn) {
    clearSessionBtn.addEventListener('click', function () {
      if (!confirm('确认清空当前对话？')) return;
      if (!SESSION_KEY || !cfg.clearSessionUrl) return;
      fetch(cfg.clearSessionUrl, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ key: SESSION_KEY })
      }).then(function () {
        if (messages) messages.innerHTML = '';
        if (welcome) welcome.style.display = '';
        loadSessionList(SESSION_KEY);
      }).catch(function () {});
    });
  }

  // 页面加载时拉取会话列表
  if (sessionBar && cfg.listSessionsUrl) {
    loadSessionList(null);
  } else {
    // 未登录或无会话 URL，退化到日期 key
    SESSION_KEY = 'session_' + (cfg.repoSlug || 'default') + '_' + new Date().toISOString().slice(0, 10);
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

    var streamUrl = cfg.queryStreamUrl;
    if (!streamUrl) {
      fetch(cfg.queryUrl, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ q: q, session_key: SESSION_KEY })
      })
        .then(function (r) { return r.json(); })
        .then(function (data) {
          removeLoadingMessage();
          if (data.error) {
            addErrorMessage(data.error);
          } else {
            addAIMessage(data.html || '', data.references || [],
              data.markdown || '', data.wiki_sources || [], data.qdrant_sources || [],
              data.confidence, data.wiki_evidence, data.chunk_evidence, data.evidence_summary);
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
      return;
    }

    var answerChunks = [];
    var streamingEl = null;
    var es = new EventSource(streamUrl + '?q=' + encodeURIComponent(q));

    es.addEventListener('progress', function (e) {
      var d = JSON.parse(e.data);
      var loading = document.getElementById('chat-loading');
      if (loading) {
        var hint = loading.querySelector('.chat-wait-hint');
        if (hint) { hint.hidden = false; hint.textContent = d.message || ''; }
      }
    });

    es.addEventListener('answer_chunk', function (e) {
      var d = JSON.parse(e.data);
      answerChunks.push(d.chunk);
      var loading = document.getElementById('chat-loading');
      if (loading) {
        var content = loading.querySelector('.chat-msg-content');
        if (content) {
          if (!streamingEl) {
            content.innerHTML = '<div class="streaming-answer" style="white-space:pre-wrap"></div>';
            streamingEl = content.querySelector('.streaming-answer');
          }
          streamingEl.textContent = answerChunks.join('');
        }
      }
    });

    es.addEventListener('done', function (e) {
      es.close();
      removeLoadingMessage();
      var d = JSON.parse(e.data);
      var answer = d.answer || answerChunks.join('');
      var wikiSources = d.wiki_sources || [];
      var qdrantSources = d.qdrant_sources || [];
      var confidence = d.confidence || null;
      var wikiEvidence = d.wiki_evidence || null;
      var chunkEvidence = d.chunk_evidence || null;
      var evidenceSummary = d.evidence_summary || '';

      fetch(cfg.queryUrl, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ q: q, session_key: SESSION_KEY, _rendered_answer: answer,
                               _wiki_sources: wikiSources, _qdrant_sources: qdrantSources,
                               _confidence: confidence, _wiki_evidence: wikiEvidence,
                               _chunk_evidence: chunkEvidence, _evidence_summary: evidenceSummary })
      })
        .then(function (r) { return r.json(); })
        .then(function (data) {
          addAIMessage(data.html || '', data.references || [],
            data.markdown || '', data.wiki_sources || [], data.qdrant_sources || [],
            data.confidence || confidence, data.wiki_evidence || wikiEvidence,
            data.chunk_evidence || chunkEvidence, data.evidence_summary || evidenceSummary);
        })
        .catch(function () {
          addAIMessage('<p>' + escapeHtml(answer) + '</p>', [], answer, wikiSources, qdrantSources,
            confidence, wikiEvidence, chunkEvidence, evidenceSummary);
        });
      isLoading = false;
      submitBtn.disabled = false;
      input.focus();
    });

    es.addEventListener('error', function (e) {
      if (es.readyState === EventSource.CLOSED) return;
      es.close();
      removeLoadingMessage();
      var msg = '查询失败，请稍后重试';
      try { msg = JSON.parse(e.data).message || msg; } catch (err) {}
      addErrorMessage(msg);
      isLoading = false;
      submitBtn.disabled = false;
      input.focus();
    });

    es.onerror = function () {
      if (es.readyState === EventSource.CLOSED) return;
      es.close();
      removeLoadingMessage();
      addErrorMessage('连接中断，请重试');
      isLoading = false;
      submitBtn.disabled = false;
      input.focus();
    };
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

  /* ── Query history (localStorage) ────────────────────────── */
  (function initHistory() {
    var STORAGE_KEY = 'llmwiki_history_' + (cfg.repoSlug || 'default');
    var MAX_HISTORY = 20;
    var historyDropdown = document.getElementById('chat-history-dropdown');
    if (!historyDropdown) return;

    function loadHistory() {
      try { return JSON.parse(localStorage.getItem(STORAGE_KEY) || '[]'); }
      catch (e) { return []; }
    }

    function saveToHistory(q) {
      var hist = loadHistory().filter(function(h) { return h !== q; });
      hist.unshift(q);
      if (hist.length > MAX_HISTORY) hist = hist.slice(0, MAX_HISTORY);
      try { localStorage.setItem(STORAGE_KEY, JSON.stringify(hist)); } catch (e) {}
      renderHistory();
    }

    function renderHistory() {
      var hist = loadHistory();
      historyDropdown.innerHTML = '';
      if (!hist.length) {
        historyDropdown.innerHTML = '<li style="padding:0.5rem;color:var(--pico-muted-color);font-size:0.85rem;">暂无历史记录</li>';
        return;
      }
      hist.forEach(function(q) {
        var li = document.createElement('li');
        li.style.cssText = 'padding:0.4rem 0.75rem;cursor:pointer;font-size:0.88rem;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;max-width:280px;';
        li.title = q;
        li.textContent = q;
        li.addEventListener('click', function() {
          input.value = q;
          input.dispatchEvent(new Event('input'));
          historyDropdown.parentElement.removeAttribute('open');
          input.focus();
        });
        historyDropdown.appendChild(li);
      });
    }

    form.addEventListener('submit', function() {
      var q = input.value.trim();
      if (q) saveToHistory(q);
    }, true);

    renderHistory();
  })();

  /* ── Sidebar task progress polling ──────────────────────── */
  (function pollSidebarTasks() {    var items = document.querySelectorAll('.kb-source-item[data-task-id]');
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
