# 需求文档 · Redroid Cluster
> 版本：v2.4 · 2026-03-03

---

## 1. 背景与目标

有三台可互通服务器，共享挂载目录（路径探测顺序：`/remote-home1/lbsun` → `/remote-home/lbsun`，均不存在则启动失败）。目标是在单机上稳定运行多个 redroid 实例执行 GUI Agent 任务，工程沉淀为可复用模板，后续复制到其他服务器。

---

## 2. 术语与分层

| 层级 | 名称 | 职责 |
|------|------|------|
| 基础设施调度层 | Orchestrator | 实例池、任务队列、调度、健康检查、补跑、日志与指标 |
| 设备交互适配层 | android_world | 将 Agent 动作转为 ADB 操作并执行，采集截图/XML/应用数据，判定任务成功与否 |
| Agent 策略层 | m3a / t3a 等 | 基于观测决策动作，不直接操作 ADB |

**其他术语：**

- **批次（run）**：一次完整执行过程，绑定单台服务器，对应唯一 `run_id`。
- **run_id**：全局唯一，命名规范为 `{host_id}_{timestamp}_{random_suffix}`，例如 `host0_20260303_143021_a3f2`。run_id 自带 host 信息，跨机不冲突。
- **run_manifest**：每个 run 开始时自动生成，记录 agent 版本、参数配置、任务列表、host_id 等，作为复现依据与 run_id 元信息。
- **结果集（result set）**：统计对象，可为单批次默认结果集，也可跨批次派生。
- **business_key**：任务幂等去重键，定义为 `task_name + run_id`。约束：同一 run 内同一 task_name 只能出现一次，任务提交前校验，发现重复直接报错拒绝入队。
- **幂等窗口**：同一 business_key 在 `idempotency_window_min` 内重复提交不重复执行，返回已有记录。采用时间范围查重（`submitted_at >= now - window_sec`），不用分桶，避免边界误判。
- **run_completed**：执行完整性判定，是统计分母的入选条件（见第 5 节）。
- **is_successful**：业务成功判定，由 android_world 的 `task.is_successful(env)` 在执行结束时实时写入结果记录，统计层直接读取，不重复计算。

---

## 3. 功能需求

### 3.1 基础能力

- 单机启动 / 停止多个 redroid 实例，数量范围 15～40。
- ADB 连通与健康检查。
- 配置化实例参数（数量、端口、资源、镜像）。
- 统一命令入口（`precheck → up → smoke → batch`）。

### 3.2 任务切分

- 维护一个总任务集合（存于共享挂载目录）。
- 提供切分脚本：输入总任务集 + 份数，输出每台机器的子任务集。
- 切分前校验任务列表，同一 run 内 task_name 不得重复，发现重复报错退出。
- 每个批次在单台服务器上独立运行，不跨机。

### 3.3 调配系统能力

**任务状态机：**
```
queued → running → succeeded
                 → failed          (NON_RETRYABLE 或超出重试上限)
                 → timeout         (hard_timeout 触发，默认关闭)
                 → cancelled       (人工)
running → queued                   (租约超时回收后经重试判定，唯一允许的逆向转移)
```

**实例状态机：**
```
idle ↔ busy
busy | idle → unhealthy            (健康检查失败)
unhealthy   → idle                 (健康检查恢复)
unhealthy   → quarantine           (连续失败 ≥ unhealthy_to_quarantine 次)
quarantine  → idle                 (低频健康检查连续通过 ≥ quarantine_recovery_threshold 次，自动恢复)
```

可选扩展（默认关闭）：启用 `draining` 时允许 `busy | idle → draining`，进入后不接收新任务。

**调度策略：** 默认 FIFO + 健康优先，quarantine 实例不参与调度。

**租约与心跳机制：**

Worker 进程可能因容器崩溃、ADB 断连、机器异常等原因静默失败，任务状态卡在 `running` 且无进程推进。租约机制用于检测这类"静默失败"并自动回收，避免实例永久占用、批次卡死。

