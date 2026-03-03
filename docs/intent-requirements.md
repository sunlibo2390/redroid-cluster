# 意图与需求文档

## 1. 背景
当前有三台可互通服务器，且共享挂载路径（不同机器可能表现为 `/remote-home/lbsun` 或 `/remote-home1/lbsun`）。目标是建设一套可复制的 redroid 运行体系，先在单机多实例稳定运行，再扩展到更多机器。

## 2. 已确认意图
- 选择 `redroid` 作为 Android 容器方案。
- 默认实施“单机多实例集群”。
- 三台服务器默认独立运行，不强制组成一个跨机统一集群。
- 保留未来多机联邦调度能力。
- 参考 `android_world` 作为第一批设备交互适配层，但架构要支持后续替换适配层并复用现有工具与文件。
- `m3a`、`t3a` 等作为 Agent 策略层实现，可在不改基础设施调度层的前提下替换。

## 3. 核心需求
### 3.1 功能需求
- 支持单机启动/停止多个 redroid 实例。
- 支持标准化健康检查（容器状态、ADB连通性、基础操作可用性）。
- 支持配置化实例参数（数量、端口、资源限制、镜像版本）。
- 支持统一命令入口，便于跨服务器重复执行。

### 3.2 工程需求
- 所有脚本、模板、文档放在共享挂载目录，方便跨机复用。
- 主机差异通过独立配置文件表达（避免硬编码路径与端口）。
- 文档完整覆盖：快速开始、部署步骤、常见故障、迁移说明。

### 3.3 可扩展需求
- 预留多机联邦配置层，不影响单机模式。
- 通过 adapter 接口解耦“设备交互适配层”和“Agent策略层”，实现低成本替换。

### 3.7 分层边界定义
- 基础设施调度层：实例池、任务队列、调度、健康检查、日志与指标。
- 设备交互适配层：将 Agent 输出动作转换为 ADB 可执行命令并执行；采集系统状态、应用数据、屏幕、XML/UI 树等观测。
- Agent策略层：根据观测生成下一步动作，不直接触达 ADB。

### 3.4 多实例调配系统补充需求
- 任务状态机：`queued/running/succeeded/failed/timeout/cancelled`。
- 实例状态机：`idle/busy/unhealthy/quarantine`，可选扩展 `draining`（默认关闭）。
- 任务租约机制：领取后定期心跳，超时自动回收并可重试。
- 调度策略：默认 `FIFO + 健康优先`，预留优先级与亲和性扩展。
- 并发互斥：单实例同一时刻仅执行一个任务。
- 错误分级与重试：可重试错误退避重试，不可重试错误直接失败归档。
- 运维控制面：暂停队列、摘除实例、重放任务、人工标记。
- 数据保留策略：日志/截图/结果的保留周期与清理规则。
- 可观测性：结构化日志与核心指标（调度成功率、时延、重试、掉线）。
- 审计与追踪：每个任务保留任务ID、输入参数、执行节点、容器ID、结果与失败原因。

### 3.5 协议与参数细化
- 路径探测顺序：优先 `/remote-home1/lbsun`，其次 `/remote-home/lbsun`，均不存在则启动失败。
- 租约参数：`lease_ttl_sec=90`，`heartbeat_interval_sec=15`，超时后回收并进入重试流程。
- 重试参数：`max_retries=2`，退避 `5s, 15s`；可重试类型包括容器临时不可达、ADB瞬断、短时资源不足。
- 幂等策略：`idempotency_key = task_type + normalized_input + business_id`；`60min` 窗口内重复提交直接返回原任务。
- 日志字段最小集合：`timestamp, level, task_id, device_id, container_id, attempt, trace_id, event, error_code`。
- 指标最小集合：`scheduler_dispatch_success_rate`、`scheduler_reclaim_success_rate`、`instance_availability`、`queue_latency_p95`。
- 长任务租约：`lease_ttl_sec` 仅用于任务存活检测，不是任务最大执行时长。长任务可持续续租执行。
- 超时分级：
  - `soft_timeout` 默认 `30min`，仅告警和高亮。
  - `hard_timeout` 默认关闭，仅在显式开启时触发强制回收。
