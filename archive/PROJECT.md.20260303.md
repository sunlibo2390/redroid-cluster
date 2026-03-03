# Redroid Cluster Project

## 1. 项目定位
本项目用于构建可复制的 `redroid` 单机多实例集群，支撑 GUI Agent 任务执行。
当前优先目标是：在单台服务器上稳定运行、可观测、可补跑、可评估；并保留后续多机扩展空间。

## 2. 分层与职责
- 基础设施调度层：`redroid` 实例池、任务队列、调度、健康检查、补跑、日志与指标。
- 设备交互适配层：以 `android_world` 为代表，将 Agent 动作转换为 ADB 操作并执行，采集系统状态/应用数据/截图/XML。
- Agent策略层：以 `m3a`、`t3a` 等为代表，基于观测决策动作，不直接操作 ADB。

## 3. 当前范围与边界
- 选型：使用 `redroid`，不采用 AVD in Docker。
- 部署模式：默认“单机多实例集群”；三台机器默认独立运行，不默认联邦。
- 复用方式：共享挂载目录沉淀脚本、配置、文档；主机差异通过配置表达。
- 非目标：当前阶段不做跨三机统一全局调度。

## 3.1 未来范围（Phase 2+）
- 多机联邦调度（可选）：将多个单机集群聚合到统一控制面，支持跨机容量感知与任务路由。
- 调度能力增强：在 `FIFO + 健康优先` 基础上扩展优先级、亲和性、配额与抢占策略。
- 设备交互适配层扩展：除 `android_world` 外，支持其他交互适配实现并复用同一调度与评估体系。
- Agent策略层扩展：支持不同策略/模型版本并行评估与 A/B 对比。
- 评估体系增强：支持多结果集对比、回归检测、自动化基线告警与趋势分析。
- 运维能力增强：灰度发布、自动回滚、跨机统一观测与容量预测。

## 4. 里程碑
1. M0：环境基线与容量评估。
2. M1：单机单实例 redroid 跑通。
3. M2：`android_world` 与模拟器端到端跑通。
4. M3：单机多实例 redroid 跑通（配置化实例数/端口/资源）。
5. M4：多实例调配系统上线（必须交付项）：
   - 任务与实例状态机落地（含状态流转约束）。
   - 任务队列与分配器（默认 `FIFO + 健康优先`）。
   - 租约与心跳机制（失联检测、超时回收）。
   - 错误码分类与重试判定（`RETRYABLE/NON_RETRYABLE`）。
   - 未完成任务处理与补跑（`parent_result_set_id` 驱动、`rerun_queue`）。
   - 并发保护（单实例单任务锁）。
   - 结构化日志与核心指标上报（调度、回收、可用率、时延）。
   - 运维控制动作（暂停队列、摘除实例、重放任务）。
6. M5：`android_world` 批次任务完成并输出评估结果。
7. M6：在其他服务器按同流程复现并通过验收。

## 5. 验收口径（摘要）
- 调配系统维度：调度成功率、回收成功率、实例可用率、排队时延。
- Agent业务维度：按结果集统计，先看 `run_completed_rate`，再看 completed 子集成功率。
- 异常恢复维度：杀容器/断ADB/超时注入后可恢复，无僵死任务、无重复并发执行。
- 验收策略：采用 `Baseline/Target/Gate` 三层；初始数值为建议值，按迭代更新 `metric_policy_version`。

### 阶段验收明细（M0~M6）
- M0：输出容量基线报告（CPU/RAM/IO/端口）与推荐实例上限。
- M1：容器启动成功、ADB 可连接、基础交互命令可执行。
- M2：`android_world` 完成至少一个端到端任务并可追溯落盘。
- M3：多实例并发稳定运行，无端口冲突，资源配额生效。
- M4（调配系统维度）：
  - 先完成 Baseline：统计调度成功率、回收成功率、实例可用率、`queue_latency_p95` 分布。
  - 再设定 Target：本迭代目标值（可调整）。
  - 最后设定 Gate：仅在明显不可接受时阻断推进。
  - 初始建议（非硬门槛）：调度成功率 `99.0%`、回收成功率 `99.9%`、实例可用率 `99.0%`、`queue_latency_p95=5s`（存在空闲实例时）。
- M5（Agent业务维度）：
  - 先满足前置门槛：`run_completed_count / result_set_size >= 95%`
  - 再在 `run_completed` 子集内统计 `task_success_rate` / `task_failure_rate`
  - 输出 `first_pass_metrics` 与 `final_metrics_after_rerun`
