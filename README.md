# codex-sidebar-recovery

**[简体中文](README.md)** | [English](README.en.md)

## Codex 侧栏项目恢复

**核心用途：项目仍在本地数据库中，但 Codex 桌面侧栏的项目文件夹消失、只剩一个测试项目，会话也不再归类到项目中。**

这个技能将一次已验证的桌面端故障恢复经验整理为可复用流程：先分清显示设置与缓存损坏，再从现存数据库记录重建侧栏元数据。它不是会话清理工具，不删除会话，不修改数据库，也不创建新的项目源码目录。

> 这是社区维护的恢复工具，不是 OpenAI 官方修复程序。实际恢复支持 Windows 和 macOS；内部存储格式不是稳定 API，必须先预演。仅在数据库记录仍完整、当前全局配置为可解析 JSON 时适用。

## 已包含

- 简明技能入口、中英文使用说明、存储依据与退出后恢复指引。
- Python 标准库脚本，默认仅备份与预演，遇到身份或归属冲突停止写入。
- 保留多根目录、旧项目 ID 映射、显式无项目标记和归档状态。
- Windows / macOS 独立等待程序，当前助手退出后仍可继续；不强制结束应用。
- 使用虚构数据的自动测试，以及 Windows / macOS / Ubuntu 测试工作流。Ubuntu 仅验证 POSIX 规划逻辑和模拟恢复，不代表支持 Linux 客户端恢复。

## 安装

在支持技能安装的 Codex 中发送：

```text
请从 https://github.com/turtoncarllyle/codex-sidebar-recovery/tree/main/codex-sidebar-recovery 安装 codex-sidebar-recovery 技能。
```

安装的是仓库内的 `codex-sidebar-recovery` 子目录，而不是整个仓库根目录。由安装工具确认当前版本的技能搜索路径；未发现新技能时，按客户端提示重新加载或重启。

本机脚本需要 Python 3.10+。优先使用 Codex 已提供的 Python 运行时，不要求修改系统 PATH；Windows 独立等待需要 `pythonw.exe`、PowerShell 5.1+ 与可用的 WMI/CIM，macOS 独立等待需要 `launchctl`。没有本机文件与进程访问权限的云端助手只能提供离线操作指导。

## 安装后这样使用

```text
使用 $codex-sidebar-recovery 排查 Codex 侧栏项目消失、会话没有归类的问题。
数据库里仍有项目，请先检查和预演。当前助手正在这个应用里运行，
不要关闭或强制结束 Codex；如需退出后恢复，请先准备独立等待程序。
```

只分析原因时，加上“只诊断和预演，不应用恢复”；需要执行时，明确说明“确认恢复预演范围内的侧栏元数据”。

## 使用步骤

1. 核对侧栏是否按项目分组，并确定当前 `CODEX_HOME`、目录联接和实际数据源。
2. 比较数据库项目记录与全局 JSON 的侧栏缓存，确认缺失的是显示元数据而非源记录。
3. 预演生成配置备份、包含已提交 WAL 的 SQLite 快照、候选配置和变更摘要。
4. 确认范围后，应用已退出可直接恢复；应用仍在运行则按系统启动独立等待程序，核对其父进程及 `waiting_for_exit` 状态后再自然退出。
5. 检查恢复状态，再打开应用验证项目列表及会话归属。脚本成功不等于 UI 已验证。

从本仓库根目录预演：

```powershell
$codexHome = if ($env:CODEX_HOME) { $env:CODEX_HOME } else { Join-Path $env:USERPROFILE '.codex' }
$output = Join-Path $codexHome ('backups\sidebar-recovery-' + (Get-Date -Format 'yyyyMMdd-HHmmss'))
python -B .\codex-sidebar-recovery\scripts\recover_sidebar.py --codex-home $codexHome --output-dir $output --preview
```

macOS 使用 `python3` 和 `~/.codex`：

```zsh
codexHome="${CODEX_HOME:-$HOME/.codex}"
output="$codexHome/backups/sidebar-recovery-$(date +%Y%m%d-%H%M%S)"
python3 -B ./codex-sidebar-recovery/scripts/recover_sidebar.py --codex-home "$codexHome" --output-dir "$output" --preview
```

`python` 不在 PATH 时，使用已核实的解释器绝对路径。host key 不明确时，脚本会停止并要求通过 `--host-key` 指定已观察到的值，不猜测逻辑目录与物理目录的对应关系。

实际恢复、取消等待和回滚说明见[退出后恢复操作](codex-sidebar-recovery/references/desktop-handoff.md)。

## 这次经验的关键点

**数据库有项目，不代表侧栏缓存也有项目。** 已观察到的故障中，项目表和会话外键仍完整，但桌面配置只剩一个项目；配置重置的具体触发进程没有得到证明，不能一概归因于某个工具。

**不要让仍在运行的应用覆盖修复。** 该案例对应版本会把内存状态写回全局 JSON。普通子进程可能随 Codex 退出；后台窗口隐藏也不代表进程独立。Windows 必须检查等待程序真正由 WMI 托管，macOS 必须检查它由 `launchd` 托管，再让用户退出。

**用明确的 ID 关系恢复，不按目录猜归属。** 多个项目可能共享根目录，一个项目也可能有多个根目录。旧项目 ID、数据库原生 ID 和 host key 都需要正确关联。

## 使用技巧与注意事项

- 每次恢复使用独立输出目录；预演结果不是写入依据，真正应用时会重新读取并备份最新状态。
- 不删除 SQLite 的 WAL/SHM 文件，也不直接复制运行中数据库的主文件作为唯一备份。
- 等待程序最多等待 24 小时；窗口关闭后若托盘实例或其他 Codex CLI 仍在运行，会继续等待，不会强杀进程。
- 等待与恢复期间不要重新打开应用，直到 `status.json` 显示完成或需要处理的错误。
- 主配置和 `.bak` 分别原子替换，不是跨两个文件的事务；若第二次写入失败，会明确报告部分恢复，避免冒充完整成功。
- 不适用于 SQLite 本身丢失/损坏、未知字段结构或普通 ChatGPT 云端项目。macOS 自动写入行为仍需结合客户端版本检查；当前 JSON 已损坏时先做人工取证和备份，不用空对象替代。
- 本地快照包含项目路径和会话元数据，必须留在本机受控目录，不上传到公开仓库、Issue 或聊天附件。

## 文件与验证

- [技能入口](codex-sidebar-recovery/SKILL.md)
- [存储与恢复依据](codex-sidebar-recovery/references/storage-and-recovery.md)
- [恢复脚本](codex-sidebar-recovery/scripts/recover_sidebar.py)
- [Windows 独立启动器](codex-sidebar-recovery/scripts/Start-DetachedRecovery.ps1)
- [macOS 独立启动器](codex-sidebar-recovery/scripts/start-detached-recovery.sh)
- [隔离测试](tests/test_recovery.py)
- [自动测试记录](https://github.com/turtoncarllyle/codex-sidebar-recovery/actions)

```powershell
python -B -m unittest discover -s tests -v
```

测试不接触真实 Codex 配置。进程检查在恢复测试中使用合成输出或隔离模拟；Windows/macOS 测试分别验证原生进程枚举和锁互斥。它们不能证明所有客户端版本兼容，真实恢复后仍需检查界面。

## 参考与许可

仓库组织和双语说明参考 [codex-session-cleanup](https://github.com/turtoncarllyle/codex-session-cleanup)，但恢复与清理是不同的操作，不能混用。技能机制可参阅 [OpenAI 官方技能说明](https://learn.chatgpt.com/docs/build-skills)。

[MIT License](LICENSE)
