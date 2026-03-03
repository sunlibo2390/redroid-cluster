# 设计文档 · Redroid Cluster
> 版本：v2.4 · 2026-03-03

---

## 1. 整体架构
```
┌─────────────────────────────────────────────────┐
│                   单台服务器                      │
│                                                 │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐      │
│  │ redroid  │  │ redroid  │  │ redroid  │ ...  │
│  │ :5555    │  │ :5556    │  │ :5557    │      │
│  └────┬─────┘  └────┬─────┘  └────┬─────┘      │
│       └──────────────┴─────────────┘            │
│                     ADB                         │
│        ┌──────────────────────────┐             │
│        │  third_party/            │             │
│        │  android_world           │             │
│        │  env / task_evals /      │             │
│        │  agents                  │             │
│        └──────────────────────────┘             │
│        ┌──────────────────────────┐             │
│        │  orchestrator/           │             │
│        │  loop / worker / db      │             │
│        └──────────────────────────┘             │
│        ┌──────────────────────────┐             │
│        │       SQLite DB          │             │
│        └──────────────────────────┘             │
└─────────────────────────────────────────────────┘
          │ 共享挂载目录
          │ /remote-home1/lbsun（或 /remote-home/lbsun）
          │  ├── tasks/           总任务集 + 切分子集
          │  ├── result_sets/     结果集清单文件
          │  ├── results/         任务结果落盘
          │  └── reports/         统计报告归档
```

---

## 2. 目录结构
```
redroid-cluster/
├── third_party/
│   └── android_world/
│       ├── android_world/
│       │   ├── agents/
│       │   ├── env/
│       │   └── task_evals/
│       ├── run.py
│       └── minimal_task_runner.py
│
├── tasks/
│   └── custom/
│
├── orchestrator/
│   ├── db.py
│   ├── state.py
│   ├── loop.py
│   ├── worker.py
│   └── ops.py
│
├── reporting/
│   ├── metrics.py
│   ├── aggregate.py
│   └── report.py
│
├── config/
│   ├── instances.yaml
│   ├── scheduler.yaml
│   ├── retry_rules.yaml
│   └── result_sets/
│
├── scripts/
│   ├── precheck.sh
│   ├── up.sh
│   ├── smoke.sh
│   └── split_tasks.py
│
├── runs/                           # .gitignore
│   ├── db/
│   ├── logs/
│   └── reports/
│
├── release_manifest.yaml
└── README.md
```

---

## 3. android_world 接入方式

### 3.1 原则

- `third_party/android_world` 整体 git clone，不做侵入式修改。
- 所有接入通过 `import` 完成，改动集中在 `orchestrator/worker.py`。

### 3.2 原项目驱动模式（参考）
```python
task.initialize_task(env)
agent = T3A(env, model)
for _ in range(task.complexity * 10):
    response = agent.step(task.goal)
    if response.done:
        break
agent_successful = task.is_successful(env) == 1
```

### 3.3 我们的接管模式
```python
def execute(conn, task_record, env, agent, reclaimed: threading.Event):
    from third_party.android_world.android_world.task_evals import task_registry

    task_type = task_registry.get_registry(...)[task_record['task_name']]
    task = task_type(task_record['params'])
    task.initialize_task(env)

    max_steps = int(task.complexity * 10)
    step_results = []

    for step_idx in range(max_steps):
        if reclaimed.is_set():
            raise TaskReclaimedError()

        response = agent.step(task.goal)
        step_results.append({
            'step': step_idx,
            'done': response.done,
            'has_valid_action': has_valid_action(response),
        })

        if response.done:
            break

    run_completed = is_run_completed(step_results)
    is_successful = task.is_successful(env) == 1 if run_completed else None

    return {
        'run_completed': run_completed,
        'is_successful': is_successful,
        'step_results':  step_results,
    }

def has_valid_action(response) -> bool:
    # TODO: 接入后根据 AgentInteractionResult 实际字段替换
    # 候选信号：ADB 返回码、UI 状态差分、executed 标志
    return response.action is not None   # 占位

def is_run_completed(step_results: list[dict]) -> bool:
    if not step_results:
        return False
    if step_results[-1]['done']:
        return True
    return all(s['has_valid_action'] for s in step_results)
```

---