- 任务类型参数：支持按 `task_type` 覆盖 `max_retries/timeout_sec/lease_ttl_sec`，未配置时使用全局默认值。
- `normalized_input` 规范：去除无关空白、排序 JSON key、移除时间戳与随机字段后再计算幂等键。
- `draining` 触发：可选功能默认关闭；开启后用于手工下线或发布升级期间停止接收新任务。
- 错误码字典：`RETRYABLE`（`E_ADB_DISCONNECT`, `E_CONTAINER_NOT_READY`, `E_RESOURCE_TEMP`）；`NON_RETRYABLE`（`E_BAD_INPUT`, `E_AUTH_FAILED`, `E_UNSUPPORTED_ACTION`）。
- 判定时机与方式：每次任务尝试终态（`run_incomplete/failed`）写入时，根据 `error_code + source + context` 做规则匹配，得到 `RETRYABLE/NON_RETRYABLE`。

#### 参数含义
- `lease_ttl_sec`：任务租约有效期（秒），用于判定任务是否失联；不限制任务总执行时长。
- `heartbeat_interval_sec`：实例心跳上报间隔（秒），用于续租与健康更新。
- `max_retries`：单任务最大重试次数（不含首次执行）。
- `retry_backoff_sec`：重试前退避时间序列（秒），按尝试次数依次使用。
- `idempotency_window_min`：幂等窗口（分钟）；窗口内重复请求返回原任务。
- `drain_cooldown_sec`：仅在启用 `draining` 时生效，表示实例停止接新任务后的冷却时长（秒）。

### 3.6 状态机转移规则
- 任务状态转移：`queued -> running -> succeeded|failed|timeout|cancelled`；仅租约超时场景允许 `running -> queued`。
- 实例状态转移：`idle <-> busy`；`busy|idle -> unhealthy|quarantine`；`unhealthy|quarantine -> idle` 需通过健康检查。
- 可选扩展：启用 `draining` 时允许 `busy|idle -> draining`，且 `draining` 不可领取新任务。

## 4. 非目标（当前阶段）
- 当前阶段不实现跨三机的统一全局调度。
- 当前阶段不绑定单一 GUI Agent 实现细节。

## 5. 约束与风险
- 路径表现可能因机器不同而不同：`/remote-home/lbsun` vs `/remote-home1/lbsun`。
- 多实例并发下存在 CPU/内存/端口冲突风险，需要统一端口规划与资源配额。
- GUI Agent 对底层环境稳定性敏感，需要建立 smoke test 与长期稳定性观测。

## 6. 验收标准（Phase 1）
- 单机可稳定运行多个 redroid 实例并被自动化脚本发现。
- ADB 批量连接成功率达到可用阈值（项目内定义具体数值）。
- 所有关键操作可通过统一命令完成，且在另一台服务器可按文档复现。
- 指标分层清晰：
  - 调配系统指标：调度成功率、回收成功率、实例可用率、任务排队时延。
  - Agent业务指标：任务完成率、任务质量指标、业务失败分类。

### 6.1 量化阈值（默认）
- 调度成功率 `>= 99.0%`。
- 回收成功率 `>= 99.9%`。
- 实例可用率 `>= 99.0%`。
- `queue_latency_p95 < 5s`（存在空闲实例时）。
- 统计口径：默认按“结果集（result set）+ 单机”统计（`result_set_id + host_id`）。

## 7. 里程碑拆解（执行顺序）
1. M0：环境基线与容量评估。
2. M1：创建单机单实例 redroid。
3. M2：连接 `android_world` 与模拟器并运行成功。
4. M3：创建单机多实例 redroid 集群。
5. M4：建立多实例任务调配系统（任务队列、租约、健康状态、空闲实例领任务、日志指标体系）。
6. M5：`android_world` 成功运行一批次任务。
7. M6：在其他服务器按同流程复现部署并通过验收。

