---
name: codex-sidebar-recovery
description: "Diagnose and recover missing Codex desktop sidebar projects and ungrouped local threads when their SQLite project records still exist. Includes Windows recovery that waits for the running app to exit without terminating it."
---

# Codex Sidebar Recovery

恢复 Codex 桌面侧栏的项目列表和已有会话归属。用于“数据库里有项目，侧栏却消失或只剩一个测试项目”的问题。此技能不删除会话，也不用于修复普通 ChatGPT 云端项目。

## 先判断故障层

- 有应用工具时，读取 `list_projects` 和 `list_threads`，核对分组偏好与项目关联。若只是分组方式错误，使用受支持的设置接口修复即可。
- 定位实际 `CODEX_HOME`，检查目录联接或符号链接。逻辑路径用于应用的 host key，物理路径用于访问文件；两者不同不代表有两套数据。
- 对比 `.codex-global-state.json` 的 `local-projects`、`project-order`、`thread-project-assignments` 与 `state_5.sqlite` 的 `projects`、`project_roots`、`project_idempotency_keys`、`threads.project_id`。
- 源数据库只读打开，并包含已提交的 WAL。不要把空缓存、旧的 `sqlite\state_5.sqlite` 或未合并 WAL 的文件副本当作权威数据。

读取 [存储与恢复依据](references/storage-and-recovery.md) 后，再使用脚本。内部存储结构有版本差异；必要字段缺失、数据库损坏或记录冲突时停止写入并说明具体差异，不猜测新格式。

## 使用恢复工具

`scripts\recover_sidebar.py` 需要 Python 3.10+，仅使用标准库。实际恢复与进程等待支持 Windows；规划和隔离测试可跨平台运行。

```powershell
python -B .\scripts\recover_sidebar.py --codex-home "$env:USERPROFILE\.codex" --output-dir "$env:USERPROFILE\.codex\backups\sidebar-recovery-run" --preview
```

根据实际技能安装位置调整脚本路径，为每次恢复选择独立输出目录。预演会生成配置备份、SQLite 一致性快照、候选配置和变更摘要，不修改源配置或数据库。

- 项目 ID 通过数据库的幂等键和当前映射还原；保留项目名称、顺序和全部根目录。
- 会话归属仅来自数据库已有外键，保留显式无项目标记和归档状态。不要按 `cwd` 猜测归属，多项目可能共享目录。
- 只允许变更四个侧栏字段；不重置迁移标记，不恢复整个旧配置，不修改账号、认证、模型、任务队列或源数据库。
- host key 不能唯一确定时，从应用状态或日志确认后传入 `--host-key`。不要把物理目录自动替换进原有的逻辑 host key。

## 应用仍在运行时

当前助手可能就在故障应用内执行。该版本会将内存中的全局状态持续写回磁盘，因此“直接改 JSON 再刷新”可能被覆盖。

完成已授权的预演和备份后，使用 [退出后恢复操作](references/windows-handoff.md) 中的独立等待程序。它通过 Windows WMI 启动，父进程不属于 Codex；用进程信息和 `status.json` 验证启动确实成功，再请用户自然退出应用。

不要结束桌面应用或当前助手进程，不使用常驻轮询反复覆盖配置，不为了写入而移除文件保护。若独立启动不可用，交付已验证的离线命令，让用户退出后执行，并明确尚未应用。

应用已关闭时可运行同一命令加 `--apply`。已有“修复问题”的授权覆盖范围内的恢复操作，无需重复征求同意；若用户仅要求诊断，则只预演。

## 验证与交付

- 写入前重新读取最新状态并再次备份，拒绝并发修改和冲突；通过同目录临时文件原子替换主 JSON 及 `.bak`。
- 区分 `waiting_for_exit`、`repaired`、`repaired_app_reopened` 与部分写入失败，不能把等待状态说成修复完成。
- `repaired_app_reopened` 只表示文件校验通过且已请求打开应用。恢复后再通过应用项目列表和会话归属或用户反馈确认侧栏效果。
- 交付根因、恢复范围、备份目录、当前状态和下一步。展示脱敏摘要，不公开原始 JSON、SQLite、会话正文和本机路径。
