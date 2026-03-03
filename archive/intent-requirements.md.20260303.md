# 意图与需求文档

## 1. 背景与目标
有三台可互通服务器，共享挂载目录（可能表现为 `/remote-home/lbsun` 或 `/remote-home1/lbsun`）。
目标是在单机上稳定运行多个 `redroid` 实例执行 GUI Agent 任务，并把工程沉淀为可复用模板，后续复制到其他服务器。

## 2. 术语与分层
- 基础设施调度层：实例、队列、调度、健康、补跑、日志指标。
- 设备交互适配层：`android_world`，负责动作转 ADB 执行 + 观测采集。
- Agent策略层：`m3a/t3a` 等，根据观测生成动作。
- 批次（run）：一次执行过程。
- 结果集（result set）：评估统计对象，可为单批次默认结果集或跨批次派生结果集。

## 3. 功能需求
### 3.1 基础能力
- 支持单机启动/停止多个 redroid 实例。
- 支持 ADB 连通与健康检查。
- 支持配置化实例参数（数量、端口、资源、镜像）。
- 支持统一命令入口。

### 3.2 调配系统能力
- 任务状态机：`queued/running/succeeded/failed/timeout/cancelled`。
- 实例状态机：`idle/busy/unhealthy/quarantine`；可选 `draining`（默认关闭）。
- 租约机制：心跳续租、超时失联判定、回收。
- 调度策略：默认 `FIFO + 健康优先`。
- 并发保护：单实例同一时刻只执行一个任务。
- 重试策略：可重试错误退避重试，不可重试错误直接失败。
- 运维动作：暂停队列、摘除实例、重放任务、人工标记。

### 3.3 可扩展能力
- 保留多机联邦扩展空间。
- 解耦设备交互适配层与 Agent策略层，支持低成本替换。

## 4. 协议与参数
### 4.1 路径与环境
- 路径探测顺序：`/remote-home1/lbsun` -> `/remote-home/lbsun`，都不存在则失败。

### 4.2 默认参数
- `lease_ttl_sec=90`
- `heartbeat_interval_sec=15`
- `max_retries=2`
- `retry_backoff_sec=5,15`
- `idempotency_window_min=60`
- `drain_cooldown_sec=120`（仅 `draining` 启用时）

### 4.3 参数语义
- `lease_ttl_sec`：任务失联检测，不限制任务总时长。
- `heartbeat_interval_sec`：续租/健康心跳周期。
- `max_retries`：单任务最大重试次数（不含首次）。
- `retry_backoff_sec`：重试退避序列（秒）。示例 `5,15` 表示第一次重试等待5秒，第二次重试等待15秒。
- `idempotency_window_min`：幂等窗口。窗口内相同业务任务重复提交不重复执行，返回已有结果记录。
- `drain_cooldown_sec`：实例进入 `draining` 的冷却时长。

### 4.4 关键规则
- 长任务：可持续续租执行。
- 超时分级：`soft_timeout` 默认 30min（告警），`hard_timeout` 默认关闭（可选强制回收）。
- `draining`：可选，默认关闭。
- 错误码分类：任务尝试终态写入时按 `error_code + source + context` 判定 `RETRYABLE/NON_RETRYABLE`。
- 判断时机：每次尝试结束并写入 `run_incomplete/failed` 结果时立即判定。
- 判断标准：规则表匹配，优先级 `error_code` > `source` > `context`，产出 `retry_decision`。
- 错误码示例：
  - `RETRYABLE`: `E_ADB_DISCONNECT`, `E_CONTAINER_NOT_READY`, `E_RESOURCE_TEMP`
  - `NON_RETRYABLE`: `E_BAD_INPUT`, `E_AGENT_INFEASIBLE`, `E_AUTH_FAILED`

## 5. 状态机与转移
- 任务：`queued -> running -> succeeded|failed|timeout|cancelled`。
- `running -> queued` 仅允许于租约超时回收场景。
- 实例：`idle <-> busy`，`busy|idle -> unhealthy|quarantine`，`unhealthy|quarantine -> idle` 需健康检查恢复。
- 可选扩展：启用 `draining` 时允许 `busy|idle -> draining`，且不接收新任务。

## 6. 结果集与评估
### 6.1 结果集机制
- 批次结束自动生成默认结果集。
- 支持派生结果集（跨批次筛选）。
- 统计与验收按 `result_set_id + host_id`。
- 结果集载体：建议使用 `result_set_manifest.yaml/json`（记录筛选条件与任务清单）+ 结果存储索引（`task_id/run_id/result_id`）。
- 设计原因：结果集比批次更灵活，能支持“跨批次同类任务对比”和“补跑后重算”。

### 6.2 Agent结果口径
- `run_completed`：达到最大步数且每步有有效 Agent 输出，且非程序报错/非 Agent 不可行/非 Agent 提前成功。
- `run_incomplete`：`run_completed` 补集。
- `task_success_rate = success_count / run_completed_count`。
- `task_failure_rate = failure_count / run_completed_count`。
- 前置门槛：`run_completed_count / result_set_size >= 95%`（默认可配置）。

