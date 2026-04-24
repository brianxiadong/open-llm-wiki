"""HTML/WebView shell for the confidential client."""

from __future__ import annotations

import json
import re
import sys
import threading
import uuid
from datetime import datetime, timezone
from html import escape
from pathlib import Path
from typing import Any

from flask import Flask, jsonify, render_template, request
from jinja2 import ChoiceLoader, DictLoader
from werkzeug.serving import make_server
from confidential_client.controller import ConfidentialClientController
from confidential_client.version import CLIENT_NAME, CLIENT_VERSION
from utils import render_markdown, safe_upload_basename, slugify

UI_TEXT = {
    "ready": "已就绪",
    "status_ready": "已完成",
    "status_processing": "处理中",
    "status_failed": "失败",
    "status_unknown": "未知",
    "confidence_none": "暂无置信度信息",
    "detail_none": "请选择左侧文档查看详情。",
}

CONFIDENCE_LEVELS = {
    "high": "高",
    "medium": "中",
    "low": "低",
}

PROGRESS_TRANSLATIONS = (
    ("Read source:", "已读取原始文档："),
    ("Analyzing source content", "正在分析文档内容"),
    ("Analysis complete", "内容分析完成"),
    ("Planning wiki updates", "正在规划知识页更新"),
    ("Plan: create", "计划创建"),
    ("Creating ", "正在生成 "),
    ("Updating ", "正在更新 "),
    ("Failed to create ", "生成失败："),
    ("Failed to update ", "更新失败："),
    ("Skipped ", "已跳过 "),
    ("Done writing pages", "知识页写入完成"),
    ("Indexing ", "正在建立索引："),
    (" fact records", " 条事实记录"),
    ("Vector index failed", "向量索引失败"),
    ("Fact index failed", "事实索引失败"),
    ("Vector indexing complete", "向量索引完成"),
    ("Rebuilding index.md", "正在重建 index.md"),
    ("Updating overview.md", "正在更新 overview.md"),
    ("Ingest complete:", "文档处理完成："),
    ("created", "新建"),
    ("updated", "更新"),
    (" chars)", " 字符)"),
    (" …", "..."),
)

STORAGE_PREFIX_RE = re.compile(r"^(?P<prefix>[0-9a-f]{8,})_(?P<name>.+)$", re.IGNORECASE)
FRONTMATTER_RE = re.compile(r"\A---\s*\n.*?\n---\s*", re.DOTALL)

CLIENT_TEMPLATE_BASE = """
<!DOCTYPE html>
<html lang="zh" data-theme="light">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <meta name="theme-color" content="#f8fafc">
  <title>{% block title %}{{ site_name }}{% endblock %}</title>
  <link rel="stylesheet" href="{{ url_for('static', filename='vendor/pico/pico.min.css') }}">
  <link rel="stylesheet" href="{{ url_for('static', filename='css/style.css') }}">
  <style>
    html {
      font-size: 15px;
    }
    body,
    input,
    button,
    select,
    textarea {
      font-size: 0.95rem;
    }
    .client-shell {
      display: grid;
      grid-template-columns: 320px 1fr;
      gap: 1.5rem;
      align-items: start;
    }
    .client-shell > * {
      min-width: 0;
    }
    .client-sidebar {
      position: sticky;
      top: 5rem;
      background: linear-gradient(180deg, var(--surface-panel) 0%, var(--surface-soft) 100%);
      border: 1px solid var(--border-strong);
      border-radius: var(--radius-lg);
      padding: 1rem;
      box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.6), var(--shadow-sm);
    }
    .client-stack {
      display: grid;
      gap: 1.5rem;
      min-width: 0;
    }
    .client-tabs {
      display: flex;
      gap: 0.75rem;
      flex-wrap: wrap;
      margin-bottom: 1rem;
    }
    .client-tab-btn {
      display: inline-flex;
      align-items: center;
      gap: 0.45rem;
      border: 1px solid var(--border);
      border-radius: 999px;
      padding: 0.55rem 0.95rem;
      background: #fff;
      color: var(--text-muted);
      font-weight: 600;
      cursor: pointer;
      transition: all var(--transition);
    }
    .client-tab-btn:hover {
      color: var(--brand);
      border-color: var(--brand);
    }
    .client-tab-btn.active {
      background: var(--brand);
      color: #fff;
      border-color: var(--brand);
      box-shadow: 0 10px 22px rgba(37, 99, 235, 0.18);
    }
    .client-tab-panel[hidden] {
      display: none !important;
    }
    .client-panel {
      background: linear-gradient(180deg, #ffffff 0%, var(--surface-soft) 100%);
      border: 1px solid var(--border);
      border-radius: var(--radius-lg);
      box-shadow: var(--shadow);
      padding: 1.5rem;
      min-width: 0;
    }
    .client-form-grid {
      display: grid;
      gap: 0.85rem;
    }
    .client-form-actions {
      display: flex;
      gap: 0.5rem;
      flex-wrap: wrap;
    }
    .client-progress-card {
      display: grid;
      gap: 0.75rem;
      margin-bottom: 1.25rem;
    }
    .client-progress-meta {
      display: flex;
      justify-content: space-between;
      gap: 0.75rem;
      flex-wrap: wrap;
      color: var(--text-muted);
      font-size: 0.9rem;
    }
    .client-source-layout {
      display: grid;
      gap: 1rem;
      min-width: 0;
    }
    .client-muted {
      color: var(--text-muted);
      font-size: 0.88rem;
    }
    .client-toast-stack {
      display: grid;
      gap: 0.5rem;
      margin-bottom: 1rem;
    }
    .client-toast {
      display: flex;
      align-items: center;
      gap: 0.6rem;
      padding: 0.8rem 1rem;
      border-radius: var(--radius);
      border: 1px solid var(--border);
      background: #fff;
      box-shadow: var(--shadow-sm);
    }
    .client-toast.success {
      background: var(--success-bg);
      color: var(--success);
      border-color: #bbf7d0;
    }
    .client-toast.error {
      background: var(--error-bg);
      color: var(--error);
      border-color: #fecaca;
    }
    .client-dialog::backdrop {
      background: rgba(15, 23, 42, 0.28);
      backdrop-filter: blur(3px);
    }
    .client-dialog {
      width: min(48rem, calc(100vw - 2rem));
      max-width: 48rem;
      border: none;
      padding: 0;
      background: transparent;
    }
    .client-dialog-card {
      background: linear-gradient(180deg, #ffffff 0%, var(--surface-soft) 100%);
      border: 1px solid var(--border);
      border-radius: var(--radius-lg);
      box-shadow: var(--shadow-lg);
      padding: 1.5rem;
    }
    .client-dialog-header {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 1rem;
      margin-bottom: 1rem;
    }
    .client-dialog-title {
      display: flex;
      align-items: center;
      gap: 0.45rem;
      margin: 0;
      font-size: 1.08rem;
    }
    .client-repo-summary {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 0.75rem;
      margin-top: 0.75rem;
      padding-top: 0.75rem;
      border-top: 1px solid var(--border-light);
      color: var(--text-muted);
      font-size: 0.82rem;
    }
    .client-badge {
      display: inline-flex;
      align-items: center;
      gap: 0.25rem;
      font-size: 0.75rem;
      padding: 0.25rem 0.7rem;
      border-radius: 999px;
      background: var(--surface-dim);
      border: 1px solid var(--border);
      color: var(--text-muted);
      font-weight: 600;
    }
    .client-badge.processing {
      background: #fff7ed;
      color: #c2410c;
      border-color: #fdba74;
    }
    .client-badge.ready {
      background: #dcfce7;
      color: #15803d;
      border-color: #86efac;
    }
    .client-badge.failed {
      background: #fef2f2;
      color: #dc2626;
      border-color: #fca5a5;
    }
    .client-documents-table {
      table-layout: fixed;
    }
    .client-documents-table th:nth-child(1),
    .client-documents-table td:nth-child(1) {
      width: 42%;
    }
    .client-documents-table th:nth-child(2),
    .client-documents-table td:nth-child(2) {
      width: 9%;
      white-space: nowrap;
    }
    .client-documents-table th:nth-child(3),
    .client-documents-table td:nth-child(3) {
      width: 9%;
      white-space: nowrap;
    }
    .client-documents-table th:nth-child(4),
    .client-documents-table td:nth-child(4) {
      width: 10%;
      white-space: nowrap;
    }
    .client-documents-table th:nth-child(5),
    .client-documents-table td:nth-child(5) {
      width: 14%;
    }
    .client-documents-table th:nth-child(6),
    .client-documents-table td:nth-child(6) {
      width: 16%;
      white-space: nowrap;
    }
    .client-doc-name-btn {
      width: 100%;
      justify-content: flex-start;
      max-width: 100%;
    }
    .client-doc-name-text {
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }
    .client-doc-time {
      display: grid;
      gap: 0.1rem;
      line-height: 1.35;
    }
    .client-doc-time-date {
      font-weight: 600;
      color: var(--text);
    }
    .client-doc-time-clock {
      color: var(--text-muted);
      font-size: 0.82rem;
    }
    .client-evidence {
      display: grid;
      gap: 0.9rem;
    }
    .client-evidence-section {
      border: 1px solid var(--border-light);
      border-radius: var(--radius);
      background: rgba(255, 255, 255, 0.72);
      padding: 0.9rem 1rem;
    }
    .client-evidence-heading {
      margin: 0 0 0.7rem;
      font-size: 0.92rem;
      font-weight: 700;
      color: var(--text);
    }
    .client-evidence-kpis {
      display: flex;
      flex-wrap: wrap;
      gap: 0.6rem;
      margin-bottom: 0.7rem;
    }
    .client-evidence-pill {
      display: inline-flex;
      align-items: center;
      gap: 0.35rem;
      padding: 0.35rem 0.7rem;
      border-radius: 999px;
      background: var(--surface-soft);
      border: 1px solid var(--border);
      color: var(--text);
      font-size: 0.82rem;
      font-weight: 600;
    }
    .client-evidence-list {
      margin: 0;
      padding-left: 1.15rem;
    }
    .client-evidence-list li + li {
      margin-top: 0.55rem;
    }
    .client-evidence-summary-text,
    .client-evidence-empty,
    .client-evidence-note {
      margin: 0;
      color: var(--text-muted);
      line-height: 1.7;
    }
    .client-evidence-meta {
      display: block;
      margin-top: 0.2rem;
      color: var(--text-muted);
      font-size: 0.82rem;
    }
    .client-evidence-snippets {
      display: grid;
      gap: 0.7rem;
    }
    .client-evidence-snippet {
      border: 1px solid var(--border-light);
      border-radius: var(--radius);
      padding: 0.85rem 0.9rem;
      background: #fff;
    }
    .client-evidence-snippet-title {
      margin: 0 0 0.35rem;
      font-size: 0.9rem;
      font-weight: 700;
      color: var(--text);
    }
    .client-evidence-snippet-text {
      margin: 0;
      color: var(--text-muted);
      line-height: 1.65;
    }
    @media (max-width: 980px) {
      .client-shell,
      .client-sidebar {
        position: static;
      }
      .client-documents-table {
        table-layout: auto;
      }
    }
  </style>
  {% block head %}{% endblock %}
</head>
<body>
  <a href="#main-content" class="skip-link">跳到主要内容</a>
  <header class="site-header">
    <nav class="container site-nav" aria-label="主导航">
      <ul>
        <li>
          <a href="{{ url_for('client_home') }}" class="brand">
            <i data-lucide="shield-check"></i>
            <span>{{ site_name }}</span>
          </a>
        </li>
      </ul>
      <ul>
        <li><span class="client-badge">本地机密模式</span></li>
        <li><span class="client-muted">文档管理 · 智能问答</span></li>
      </ul>
    </nav>
  </header>

  <main id="main-content" class="container main-content" tabindex="-1">
    {% block content %}{% endblock %}
  </main>

  <footer class="site-footer container" role="contentinfo">
    <div class="site-footer-inner">
      <p class="site-footer-tagline">{{ site_name }} — 客户端侧完整运行链路</p>
      <p class="site-footer-meta">本地加密仓库 · 本地文档处理 · 本地问答检索</p>
    </div>
  </footer>

  <script src="{{ url_for('static', filename='vendor/lucide/lucide.min.js') }}"></script>
  <script>
    window.CLIENT_BOOTSTRAP = {{ bootstrap|tojson }};
  </script>
  {% block scripts %}{% endblock %}
</body>
</html>
"""

