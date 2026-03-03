# Redroid Cluster Project

## 项目目标
在单台服务器上稳定运行多个 `redroid` 容器，形成可供 GUI Agent 并发执行任务的“单机多实例集群”；同时在架构与工具层面预留后续扩展到多机联邦集群的空间。

## 当前范围（Phase 1）
- 基于 `redroid`（不采用 AVD in Docker）。
- 单机多实例为默认模式。
- 三台服务器分别独立部署，不默认组成跨机统一集群。
- 在共享挂载路径下沉淀可复用脚本、配置模板、文档。

## 未来范围（Phase 2+）
- 引入可选的多机联邦调度层（非强依赖）。
- 支持不同设备交互适配层与 Agent 策略层的快速切换。

## 设计原则
- 基础设施调度层、设备交互适配层、Agent策略层解耦。
- 配置驱动（实例数、端口段、资源配额、镜像版本）。
- 标准化命令入口（预检、启动、停止、状态检查）。
- 文档先行，保证可复制部署。

## 分层定义
- 基础设施调度层：管理 `redroid` 多实例、任务队列、调度、健康检查、日志与可观测性。
- 设备交互适配层：以 `android_world` 为代表，负责“Agent动作 -> ADB可执行命令”转换与执行，并从模拟器采集系统状态、应用数据、屏幕与 XML/UI 树。
- Agent策略层：以 `m3a`、`t3a` 等为代表，基于观测输出动作策略，不直接操作 ADB。

## 目录规划（草案）
```text
redroid-cluster/
  compose/
  env/
  scripts/
  config/
  docs/
```

## 里程碑（已补充）
1. M0: 环境基线与容量评估（单机资源上限、端口规划、实例密度建议）。
2. M1: 创建单机单实例 redroid，并完成基础健康检查。
3. M2: 连接 `android_world` 与该实例并成功执行至少一个端到端任务。
4. M3: 创建单机多实例 redroid 集群（配置化实例数、端口、资源）。
5. M4: 建立多实例任务调配系统：
   - 维护任务队列与任务状态机。
   - 维护模拟器健康状态与隔离状态。
   - 空闲模拟器自动领取任务执行（含租约/超时回收）。
   - 默认调度策略为 FIFO + 健康优先，预留优先级/亲和性扩展。
   - 建立统一日志、指标与任务追踪体系。
6. M5: `android_world` 在多实例环境下完成一批次任务。
7. M6: 在另外服务器按同文档与同命令复现部署并通过验收。

## 阶段验收
- M0 验收：得到单机容量基线报告（CPU/RAM/IO/端口）与推荐实例数。
- M1 验收：容器可启动、ADB 可连接、基础命令可执行。
- M2 验收：`android_world` 能稳定跑通最小任务流（启动 -> 操作 -> 结果落盘）。
- M3 验收：多实例可并存运行，无端口冲突，资源配额生效。
- M4 验收（调配系统维度）：
  - 调度成功率（任务被成功分配并启动执行）`>= 99.0%`。
  - 回收成功率（超时/异常任务可被正确回收）`>= 99.9%`。
  - 实例可用率（处于 `idle` 或 `busy`）`>= 99.0%`。
  - P95 排队时延 `< 5s`（空闲实例存在时）。
  - 实例健康状态可追踪，坏实例可自动隔离。
- M5 验收（Agent业务维度）：批次任务执行完成，按“运行结束”和“未运行完成”分层统计；业务成功率/失败率仅在“运行结束”任务集合内计算。
- M5.5 验收（异常恢复）：完成异常注入（杀容器、断ADB、制造超时）并验证系统恢复。
- M6 验收：跨服务器冷启动复现成功，命令与文档一致。

## 默认参数（可配置）
- `lease_ttl_sec=90`
- `heartbeat_interval_sec=15`
- `max_retries=2`
- `retry_backoff_sec=5,15`
- `idempotency_window_min=60`
- `drain_cooldown_sec=120`

