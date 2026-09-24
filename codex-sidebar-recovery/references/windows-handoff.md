# Windows 退出后恢复操作

适用于助手就在待修复应用内运行的情况。所有命令必须使用已经核实的实际路径；先按 `storage-and-recovery.md` 确认此版本与数据结构适用。

## 1. 准备并预演

确定脚本、Python 3.10+、逻辑 `CODEX_HOME` 和新的独立输出目录。优先使用 Codex 提供的运行时查询工具定位 Python；不要硬编码其他用户的缓存路径。

以下命令以仓库根目录为当前目录，`$python` 替换成已经核实的路径：

```powershell
$python = (Get-Command python -ErrorAction Stop).Source
$pythonw = Join-Path (Split-Path $python) 'pythonw.exe'
$codexHome = if ($env:CODEX_HOME) { $env:CODEX_HOME } else { Join-Path $env:USERPROFILE '.codex' }
$output = Join-Path $codexHome ('backups\sidebar-recovery-' + (Get-Date -Format 'yyyyMMdd-HHmmss'))
$script = '.\codex-sidebar-recovery\scripts\recover_sidebar.py'
& $python -B $script --codex-home $codexHome --output-dir $output --preview
```

Windows 的 `python` 命令可能只是商店别名，不等于可运行的解释器。检查版本和 `pythonw.exe` 存在；需要时改用运行时工具返回的绝对路径。若提示 host key 不明确，从当前配置/日志确认后在预演和实际执行时使用同一 `--host-key`。

输出包含 `snapshot-*` 子目录中的原 JSON、原 `.bak`（如果存在）、SQLite 快照、候选 JSON 与 `plan.json`。这些文件只留本地。预演期间可以继续工作，因为实际恢复会重新读取退出后的最终状态。

## 2. 应用尚在运行

只有用户已经授权恢复时才启动等待程序。若用户只要求诊断，不启动任何可能稍后写入的进程。

```powershell
.\codex-sidebar-recovery\scripts\Start-DetachedRecovery.ps1 `
    -CodexHome $codexHome -OutputDirectory $output -Pythonw $pythonw
```

启动器使用 `Win32_Process.Create` 和隐藏窗口的 startup 参数；返回等待程序 PID、父进程名称及状态文件路径。必须实际核验：

1. 等待程序 PID 仍存活，命令行确实指向本次脚本和输出目录。
2. 父进程为 WMI 托管进程（通常 `WmiPrvSE.exe`），而非 Codex、ChatGPT 或其终端子进程。
3. `Get-Content -LiteralPath (Join-Path $output 'status.json') -Encoding UTF8` 显示 `waiting_for_exit`，并能看到尚未退出的客户端。

全部核验后，才告诉用户：退出 Codex 会停止当前助手，但已验证独立的恢复程序会等待应用自然退出后继续。关闭窗口不一定退出托盘实例。它等待所有被识别的客户端连续消失至少 5 秒，然后只执行一次恢复，默认最多等待 24 小时。

不要强制关闭应用，不使用 `taskkill` 或 `Stop-Process`。普通后台 `Start-Process` 或 Python 子进程是否能脱离客户端的进程管理不能想当然；WMI 也可能受企业策略限制，必须以实际 PID 和状态核验为准。

### 可选重新打开

默认不自动重开。需要时先从本机读取真实 Store AppID：

```powershell
Get-StartApps | Where-Object { $_.Name -match 'Codex|ChatGPT' } | Select-Object Name, AppID
```

将明确选中的 AppID 通过 `-RestartAppId` 传给启动器，不编造产品包名。脚本恢复并校验后调用系统打开请求；`repaired_app_reopened` 不代表窗口已经出现，更不代表侧栏验证成功。非 Store 安装或无法确认 AppID 时，让用户手动重开。

## 3. 应用已经退出

如果 WMI 不可用，先把经过预演的绝对路径命令交给用户。用户自然退出应用后，在独立 PowerShell 中运行：

```powershell
& $python -B $script --codex-home $codexHome --output-dir $output --apply
```

变量需要在这个独立终端中重新设置，或者使用完整的已核实路径命令。脚本发现任何受保护客户端仍在运行时会拒绝写入。运行到结束前保持应用关闭，不使用本技能关闭进程。

## 4. 状态与取消

| 状态 | 含义与处理 |
| --- | --- |
| `preview` | 已备份与规划，源配置未修改；摘要输出到终端。 |
| `waiting_for_exit` | 等待中，不能报告完成。 |
| `repaired` | 主 JSON 与 `.bak` 已恢复并完成数据检查，界面待验证。 |
| `repaired_app_reopened` | 恢复检查通过并已请求打开应用，界面仍待验证。 |
| `unchanged` | 当前主配置已符合恢复结果，未重写；不会修复与主配置不同的旧 `.bak`。 |
| `primary_restored_verification_pending` | 主文件已替换，备用文件或后续检查失败；保持应用关闭，检查错误与快照，不盲目宣称回滚。 |
| `cancelled` / `expired_without_changes` | 等待期间取消或超时，未应用恢复。 |
| `failed` | 查看 `error`；未进入主文件写入后阶段的失败，不重试未知格式。 |

取消等待：

```powershell
& $python -B $script --codex-home $codexHome --output-dir $output --cancel
```

这只创建 `CANCEL` 标记，等待程序下次轮询时处理。已经开始的写入不能靠取消安全撤销；检查最终状态。不要把 `cancellation_requested` 当作 `cancelled`。超时或取消后重新执行应使用新的输出目录。一个输出目录只服务同一 home、同一次恢复；不要并行启动多个恢复任务。

## 5. 复核与回滚

写入前会检查应用退出状态、原始文件字节与数据库相关行是否仍匹配快照。每个 JSON 使用同目录临时文件和 `os.replace` 单独替换；两文件并非事务。应用突然重启仍可能造成竞争，因此恢复期间应保持关闭。

恢复失败后保留所有快照和 `status.json`。如果需要撤销，必须得到撤销操作授权，先自然退出应用，确认目标就是本次使用的 home，然后以 `status.json` 的 `backup` 为依据恢复该快照内的原 JSON 和原 `.bak`。若原本没有 `.bak`，只在确认是本次新建时移除该备用文件。不要恢复整目录或覆盖恢复后产生的新状态；先备份当前失败现场。

本工具不写源数据库，不需要为了回滚而覆盖数据库、WAL、会话文件或项目源码。最后检查项目可见性、顺序、多根目录以及几个已知会话的归属；归档会话不应被解档。