CLIENT_TEMPLATE_INDEX = """
{% extends "client/base.html" %}

{% block title %}机密客户端 - {{ site_name }}{% endblock %}

{% block content %}
<div id="toast-stack" class="client-toast-stack"></div>

<div class="client-shell">
  <aside class="client-sidebar">
    <div class="sidebar-group">
      <div class="sidebar-group-title">
        <i data-lucide="library-big"></i>
        当前知识库
      </div>
      <div class="client-form-grid">
        <label for="repo-select">已存在知识库</label>
        <select id="repo-select">
          {% if bootstrap.repos %}
          {% for repo in bootstrap.repos %}
          <option value="{{ repo.repo_uuid }}" {% if repo.repo_uuid == bootstrap.active_repo_uuid %}selected{% endif %}>
            {{ repo.name }} [{{ repo.slug }}]
          </option>
          {% endfor %}
          {% else %}
          <option value="">还没有本地知识库</option>
          {% endif %}
        </select>

        <label for="repo-passphrase">访问口令</label>
        <input id="repo-passphrase" type="password" placeholder="输入当前知识库口令">
        <span id="repo-passphrase-hint" class="client-muted">当前知识库默认启用本地加密。</span>

        <div class="client-form-actions">
          <button type="button" class="action-btn action-btn-primary" id="open-repo-btn">
            <i data-lucide="folder-open"></i>
            载入内容
          </button>
          <button type="button" class="action-btn" id="refresh-bootstrap-btn">
            <i data-lucide="refresh-cw"></i>
            刷新
          </button>
        </div>
      </div>
      <div class="client-repo-summary">
        <span id="active-repo-label">未载入知识库</span>
        <span id="repo-count-label">{{ bootstrap.repos|length }} 个本地库</span>
      </div>
    </div>

    <div class="sidebar-group">
      <div class="sidebar-group-title">
        <i data-lucide="plus-circle"></i>
        新建知识库
      </div>
      <button type="button" class="action-btn action-btn-primary" id="open-create-dialog-btn">
        <i data-lucide="shield-plus"></i>
        创建知识库
      </button>
      <p class="client-muted" style="margin:0.9rem 0 0;">
        大模型、Embedding、Qdrant、MinerU 参数从本地私有配置文件自动读取，终端用户不感知。
      </p>
    </div>
  </aside>

  <section class="client-stack">
    <div class="client-tabs" role="tablist" aria-label="主功能标签">
      <button
        type="button"
        class="client-tab-btn active"
        id="tab-documents"
        data-target="panel-documents"
        role="tab"
        aria-controls="panel-documents"
        aria-selected="true"
      >
        <i data-lucide="folder-open"></i>
        文档管理
      </button>
      <button
        type="button"
        class="client-tab-btn"
        id="tab-query"
        data-target="panel-query"
        role="tab"
        aria-controls="panel-query"
        aria-selected="false"
      >
        <i data-lucide="search"></i>
        智能问答
      </button>
    </div>

    <article class="client-panel client-tab-panel" id="panel-documents" role="tabpanel" aria-labelledby="tab-documents">
      <header class="page-header">
        <h2 style="margin:0;display:flex;align-items:center;gap:0.5rem;font-size:1.5rem;">
          <i data-lucide="folder-open"></i>
          文档管理
          <span class="badge badge-muted" id="documents-count-badge" style="font-size:0.75rem;" hidden>0 个文件</span>
        </h2>
        <div class="page-header-actions">
          <button type="button" class="action-btn action-btn-primary" id="pick-file-btn">
            <i data-lucide="upload"></i>
            上传文档
          </button>
          <button type="button" class="action-btn" id="refresh-documents-btn">
            <i data-lucide="list-restart"></i>
            刷新列表
          </button>
        </div>
      </header>

      <div class="source-toolbar">
        <div class="source-toolbar-left">
          <span class="client-muted">管理本地机密文档，查看处理状态与最近更新时间。</span>
        </div>
        <div class="source-toolbar-right">
          <div class="source-filter">
            <i data-lucide="filter"></i>
            <select id="status-filter">
              <option value="all">全部状态</option>
              <option value="ready">已完成</option>
              <option value="processing">处理中</option>
              <option value="failed">失败</option>
            </select>
          </div>
          <div class="source-sort">
            <i data-lucide="arrow-up-down"></i>
            <select id="sort-by">
              <option value="date-desc">时间 新→旧</option>
              <option value="date-asc">时间 旧→新</option>
              <option value="name-asc">名称 A→Z</option>
              <option value="name-desc">名称 Z→A</option>
            </select>
          </div>
        </div>
      </div>

      <div class="client-progress-card">
        <div class="page-header" style="margin-bottom:0;">
          <h3 style="margin:0;font-size:1.05rem;display:flex;align-items:center;gap:0.4rem;">
            <i data-lucide="activity"></i>
            处理进度
          </h3>
          <span id="progress-badge" class="client-badge">空闲</span>
        </div>
        <progress id="progress-bar" value="0" max="100"></progress>
        <div class="client-progress-meta">
          <span id="progress-message">等待上传文档</span>
          <span id="progress-percent">0%</span>
        </div>
      </div>

      <div class="client-source-layout">
        <article id="documents-table-card" style="padding:0;">
          <div style="overflow-x:auto;">
            <table class="source-table client-documents-table" role="grid">
              <thead>
                <tr>
                  <th>文件名</th>
                  <th>大小</th>
                  <th>类型</th>
                  <th>状态</th>
                  <th>最近更新</th>
                  <th>操作</th>
                </tr>
              </thead>
              <tbody id="documents-body">
                <tr>
                  <td colspan="6" class="client-muted">请选择知识库并输入口令后载入内容。</td>
                </tr>
              </tbody>
            </table>
          </div>
        </article>

        <article class="empty-state" id="documents-empty" hidden>
          <i data-lucide="file-plus"></i>
          <h3>还没有文档</h3>
          <p>上传文档后，可以将其摄入为本地机密知识页面。</p>
        </article>

        <article id="document-detail-card" hidden>
          <header style="padding:0;margin-bottom:1rem;border:none;">
            <h3 style="margin:0;display:flex;align-items:center;gap:0.4rem;font-size:1.1rem;">
              <i data-lucide="file-text"></i>
              文档详情
            </h3>
          </header>
          <div id="document-detail" class="client-muted" style="white-space:pre-wrap;line-height:1.7;">
            请选择左侧文档查看详情。
          </div>
        </article>
      </div>
    </article>

    <article
      class="client-panel client-tab-panel"
      id="panel-query"
      role="tabpanel"
      aria-labelledby="tab-query"
      hidden
    >
      <header class="page-header">
        <h2 style="margin:0;display:flex;align-items:center;gap:0.5rem;font-size:1.5rem;">
          <i data-lucide="search"></i>
          智能问答
        </h2>
      </header>

      <form id="query-form" class="query-form" style="margin-bottom:1rem;">
        <div class="query-input-row">
          <input
            type="text"
            id="query-input"
            name="q"
            placeholder="输入你的问题，客户端会直接在本地机密知识库中完成检索和生成..."
            required
          >
          <button type="submit" id="query-submit">
            <i data-lucide="send-horizontal"></i>
            查询
          </button>
        </div>
      </form>

      <div id="query-loading" class="loading-indicator" hidden>
        <i data-lucide="loader-2" class="icon-spin"></i>
        <span aria-busy="true">正在查询，请稍候…</span>
      </div>

      <div id="query-result" hidden>
        <article>
          <header style="padding:0;margin-bottom:1rem;border:none;">
            <h3 style="margin:0;display:flex;align-items:center;gap:0.4rem;font-size:1.15rem;">
              <i data-lucide="message-square-text"></i>
              查询结果
            </h3>
          </header>
          <div id="result-content" class="wiki-content rendered-content" style="margin-bottom:1.25rem;"></div>

          <section id="result-references" class="mb-2">
            <h4 style="display:flex;align-items:center;gap:0.35rem;font-size:1rem;margin:0 0 0.5rem;">
              <i data-lucide="book-marked"></i>
              证据摘要
            </h4>
            <div
              id="evidence-text"
              class="client-evidence"
            >暂无置信度信息。</div>
          </section>
        </article>
      </div>

      <div id="query-error" hidden>
        <article class="flash-toast flash-error" role="alert" style="display:flex;align-items:flex-start;gap:0.5rem;">
          <i data-lucide="alert-circle" style="flex-shrink:0;margin-top:0.1rem;"></i>
          <span id="error-message"></span>
        </article>
      </div>
    </article>
  </section>
</div>

<dialog id="upload-dialog" class="client-dialog">
  <article class="client-dialog-card">
    <header class="client-dialog-header">
      <h3 class="client-dialog-title">
        <i data-lucide="upload"></i>
        新增文档
      </h3>
      <button type="button" class="flash-close" id="close-upload-dialog" aria-label="关闭">&times;</button>
    </header>
    <form id="upload-form">
      <article class="upload-card" id="upload-zone" style="margin-bottom:0;">
        <div class="drop-zone" id="drop-zone">
          <i data-lucide="cloud-upload" style="font-size:2rem;color:var(--brand);"></i>
          <p style="margin:0.5rem 0 0;font-weight:500;">拖拽文件到此处，或点击选择</p>
          <p class="upload-hint" style="margin:0.25rem 0 0;">
            Markdown、TXT、CSV、Excel、PDF、Word、PPT、图片
          </p>
          <input
            type="file"
            name="files"
            id="file-input"
            accept=".md,.txt,.csv,.xlsx,.xls,.pdf,.doc,.docx,.ppt,.pptx,.png,.jpg,.jpeg"
            multiple
            style="display:none;"
          >
        </div>
        <div id="file-selected" class="upload-selected" style="display:none;">
          <div class="upload-selected-meta">
            <span class="upload-selected-label">已选择文件</span>
            <div class="upload-selected-file">
              <i data-lucide="file-text"></i>
              <span id="file-name-display"></span>
            </div>
            <button type="button" id="clear-file" class="btn-sm upload-selected-clear">
              <i data-lucide="x"></i>
              重新选择
            </button>
          </div>
          <div class="upload-selected-actions" style="margin-top:0.8rem;">
            <button type="submit" class="action-btn action-btn-primary" id="upload-submit-btn">
              <i data-lucide="play-circle"></i>
              上传并处理
            </button>
          </div>
        </div>
      </article>
    </form>
  </article>
</dialog>

<dialog id="create-dialog" class="client-dialog">
  <article class="client-dialog-card">
    <header class="client-dialog-header">
      <h3 class="client-dialog-title">
        <i data-lucide="shield-plus"></i>
        创建知识库
      </h3>
      <button type="button" class="flash-close" id="close-create-dialog" aria-label="关闭">&times;</button>
    </header>
    <div class="client-form-grid">
      <label for="create-name">名称</label>
      <input id="create-name" type="text" value="本地机密知识库">

      <label for="create-slug">标识</label>
      <input id="create-slug" type="text" value="confidential-kb">

      <label for="create-storage-mode">本地存储模式</label>
      <select id="create-storage-mode">
        <option value="encrypted">本地加密模式</option>
        <option value="plain">本地明文模式</option>
      </select>

      <div id="create-passphrase-group">
        <label for="create-passphrase">口令</label>
        <input id="create-passphrase" type="password" placeholder="新知识库访问口令">
      </div>
      <span id="create-storage-hint" class="client-muted">加密模式需要口令；明文模式不加密、不需要口令，使用更方便。</span>

      <div class="client-form-actions">
        <button type="button" class="action-btn" id="cancel-create-dialog">
          <i data-lucide="x"></i>
          取消
        </button>
        <button type="button" class="action-btn action-btn-primary" id="create-repo-btn">
          <i data-lucide="shield-plus"></i>
          创建知识库
        </button>
      </div>
    </div>
  </article>
</dialog>
{% endblock %}

{% block scripts %}
<script>
(function () {
  var state = {
    repos: window.CLIENT_BOOTSTRAP.repos || [],
    activeRepoId: window.CLIENT_BOOTSTRAP.active_repo_uuid || '',
    passphrase: '',
    documents: [],
    tasks: {},
    taskOrder: [],
    taskNotices: {},
    pollingTimer: null,
    pollingInFlight: false
  };

  var repoSelect = document.getElementById('repo-select');
  var repoPassphrase = document.getElementById('repo-passphrase');
  var repoPassphraseHint = document.getElementById('repo-passphrase-hint');
  var openCreateDialogBtn = document.getElementById('open-create-dialog-btn');
  var createName = document.getElementById('create-name');
  var createSlug = document.getElementById('create-slug');
  var createStorageMode = document.getElementById('create-storage-mode');
  var createPassphrase = document.getElementById('create-passphrase');
  var createPassphraseGroup = document.getElementById('create-passphrase-group');
  var createStorageHint = document.getElementById('create-storage-hint');
  var createDialog = document.getElementById('create-dialog');
  var closeCreateDialogBtn = document.getElementById('close-create-dialog');
  var cancelCreateDialogBtn = document.getElementById('cancel-create-dialog');
  var activeRepoLabel = document.getElementById('active-repo-label');
  var repoCountLabel = document.getElementById('repo-count-label');
  var documentsBody = document.getElementById('documents-body');
  var documentsCountBadge = document.getElementById('documents-count-badge');
  var documentsEmpty = document.getElementById('documents-empty');
  var documentsTableCard = document.getElementById('documents-table-card');
  var documentDetailCard = document.getElementById('document-detail-card');
  var documentDetail = document.getElementById('document-detail');
  var toastStack = document.getElementById('toast-stack');
  var progressBar = document.getElementById('progress-bar');
  var progressMessage = document.getElementById('progress-message');
  var progressPercent = document.getElementById('progress-percent');
  var progressBadge = document.getElementById('progress-badge');
  var queryLoading = document.getElementById('query-loading');
  var queryResult = document.getElementById('query-result');
  var queryError = document.getElementById('query-error');
  var errorMessage = document.getElementById('error-message');
  var answerHtml = document.getElementById('result-content');
  var evidenceText = document.getElementById('evidence-text');
  var fileInput = document.getElementById('file-input');
  var fileSelected = document.getElementById('file-selected');
  var fileNameDisplay = document.getElementById('file-name-display');
  var dropZone = document.getElementById('drop-zone');
  var uploadForm = document.getElementById('upload-form');
  var uploadSubmitBtn = document.getElementById('upload-submit-btn');
  var uploadDialog = document.getElementById('upload-dialog');
  var closeUploadDialogBtn = document.getElementById('close-upload-dialog');
  var tabButtons = Array.prototype.slice.call(document.querySelectorAll('.client-tab-btn'));
  var tabPanels = Array.prototype.slice.call(document.querySelectorAll('.client-tab-panel'));

  function escapeHtml(value) {
    return String(value == null ? '' : value)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;');
  }

  function displayFilename(filename) {
    var text = String(filename || '').trim();
    var matched = text.match(/^([0-9a-f]{8,})_(.+)$/i);
    return matched ? matched[2] : text;
  }

  function formatDateTimeParts(value) {
    if (!value) {
      return { date: '-', time: '' };
    }
    var parsed = new Date(value);
    if (Number.isNaN(parsed.getTime())) {
      var text = String(value);
      var compact = text.replace('T', ' ').replace(/(\\.\\d+)?(?:Z|[+-]\\d\\d:\\d\\d)?$/, '');
      var pieces = compact.split(' ');
      return { date: pieces[0] || text, time: pieces[1] || '' };
    }
    var date = [
      parsed.getFullYear(),
      String(parsed.getMonth() + 1).padStart(2, '0'),
      String(parsed.getDate()).padStart(2, '0')
    ].join('-');
    var time = [
      String(parsed.getHours()).padStart(2, '0'),
      String(parsed.getMinutes()).padStart(2, '0')
    ].join(':');
    return { date: date, time: time };
  }

  function setActiveTab(targetId) {
    tabButtons.forEach(function (button) {
      var active = button.dataset.target === targetId;
      button.classList.toggle('active', active);
      button.setAttribute('aria-selected', active ? 'true' : 'false');
    });
    tabPanels.forEach(function (panel) {
      panel.hidden = panel.id !== targetId;
    });
    refreshIcons();
  }

  function createIcon(name) {
    return '<i data-lucide="' + name + '"></i>';
  }

  function refreshIcons() {
    if (window.lucide && window.lucide.createIcons) {
      window.lucide.createIcons();
    }
  }

  function showToast(type, message) {
    var item = document.createElement('div');
    item.className = 'client-toast ' + type;
    item.innerHTML = createIcon(type === 'success' ? 'check-circle' : 'alert-circle') +
      '<span>' + message + '</span>';
    toastStack.prepend(item);
    refreshIcons();
    window.setTimeout(function () {
      item.remove();
    }, 2800);
  }

  function currentRepo() {
    return state.repos.find(function (item) {
      return item.repo_uuid === state.activeRepoId;
    }) || null;
  }

  function currentRepoRequiresPassphrase() {
    var repo = currentRepo();
    return !repo || repo.requires_passphrase !== false;
  }

  function syncRepoAccessUI() {
    if (currentRepoRequiresPassphrase()) {
      repoPassphrase.disabled = false;
      repoPassphrase.placeholder = '输入当前知识库口令';
      repoPassphraseHint.textContent = '当前知识库默认启用本地加密。';
      return;
    }
    repoPassphrase.value = '';
    state.passphrase = '';
    repoPassphrase.disabled = true;
    repoPassphrase.placeholder = '当前知识库无需口令';
    repoPassphraseHint.textContent = '当前知识库为本地明文模式，无需输入口令。';
  }

  function syncCreateAccessUI() {
    var encrypted = (createStorageMode.value || 'encrypted') === 'encrypted';
    createPassphrase.disabled = !encrypted;
    createPassphraseGroup.hidden = !encrypted;
    if (!encrypted) {
      createPassphrase.value = '';
      createStorageHint.textContent = '明文模式不会对本地仓库加密，也不需要访问口令，适合单机便捷使用。';
      return;
    }
    createStorageHint.textContent = '加密模式需要口令；明文模式不加密、不需要口令，使用更方便。';
  }

  function resolveActivePassphrase() {
    if (!currentRepoRequiresPassphrase()) {
      state.passphrase = '';
      return '';
    }
    state.passphrase = repoPassphrase.value.trim();
    if (!state.passphrase) {
      showToast('error', '请先输入访问口令');
      return null;
    }
    return state.passphrase;
  }

  function renderRepoSummary() {
    repoCountLabel.textContent = state.repos.length + ' 个本地库';
    var repo = currentRepo();
    if (!repo) {
      activeRepoLabel.textContent = '未载入知识库';
      return;
    }
    activeRepoLabel.textContent = '当前：' + repo.name + (repo.storage_mode === 'plain' ? ' · 明文' : ' · 加密');
  }

  function isTaskActive(task) {
    return task && (task.status === 'queued' || task.status === 'running');
  }

  function taskStatusText(task) {
    if (!task) {
      return '处理中';
    }
    if (task.status === 'queued') {
      return '排队中';
    }
    if (task.status === 'failed') {
      return '失败';
    }
    if (task.status === 'done') {
      return '已完成';
    }
    return '处理中';
  }

  function statusBadge(status) {
    var map = {
      ready: ['ready', '已完成'],
      processing: ['processing', '处理中'],
      failed: ['failed', '失败'],
      queued: ['processing', '排队中']
    };
    var info = map[status] || ['unknown', '未知'];
    return '<span class="client-badge ' + info[0] + '">' + info[1] + '</span>';
  }

  function decorateDocument(doc) {
    var normalized = Object.assign({}, doc);
    normalized.status = normalized.status || 'processing';
    normalized.progress = Number(normalized.progress || 0);
    normalized.progress_message = normalized.progress_message || '';
    if (!normalized.detail) {
      var statusText = normalized.status === 'ready'
        ? '已完成'
        : normalized.status === 'failed'
          ? '失败'
          : '处理中';
      normalized.detail = [
        '文件名: ' + (displayFilename(normalized.filename) || '-'),
        '状态: ' + statusText,
        '进度: ' + normalized.progress + '%',
        normalized.progress_message ? ('进度说明: ' + normalized.progress_message) : '',
        '最后更新: ' + ((function () {
          var parts = formatDateTimeParts(normalized.updated_at || '');
          return parts.time ? (parts.date + ' ' + parts.time) : parts.date;
        })())
      ].filter(Boolean).join('\\n');
    }
    return normalized;
  }

  function upsertDocument(doc) {
    var normalized = decorateDocument(doc);
    var index = state.documents.findIndex(function (item) {
      return item.filename === normalized.filename;
    });
    if (index >= 0) {
      state.documents[index] = Object.assign({}, state.documents[index], normalized);
    } else {
      state.documents.push(normalized);
    }
  }

  function mergeDocuments(documents) {
    (documents || []).forEach(upsertDocument);
  }

  function taskDocument(task) {
    var status = task.status === 'failed' ? 'failed' : (task.status === 'done' ? 'ready' : 'processing');
    return decorateDocument({
      filename: task.filename || '',
      file_ext: task.file_ext || '',
      size_display: task.size_display || '-',
      size_bytes: task.size_bytes || 0,
      status: status,
      progress: task.progress || 0,
      progress_message: task.message || '',
      updated_at: task.updated_at || task.created_at || '',
      detail: [
        '文件名: ' + (displayFilename(task.filename) || '-'),
        '状态: ' + taskStatusText(task),
        '进度: ' + Number(task.progress || 0) + '%',
        '进度说明: ' + (task.message || (task.status === 'queued' ? '等待排队处理' : '已接收文档，准备处理')),
        '最后更新: ' + ((function () {
          var parts = formatDateTimeParts(task.updated_at || task.created_at || '');
          return parts.time ? (parts.date + ' ' + parts.time) : parts.date;
        })())
      ].join('\\n')
    });
  }

  function repoTasks(repoUuid) {
    return state.taskOrder
      .map(function (taskId) { return state.tasks[taskId]; })
      .filter(function (task) { return task && task.repo_uuid === repoUuid; });
  }

  function activeTaskIds() {
    return state.taskOrder.filter(function (taskId) {
      return isTaskActive(state.tasks[taskId]);
    });
  }

  function refreshDocumentsFromTasks() {
    if (!state.activeRepoId) {
      return;
    }
    repoTasks(state.activeRepoId).forEach(function (task) {
      if (isTaskActive(task)) {
        upsertDocument(taskDocument(task));
      }
    });
  }

  function renderRepos() {
    repoSelect.innerHTML = '';
    state.repos.forEach(function (repo) {
      var opt = document.createElement('option');
      opt.value = repo.repo_uuid;
      opt.textContent = repo.name + ' [' + repo.slug + ']';
      repoSelect.appendChild(opt);
    });
    if (state.repos.length === 0) {
      var emptyOpt = document.createElement('option');
      emptyOpt.value = '';
      emptyOpt.textContent = '还没有本地知识库';
      repoSelect.appendChild(emptyOpt);
      state.activeRepoId = '';
    } else if (!state.activeRepoId) {
      state.activeRepoId = state.repos[0].repo_uuid;
    }
    repoSelect.value = state.activeRepoId;
    renderRepoSummary();
    syncRepoAccessUI();
  }

  function renderDocuments() {
    state.documents.sort(function (a, b) {
      return String(b.updated_at || '').localeCompare(String(a.updated_at || ''));
    });
    documentsBody.innerHTML = '';
    documentsCountBadge.hidden = !state.documents.length;
    documentsCountBadge.textContent = state.documents.length + ' 个文件';
    if (!state.documents.length) {
      documentsTableCard.hidden = true;
      documentDetailCard.hidden = true;
      documentsEmpty.hidden = false;
      documentDetail.textContent = '请选择左侧文档查看详情。';
      return;
    }
    documentsTableCard.hidden = false;
    documentsEmpty.hidden = true;
    documentDetailCard.hidden = false;
    state.documents.forEach(function (doc, index) {
      var rawName = doc.filename || '';
      var friendlyName = displayFilename(rawName) || '-';
      var updatedAt = formatDateTimeParts(doc.updated_at || '');
      var docIndex = String(index);
      var deleteDisabled = doc.status === 'processing' ? ' disabled' : '';
      var tr = document.createElement('tr');
      tr.dataset.filename = rawName;
      tr.dataset.date = String(doc.updated_at || '');
      tr.dataset.status = doc.status || '';
      tr.innerHTML = [
        '<td><button type="button" class="btn-sm doc-open client-doc-name-btn" data-doc-index="' +
          docIndex + '">' +
          '<i data-lucide="eye"></i><span class="client-doc-name-text">' + escapeHtml(friendlyName) + '</span></button></td>',
        '<td>' + (doc.size_display || '-') + '</td>',
        '<td><span class="badge badge-muted">' + (doc.file_ext || '-') + '</span></td>',
        '<td>' + statusBadge(doc.status) + '</td>',
        '<td><div class="client-doc-time"><span class="client-doc-time-date">' + escapeHtml(updatedAt.date) +
          '</span>' + (updatedAt.time ? '<span class="client-doc-time-clock">' + escapeHtml(updatedAt.time) +
          '</span>' : '') + '</div></td>',
        '<td class="source-actions">' +
          '<button type="button" class="btn-sm doc-open" data-doc-index="' + docIndex + '">' +
          '<i data-lucide="file-text"></i>详情</button>' +
          '<button type="button" class="btn-sm doc-delete secondary" data-doc-index="' + docIndex + '"' +
          deleteDisabled + '><i data-lucide="trash-2"></i>删除</button></td>'
      ].join('');
      documentsBody.appendChild(tr);
    });
    document.querySelectorAll('.doc-open').forEach(function (btn) {
      btn.addEventListener('click', function () {
        var doc = state.documents[Number(btn.dataset.docIndex || -1)];
        if (doc) {
          documentDetail.textContent = doc.detail || '';
        }
      });
    });
    document.querySelectorAll('.doc-delete').forEach(function (btn) {
      btn.addEventListener('click', function () {
        var doc = state.documents[Number(btn.dataset.docIndex || -1)];
        if (doc) {
          deleteDocument(doc.filename || '', doc.status || '');
        }
      });
    });
    if (state.documents[0]) {
      documentDetail.textContent = state.documents[0].detail || '';
    }
    refreshIcons();
  }

  function updateProgress(payload) {
    var progress = Number(payload.progress || 0);
    progressBar.value = progress;
    progressPercent.textContent = progress + '%';
    progressMessage.textContent = payload.message || '等待上传文档';
    progressBadge.className = 'client-badge ' + (payload.status || '');
    progressBadge.textContent = payload.badge || '处理中';
  }

  function syncTask(task, options) {
    var opts = options || {};
    if (!task || !task.task_id) {
      return;
    }
    state.tasks[task.task_id] = task;
    if (state.taskOrder.indexOf(task.task_id) === -1) {
      state.taskOrder.push(task.task_id);
    }
    if (task.repo_uuid === state.activeRepoId) {
      if (task.documents && task.documents.length) {
        mergeDocuments(task.documents);
      } else if (isTaskActive(task) || task.status === 'failed') {
        upsertDocument(taskDocument(task));
      }
    }
    if ((task.status === 'done' || task.status === 'failed') && !opts.silent) {
      var noticeKey = task.status + ':' + task.task_id;
      if (!state.taskNotices[noticeKey]) {
        var noticeMessage = task.filename || '文档';
        if (task.status === 'done') {
          noticeMessage += ' 处理完成';
        } else {
          noticeMessage += ' 处理失败：' + (task.message || '未知错误');
        }
        state.taskNotices[noticeKey] = true;
        showToast(task.status === 'done' ? 'success' : 'error', noticeMessage);
      }
    }
  }

  function replaceActiveTasks(tasks, options) {
    var opts = options || {};
    var activeIds = {};
    (tasks || []).forEach(function (task) {
      activeIds[task.task_id] = true;
      syncTask(task, opts);
    });
    state.taskOrder = state.taskOrder.filter(function (taskId) {
      var task = state.tasks[taskId];
      if (!task) {
        return false;
      }
      if (!isTaskActive(task)) {
        return true;
      }
      if (activeIds[taskId]) {
        return true;
      }
      delete state.tasks[taskId];
      return false;
    });
  }

  function updateProgressFromTasks() {
    var activeTasks = repoTasks(state.activeRepoId).filter(isTaskActive);
    if (!activeTasks.length) {
      updateProgress({ progress: 0, message: '等待上传文档', status: 'ready', badge: '空闲' });
      return;
    }
    var current = activeTasks[activeTasks.length - 1];
    var suffix = activeTasks.length > 1 ? ('，队列中还有 ' + (activeTasks.length - 1) + ' 个文件') : '';
    updateProgress({
      progress: current.progress || 0,
      message: (current.message || '已接收文档，准备处理') + suffix,
      status: current.status === 'failed' ? 'failed' : 'processing',
      badge: taskStatusText(current)
    });
  }

  function pollTasksNow() {
    var ids = activeTaskIds();
    if (!ids.length) {
      if (state.pollingTimer) {
        window.clearInterval(state.pollingTimer);
        state.pollingTimer = null;
      }
      updateProgressFromTasks();
      renderDocuments();
      applyDocumentFilters();
      return;
    }
    if (state.pollingInFlight) {
      return;
    }
    state.pollingInFlight = true;
    Promise.all(
      ids.map(function (taskId) {
        return fetch('/api/tasks/' + taskId)
          .then(function (resp) { return resp.json().then(function (data) { return [resp, data]; }); })
          .then(function (parts) {
            if (!parts[0].ok) {
              throw new Error(parts[1].error || '任务不存在');
            }
            return parts[1];
          });
      })
    )
      .then(function (tasks) {
        tasks.forEach(function (task) {
          syncTask(task);
        });
        refreshDocumentsFromTasks();
        updateProgressFromTasks();
        renderDocuments();
        applyDocumentFilters();
        uploadSubmitBtn.disabled = false;
      })
      .catch(function (err) {
        showToast('error', err.message || '轮询处理进度失败');
      })
      .finally(function () {
        state.pollingInFlight = false;
        if (!activeTaskIds().length && state.pollingTimer) {
          window.clearInterval(state.pollingTimer);
          state.pollingTimer = null;
        }
      });
  }

  function ensureTaskPolling() {
    if (!activeTaskIds().length) {
      if (state.pollingTimer) {
        window.clearInterval(state.pollingTimer);
        state.pollingTimer = null;
      }
      return;
    }
    if (!state.pollingTimer) {
      state.pollingTimer = window.setInterval(pollTasksNow, 1200);
    }
    pollTasksNow();
  }

  function bootstrap() {
    fetch('/api/bootstrap')
      .then(function (resp) { return resp.json(); })
      .then(function (data) {
        state.repos = data.repos || [];
        if (!state.repos.find(function (repo) { return repo.repo_uuid === state.activeRepoId; })) {
          state.activeRepoId = state.repos.length ? state.repos[0].repo_uuid : '';
        }
        replaceActiveTasks(data.tasks || [], { silent: true });
        state.documents = [];
        refreshDocumentsFromTasks();
        renderRepos();
        renderDocuments();
        applyDocumentFilters();
        updateProgressFromTasks();
        ensureTaskPolling();
      })
      .catch(function () {
        showToast('error', '刷新知识库列表失败');
      });
  }

  function openRepo() {
    if (!state.activeRepoId) {
      showToast('error', '请先选择知识库');
      return;
    }
    var passphrase = resolveActivePassphrase();
    if (passphrase === null) {
      return;
    }
    fetch('/api/repositories/' + state.activeRepoId + '/documents', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ passphrase: passphrase })
    })
      .then(function (resp) { return resp.json().then(function (data) { return [resp, data]; }); })
      .then(function (parts) {
        var resp = parts[0];
        var data = parts[1];
        if (!resp.ok) {
          throw new Error(data.error || '载入失败');
        }
        state.documents = data.documents || [];
        replaceActiveTasks(data.tasks || [], { silent: true });
        refreshDocumentsFromTasks();
        renderDocuments();
        applyDocumentFilters();
        updateProgressFromTasks();
        ensureTaskPolling();
        renderRepoSummary();
        showToast('success', '知识库内容已载入');
      })
      .catch(function (err) {
        showToast('error', err.message);
      });
  }

  function createRepo() {
    var storageMode = createStorageMode.value || 'encrypted';
    var passphrase = storageMode === 'encrypted' ? createPassphrase.value.trim() : '';
    if (storageMode === 'encrypted' && !passphrase) {
      showToast('error', '请先输入新知识库口令');
      return;
    }
    fetch('/api/repositories', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        name: createName.value.trim() || '本地机密知识库',
        slug: createSlug.value.trim() || 'confidential-kb',
        passphrase: passphrase,
        storage_mode: storageMode
      })
    })
      .then(function (resp) { return resp.json().then(function (data) { return [resp, data]; }); })
      .then(function (parts) {
        var resp = parts[0];
        var data = parts[1];
        if (!resp.ok) {
          throw new Error(data.error || '创建失败');
        }
        state.repos = data.repos || [];
        state.activeRepoId = data.repo.repo_uuid;
        repoPassphrase.value = passphrase;
        state.passphrase = passphrase;
        renderRepos();
        state.documents = data.documents || [];
        renderDocuments();
        applyDocumentFilters();
        closeCreateDialog();
        showToast('success', '已创建知识库：' + data.repo.name);
      })
      .catch(function (err) {
        showToast('error', err.message);
      });
  }

  function resetFilePicker() {
    fileInput.value = '';
    fileSelected.style.display = 'none';
    dropZone.style.display = 'flex';
  }

  function openUploadDialog() {
    setActiveTab('panel-documents');
    if (uploadDialog.showModal) {
      uploadDialog.showModal();
    } else {
      uploadDialog.setAttribute('open', 'open');
    }
    refreshIcons();
  }

  function closeUploadDialog() {
    if (uploadDialog.open && uploadDialog.close) {
      uploadDialog.close();
    } else {
      uploadDialog.removeAttribute('open');
    }
  }

  function openCreateDialog() {
    syncCreateAccessUI();
    if (createDialog.showModal) {
      createDialog.showModal();
    } else {
      createDialog.setAttribute('open', 'open');
    }
    refreshIcons();
  }

  function closeCreateDialog() {
    if (createDialog.open && createDialog.close) {
      createDialog.close();
    } else {
      createDialog.removeAttribute('open');
    }
  }

  function bindFilePicker() {
    dropZone.addEventListener('click', function () {
      fileInput.click();
    });
    ['dragenter', 'dragover'].forEach(function (evt) {
      dropZone.addEventListener(evt, function (event) {
        event.preventDefault();
        event.stopPropagation();
        dropZone.classList.add('drop-active');
      });
    });
    ['dragleave', 'drop'].forEach(function (evt) {
      dropZone.addEventListener(evt, function (event) {
        event.preventDefault();
        event.stopPropagation();
        dropZone.classList.remove('drop-active');
      });
    });
    dropZone.addEventListener('drop', function (event) {
      if (event.dataTransfer.files.length) {
        fileInput.files = event.dataTransfer.files;
        showSelectedFile();
      }
    });
    fileInput.addEventListener('change', showSelectedFile);
    document.getElementById('clear-file').addEventListener('click', resetFilePicker);
    document.getElementById('pick-file-btn').addEventListener('click', function () {
      openUploadDialog();
    });
    closeUploadDialogBtn.addEventListener('click', closeUploadDialog);
    uploadDialog.addEventListener('click', function (event) {
      if (event.target === uploadDialog) {
        closeUploadDialog();
      }
    });
    openCreateDialogBtn.addEventListener('click', openCreateDialog);
    closeCreateDialogBtn.addEventListener('click', closeCreateDialog);
    cancelCreateDialogBtn.addEventListener('click', closeCreateDialog);
    createStorageMode.addEventListener('change', syncCreateAccessUI);
    createDialog.addEventListener('click', function (event) {
      if (event.target === createDialog) {
        closeCreateDialog();
      }
    });
  }

  function showSelectedFile() {
    if (!fileInput.files.length) {
      resetFilePicker();
      return;
    }
    if (fileInput.files.length === 1) {
      fileNameDisplay.textContent = fileInput.files[0].name +
        ' (' + (fileInput.files[0].size / 1024).toFixed(1) + ' KB)';
    } else {
      fileNameDisplay.textContent = '已选择 ' + fileInput.files.length + ' 个文件，加入处理队列';
    }
    fileSelected.style.display = 'block';
    dropZone.style.display = 'none';
  }

  function uploadDocument(event) {
    event.preventDefault();
    if (!state.activeRepoId) {
      showToast('error', '请先选择知识库');
      return;
    }
    var passphrase = resolveActivePassphrase();
    if (passphrase === null) {
      return;
    }
    if (!fileInput.files.length) {
      showToast('error', '请先选择要上传的文档');
      return;
    }
    uploadSubmitBtn.disabled = true;

    var formData = new FormData();
    formData.append('passphrase', passphrase);
    Array.prototype.slice.call(fileInput.files).forEach(function (file) {
      formData.append('files', file);
    });

    fetch('/api/repositories/' + state.activeRepoId + '/documents/upload', {
      method: 'POST',
      body: formData
    })
      .then(function (resp) { return resp.json().then(function (data) { return [resp, data]; }); })
      .then(function (parts) {
        var resp = parts[0];
        var data = parts[1];
        if (!resp.ok) {
          throw new Error(data.error || '上传失败');
        }
        (data.tasks || []).forEach(function (task) {
          syncTask(task, { silent: true });
        });
        refreshDocumentsFromTasks();
        renderDocuments();
        applyDocumentFilters();
        updateProgressFromTasks();
        ensureTaskPolling();
        resetFilePicker();
        closeUploadDialog();
        uploadSubmitBtn.disabled = false;
        showToast('success', '已加入处理队列');
      })
      .catch(function (err) {
        uploadSubmitBtn.disabled = false;
        showToast('error', err.message);
      });
  }

  function deleteDocument(filename, status) {
    if (!state.activeRepoId) {
      showToast('error', '请先选择知识库');
      return;
    }
    var passphrase = resolveActivePassphrase();
    if (passphrase === null) {
      return;
    }
    if (!filename) {
      showToast('error', '未找到要删除的文档');
      return;
    }
    if (status === 'processing') {
      showToast('error', '文档仍在处理中，暂时不能删除');
      return;
    }
    if (window.confirm && !window.confirm('删除后会同时清理本地文件与向量索引，确认继续吗？')) {
      return;
    }
    fetch('/api/repositories/' + state.activeRepoId + '/documents/delete', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        passphrase: passphrase,
        filename: filename
      })
    })
      .then(function (resp) { return resp.json().then(function (data) { return [resp, data]; }); })
      .then(function (parts) {
        var resp = parts[0];
        var data = parts[1];
        if (!resp.ok) {
          throw new Error(data.error || '删除失败');
        }
        state.documents = data.documents || [];
        replaceActiveTasks(data.tasks || [], { silent: true });
        refreshDocumentsFromTasks();
        renderDocuments();
        applyDocumentFilters();
        updateProgressFromTasks();
        documentDetail.textContent = state.documents[0] ? (state.documents[0].detail || '') : '请选择左侧文档查看详情。';
        showToast('success', '文档已删除');
      })
      .catch(function (err) {
        showToast('error', err.message || '删除失败');
      });
  }

  function runQuery(event) {
    event.preventDefault();
    if (!state.activeRepoId) {
      showToast('error', '请先选择知识库');
      return;
    }
    var passphrase = resolveActivePassphrase();
    if (passphrase === null) {
      return;
    }
    var queryInput = document.getElementById('query-input');
    var question = queryInput.value.trim();
    if (!question) {
      showToast('error', '请输入问题');
      return;
    }
    queryLoading.hidden = false;
    queryResult.hidden = true;
    queryError.hidden = true;
    answerHtml.innerHTML = '';
    evidenceText.innerHTML = '<p class="client-evidence-note">正在汇总证据...</p>';
    fetch('/api/repositories/' + state.activeRepoId + '/query', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        passphrase: passphrase,
        question: question
      })
    })
      .then(function (resp) { return resp.json().then(function (data) { return [resp, data]; }); })
      .then(function (parts) {
        var resp = parts[0];
        var data = parts[1];
        if (!resp.ok) {
          throw new Error(data.error || '查询失败');
        }
        answerHtml.innerHTML = data.html || '<p class="client-muted">没有返回内容。</p>';
        evidenceText.innerHTML = data.evidence_html || '<p class="client-evidence-empty">' +
          escapeHtml(data.evidence_text || '暂无置信度信息。') + '</p>';
        queryLoading.hidden = true;
        queryResult.hidden = false;
        showToast('success', '问答完成');
      })
      .catch(function (err) {
        queryLoading.hidden = true;
        queryResult.hidden = true;
        queryError.hidden = false;
        errorMessage.textContent = err.message;
        evidenceText.innerHTML = '<p class="client-evidence-empty">暂无置信度信息。</p>';
        showToast('error', err.message);
      });
  }

  function applyDocumentFilters() {
    var statusValue = document.getElementById('status-filter').value;
    var sortValue = document.getElementById('sort-by').value;
    var rows = Array.prototype.slice.call(documentsBody.querySelectorAll('tr'));
    rows.forEach(function (row) {
      if (!row.dataset.filename) {
        return;
      }
      row.style.display = statusValue === 'all' || row.dataset.status === statusValue ? '' : 'none';
    });
    rows.sort(function (a, b) {
      var parts = sortValue.split('-');
      var key = parts[0];
      var dir = parts[1] === 'asc' ? 1 : -1;
      if (key === 'name') {
        return (a.dataset.filename || '').localeCompare(b.dataset.filename || '') * dir;
      }
      return (a.dataset.date || '').localeCompare(b.dataset.date || '') * dir;
    });
    rows.forEach(function (row) {
      documentsBody.appendChild(row);
    });
  }

  repoSelect.addEventListener('change', function () {
    state.activeRepoId = repoSelect.value;
    state.documents = [];
    syncRepoAccessUI();
    refreshDocumentsFromTasks();
    renderRepoSummary();
    renderDocuments();
    applyDocumentFilters();
    updateProgressFromTasks();
  });
  tabButtons.forEach(function (button) {
    button.addEventListener('click', function () {
      setActiveTab(button.dataset.target);
    });
  });
  document.getElementById('refresh-bootstrap-btn').addEventListener('click', bootstrap);
  document.getElementById('open-repo-btn').addEventListener('click', openRepo);
  document.getElementById('refresh-documents-btn').addEventListener('click', openRepo);
  document.getElementById('status-filter').addEventListener('change', applyDocumentFilters);
  document.getElementById('sort-by').addEventListener('change', applyDocumentFilters);
  document.getElementById('create-repo-btn').addEventListener('click', createRepo);
  uploadForm.addEventListener('submit', uploadDocument);
  document.getElementById('query-form').addEventListener('submit', runQuery);

  bindFilePicker();
  replaceActiveTasks(window.CLIENT_BOOTSTRAP.tasks || [], { silent: true });
  refreshDocumentsFromTasks();
  renderRepos();
  syncCreateAccessUI();
  renderDocuments();
  setActiveTab('panel-documents');
  updateProgressFromTasks();
  ensureTaskPolling();
  refreshIcons();
})();
</script>
{% endblock %}
"""