### 参数含义
- `lease_ttl_sec`：任务租约有效期（秒）。超过该时长未续租，任务会被判定失联并进入回收流程。
- `heartbeat_interval_sec`：实例心跳上报间隔（秒），用于续租和更新实例健康状态。
- `max_retries`：单任务最大重试次数，不含首次执行；达到上限后任务标记失败。
- `retry_backoff_sec`：每次重试前的退避时间序列（秒），用于降低瞬时抖动和雪崩重试。
- `idempotency_window_min`：幂等去重窗口（分钟）；窗口内重复请求返回已有任务，不重复执行。
- `drain_cooldown_sec`：实例进入 `draining` 后的冷却期（秒）；冷却期内不分配新任务。

## 参数语义与补充规则
- 长任务续租：`lease_ttl_sec` 用于任务存活检测，不是任务最长执行时长限制。长任务只要持续心跳即可继续执行。
- 超时分级：采用 `soft_timeout` 与 `hard_timeout` 两级机制；
  - `soft_timeout`（默认 `30min`）：仅告警与高亮，不中断任务。
  - `hard_timeout`（默认关闭）：仅在显式开启时才执行强制回收。
- 任务类型覆盖：支持按 `task_type` 覆盖 `max_retries/timeout_sec/lease_ttl_sec`。
- 幂等输入标准化：去空白、JSON key 排序、剔除时间戳与随机字段后计算幂等键。
- `draining` 触发：可选功能，默认关闭；开启后用于手工下线或发布升级时“停止接收新任务、允许当前任务跑完”。
- 错误码分层：`RETRYABLE` 与 `NON_RETRYABLE` 分开统计与处理；在每次任务尝试终态落库时依据 `error_code + source + context` 判定并决定是否补跑。
- 阈值统计范围：默认按“结果集 + 单机”统计（`result_set_id + host_id`）。
- 日志保留：结构化日志 `14d`（14天），任务结果 `30d`（30天），截图 `7d`（7天）；80%/90% 磁盘水位触发分级清理。
- 报告格式：统一输出 `batch_report.json`，包含 `scheduler_metrics`、`agent_metrics`、`version_triplet`。

## 状态机与规则
- 任务状态允许转移：`queued -> running -> succeeded|failed|timeout|cancelled`，`running -> queued` 仅在租约超时回收场景允许。
- 实例状态允许转移：`idle <-> busy`，`busy|idle -> unhealthy|quarantine|draining`，`unhealthy|quarantine -> idle` 仅通过健康检查恢复。
- 幂等规则：`idempotency_key = task_type + normalized_input + business_id`；窗口期内重复提交返回已有任务记录，不重复执行。
- 调度规则：先按 `FIFO`，同批次按健康分值（最近失败率、掉线次数、最近恢复时间）排序；`draining` 与 `quarantine` 实例不可分配。

## Agent 结果统计口径（M5）
- 统计对象：采用“结果集（result set）”而非仅“批次（batch）”。单次批次结束后会自动产出一个默认结果集；也支持跨批次筛选生成派生结果集。
- 运行结束（`run_completed`）：达到最大步数且每一步 Agent 都有有效输出，且非程序报错、非 Agent 主动判定不可行、非 Agent 主动成功提前结束。
- 未运行完成（`run_incomplete`）：`run_completed` 的补集。
- 业务成功率：`task_success_rate = success_count / run_completed_count`。
- 业务失败率：`task_failure_rate = failure_count / run_completed_count`。
- 前置通过条件（系统可用性门槛）：`run_completed_count / result_set_size >= 95%`（默认，可配置）。
- 批次规模：默认 `N=500`（可按资源调整）。
- 标识建议：`run_id`（批次标识）、`result_set_id`（结果集标识）。

