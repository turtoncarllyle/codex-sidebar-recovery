# 存储与恢复依据

本说明记录 2026 年 9 月一次 Windows Codex 桌面恢复中实际观察到的结构，不是官方稳定格式。优先检查应用工具和当前安装版本，不能因为文件名相同就直接套用。

## 故障判断

| 现象 | 下一步 |
| --- | --- |
| 项目存在，只是会话列表未按项目分组 | 读取应用侧栏偏好，优先使用受支持的设置工具，不改文件。 |
| 应用接口或 SQLite 有项目，而全局 JSON 的项目与归属缓存缺失 | 本技能的目标场景，先备份和预演。 |
| 数据库没有项目/会话外键，或 schema 不匹配 | 停止自动恢复，不按工作目录推测新关联。 |
| 当前全局 JSON 无法解析 | 本脚本拒绝应用；保留原文件、备份和损坏证据，另行人工取证。 |
| JSON 和数据库都有项目，界面仍缺失 | 检查 host、分组、应用版本和加载错误，不循环改写 JSON。 |

## 关键数据源

| 数据源 | 本技能使用的内容 |
| --- | --- |
| `CODEX_HOME\state_5.sqlite` | 根目录的当前状态库；不能默认 `sqlite\` 子目录下同名旧库就是当前库。 |
| `projects` | `id`、`name`、`position`、`created_at_ms`、`updated_at_ms`。 |
| `project_roots` | `project_id`、`position`、`path`；完整保留所有根目录及顺序。 |
| `project_idempotency_keys` | `key`、`project_id`；将旧侧栏 ID 对应到原生 ID。 |
| `threads` | 仅查询 `id`、`project_id`、`archived`；不读取会话正文，不更新归档标志。 |
| `.codex-global-state.json` | 桌面项目缓存、排列、host 映射和会话归属。 |
| `.codex-global-state.json.bak` | 同时维护的备用副本；原内容先独立备份。 |

SQLite 必须用 `mode=ro` 及 `query_only` 读取。脚本通过 SQLite backup API 创建一致性快照，读取已提交的 WAL；不要使用 `immutable=1` 跳过活动 WAL，也不要删除锁文件“解锁”。快照会包含整个数据库，所以仍是敏感文件，即使脚本只查询少量字段。

## 只恢复四个字段

以下完全是虚构数据，结构用于解释 ID 对应关系：

```json
{
  "local-projects": {
    "legacy-demo": {
      "id": "legacy-demo",
      "name": "Demo",
      "rootPaths": ["D:\\work\\demo", "D:\\work\\shared"],
      "createdAt": 1000,
      "updatedAt": 2000
    }
  },
  "project-order": ["legacy-demo"],
  "app-server-project-id-by-legacy-project-id-by-host": {
    "local:C:\\Users\\example\\.codex": {
      "legacy-demo": "native-demo"
    }
  },
  "thread-project-assignments": {
    "thread-demo": {"projectId": "legacy-demo", "projectKind": "local"}
  }
}
```

- host key 优先匹配当前配置中确实存在的逻辑 home；只有一个本地候选时才能自动采用。多个候选、完全缺失时需读取应用证据，显式指定 `--host-key`。
- `C:\Users\example\.codex` 联接到 `E:\data\.codex` 时，可以访问同一套文件，但不能将 JSON 内的逻辑 host key 擅自改成物理路径。
- 优先保留当前可验证的项目身份。一个原生 ID 对应多个无法区分的旧 ID 时停止，不擅自挑第一个。
- 按数据库 `position` 重建已迁移项目顺序，将当前仍存在但尚未迁移的项目保留在末尾；不删除当前额外项目。
- `threads.project_id IS NULL` 不推测关联；`projectless-thread-ids` 中显式声明的无项目会话也不重新归类。归档会话的现有外键可以恢复，但不解除归档。
- 多项目共享路径时，`cwd` 无法唯一确定所属项目，因此不用于归属恢复。
- `app-server-pending-project-deletions-by-host` 有待删除记录时停止。不要把用户正在删除的项目恢复回来。
- 保留其他 host 映射与未知无关字段。迁移完成标记、最近目录、认证、模型设置、任务队列均不改动。

## 为什么不能运行中直接改

案例中检查到桌面应用启动时加载全局 JSON，随后内存状态会整体写回；数据库返回的完整项目列表没有自动补回已经损坏的旧侧栏缓存。在线修改文件可能短暂生效，随后又被旧内存状态覆盖。

故障现场还曾有全零字节的损坏 JSON 备份，这支持“配置异常”的判断，但不能据此认定是某个清理程序或特定工具所致。公开总结不应把未证明的触发因素写成结论。

脚本因此要求 Windows 上 `ChatGPT.exe` 与 `codex.exe`（大小写不敏感）均退出。它保守等待同名的其他 CLI 实例，不主动杀进程。若新版本新增写入进程或更改文件格式，应先更新识别依据与测试，不关闭保护强行应用。

## 应用后的证据

比较四个字段的前后值，核对所有项目根目录与外键归属，确认其他顶级字段未改变、数据库相关行未改变。主配置、备用配置和快照必须能解析，快照 `PRAGMA quick_check` 应返回 `ok`。

随后核对应用接口和真实侧栏。若只完成脚本校验，报告“文件恢复完成，界面待确认”；不要把自动测试或打开应用的请求当成 UI 验收。