- 任务分配时设置 `lease_expires_at = now() + lease_ttl_sec`。`lease_ttl_sec` 是租约超时阈值，调度器发现 `lease_expires_at < now()` 时触发回收。
- Worker 每隔 `heartbeat_interval_sec` 执行一次续租（CAS 更新 `lease_expires_at`），只要任务在正常执行心跳就不会超时，长任务持续心跳即可继续执行。
- Worker 通过共享 `reclaimed` 事件感知回收：心跳线程续租失败时置位该事件，主执行线程每步开始前检查并退出，不写脏结果。
- 调度器触发回收后，写入 `task_attempts`（`error_code=E_LEASE_TIMEOUT`），然后走标准重试判定流程，不直接决定任务去向。

**并发保护：** 单实例同一时刻只执行一个任务，分配时加事务锁。

**重试策略：**

- 每次尝试终态写入时按 `error_code` 判定 RETRYABLE / NON_RETRYABLE，规则表匹配，首条命中即止。
- RETRYABLE 且未超出重试上限：退避后重入 queued。
- NON_RETRYABLE 或超出上限：直接转 failed。

**error_code 枚举（`E_*` 大写）：**

| error_code | 类型 |
|---|---|
| `E_LEASE_TIMEOUT` | RETRYABLE |
| `E_ADB_DISCONNECT` | RETRYABLE |
| `E_CONTAINER_NOT_READY` | RETRYABLE |
| `E_RESOURCE_TEMP` | RETRYABLE |
| `E_BAD_INPUT` | NON_RETRYABLE |
| `E_AGENT_INFEASIBLE` | NON_RETRYABLE |
| `E_AUTH_FAILED` | NON_RETRYABLE |

**incomplete_reason 枚举（小写下划线，落盘供人读）：**

| incomplete_reason | 说明 |
|---|---|
| `infra_timeout` | 基础设施超时 |
| `adb_disconnect` | ADB 断连 |
| `container_restart` | 容器重启 |
| `env_not_ready` | 环境未就绪 |
| `agent_infeasible` | Agent 声明不可行 |
| `bad_input` | 任务输入有误 |
| `program_error` | 程序异常 |

`error_code` 与 `incomplete_reason` 是两套独立枚举，前者用于机器判定重试逻辑，后者用于人读落盘记录，不混用。

**超时策略（任务执行时长）：**

与租约机制无关，用于限制任务总执行时长：

- `soft_timeout_min`：超过阈值只告警，不干预任务执行。
- `hard_timeout`：默认关闭。开启后超过阈值强制终止任务，即使 Worker 仍在心跳续租。

**运维动作：** 暂停队列、摘除实例、重放任务、人工标记。

### 3.4 可扩展能力

- 保留多机联邦扩展空间（Phase 2+）。
- 解耦设备交互适配层与 Agent 策略层，支持低成本替换。

---

## 4. 默认参数

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `lease_ttl_sec` | 90 | 租约超时阈值，`lease_expires_at < now()` 时触发回收 |
| `heartbeat_interval_sec` | 15 | Worker 续租心跳间隔，需远小于 `lease_ttl_sec` |
| `max_retries` | 2 | 单任务最大重试次数（不含首次） |
| `retry_backoff_sec` | 5, 15 | 重试退避序列，第 1 次等 5s，第 2 次等 15s |
| `idempotency_window_min` | 60 | 幂等去重窗口，采用时间范围查重 |
| `soft_timeout_min` | 30 | 任务执行时长告警阈值，超出只告警不干预 |
| `hard_timeout` | 关闭 | 开启后强制终止超出执行时长的任务，与租约机制无关 |
| `drain_cooldown_sec` | 120 | draining 冷却期，仅启用 draining 时生效 |
| `unhealthy_to_quarantine` | 2 | 连续健康检查失败次数阈值，达到后进入 quarantine |
| `quarantine_check_interval_sec` | 300 | quarantine 实例的健康检查间隔 |
| `quarantine_recovery_threshold` | 2 | quarantine 实例连续通过健康检查次数，达到后自动恢复为 idle |
| `poll_interval_sec` | 2～3 | 调度主循环轮询间隔 |