def _resource_root() -> Path:
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        return Path(meipass)
    return Path(__file__).resolve().parent.parent


def _translate_ingest_message(message: str) -> str:
    text = message
    for source, target in PROGRESS_TRANSLATIONS:
        text = text.replace(source, target)
    return text


def _status_text(status: str) -> str:
    mapping = {
        "ready": UI_TEXT["status_ready"],
        "processing": UI_TEXT["status_processing"],
        "failed": UI_TEXT["status_failed"],
    }
    return mapping.get(status, UI_TEXT["status_unknown"])


def _display_filename(filename: str | None) -> str:
    text = str(filename or "").strip()
    if not text:
        return "-"
    matched = STORAGE_PREFIX_RE.match(text)
    if matched:
        return matched.group("name")
    return text


def _format_timestamp(value: str | None) -> str:
    text = str(value or "").strip()
    if not text:
        return "-"
    normalized = text[:-1] + "+00:00" if text.endswith("Z") else text
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        compact = text.replace("T", " ", 1)
        compact = re.sub(r"(\.\d+)?(?:Z|[+-]\d\d:\d\d)$", "", compact)
        return compact
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone().strftime("%Y-%m-%d %H:%M")


def _format_confidence(confidence: dict) -> str:
    if not confidence:
        return UI_TEXT["confidence_none"]
    lines = ["置信度"]
    level = str(confidence.get("level") or "").strip().lower()
    if level:
        lines.append(f"- 级别：{CONFIDENCE_LEVELS.get(level, level)}")
    score = confidence.get("score")
    if score not in (None, ""):
        lines.append(f"- 分值：{score}")
    for item in list(confidence.get("reasons") or []):
        lines.append(f"- 依据：{item}")
    return "\n".join(lines)