## 4. 数据库 Schema（SQLite）
```sql
CREATE TABLE instances (
    instance_id          TEXT PRIMARY KEY,
    host_id              TEXT NOT NULL,
    adb_serial           TEXT NOT NULL,
    status               TEXT NOT NULL DEFAULT 'idle',
    current_task_id      TEXT,
    consecutive_failures INTEGER DEFAULT 0,
    quarantine_passes    INTEGER DEFAULT 0,
    last_heartbeat       REAL,
    created_at           REAL,
    updated_at           REAL
);

CREATE TABLE tasks (
    task_id              TEXT PRIMARY KEY,
    business_key         TEXT NOT NULL,      -- task_name:run_id
    task_type            TEXT,
    payload              TEXT,               -- JSON
    status               TEXT NOT NULL DEFAULT 'queued',
    attempt_index        INTEGER DEFAULT 0,
    max_attempts         INTEGER DEFAULT 3,
    assigned_to          TEXT,
    lease_expires_at     REAL,
    retry_after          REAL,
    created_at           REAL,
    updated_at           REAL,
    result_set_id        TEXT,
    parent_result_set_id TEXT
);

-- 幂等表：时间范围查重，不用分桶
CREATE TABLE idempotency_log (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    business_key   TEXT NOT NULL,
    task_id        TEXT NOT NULL,
    submitted_at   REAL NOT NULL
);
CREATE INDEX idx_idempotency ON idempotency_log(business_key, submitted_at);

CREATE TABLE task_attempts (
    attempt_id        TEXT PRIMARY KEY,
    task_id           TEXT NOT NULL,
    attempt_index     INTEGER NOT NULL,
    instance_id       TEXT NOT NULL,
    status            TEXT NOT NULL,
    error_code        TEXT,               -- E_* 大写枚举，用于重试判定
    error_source      TEXT,
    error_context     TEXT,
    retry_decision    TEXT,               -- RETRYABLE / NON_RETRYABLE
    run_completed     INTEGER,            -- 0 / 1
    is_successful     INTEGER,            -- 0 / 1 / NULL
    terminal_state    TEXT,
    incomplete_reason TEXT,               -- 小写下划线枚举，供人读
    last_step         INTEGER,
    started_at        REAL,
    ended_at          REAL
);

CREATE TABLE result_sets (
    result_set_id        TEXT PRIMARY KEY,
    run_id               TEXT,
    host_id              TEXT,
    parent_result_set_id TEXT,
    description          TEXT,
    filters              TEXT,
    created_at           REAL
);

CREATE INDEX idx_tasks_status     ON tasks(status, retry_after);
CREATE INDEX idx_instances_status ON instances(status);
```

**幂等提交逻辑（时间范围查重）：**
```python
def submit_task(conn, task_name, run_id, payload, config):
    business_key = f"{task_name}:{run_id}"
    window_sec   = config.idempotency_window_min * 60
    now          = time.time()

    # 时间范围查重，避免分桶边界问题
    existing = conn.execute("""
        SELECT task_id FROM idempotency_log
        WHERE business_key = ?
          AND submitted_at >= ?
        LIMIT 1
    """, (business_key, now - window_sec)).fetchone()

    if existing:
        return existing['task_id'], False   # 窗口内已存在，不重复提交

    task_id = new_uuid()
    with conn:
        conn.execute("""
            INSERT INTO idempotency_log(business_key, task_id, submitted_at)
            VALUES (?, ?, ?)
        """, (business_key, task_id, now))
        conn.execute("INSERT INTO tasks (...) VALUES (...)", ...)
    return task_id, True
```

**payload 格式约定：**
```json
{
    "task_name": "TurnOnWifi",
    "app_package": "com.android.settings",
    "difficulty": "easy",
    "params": {"seed": 42},
    "input_data_path": "/remote-home1/lbsun/tasks/input/T001.json"
}
```

---

## 5. 模块设计

### 5.1 db.py
```python
def submit_task(conn, task_name, run_id, payload, config) -> tuple[str, bool]: ...
def get_expired_leases(conn, now: float) -> list[dict]: ...
def get_idle_instances(conn) -> list[dict]: ...
def get_queued_tasks(conn, now: float) -> list[dict]: ...
def get_checkable_instances(conn, now: float, config) -> list[dict]: ...
def assign_task(conn, task_id, instance_id, lease_expires_at) -> None: ...
def update_instance_health(conn, instance_id, status,
                           consecutive_failures, quarantine_passes) -> None: ...
def write_attempt(conn, attempt: dict) -> None: ...
def cas_task_status(conn, task_id, expected, new, **kwargs) -> int: ...
def cas_lease(conn, task_id, new_lease_expires_at) -> int: ...
```