---

## 5. run_completed 判定

**run_completed（计入统计分母），满足以下任一且排除无效执行：**

1. Agent 提前终止（success 或 failed），即 `AgentInteractionResult.done = True`。
2. Agent 到达最大步数，且每步均被 android_world 执行层确认执行（非 no-op）。

"确认执行"的具体信号（ADB 返回码、UI 状态差分、`AgentInteractionResult` 中的执行标志）待接入 android_world 后根据实际字段确认，当前实现以占位逻辑代替，接入后替换。

**run_incomplete（不计入统计分母）：**

- 程序报错 / 环境异常。
- Agent 声明不可行（`incomplete_reason: agent_infeasible`）。
- 到达最大步数，但存在未被确认执行的步骤。

`run_incomplete` 必须落盘，至少记录：`terminal_state`、`incomplete_reason`、`last_step`、`error_code`、`attempt_index`。

`run_completed` 由 `orchestrator/worker.py` 判定并写入；`is_successful` 由 android_world 判定并写入，两者独立，统计层均只读取不重算。

---

## 6. 结果集与统计

### 6.1 结果集机制

- 每次批次结束自动生成默认结果集（`mode: merge`）。
- 支持手动编写清单文件定义跨批次派生结果集。
- 统计范围：merge 模式按 `result_set_id` 聚合；compare 模式按 group label 分组。
- 结果集载体：清单文件（YAML，存于共享挂载目录）+ 结果记录索引（`task_id / run_id / attempt_id`）。

### 6.2 结果集清单格式

**merge 模式：**
```yaml
result_set_id: wifi_tasks_merged
description: wifi tasks across all hosts
mode: merge
dedup_strategy: best
run_ids: [host0_20260303_143021_a3f2, host1_20260303_143021_b1c3]
filters:
  task_type: TurnOnWifi
  difficulty_in: [easy, medium]
```

**compare 模式：**
```yaml
result_set_id: wifi_ab_compare
description: m3a v1 vs v2 on wifi tasks
mode: compare
dedup_strategy: last

groups:
  - label: m3a_v1
    run_ids: [host0_20260303_143021_a3f2, host1_20260303_143021_b1c3]
    filters:
      task_type: TurnOnWifi

  - label: m3a_v2
    run_ids: [host0_20260303_160000_c4d5, host1_20260303_160000_e6f7]
    filters:
      task_type: TurnOnWifi
    dedup_strategy: best
```

**dedup_strategy 语义：**

| 值 | 说明 |
|----|------|
| `last` | 同一 task_id 取最新一次尝试（默认） |
| `first` | 同一 task_id 取最早一次尝试 |
| `best` | 同一 task_id 取最好结果（success > failed > incomplete） |

### 6.3 指标口径
```
task_success_rate = success_count / run_completed_count
task_failure_rate = failure_count / run_completed_count
前置门槛：run_completed_count / result_set_size >= 95%（默认，可配置）
```

### 6.4 补跑机制

- 可补跑（对应 `error_code` 为 RETRYABLE）：`E_LEASE_TIMEOUT`、`E_ADB_DISCONNECT`、`E_CONTAINER_NOT_READY`、`E_RESOURCE_TEMP`。
- 不可补跑（对应 `error_code` 为 NON_RETRYABLE）：`E_BAD_INPUT`、`E_AGENT_INFEASIBLE`。
- 补跑入口：指定 `parent_result_set_id`，系统自动筛选可补跑任务生成 `rerun_queue`。
- 补跑上限：`max_rerun_rounds=2`，`max_attempts_per_task=3`；超限标记 `incomplete_final`。
- 双口径输出：`first_pass_metrics` 与 `final_metrics_after_rerun`。

### 6.5 统计层职责