def _format_document_detail(document: dict | None) -> str:
    if not document:
        return UI_TEXT["detail_none"]
    affected_pages = list(document.get("affected_pages") or [])
    lines = [
        f"文档：{_display_filename(document.get('filename'))}",
        f"类型：{document.get('file_ext', '') or '-'}",
        f"状态：{_status_text(str(document.get('status') or ''))}",
        f"进度：{document.get('progress', 0)}%",
        f"最近状态：{document.get('progress_message', '') or '-'}",
        f"处理文件：{document.get('processed_filename', '') or '-'}",
        f"关联页面：{', '.join(affected_pages) if affected_pages else '-'}",
        f"最近更新时间：{_format_timestamp(document.get('updated_at'))}",
        f"最近完成时间：{_format_timestamp(document.get('last_ingested_at'))}",
    ]
    return "\n".join(lines)


def _format_score(value: Any) -> str:
    if value in (None, ""):
        return "-"
    try:
        return f"{float(value):.3f}".rstrip("0").rstrip(".")
    except (TypeError, ValueError):
        return str(value)


def _clean_evidence_snippet(snippet: str | None) -> str:
    text = str(snippet or "").strip()
    if not text:
        return ""
    text = FRONTMATTER_RE.sub("", text, count=1)
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) > 220:
        return f"{text[:217].rstrip()}..."
    return text


