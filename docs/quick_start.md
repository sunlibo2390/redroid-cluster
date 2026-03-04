# Quick Start（1页版）

## 0) 进入目录
```bash
cd /remote-home1/lbsun/redroid-cluster
```

## 1) 拉起 20 个 redroid
```bash
bash scripts/redroid-cluster.sh up 20 15500 redroid/redroid:12.0.0-latest
bash scripts/redroid-cluster.sh status
```

## 2) 基础连通性检查
```bash
bash scripts/redroid-cluster.sh smoke 1 15500
adb devices -l
```

## 3) 跑 Android World 任务（adb-only）
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
- `runs/results/adb_only_run_*.pkl`
- `runs/results/adb_only_steps_*/.../steps.jsonl`

## 4) 导出 pkl 可视化 HTML
```bash
source /root/miniconda3/bin/activate /root/miniconda3/envs/android_world
python tools/pkl_viewer_export.py runs/results/adb_only_run_<ts>.pkl --out runs/results/pkl_viewer_<ts>.html
```

远程临时静态服务：
```bash
cd /remote-home1/lbsun/redroid-cluster/runs/results
python3 -m http.server 18890
```

本地隧道：
```bash
ssh -N -L 18890:127.0.0.1:18890 -p 21114 root@10.176.50.205
```

本地浏览器打开：`http://127.0.0.1:18890/pkl_viewer_<ts>.html`。

## 5) 启动实时监控
```bash
bash scripts/deploy_live_dashboard.sh
```

本地隧道（示例）：
```bash
ssh -N -L 28080:127.0.0.1:18080 -p 21114 root@10.176.50.205
```

本地浏览器打开：`http://127.0.0.1:28080`。

## 6) 常见故障快速处理

1. 某实例 ADB 离线
```bash
adb connect 127.0.0.1:<port>
docker restart redroid-<idx>
```

2. 停止全部 redroid
```bash
bash scripts/redroid-cluster.sh down
```

3. 看 dashboard 日志
```bash
tail -f runs/logs/live_dashboard.log
```
