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
- `confidential_client/manager.py`
  - 本地 repo 目录管理、bundle 导入导出、服务配置更新
- `confidential_client/controller.py`
  - GUI / CLI 共用控制层
- `confidential_client/health.py`
  - LLM / Embedding / Qdrant / MinerU 健康检查
- `confidential_client/gui.py` / `desktop.py`
  - Tkinter 桌面客户端壳层
- `scripts/build-confidential-client.sh`
  - 客户端 launcher 打包脚本
- `scripts/build-confidential-client-binary.sh` + `packaging/confidential-client.spec`
  - PyInstaller 独立二进制打包配置
- `scripts/build-macos-app.sh` + `packaging/macos/Info.plist.template`
  - macOS app bundle 构建骨架
- `scripts/build-windows-installer.ps1` + `packaging/windows/open-llm-wiki-client.iss`
  - Windows 安装包构建骨架
- `scripts/sign-macos-client.sh` / `scripts/sign-windows-client.ps1`
  - macOS / Windows 签名脚本
- `confidential_client/update.py` + `packaging/appcast.sample.json`
  - 自动更新检查与更新清单样例

## 测试范围

- 仓库创建、解锁、错误口令失败
- Qdrant payload 不含明文文本与标题
- 本地 runtime 跑通创建 -> 摄入 -> 查询 -> 历史写入
- CLI 创建与导出链路
- 本地 manager 的创建 / 列表 / 删除 / 导入导出
- controller 历史上下文拼装
- 外部服务健康检查
- launcher 打包脚本 smoke test
- 二进制打包配置存在性校验
- 自动更新版本比较与本地更新配置持久化

## 当前状态

当前已具备：

- 本地加密 repo 管理
- 桌面客户端壳层
- 桌面端异步执行，避免 ingest / query 阻塞界面
- 导入 / 导出恢复
- 服务配置编辑
- 健康检查
- ingest / query / history 整体链路
- launcher 打包
- PyInstaller 二进制打包配置
- macOS / Windows 安装包脚本骨架
- 签名脚本骨架
- 自动更新检查

## 下一阶段

- 安装包签名实机接入
- 自动更新下载与应用