def _format_fact_fields(fields: dict[str, Any] | None) -> str:
    if not fields:
        return "无结构化字段"
    pairs: list[str] = []
    for key, value in fields.items():
        text = json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list)) else str(value)
        text = re.sub(r"\s+", " ", text).strip()
        if len(text) > 48:
            text = f"{text[:45].rstrip()}..."
        pairs.append(f"{key}={text}")
        if len(pairs) >= 4:
            break
    return "；".join(pairs) if pairs else "无结构化字段"


def _format_evidence_text(result) -> str:
    lines = [_format_confidence(result.confidence)]
    summary = str(result.evidence_summary or "").strip()
    if summary:
        lines.extend(["", f"证据摘要：{summary}"])

    lines.extend(["", "Wiki 证据"])
    if result.wiki_evidence:
        for item in result.wiki_evidence:
            segments = [item.get("title", "") or "未命名页面"]
            if item.get("type"):
                segments.append(str(item.get("type")))
            if item.get("url"):
                segments.append(str(item.get("url")))
            if item.get("reason"):
                segments.append(f"原因：{item.get('reason')}")
            lines.append(f"- {' | '.join(segments)}")
    else:
        lines.append("- 暂无")

    lines.extend(["", "片段证据"])
    if result.chunk_evidence:
        for item in result.chunk_evidence:
            heading = item.get("heading") or "正文片段"
            lines.append(
                f"- {item.get('title', '') or '未命名片段'} / {heading} | 分数={_format_score(item.get('score'))}"
            )
            snippet = _clean_evidence_snippet(item.get("snippet"))
            if snippet:
                lines.append(f"  {snippet}")
    else:
        lines.append("- 暂无")

    lines.extend(["", "事实证据"])
    if result.fact_evidence:
        for item in result.fact_evidence:
            lines.append(
                f"- {item.get('source_markdown_filename', '')} | "
                f"{item.get('sheet', '')} 行={item.get('row_index', 0)}"
            )
            lines.append(f"  {_format_fact_fields(item.get('fields', {}))}")
    else:
        lines.append("- 暂无")
    return "\n".join(lines)


