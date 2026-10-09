# TRIAID FIN 单机版

这套单机运行层不是另一套 FIN。它与云端共用 `triaid_fin` 核心、策略状态、审计、State Break 和美股 0.7 guarded route，仅把持久化和调度替换为本地适配器。

## Windows 直接启动

双击 `run_standalone_windows.cmd`。

首次运行会在当前目录建立 `.venv-standalone`，安装仓库锁定的依赖，随后先执行 `standalone_selfcheck.py`。只有自检通过后才会运行 US、CN、HK 三个市场。

数据目录固定为：

`TRIAID_FIN_LOCAL_DATA`

报告统一写入：

`TRIAID_FIN_LOCAL_DATA/TRIAID_FIN_OUTPUT`

其中：

- `latest.json` 是最新完整机器可读报告
- `latest.txt` 是最新文本摘要
- `runtime_status.json` 是单机运行状态
- `TRIAID_FIN_YYYYMMDD_HHMMSS.json/.txt` 是每次运行的不可混淆快照

## 证据纪律

1. 盘中或未结算日线只能运行 `MANUAL_PREVIEW`，不会进入正式证据链。
2. 完整日线才允许 `OFFICIAL_EVIDENCE`。
3. 关机期间不会伪造本不存在的历史决策；重新启动后恢复到当前可验证状态，并记录 offline gap。
4. 快速 State Break 层只有降风险权限，没有反向权，也不能用当天结果回写当天决策。
5. 美股主路线先由长期收益排序决定方向，再由 State Break 和真实底层资产/风险簇集中度统一缩放风险。
6. 单机版只写本地 file backend，不需要 Supabase，也不会成为云端 official writer。
7. Broker execution 保持关闭，单机版仍是研究和应验平台。

## 自检

`standalone_selfcheck.py` 会确认：

- 本地目录可写
- file backend 可以完成写入读取闭环
- StateAwareEvolutionLabEngine 能正常实例化
- US route 确实为 `us-return-max-route@0.7.0`
- 不依赖远端持久化
- 本地 RunStore 没有结构完整性错误

任一关键检查失败，Windows 启动器会停止，不继续生成正式本地运行。
