# Live Dashboard 部署与使用手册

## 1. 目标

在远程 headless Linux 上部署多设备实时监控网页，支持：
- 监控全部设备（默认）
- 分页网格查看（默认 `2x4`）
- 聚焦指定设备
- 网页交互控机（tap/swipe/back/home/recents/text）

## 2. 启动方式

### 2.1 推荐：一键部署启动

```bash
cd /remote-home1/lbsun/redroid-cluster
bash scripts/deploy_live_dashboard.sh
```

该脚本会执行：
- 检查/安装 `Pillow`
- 启动 dashboard
- 校验监听端口

默认参数：
- `HOST=0.0.0.0`
- `PORT=18080`
- `ROWS=2`
- `COLS=4`
- `CAPTURE_INTERVAL=2.0`

自定义示例：
```bash
HOST=0.0.0.0 PORT=18080 ROWS=2 COLS=4 CAPTURE_INTERVAL=1.5 bash scripts/deploy_live_dashboard.sh
```

### 2.2 手动启动（需要时）

```bash
bash scripts/start_live_dashboard.sh
```

如果提示缺少 `Pillow`：
```bash
python3 -m pip install --user pillow
```

## 3. 远程访问（本地浏览器）

如果服务器是 headless，建议端口转发。

本地机器执行（示例本地用 `28080`）：
```bash
ssh -N -L 28080:127.0.0.1:18080 -p <ssh_port> <user>@<server>
```

本地浏览器打开：
```text
http://127.0.0.1:28080
```

## 4. 页面操作

- 默认：`Monitor all devices`
- 调布局：`Rows/Cols` + `Apply Layout`
- 翻页：`Prev/Next`
- 聚焦：取消 `Monitor all devices`，勾选设备后 `Apply Focus`
- 交互：勾选 `Interactive mode`
  - 点击=Tap
  - 拖拽=Swipe
  - `Back/Home/Recents` 按钮
  - `Send Text` 文本输入
- `Active` 下拉框用于指定按键/文本目标设备；点击网格控机后会自动切换

## 5. 运行状态与停止

查看日志：
```bash
tail -f /remote-home1/lbsun/redroid-cluster/runs/logs/live_dashboard.log
```

查看监听：
```bash
ss -lntp | grep 18080
```

停止：
```bash
kill "$(cat /remote-home1/lbsun/redroid-cluster/runs/live_dashboard/live_dashboard.pid)"
```

## 6. 常见问题

1. 启动后看不到监听端口
- 看日志：
```bash
tail -n 120 /remote-home1/lbsun/redroid-cluster/runs/logs/live_dashboard.log
```
- 常见原因：缺 `Pillow` 或进程秒退。

2. 页面设备数量少于容器数量
- Dashboard 依据 `adb devices -l` 的在线设备渲染，不是 `docker ps`。
- 先排查离线端口：
```bash
adb devices -l
```

3. 页面有图但操作无效
- 检查是否勾选 `Interactive mode`
- 检查设备是否 `state=device`
- 检查点击是否落在设备图像区域（不是顶部元信息条）

4. 页面刷新慢
- 降低每页设备数（例如 `2x3`）
- 增大 `Refresh(ms)` 或 `CAPTURE_INTERVAL`