def _format_evidence_html(result) -> str:
    sections: list[str] = []
    confidence = result.confidence or {}
    level = str(confidence.get("level") or "").strip().lower()
    score = confidence.get("score")
    confidence_reasons = [str(item).strip() for item in list(confidence.get("reasons") or []) if str(item).strip()]
    confidence_bits: list[str] = []
    if level:
        confidence_bits.append(
            '<span class="client-evidence-pill">级别：'
            f"{escape(CONFIDENCE_LEVELS.get(level, level))}</span>"
        )
    if score not in (None, ""):
        confidence_bits.append(
            f'<span class="client-evidence-pill">分值：{escape(_format_score(score))}</span>'
        )
    reason_markup = ""
    if confidence_reasons:
        reason_markup = '<ul class="client-evidence-list">' + "".join(
            f"<li>{escape(item)}</li>" for item in confidence_reasons
        ) + "</ul>"
    sections.append(
        '<section class="client-evidence-section">'
        '<h5 class="client-evidence-heading">置信度</h5>'
        + (
            '<div class="client-evidence-kpis">' + "".join(confidence_bits) + "</div>"
            if confidence_bits
            else '<p class="client-evidence-empty">暂无置信度信息。</p>'
        )
        + reason_markup
        + "</section>"
    )

    summary = str(result.evidence_summary or "").strip()
    if summary:
        sections.append(
            '<section class="client-evidence-section">'
            '<h5 class="client-evidence-heading">证据摘要</h5>'
            f'<p class="client-evidence-summary-text">{escape(summary)}</p>'
            "</section>"
        )

    if result.wiki_evidence:
        wiki_items = []
        for item in result.wiki_evidence:
            title = escape(item.get("title") or "未命名页面")
            meta: list[str] = []
            if item.get("type"):
                meta.append(str(item.get("type")))
            if item.get("url"):
                meta.append(str(item.get("url")))
            meta_markup = (
                f'<span class="client-evidence-meta">{escape(" | ".join(meta))}</span>' if meta else ""
            )
            reason_markup = (
                f'<span class="client-evidence-meta">{escape(str(item.get("reason") or ""))}</span>'
                if item.get("reason")
                else ""
            )
            wiki_items.append(f"<li><strong>{title}</strong>{meta_markup}{reason_markup}</li>")
        sections.append(
            '<section class="client-evidence-section">'
            '<h5 class="client-evidence-heading">Wiki 证据</h5>'
            '<ul class="client-evidence-list">' + "".join(wiki_items) + "</ul>"
            "</section>"
        )

    if result.chunk_evidence:
        snippet_cards = []
        for item in result.chunk_evidence:
            title = escape(item.get("title") or "未命名片段")
            heading = escape(item.get("heading") or "正文片段")
            score_text = escape(_format_score(item.get("score")))
            snippet = escape(_clean_evidence_snippet(item.get("snippet")) or "未返回片段摘要。")
            snippet_cards.append(
                '<article class="client-evidence-snippet">'
                f'<h6 class="client-evidence-snippet-title">{title}</h6>'
                f'<span class="client-evidence-meta">{heading} | 分数 {score_text}</span>'
                f'<p class="client-evidence-snippet-text">{snippet}</p>'
                "</article>"
            )
        sections.append(
            '<section class="client-evidence-section">'
            '<h5 class="client-evidence-heading">片段证据</h5>'
            '<div class="client-evidence-snippets">' + "".join(snippet_cards) + "</div>"
            "</section>"
        )

    if result.fact_evidence:
        fact_items = []
        for item in result.fact_evidence:
            source = escape(item.get("source_markdown_filename") or "未命名来源")
            sheet = escape(str(item.get("sheet") or "-"))
            row_index = escape(str(item.get("row_index") or "-"))
            fields_summary = escape(_format_fact_fields(item.get("fields") or {}))
            fact_items.append(
                "<li>"
                f"<strong>{source}</strong>"
                f'<span class="client-evidence-meta">工作表 {sheet} · 第 {row_index} 行</span>'
                f'<span class="client-evidence-meta">{fields_summary}</span>'
                "</li>"
            )
        sections.append(
            '<section class="client-evidence-section">'
            '<h5 class="client-evidence-heading">事实证据</h5>'
            '<ul class="client-evidence-list">' + "".join(fact_items) + "</ul>"
            "</section>"
        )

    return '<div class="client-evidence">' + "".join(sections) + "</div>"