- M5.5（异常恢复）：完成异常注入（杀容器、断ADB、超时）并满足“无僵死任务、无重复并发执行、回收成功率达标”。
- M6：在另一台服务器从零部署并通过同一套 `precheck -> up -> smoke -> batch` 验收流程。

## 6. 默认参数（可配置）
- `lease_ttl_sec=90`
- `heartbeat_interval_sec=15`
- `max_retries=2`
- `retry_backoff_sec=5,15`
- `idempotency_window_min=60`
- `drain_cooldown_sec=120`（仅启用 `draining` 时生效）

### 参数含义
- `lease_ttl_sec`：任务租约有效期，用于失联检测，不限制任务总执行时长。
- `heartbeat_interval_sec`：续租与健康上报心跳间隔。
- `max_retries`：单任务最大重试次数（不含首次执行）。
- `retry_backoff_sec`：重试退避序列（秒）。示例 `5,15` 表示第1次重试前等5秒，第2次重试前等15秒。
- `idempotency_window_min`：幂等去重窗口。窗口内“同一业务任务”的重复提交不重复执行，直接返回已存在记录。
- `drain_cooldown_sec`：`draining` 冷却期。

## 7. 关键运行规则
- 租约规则：`lease_ttl_sec` 是存活检测；长任务持续心跳即可继续执行。
- 超时规则：`soft_timeout` 默认 30min（仅告警），`hard_timeout` 默认关闭（显式开启才强制回收）。
- `draining`：可选功能，默认关闭；开启后用于维护/发布时“停接新任务、放行当前任务”。
- 错误码分类：每次尝试终态写入时按 `error_code + source + context` 归类 `RETRYABLE/NON_RETRYABLE`，决定是否补跑。
  - 判断时机：任务一次尝试结束（`run_incomplete` 或 `failed`）写结果时。
  - 判断标准：规则表匹配，优先级 `error_code` > `source` > `context`。
  - 示例：
    - `RETRYABLE`：`E_ADB_DISCONNECT`、`E_CONTAINER_NOT_READY`、`E_RESOURCE_TEMP`
    - `NON_RETRYABLE`：`E_BAD_INPUT`、`E_AGENT_INFEASIBLE`、`E_AUTH_FAILED`
- 统计范围：默认 `result_set_id + host_id`。
- 日志保留：结构化日志 14天、任务结果 30天、截图 7天；80%/90% 磁盘水位触发分级清理。

## 8. 结果集与评估
- 统计对象：优先“结果集（result set）”，不是仅“批次（run）”。
- 为什么：同一批次可能混入不同任务类型与异常噪声；结果集可精确选取“目标任务集合”并支持跨批次可比评估。
- 每次批次结束自动生成默认结果集；支持跨批次派生结果集。
- 结果集形式：一个清单文件（如 YAML/JSON）+ 对应结果记录集合（按 `task_id/run_id/result_id` 索引）。
- M5 指标：
  - `run_completed` 与 `run_incomplete` 分层。
  - `task_success_rate = success_count / run_completed_count`。
  - 前置门槛：`run_completed_count / result_set_size >= 95%`（默认可配）。

### 未运行完成任务处理
- `run_incomplete` 必须落结果：`terminal_state`, `incomplete_reason`, `last_step`, `error_code`, `attempt_index`。
- 补跑入口：运行时指定 `parent_result_set_id`，系统自动筛选可补跑任务生成 `rerun_queue`（任务级补跑）。
- 补跑上限：`max_rerun_rounds=2`、`max_attempts_per_task=3`，超限标记 `incomplete_final`。
- 双口径输出：`first_pass_metrics` 与 `final_metrics_after_rerun`。

## 9. 评估框架（解耦）
- 结果集评估框架：加载结果集、过滤 completed、聚合、输出。
- 评估函数插件：单任务判定与打标（如 `task_success_v1`）。
- 执行入口：指定结果集、评估函数、配置，生成统一报告。

示例：
```bash
evaluate \
  --result-set-file config/result_sets/login_tasks.yaml \
  --evaluators task_success_v1,failure_breakdown_v1 \
  --output reports/eval_login_20260303.json
```

## 10. 版本冻结与回滚
- 冻结对象是整条运行链路，不是单一组件。
- 使用 `release_manifest` 统一冻结：
  - `orchestrator_version`
  - `android_world_commit`
  - `agent_version`
  - `redroid_image_tag`
  - `evaluator_versions`, `evaluator_config_version`
  - `config_version`, `report_schema_version`
- 回滚：关键组件升级失败时按 `release_manifest` 整体回滚，并执行 `smoke + 50-task mini-batch` 验证。