### 5.2 state.py
```python
def on_lease_expired(task, instance) -> tuple[str, str]:
    # 返回 (error_code='E_LEASE_TIMEOUT', new_instance_status='unhealthy')

def on_task_finished(attempt, retry_rules) -> str:
    # 返回 RETRYABLE / NON_RETRYABLE
    # 匹配 error_code，首条命中即止

def on_health_check(instance, adb_ok: bool, config) -> str:
    # unhealthy：
    #   ok=True  → idle，重置 consecutive_failures
    #   ok=False, failures < threshold → unhealthy
    #   ok=False, failures >= threshold → quarantine
    # quarantine（低频）：
    #   ok=True, passes < recovery_threshold → quarantine，passes+1
    #   ok=True, passes >= recovery_threshold → idle
    #   ok=False → quarantine，重置 quarantine_passes
```

### 5.3 loop.py
```python
def tick(conn, config):
    # 事务 1：租约回收
    for task in db.get_expired_leases(conn, now()):
        error_code, new_inst_s = state.on_lease_expired(task, ...)
        attempt = build_attempt(task, error_code=error_code)
        retry_decision = state.on_task_finished(attempt, retry_rules)
        new_task_s = 'queued' if (retry_decision == 'RETRYABLE'
                                  and task['attempt_index'] < task['max_attempts']) \
                              else 'failed'
        with conn:
            db.write_attempt(conn, attempt)
            db.cas_task_status(conn, task['task_id'], 'running', new_task_s)
            db.update_instance_health(conn, task['assigned_to'], new_inst_s, ...)

    # 事务 2：健康检查
    for instance in db.get_checkable_instances(conn, now(), config):
        ok = adb_ping(instance['adb_serial'])
        new_status = state.on_health_check(instance, ok, config)
        with conn:
            db.update_instance_health(conn, instance['instance_id'], new_status, ...)

    # 事务 3：任务分配
    with conn:
        for inst, task in zip(db.get_idle_instances(conn),
                              db.get_queued_tasks(conn, now())):
            db.assign_task(conn, task['task_id'], inst['instance_id'],
                           now() + config.lease_ttl_sec)
```

### 5.4 worker.py
```python
def run_task(conn, task_record, env, agent, config):
    reclaimed  = threading.Event()
    stop_event = threading.Event()

    def heartbeat_loop():
        while not stop_event.is_set():
            rows = db.cas_lease(conn, task_record['task_id'],
                                now() + config.lease_ttl_sec)
            if rows == 0:
                reclaimed.set()
                return
            stop_event.wait(config.heartbeat_interval_sec)

    threading.Thread(target=heartbeat_loop, daemon=True).start()
    try:
        result = execute(conn, task_record, env, agent, reclaimed)
        if reclaimed.is_set():
            log.warn(f"task {task_record['task_id']} reclaimed mid-execution, dropping")
            return
        write_result(conn, task_record, result)
    finally:
        stop_event.set()

def write_result(conn, task_record, result):
    rows = db.cas_task_status(conn, task_record['task_id'],
                              expected='running', new=result['final_status'])
    if rows == 0:
        log.warn(f"task {task_record['task_id']} already reclaimed, dropping result")
        return
    db.write_attempt(conn, build_attempt(task_record, result))
```

### 5.5 ops.py
```python
def pause_queue(conn) -> None: ...
def resume_queue(conn) -> None: ...
def remove_instance(conn, instance_id) -> None:
def replay_task(conn, task_id) -> None:
def mark_task(conn, task_id, label) -> None:
```

---