def _repo_payload(item) -> dict[str, Any]:
    return {
        "repo_uuid": item.repo_uuid,
        "name": item.name,
        "slug": item.slug,
        "mode": item.mode,
        "storage_mode": item.storage_mode,
        "requires_passphrase": item.storage_mode == "encrypted",
        "updated_at": item.updated_at,
    }


def _documents_payload(documents: list[dict]) -> list[dict]:
    payload: list[dict] = []
    for item in documents:
        row = dict(item)
        row["progress_message"] = _translate_ingest_message(str(item.get("progress_message") or ""))
        row["detail"] = _format_document_detail(row)
        payload.append(row)
    return payload


class _IngestTaskRegistry:
    def __init__(self, controller: ConfidentialClientController) -> None:
        self._controller = controller
        self._lock = threading.Lock()
        self._tasks: dict[str, dict[str, Any]] = {}
        self._queue: list[dict[str, Any]] = []
        self._running_task_id: str | None = None
        self._uploads_dir = controller.manager.client_home / "uploads"
        self._uploads_dir.mkdir(parents=True, exist_ok=True)

    def start(
        self,
        *,
        repo_uuid: str,
        passphrase: str,
        upload_path: Path,
        filename: str,
    ) -> dict[str, Any]:
        task_id = uuid.uuid4().hex
        size_bytes = upload_path.stat().st_size if upload_path.exists() else 0
        file_ext = Path(filename).suffix.lower()
        with self._lock:
            queued = self._running_task_id is not None
            self._tasks[task_id] = {
                "task_id": task_id,
                "status": "queued" if queued else "running",
                "badge": "排队中" if queued else "处理中",
                "progress": 0,
                "message": "等待排队处理" if queued else "已接收文档，准备处理",
                "repo_uuid": repo_uuid,
                "filename": filename,
                "file_ext": file_ext,
                "size_bytes": size_bytes,
                "size_display": f"{(size_bytes / 1024):.1f} KB" if size_bytes else "-",
                "documents": [],
                "created_at": self._stamp(),
                "updated_at": self._stamp(),
            }
            spec = {
                "task_id": task_id,
                "repo_uuid": repo_uuid,
                "passphrase": passphrase,
                "upload_path": upload_path,
            }
            if queued:
                self._queue.append(spec)
            else:
                self._running_task_id = task_id
                self._launch_worker(spec)
        return self.snapshot(task_id)

    def _launch_worker(self, spec: dict[str, Any]) -> None:
        task_id = str(spec["task_id"])
        repo_uuid = str(spec["repo_uuid"])
        passphrase = str(spec["passphrase"])
        upload_path = Path(spec["upload_path"])

        def worker() -> None:
            try:
                with self._lock:
                    if task_id in self._tasks:
                        self._tasks[task_id].update(
                            {
                                "status": "running",
                                "badge": "处理中",
                                "progress": 0,
                                "message": "已接收文档，准备处理",
                                "updated_at": self._stamp(),
                            }
                        )
                self._controller.ingest_file(
                    repo_uuid,
                    passphrase,
                    upload_path,
                    on_event=lambda event: self._update_from_event(task_id, event),
                )
                documents = _documents_payload(self._controller.list_documents(repo_uuid, passphrase))
                with self._lock:
                    self._tasks[task_id].update(
                        {
                            "status": "done",
                            "badge": "已完成",
                            "progress": 100,
                            "message": "文档处理完成",
                            "documents": documents,
                            "updated_at": self._stamp(),
                        }
                    )
            except Exception as exc:
                with self._lock:
                    self._tasks[task_id].update(
                        {
                            "status": "failed",
                            "badge": "失败",
                            "message": str(exc),
                            "updated_at": self._stamp(),
                        }
                    )
            finally:
                try:
                    upload_path.unlink(missing_ok=True)
                except OSError:
                    pass
                try:
                    upload_path.parent.rmdir()
                except OSError:
                    pass
                self._start_next()

        threading.Thread(target=worker, daemon=True).start()

    def _start_next(self) -> None:
        next_spec: dict[str, Any] | None = None
        with self._lock:
            self._running_task_id = None
            if self._queue:
                next_spec = self._queue.pop(0)
                self._running_task_id = str(next_spec["task_id"])
        if next_spec:
            self._launch_worker(next_spec)

    def list_tasks(self, repo_uuid: str | None = None, *, active_only: bool = False) -> list[dict[str, Any]]:
        with self._lock:
            tasks = [dict(task) for task in self._tasks.values()]
        if repo_uuid is not None:
            tasks = [task for task in tasks if task.get("repo_uuid") == repo_uuid]
        if active_only:
            tasks = [task for task in tasks if task.get("status") in {"queued", "running"}]
        tasks.sort(key=lambda item: str(item.get("updated_at") or item.get("created_at") or ""))
        return tasks

    def _stamp(self) -> str:
        return datetime.now(timezone.utc).isoformat()

    def _update_from_event(self, task_id: str, event: dict[str, Any]) -> None:
        status = str(event.get("status") or "processing")
        badge = {
            "ready": "已完成",
            "processing": "处理中",
            "failed": "失败",
        }.get(status, "处理中")
        task_status = "running" if status in {"processing", "ready"} else status
        with self._lock:
            if task_id not in self._tasks:
                return
            self._tasks[task_id].update(
                {
                    "progress": int(event.get("progress", 0) or 0),
                    "message": _translate_ingest_message(str(event.get("message") or "")),
                    "status": task_status,
                    "badge": badge,
                    "updated_at": self._stamp(),
                }
            )

    def snapshot(self, task_id: str) -> dict[str, Any]:
        with self._lock:
            return dict(self._tasks.get(task_id) or {})