### 6.3 未完成任务处理与补跑
- `run_incomplete` 必须落盘，至少字段：
  - `terminal_state`, `incomplete_reason`, `last_step`, `error_code`, `attempt_index`
- 可补跑原因：`infra_timeout`, `adb_disconnect`, `container_restart`, `env_not_ready`
- 不可补跑原因：`bad_input`, `agent_infeasible`
- 补跑模式：指定 `parent_result_set_id`，系统自动筛选可补跑任务进入 `rerun_queue`（任务级补跑）。
- 补跑上限：`max_rerun_rounds=2`，`max_attempts_per_task=3`；超限标记 `incomplete_final`。
- 双口径输出：`first_pass_metrics` 与 `final_metrics_after_rerun`。

### 6.4 异常时序（示例）
1. `task_id=T1` 在 `device=D3` 上 `running`。
2. 发生 `adb_disconnect`，租约心跳超时。
3. 先写入 `run_incomplete` 记录（`attempt_index=1` 等）。
4. 再执行环境回收（重建/替换实例）。
5. 按原因自动判定补跑并入 `rerun_queue`。
6. 补跑为 `attempt_index=2`。
7. 报告输出首轮与最终双口径指标。

## 7. 评估框架解耦设计
### 7.1 职责
- 结果集评估框架：加载结果集、过滤 completed、聚合、落报告。
- 评估函数：单任务判定与打标（`valid/labels/metrics`）。
- 执行入口：指定结果集、评估函数、输出路径、配置版本。

### 7.2 CLI 示例
```bash
evaluate \
  --result-set-file config/result_sets/login_tasks.yaml \
  --evaluators task_success_v1,failure_breakdown_v1 \
  --output reports/eval_login_20260303.json
```

### 7.3 结果集名单文件示例
```yaml
result_set_id: login_tasks_v1
description: cross-run login tasks
run_ids: [run_20260303_a, run_20260303_b]
filters:
  task_type: login
  app_package: com.example.app
  difficulty_in: [easy, medium]
```

### 7.4 评估函数接口
```python
def evaluate_one(task_result: dict, config: dict) -> dict:
    # return {"valid": bool, "labels": {...}, "metrics": {...}}
```

### 7.5 报告规范
- `batch_report.json` 最小字段：
  - `run_id`, `host_id`, `start_time`, `end_time`
  - `result_set_id`, `result_set_size`
  - `batch_size`, `success_count`, `failed_count`, `timeout_count`
  - `scheduler_metrics`, `agent_metrics`, `version_triplet`
- `evaluation_report.json` 最小字段：
  - `result_set_id`, `run_ids`, `result_set_size`
  - `run_completed_count`, `run_incomplete_count`, `incomplete_breakdown`
  - `first_pass_metrics`, `final_metrics_after_rerun`
  - `evaluators`, `scheduler_metrics`
  - `version_triplet`, `evaluator_versions`, `evaluator_config_version`

## 8. 验收标准
### 8.1 调配系统指标策略（Baseline / Target / Gate）
- Baseline：先完整采集调度成功率、回收成功率、实例可用率、`queue_latency_p95` 的真实分布。
- Target：按当前迭代设目标值（可调整，不固定为永久标准）。
- Gate：仅当指标明显不可接受时作为阻断条件。
- 初始建议（非硬门槛）：调度成功率 `99.0%`、回收成功率 `99.9%`、实例可用率 `99.0%`、`queue_latency_p95=5s`（有空闲实例时）。
- 阈值版本化：每轮评审更新 `metric_policy_version`，并在报告中记录生效版本。

### 8.2 M5（业务评估）
- 按结果集统计。
- 先满足 `run_completed_rate` 门槛，再评估 completed 子集业务成功率。
- 输出失败分类与关键日志索引。

### 8.3 异常注入
- 杀容器：每100任务注入3次，恢复上限60s。
- 断ADB：每100任务注入5次，恢复上限30s。
- 超时：每100任务注入5次，需触发回收与重试。
- 通过标准：无僵死任务、无重复并发执行、回收成功率达标。

## 9. 运维与复现
### 9.1 日志保留
- 结构化日志 14天、任务结果 30天、截图 7天。
- 磁盘使用率 >=80% 清理截图，>=90% 清理最旧调试日志。

### 9.2 跨机复现
1. 前置检查（Docker、端口、磁盘、权限）。
2. 执行 `precheck -> up -> smoke -> batch`。
3. 输出同结构报告并归档版本信息。

## 10. 版本冻结与回滚
- 冻结对象：整条运行链路。
- 冻结载体：`release_manifest`。
- 最小字段：
  - `orchestrator_version`
  - `android_world_commit`
  - `agent_version`
  - `redroid_image_tag`
  - `evaluator_versions`, `evaluator_config_version`
  - `config_version`, `report_schema_version`
- 回滚触发：smoke 失败或 batch 未达阈值。
- 回滚动作：恢复上一个 `release_manifest`，重跑 `smoke + 50-task mini-batch`。