## 8. 各里程碑验收口径
- M0：输出容量基线报告与推荐实例上限。
- M1：容器启动成功，ADB 可连接，基础交互命令可执行。
- M2：`android_world` 完成至少一个端到端任务，产出可追溯结果。
- M3：多实例并发稳定运行，端口与资源分配规则正确。
- M4（调配系统维度）：支持任务入队、分配、执行、完成/失败状态流转；具备租约超时回收与基础重试能力；输出调配系统指标。
- M5（Agent业务维度）：批次任务完成后按“运行结束/未运行完成”分层；业务成功率与失败率仅在“运行结束”任务集合中计算，并输出关键日志索引。
- M5.5（异常恢复测试）：完成异常注入测试（杀容器、断ADB、制造超时），并验证调配系统恢复能力。
- M6：在另一台服务器从零部署并通过同一套 smoke test 与 batch test。

## 9. 异常注入测试矩阵
- 杀容器：每 100 任务注入 3 次，恢复时间上限 60s。
- 断ADB：每 100 任务注入 5 次，恢复时间上限 30s。
- 任务超时：每 100 任务注入 5 次，必须触发回收与重试。
- 通过标准：无僵死任务、无并发重复执行、回收成功率达标。

## 9.1 日志保留与清理
- 保留周期：结构化日志 `14d`（14天），任务结果与摘要 `30d`（30天），调试截图 `7d`（7天）。
- 磁盘水位：使用率 `>= 80%` 开始清理截图，`>= 90%` 清理最旧调试日志，业务结果文件不在自动清理范围内。
- 清理策略：按时间分层清理，且保留最近 `24h` 的全部故障样本。

## 10. 跨机复现清单
1. 在第二台机器执行前置检查（Docker、端口、磁盘、权限）。
2. 按统一命令执行：`precheck -> up -> smoke -> batch`。
3. 输出与主机1同结构报告（容量、调配指标、业务指标、失败分类）。
4. 记录镜像与代码版本三元组并归档。

## 10.1 报告输出格式（JSON）
- `batch_report.json` 最小字段：
  - `run_id`, `host_id`, `start_time`, `end_time`
  - `result_set_id`, `result_set_size`
  - `batch_size`, `success_count`, `failed_count`, `timeout_count`
  - `scheduler_metrics`（`dispatch_success_rate`, `reclaim_success_rate`, `instance_availability`, `queue_latency_p95`）
  - `agent_metrics`（`task_success_rate`, `failure_breakdown`）
  - `version_triplet`（`redroid_image_tag`, `android_world_commit`, `orchestrator_version`）

## 10.1.1 结果集机制
- 批次（`run`）结束后自动生成默认结果集（该批次全量任务结果）。
- 支持基于筛选条件生成派生结果集（可跨批次），用于目标任务集评估。
- 统计与验收优先基于 `result_set_id`，不强绑定 `run_id`。
- 支持补跑派生结果集：对可补跑的 `run_incomplete` 任务执行补跑后，产出 `result_set_id + \"-final\"` 作为最终态结果集。

## 10.1.2 评估框架与评估函数解耦
- 结果集评估框架职责：
  - 读取结果集（`result_set_id` 或名单文件路径）。
  - 过滤 `run_completed` 集合并进行统一聚合。
  - 调用一个或多个评估函数插件。
  - 产出统一结构的评估报告。
- 评估函数职责：
  - 输入单任务结果，输出 `valid/labels/metrics`。
  - 不关心结果集加载、批次拼接、报告落盘。
- 执行入口职责：
  - 指定评估函数、结果集文件、输出路径与评估配置版本。

### CLI 规范（示例）
```bash
evaluate \
  --result-set-file config/result_sets/login_tasks.yaml \
  --evaluators task_success_v1,failure_breakdown_v1 \
  --output reports/eval_login_20260303.json
```

