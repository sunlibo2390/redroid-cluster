# redroid-cluster 统一手册（状态 + 脚本 + 操作）

本文档是当前项目的单一入口：
- 当前进展与可交付范围
- 项目文件结构与职责
- 脚本清单（用途/参数/输出）
- 端到端操作流程（含实时可视化监控手册）
- 常见问题与排障

## 1. 当前状态（截至 2026-03-04）

- M0：完成（环境预检、容量探针脚本具备）
- M1：基本完成（非沙盘已验证可拉起 20 个 redroid）
- M2：基本完成（Android World adb-only 已跑通 `ContactsAddContact`，并落地完整步骤数据）
- M3+：里程碑文档存在，尚未形成稳定自动化验收闭环

当前建议口径：
- `M1 = 完成（有已知风险：偶发单设备 ADB offline）`
- `M2 = 完成`

## 2. 项目结构与职责

### 2.1 你现在主要会用到的目录

- `scripts/`：运维与执行脚本（集群、检查、对比、可视化启动）
- `tools/`：运行时工具（实时监控、pkl 导出工具）
- `docs/`：文档（本文件、里程碑、设计说明）
- `orchestrator/`：M2/M4 相关的 worker 与结果落盘
- `third_party/android_world/`：Android World 主体代码与 `run.py`
- `config/`：默认实例配置、容量探针参数
- `runs/`：运行产物目录（logs/reports/results/live_dashboard）

### 2.2 核心文件（主路径）

- 集群主入口：`scripts/redroid-cluster.sh`
- Android World 执行入口：`third_party/android_world/run.py`
- 实时监控：`tools/live_dashboard.py`
- 实时监控启动：`scripts/start_live_dashboard.sh`
- PKL 导出可视化：`tools/pkl_viewer_export.py`
- 总览文档（本文件）：`docs/project_status_and_scripts.md`

### 2.3 辅助文件

- `tools/pkl_viewer_export.py`：把 pkl 导出成单 HTML 查看器（当前主路径）
- `docs/live_dashboard_deploy.md`：实时监控部署手册

## 3. 已支持业务能力

### 3.1 redroid 多实例生命周期管理

能力：拉起、销毁、状态、smoke；支持代理检测与 binder 保护。

主命令：
```bash
bash scripts/redroid-cluster.sh up [count] [adb_base_port] [image]
bash scripts/redroid-cluster.sh status
bash scripts/redroid-cluster.sh smoke [count] [adb_base_port]
bash scripts/redroid-cluster.sh down
```

### 3.2 Android World adb-only 任务执行

能力：在 redroid 设备上跑 Android World 任务，不依赖 emulator gRPC。

典型命令：
```bash
source /root/miniconda3/bin/activate /root/miniconda3/envs/android_world
python third_party/android_world/run.py \
  --adb_only=true \
  --adb_serial=127.0.0.1:15500 \
  --adb_only_agent=m3a \
  --tasks=ContactsAddContact \
  --n_task_combinations=1 \
  --record_steps=true \
  --output_path=runs/results
```

产物：
- `runs/results/adb_only_run_<ts>.pkl`
- `runs/results/adb_only_steps_<ts>/<task-combo>/steps.jsonl`
- `step_*_before_raw.png / step_*_before_som.png / step_*_after_som.png`

说明：
- 已改为 `after_som.png` 保存 after 原图（无 bbox 覆盖）。

### 3.3 实时可视化监控 + 网页交互控机

能力：
- 默认监控全部在线设备
- 默认布局 `2x4`（每页 8 台）
- 分页、焦点设备筛选
- 交互模式：tap / swipe / Back / Home / Recents / 输入文本

脚本与文档：
- 部署脚本：`scripts/deploy_live_dashboard.sh`
- 启动脚本：`scripts/start_live_dashboard.sh`
- 主程序：`tools/live_dashboard.py`
- 部署文档：`docs/live_dashboard_deploy.md`

## 4. 脚本清单（用途、输入、输出）

### 4.1 集群与基础运维