## 6. 统计层设计
```python
# reporting/metrics.py

def dedup(records: list[dict], strategy: str) -> list[dict]:
    # 按 task_id 分组，按 strategy 取一条
    # best：success > failed > incomplete
    # last / first：按 started_at 排序

def compute_merge(records: list[dict], dedup_strategy: str) -> dict:
    deduped    = dedup(records, dedup_strategy)
    completed  = [r for r in deduped if r['run_completed']]
    success    = [r for r in completed if r['is_successful']]
    failed     = [r for r in completed if not r['is_successful']]
    incomplete = [r for r in deduped if not r['run_completed']]
    return {
        'result_set_size':      len(deduped),
        'run_completed_count':  len(completed),
        'run_incomplete_count': len(incomplete),
        'success_count':        len(success),
        'failed_count':         len(failed),
        'task_success_rate':    len(success) / len(completed) if completed else None,
        'completed_rate':       len(completed) / len(deduped) if deduped else None,
    }

def compute_compare(manifest: dict, all_records: list[dict]) -> dict:
    top_strategy = manifest.get('dedup_strategy', 'last')
    results = {}
    for group in manifest['groups']:
        strategy      = group.get('dedup_strategy', top_strategy)
        group_records = filter_records(all_records, group['run_ids'],
                                       group.get('filters'))
        results[group['label']] = compute_merge(group_records, strategy)
    return results
```
```python
# reporting/report.py
def generate_batch_report(run_id, host_id, records, config) -> dict: ...

def generate_aggregated_report(manifest, all_records) -> dict:
    if manifest['mode'] == 'merge':
        metrics = compute_merge(
            filter_records(all_records, manifest['run_ids'], manifest.get('filters')),
            manifest.get('dedup_strategy', 'last')
        )
    else:
        metrics = compute_compare(manifest, all_records)
```

---

## 7. 错误码与 incomplete_reason 规则表

**error_code（`E_*` 大写，用于机器判定重试）：**
```yaml
# config/retry_rules.yaml
retry_rules:
  - error_code: E_LEASE_TIMEOUT
    decision: RETRYABLE
  - error_code: E_ADB_DISCONNECT
    decision: RETRYABLE
  - error_code: E_CONTAINER_NOT_READY
    decision: RETRYABLE
  - error_code: E_RESOURCE_TEMP
    decision: RETRYABLE
  - error_code: E_BAD_INPUT
    decision: NON_RETRYABLE
  - error_code: E_AGENT_INFEASIBLE
    decision: NON_RETRYABLE
  - error_code: E_AUTH_FAILED
    decision: NON_RETRYABLE
```

**incomplete_reason（小写下划线，落盘供人读，不参与重试判定）：**

| incomplete_reason | 对应 error_code |
|---|---|
| `infra_timeout` | `E_LEASE_TIMEOUT` |
| `adb_disconnect` | `E_ADB_DISCONNECT` |
| `container_restart` | `E_CONTAINER_NOT_READY` |
| `env_not_ready` | `E_RESOURCE_TEMP` |
| `agent_infeasible` | `E_AGENT_INFEASIBLE` |
| `bad_input` | `E_BAD_INPUT` |
| `program_error` | — |

两套枚举职责分离，`error_code` 驱动重试逻辑，`incomplete_reason` 仅供人工排查，不混用。

---

## 8. 关键时序：租约超时回收
```
Worker              SQLite            Scheduler
  |                    |                  |
  |──── cas_lease ────>|                  |
  |                    |                  |
  | [容器崩/ADB断/进程挂，心跳停止]          |
  |                    |                  |
  |                    |<──── tick() ─────|
  |                    |  lease_expires_at < now()
  |                    |<──── BEGIN TX ───|
  |                    |  write_attempt(E_LEASE_TIMEOUT)
  |                    |  retry_decision → queued/failed
  |                    |  instances → unhealthy
  |                    |──── COMMIT ─────>|
  |                    |                  |
  | [Worker 意外恢复]   |                  |
  |──── cas_lease ────>|                  |
  |     rowcount=0     |                  |
  |   reclaimed.set()  |                  |
  | 主线程检查 reclaimed |                  |
  |── 终止执行，不写结果  |                  |
```

---

## 9. 任务切分脚本
```python
# scripts/split_tasks.py
# 用法：python split_tasks.py --input tasks/all_tasks.yaml --parts 3
# 输出：tasks/split/host_0.yaml, host_1.yaml, host_2.yaml

def validate_no_duplicates(task_list: list) -> None:
    names = [t['task_name'] for t in task_list]
    dupes = [n for n, c in Counter(names).items() if c > 1]
    if dupes:
        raise ValueError(f"同一 run 内 task_name 重复: {dupes}")

def split(task_list: list, n: int) -> list[list]:
    validate_no_duplicates(task_list)
    size = math.ceil(len(task_list) / n)
    return [task_list[i*size:(i+1)*size] for i in range(n)]
```

---

## 10. 版本冻结文件示例
```yaml
# release_manifest.yaml
orchestrator_version: 0.1.0
android_world_commit: c8fb7fc0
agent_version: t3a-gpt4
redroid_image_tag: redroid:12.0.0-latest
config_version: config-20260303
report_schema_version: v2
metric_policy_version: policy-20260303
```