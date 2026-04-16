# 加密知识库客户端实施计划

## 目标

围绕“仅客户端运行、不与平台服务端通信、不在平台数据库留存”的约束，落地第一版可运行链路：

- 创建本地机密 repo
- 本地导入文档并摄入
- 本地查询并保存历史
- 与 `MinerU / LLM / Embedding / Qdrant` 正常交互
- Qdrant payload 不落可读业务元数据

## 已落地模块

- `llmwiki_core/contracts.py`
  - 抽出服务端 / 客户端共享的 repo 与 query contract
- `confidential_client/repository.py`
  - 本地 `manifest.json + vault.bin` 加密仓库
- `confidential_client/crypto.py`
  - `scrypt + AES-GCM` 本地加密封装
- `confidential_client/qdrant.py`
  - 机密模式 Qdrant adapter，本地 `qdrant-map.sqlite` 恢复元数据
- `confidential_client/runtime.py`
  - 机密模式 ingest / query 本地运行时
- `confidential_client/cli.py`
  - `create / ingest / query / history / export` CLI

## 测试范围

- 仓库创建、解锁、错误口令失败
- Qdrant payload 不含明文文本与标题
- 本地 runtime 跑通创建 -> 摄入 -> 查询 -> 历史写入
- CLI 创建与导出链路

## 下一阶段

- 增加 PySide6 桌面壳层
- 支持导入备份包恢复
- 增加本地会话管理与多知识库切换
- 增加客户端侧健康检查与服务配置页