1. `scripts/redroid-cluster.sh`
- 用途：统一入口（up/down/status/smoke）
- 关键环境变量：
  - `DOCKER_PROXY_URL`（默认 `http://127.0.0.1:10090`）
  - `AUTO_CONFIG_DOCKER_PROXY=1|0`
  - `ALLOW_NO_BINDER=1`（仅排障临时使用）

2. `scripts/up.sh` / `scripts/down.sh` / `scripts/status.sh`
- 用途：按 `config/instances.yaml` 启停/查看 `redroid-*`

3. `scripts/ensure_docker.sh`
- 用途：在非 systemd/嵌套环境尽力拉起 dockerd

4. `scripts/configure_docker_proxy.sh`
- 用途：配置 daemon 代理（systemd 分支）并生成 `scripts/proxy-env.sh`

### 4.2 检查与验收

1. `scripts/precheck.sh`
- 用途：M0 预检（命令、docker、磁盘、端口）
- 输出：`runs/reports/precheck-<ts>.txt`

2. `scripts/m1-host-check.sh`
- 用途：M1 主机前置检查（docker/adb/binder/kvm）

3. `scripts/smoke.sh`
- 用途：单实例连通性检查与截图
- 输出：`runs/logs/m1-smoke-<ts>/...`

4. `scripts/m1_m2_gate.sh`
- 用途：聚合 M1/M2 gate
- 输出：`runs/reports/m1-m2-gate-<ts>.json`

### 4.3 M2 / Android World 相关

1. `scripts/m2_androidworld_probe.sh`
- 用途：`minimal_task_runner.py` 探测（探针性质）

2. `scripts/m2_adb_only_e2e.sh`
- 用途：执行 `python3 -m orchestrator.worker --adb-only-e2e`

3. `orchestrator/worker.py`
- 用途：M2/M4 的任务执行与结果落盘逻辑
- 自测：`python3 -m orchestrator.worker --self-test`

### 4.4 IO/运行时对比与收敛

1. `scripts/compare_data_root_io.sh`
- 用途：比较不同 docker data-root 下 20 实例行为
- 注意：会重启 dockerd，具破坏性
- 输出：`runs/results/io-compare-<ts>/...`

2. `scripts/force_converge_docker.sh`
- 用途：强制清理并收敛 docker 运行时（dockerd/containerd/shim）

3. `scripts/capacity_probe.sh`
- 用途：M0 容量探针（当前主机层）

### 4.5 可视化

1. `scripts/start_live_dashboard.sh`
- 用途：启动实时监控网页
- 输出：
  - pid：`runs/live_dashboard/live_dashboard.pid`
  - log：`runs/logs/live_dashboard.log`

2. `scripts/deploy_live_dashboard.sh`
- 用途：部署并启动实时监控（自动检查/安装 Pillow、启动并验证监听）

3. `tools/pkl_viewer_export.py`
- 用途：把 `adb_only_run_*.pkl` 导出为单 HTML，可直接浏览器查看

## 5. 标准操作流程（推荐）

### 5.1 第一次在新机器上

1. 环境预检
```bash
bash scripts/precheck.sh
bash scripts/m1-host-check.sh
```

2. 拉起实例（示例 20 台）
```bash
bash scripts/redroid-cluster.sh up 20 15500 redroid/redroid:12.0.0-latest
bash scripts/redroid-cluster.sh status
```

3. 基础 smoke
```bash
bash scripts/redroid-cluster.sh smoke 1 15500
```

### 5.2 跑一个 Android World 任务（M2）

```bash
source /root/miniconda3/bin/activate /root/miniconda3/envs/android_world
python third_party/android_world/run.py \
  --adb_only=true \
  --adb_serial=127.0.0.1:15500 \
  --adb_only_agent=m3a \
  --tasks=ContactsAddContact \
  --n_task_combinations=1 \
  --record_steps=true \
  --output_path=runs/results
```

### 5.3 查看结果（HTML 导出）