统计层只做：读取结果记录、按 `dedup_strategy` 去重、过滤 `run_completed` 子集、聚合指标、生成报告。不重复实现任何业务判定逻辑。

---

## 7. 报告规范

**batch_report.json 最小字段：**

`run_id`、`host_id`、`start_time`、`end_time`、`result_set_id`、`result_set_size`、`batch_size`、`run_completed_count`、`run_incomplete_count`、`success_count`、`failed_count`、`timeout_count`、`scheduler_metrics`、`version_triplet`

**aggregated_report.json 最小字段：**

`result_set_id`、`mode`、`run_ids`、`host_ids`、`result_set_size`、`run_completed_count`、`run_incomplete_count`、`incomplete_breakdown`、`first_pass_metrics`、`final_metrics_after_rerun`、`scheduler_metrics`、`version_triplet`

compare 模式下 `first_pass_metrics` / `final_metrics_after_rerun` 均为按 group label 分组的对象。

---

## 8. 验收标准

### 8.1 里程碑

| 里程碑 | 验收条件 |
|--------|----------|
| M0 | 输出容量基线报告（CPU/RAM/IO/端口）与推荐实例上限 |
| M1 | 容器启动成功、ADB 可连接、基础交互命令可执行 |
| M2 | android_world 完成至少一个端到端任务并可追溯落盘 |
| M3 | 多实例并发稳定运行，无端口冲突，资源配额生效 |
| M4 | 调配系统上线，见 8.2 |
| M5 | Agent 业务指标达标，见 8.3 |
| M5.5 | 异常注入全部通过，见 8.4 |
| M6 | 另一台服务器从零部署并通过同一套验收流程 |

### 8.2 M4（调配系统）Baseline / Target / Gate 策略

- **Baseline**：先完整采集调度成功率、回收成功率、实例可用率、`queue_latency_p95` 真实分布。
- **Target**：按当前迭代设目标值，可按轮调整。
- **Gate**：仅当指标明显不可接受时作为阻断条件。
- **初始建议（非硬门槛）**：调度成功率 99.0%、回收成功率 99.9%、实例可用率 99.0%、`queue_latency_p95 = 5s`。
- 阈值版本化：每轮评审更新 `metric_policy_version` 并在报告中记录。

### 8.3 M5（Agent 业务）

1. 先满足前置门槛：`run_completed_count / result_set_size >= 95%`。
2. 在 run_completed 子集内统计 `task_success_rate` / `task_failure_rate`。
3. 输出 `first_pass_metrics` 与 `final_metrics_after_rerun`，以及失败分类与关键日志索引。

### 8.4 M5.5（异常恢复）

| 注入类型 | 频率 | 恢复上限 |
|----------|------|----------|
| 杀容器 | 每 100 任务 3 次 | 60s |
| 断 ADB | 每 100 任务 5 次 | 30s |
| 超时 | 每 100 任务 5 次 | 需触发回收与重试 |

通过标准：无僵死任务、无重复并发执行、回收成功率达标。

---

## 9. 运维与复现

### 9.1 日志保留策略

| 类型 | 保留时长 | 清理触发 |
|------|----------|----------|
| 结构化日志 | 14 天 | 磁盘 ≥ 90% 清理最旧 |
| 任务结果 | 30 天 | — |
| 截图 | 7 天 | 磁盘 ≥ 80% 优先清理 |

### 9.2 跨机复现流程

1. 前置检查（Docker、端口、磁盘、权限）。
2. 执行 `precheck → up → smoke → batch`。
3. 输出同结构报告并归档版本信息。

---

## 10. 版本冻结与回滚

**release_manifest 最小字段：**

`orchestrator_version`、`android_world_commit`、`agent_version`、`redroid_image_tag`、`config_version`、`report_schema_version`、`metric_policy_version`

**回滚触发：** smoke 失败或 batch 未达阈值。

**回滚动作：** 恢复上一个 release_manifest，重跑 `smoke + 50-task mini-batch` 验证。