### 未运行完成任务的记录与补跑
- 记录策略：`run_incomplete` 任务也必须落结果，至少包含 `terminal_state`, `incomplete_reason`, `last_step`, `error_code`, `attempt_index`。
- 补跑触发：仅对可补跑原因触发（如 `infra_timeout`, `adb_disconnect`, `container_restart`, `env_not_ready`）。
- 不补跑原因：`bad_input`, `agent_infeasible` 等非环境类问题。
- 补跑边界：采用“有限补跑”，默认 `max_rerun_rounds=2`、`max_attempts_per_task=3`，超过上限标记 `incomplete_final`。
- 补跑入口：运行时指定 `parent_result_set_id`，系统从父结果集中自动筛选可补跑任务并生成 `rerun_queue`，按任务级补跑而非整批重跑。
- 评估口径：同时输出 `first_pass_metrics`（首轮）与 `final_metrics_after_rerun`（补跑收敛后）。

### 环境异常时序（示例）
1. `task_id=T1` 进入 `running`，绑定 `device=D3`。
2. 运行中发生 `adb_disconnect`，租约心跳超时。
3. 先落结果：写入 `attempt_index=1`、`terminal_state=run_incomplete`、`incomplete_reason=adb_disconnect`、`last_step`、`error_code`。
4. 再回收环境：重建或替换异常实例。
5. 自动判定补跑：`adb_disconnect` 属于可补跑原因，写入 `rerun_queue`。
6. 补跑执行：同一 `task_id=T1` 以 `attempt_index=2` 重新调度；成功或失败都再次落结果。
7. 评估输出：首轮计入 `first_pass_metrics`，最终态计入 `final_metrics_after_rerun`。

## 评估框架设计（解耦）
- 结果集评估框架：负责加载结果集、过滤 `run_completed`、聚合统计、输出报告；不内置业务成功判定逻辑。
- 评估函数插件：对单任务结果执行判定与打标（例如 `task_success_v1`、`failure_breakdown_v1`）。
- 执行入口（CLI/API）：通过参数指定“结果集 + 评估函数 + 配置”，生成统一格式统计结果。

### 调用方式（示例）
```bash
evaluate \
  --result-set-file config/result_sets/login_tasks.yaml \
  --evaluators task_success_v1,failure_breakdown_v1 \
  --output reports/eval_login_20260303.json
```

### 评估函数接口（约定）
```python
def evaluate_one(task_result: dict, config: dict) -> dict:
    # return {"valid": bool, "labels": {...}, "metrics": {...}}
```

### 评估结果元数据
- 必须记录：`result_set_id`、`run_ids`、`version_triplet`、`evaluator_versions`、`evaluator_config_version`。

## 异常注入与复现
- 异常注入矩阵：
  - 杀容器：每 100 任务注入 3 次，恢复时间上限 60s。
  - 断ADB：每 100 任务注入 5 次，恢复时间上限 30s。
  - 任务超时：每 100 任务注入 5 次，需触发回收与重试。
- 通过标准：注入后调度系统无僵死任务、无重复并发执行、回收成功率满足阈值。
- 跨机复现：第二台机器按同文档从零执行 `precheck -> up -> smoke -> batch`，并输出同结构报告。

## 版本冻结与回滚
- 冻结对象：不是仅项目代码，而是整条运行链路（基础设施调度层、设备交互适配层、Agent策略层、运行镜像、评估链路）。
- 推荐使用 `release_manifest` 统一冻结：
  - `orchestrator_version`（本项目控制面版本）
  - `android_world_commit`（设备交互适配层版本）
  - `agent_version`（如 `m3a/t3a` 版本与关键参数）
  - `redroid_image_tag`（运行镜像版本）
  - `evaluator_versions`、`evaluator_config_version`
  - `config_version`、`report_schema_version`
- 回滚策略：任一关键组件升级失败时，按 `release_manifest` 整体回滚并重跑 `smoke + 50-task mini-batch`。