1. 导出 HTML
```bash
cd /remote-home1/lbsun/redroid-cluster
source /root/miniconda3/bin/activate /root/miniconda3/envs/android_world
python tools/pkl_viewer_export.py runs/results/adb_only_run_<ts>.pkl --out runs/results/pkl_viewer_<ts>.html
```

2. 远程浏览（任选其一）
- 方式 A：端口转发后本地打开
```bash
cd /remote-home1/lbsun/redroid-cluster/runs/results
python3 -m http.server 18890
```

本地隧道：
```bash
ssh -N -L 18890:127.0.0.1:18890 -p 21114 root@10.176.50.205
```

本地打开：`http://127.0.0.1:18890/pkl_viewer_<ts>.html`

- 方式 B：直接下载 HTML 到本地后打开

## 6. 实时可视化监控操作手册（完整）

### 6.1 启动

```bash
cd /remote-home1/lbsun/redroid-cluster
bash scripts/deploy_live_dashboard.sh
```

自定义参数：
```bash
HOST=0.0.0.0 PORT=18080 ROWS=2 COLS=4 CAPTURE_INTERVAL=2.0 bash scripts/start_live_dashboard.sh
```

### 6.2 远程访问（headless 推荐）

1. 在本地机器建立隧道（如果 `18080` 被占用可改 `28080`）
```bash
ssh -N -L 28080:127.0.0.1:18080 -p 21114 root@10.176.50.205
```

2. 本地浏览器打开
- `http://127.0.0.1:28080`

### 6.3 页面操作

- 默认：`Monitor all devices`，监控所有设备
- 布局：`Rows/Cols` 后点 `Apply Layout`
- 分页：`Prev/Next`
- 聚焦：取消 `Monitor all devices`，勾选设备后点 `Apply Focus`
- 交互控机：勾选 `Interactive mode`
  - 点击：tap
  - 拖拽：swipe
  - `Back/Home/Recents`：按键
  - `Send Text`：输入文本
- `Active`：指定按键/文本发送目标设备

### 6.4 状态与停止

查看日志：
```bash
tail -f runs/logs/live_dashboard.log
```

停止：
```bash
kill "$(cat runs/live_dashboard/live_dashboard.pid)"
```

### 6.5 常见故障

1. 启动成功但端口未监听
- 先看日志，典型是缺 `Pillow`
```bash
python3 -m pip install --user pillow
```

2. 页面设备少于容器数
- 先看 `adb devices -l`，dashboard 只显示 adb 在线设备

3. 交互无效
- 确认 `Interactive mode` 已开启
- 确认点击在设备图像区域，不是元信息条区域

## 7. 常见问题与排障顺序

1. `adb offline`（最常见）
- 现象：容器 `Up`，但某端口 offline/缺失
- 快速处理：
```bash
adb connect 127.0.0.1:<port>
# 不行再重启单容器
docker restart redroid-<idx>
```

2. binder 映射错误
- 现象：容器启动但 ADB 异常、任务不稳定
- 核心检查：`/dev/binder` 不应是 kvm（`10,232`）
- 正确透传：`/dev/binderfs/binder`、`/dev/binderfs/hwbinder`、`/dev/binderfs/vndbinder`

3. docker daemon 代理缺失
- 现象：拉镜像卡住/失败
- 处理：`redroid-cluster.sh` 已带自动检测；必要时宿主侧固定配置

4. 慢盘导致启动慢
- 用 `scripts/compare_data_root_io.sh` 对比后再选 data-root

## 8. 关键产物目录约定

- 报告：`runs/reports/`
- 运行日志：`runs/logs/`
- 任务结果：`runs/results/adb_only_run_*.pkl`
- 步骤目录：`runs/results/adb_only_steps_*`
- dashboard 运行态：`runs/live_dashboard/`
- IO 对比：`runs/results/io-compare-*`

## 9. 补充说明

- 本文档覆盖了项目主路径文件与当前实际使用路径。
- `third_party/android_world/` 下大量上游文件为原始仓库内容，未逐文件改写；当前只围绕本项目用到的入口（`run.py`/`minimal_task_runner.py`/任务元数据）进行接入与适配。