class _EmbeddedServer:
    def __init__(self, app: Flask) -> None:
        self._server = make_server("127.0.0.1", 0, app, threaded=True)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    @property
    def url(self) -> str:
        host, port = self._server.server_address[:2]
        return f"http://{host}:{port}"

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._server.shutdown()
        self._thread.join(timeout=2)


def create_client_web_app(controller: ConfidentialClientController | None = None) -> Flask:
    controller = controller or ConfidentialClientController()
    root = _resource_root()
    app = Flask(
        __name__,
        static_folder=str(root / "static"),
        static_url_path="/static",
    )
    app.jinja_loader = ChoiceLoader(
        [
            DictLoader(
                {
                    "client/base.html": CLIENT_TEMPLATE_BASE,
                    "client/index.html": CLIENT_TEMPLATE_INDEX,
                }
            )
        ]
    )
    task_registry = _IngestTaskRegistry(controller)

    @app.context_processor
    def inject_globals() -> dict[str, Any]:
        return {"site_name": CLIENT_NAME}

    def bootstrap_payload() -> dict[str, Any]:
        repos = [_repo_payload(item) for item in controller.list_repositories()]
        return {
            "client_name": CLIENT_NAME,
            "client_version": CLIENT_VERSION,
            "repos": repos,
            "active_repo_uuid": repos[0]["repo_uuid"] if repos else "",
            "tasks": task_registry.list_tasks(active_only=True),
        }

    def error(message: str, status: int = 400):
        return jsonify({"error": message}), status

    def repo_requires_passphrase(repo_uuid: str) -> bool:
        return controller.manager.get_repository(repo_uuid).requires_passphrase

    def resolve_repo_passphrase(repo_uuid: str, raw_value: Any) -> str:
        passphrase = str(raw_value or "").strip()
        if repo_requires_passphrase(repo_uuid) and not passphrase:
            raise ValueError("请先输入访问口令")
        return passphrase

    @app.get("/")
    def client_home():
        return render_template("client/index.html", bootstrap=bootstrap_payload())

    @app.get("/api/bootstrap")
    def client_bootstrap():
        return jsonify(bootstrap_payload())

    @app.post("/api/repositories")
    def create_repository():
        data = request.get_json(silent=True) or {}
        passphrase = str(data.get("passphrase") or "").strip()
        storage_mode = str(data.get("storage_mode") or "encrypted").strip().lower()
        if storage_mode not in {"encrypted", "plain"}:
            return error("不支持的本地存储模式")
        if storage_mode == "encrypted" and not passphrase:
            return error("请先输入新知识库口令")
        if not passphrase:
            passphrase = ""
        name = str(data.get("name") or "本地机密知识库").strip()
        slug = str(data.get("slug") or slugify(name) or "confidential-kb").strip()
        try:
            services = controller.load_default_services()
            repo = controller.create_repository(
                name=name,
                slug=slug,
                passphrase=passphrase,
                services=services,
                storage_mode=storage_mode,
            )
            return jsonify(
                {
                    "repo": _repo_payload(repo),
                    "repos": [_repo_payload(item) for item in controller.list_repositories()],
                    "documents": [],
                }
            )
        except Exception as exc:
            return error(str(exc))

    @app.post("/api/repositories/<repo_uuid>/documents")
    def list_documents(repo_uuid: str):
        data = request.get_json(silent=True) or {}
        try:
            passphrase = resolve_repo_passphrase(repo_uuid, data.get("passphrase"))
            documents = controller.list_documents(repo_uuid, passphrase)
        except Exception as exc:
            return error(str(exc))
        return jsonify(
            {
                "documents": _documents_payload(documents),
                "tasks": task_registry.list_tasks(repo_uuid, active_only=True),
            }
        )

    @app.post("/api/repositories/<repo_uuid>/documents/upload")
    def upload_document(repo_uuid: str):
        try:
            passphrase = resolve_repo_passphrase(repo_uuid, request.form.get("passphrase"))
        except Exception as exc:
            return error(str(exc))
        uploads = [item for item in request.files.getlist("files") if item and item.filename]
        if not uploads:
            upload = request.files.get("file")
            if upload is not None and upload.filename:
                uploads = [upload]
        if not uploads:
            return error("请先选择要上传的文档")
        tasks = []
        for upload in uploads:
            filename = Path(str(upload.filename or "")).name.strip()
            if not filename or filename in {".", ".."}:
                filename = safe_upload_basename(str(upload.filename or "")) or "upload"
            upload_dir = task_registry._uploads_dir / uuid.uuid4().hex
            upload_dir.mkdir(parents=True, exist_ok=True)
            temp_path = upload_dir / filename
            upload.save(temp_path)
            task = task_registry.start(
                repo_uuid=repo_uuid,
                passphrase=passphrase,
                upload_path=temp_path,
                filename=filename,
            )
            tasks.append(task)
        return jsonify({"tasks": tasks})

    @app.post("/api/repositories/<repo_uuid>/documents/delete")
    def delete_document(repo_uuid: str):
        data = request.get_json(silent=True) or {}
        filename = str(data.get("filename") or "").strip()
        if not filename:
            return error("请提供要删除的文档")
        active_tasks = task_registry.list_tasks(repo_uuid, active_only=True)
        if any(str(task.get("filename") or "") == filename for task in active_tasks):
            return error("文档仍在处理中，暂时不能删除")
        try:
            passphrase = resolve_repo_passphrase(repo_uuid, data.get("passphrase"))
            documents = controller.delete_document(repo_uuid, passphrase, filename)
        except Exception as exc:
            return error(str(exc))
        return jsonify(
            {
                "documents": _documents_payload(documents),
                "tasks": active_tasks,
            }
        )

    @app.get("/api/tasks/<task_id>")
    def task_status(task_id: str):
        task = task_registry.snapshot(task_id)
        if not task:
            return error("任务不存在", status=404)
        return jsonify(task)

    @app.post("/api/repositories/<repo_uuid>/query")
    def query_repository(repo_uuid: str):
        data = request.get_json(silent=True) or {}
        question = str(data.get("question") or "").strip()
        if not question:
            return error("请输入问题")
        try:
            passphrase = resolve_repo_passphrase(repo_uuid, data.get("passphrase"))
            result = controller.query(repo_uuid, passphrase, question)
            _, html = render_markdown(result.answer)
        except Exception as exc:
            return error(str(exc))
        return jsonify(
            {
                "answer": result.answer,
                "html": html,
                "confidence": result.confidence,
                "evidence_html": _format_evidence_html(result),
                "evidence_text": _format_evidence_text(result),
            }
        )

    return app


def launch_desktop_app(controller: ConfidentialClientController | None = None) -> None:
    app = create_client_web_app(controller=controller)
    server = _EmbeddedServer(app)
    server.start()
    try:
        import webview
    except ImportError as exc:
        server.stop()
        raise RuntimeError("缺少 pywebview 依赖，请先安装 requirements.txt 中的 pywebview") from exc
    try:
        webview.create_window(
            f"{CLIENT_NAME} {CLIENT_VERSION}",
            server.url,
            width=1440,
            height=920,
            min_size=(1180, 760),
            text_select=True,
        )
        webview.start()
    finally:
        server.stop()