### 结果集名单文件（示例）
```yaml
result_set_id: login_tasks_v1
description: cross-run login tasks
run_ids: [run_20260303_a, run_20260303_b]
filters:
  task_type: login
  app_package: com.example.app
  difficulty_in: [easy, medium]
```

### 评估函数接口约定（伪代码）
```python
def evaluate_one(task_result: dict, config: dict) -> dict:
    # return {"valid": bool, "labels": {...}, "metrics": {...}}
```

## 10.2 Agent 结果口径定义
- `run_completed`：达到最大步数且每一步 Agent 有有效输出，且非程序报错、非 Agent 主动判定不可行、非 Agent 主动成功提前结束。
- `run_incomplete`：`run_completed` 的补集。
- `task_success_rate = success_count / run_completed_count`。
- `task_failure_rate = failure_count / run_completed_count`。
- 前置通过门槛：`run_completed_count / result_set_size >= 95%`（默认可配置）；仅满足该门槛后再判定业务成功率阈值。
- 默认批次规模：`N=500`（可配置）。

## 10.2.1 未运行完成任务处理与补跑
- 记录要求：`run_incomplete` 任务必须写入结果，至少包含 `terminal_state`, `incomplete_reason`, `last_step`, `error_code`, `attempt_index`。
- 可补跑原因：`infra_timeout`, `adb_disconnect`, `container_restart`, `env_not_ready`。
- 不可补跑原因：`bad_input`, `agent_infeasible` 等非环境因素。
- 补跑模式：运行时指定 `parent_result_set_id`，系统从父结果集中自动筛选可补跑任务生成 `rerun_queue`（任务级补跑，不做整批重跑）。
- 补跑上限：默认 `max_rerun_rounds=2`，`max_attempts_per_task=3`；超过上限标记 `incomplete_final`。
- 评估输出：同时提供 `first_pass_metrics` 与 `final_metrics_after_rerun`，并给出 `incomplete_breakdown`。

### 10.2.1.1 环境异常处理时序（示例）
1. `task_id=T1` 分配到 `device=D3`，状态 `running`。
2. 发生环境异常（例如 `adb_disconnect`），租约心跳超时。
3. 先落 `run_incomplete` 结果记录（含 `attempt_index=1`, `incomplete_reason`, `last_step`, `error_code`）。
4. 后执行环境回收（实例重建或替换）。
5. 根据 `incomplete_reason` 自动判定是否入 `rerun_queue`。
6. 若可补跑，则以 `attempt_index=2` 重新调度同一 `task_id`。
7. 最终按“首轮 vs 补跑后最终态”输出双口径指标。

## 10.3 评估报告输出规范
- `evaluation_report.json` 最小字段：
  - `result_set_id`, `run_ids`, `result_set_size`
  - `run_completed_count`, `run_incomplete_count`
  - `incomplete_breakdown`
  - `first_pass_metrics`, `final_metrics_after_rerun`
  - `evaluators`（各评估函数输出汇总）
  - `scheduler_metrics`（可选附带）
  - `version_triplet`
  - `evaluator_versions`, `evaluator_config_version`

## 11. 版本冻结与回滚
- 冻结范围：整条运行链路，而非单一组件。
  - 基础设施调度层：本项目控制面代码与配置。
  - 设备交互适配层：`android_world` 版本。
  - Agent策略层：`m3a/t3a` 版本与关键参数。
  - 运行环境：`redroid` 镜像与关键启动参数。
  - 评估链路：评估函数版本、评估配置版本、报告 schema 版本。
- 冻结格式：使用 `release_manifest` 统一记录：
  - `orchestrator_version`, `android_world_commit`, `agent_version`
  - `redroid_image_tag`
  - `evaluator_versions`, `evaluator_config_version`
  - `config_version`, `report_schema_version`
- 回滚触发：smoke 失败或 batch 未达阈值。
- 回滚动作：恢复上一个 `release_manifest`，重跑 `smoke + 50-task mini-batch`。
