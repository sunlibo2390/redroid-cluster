#!/usr/bin/env python3
import argparse
import io
import json
import math
import os
import re
import subprocess
import threading
import time
from dataclasses import dataclass, asdict
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Dict, List, Optional
from urllib.parse import parse_qs, urlparse

from PIL import Image, ImageDraw, ImageOps

PNG_SIG = b"\x89PNG\r\n\x1a\n"
CELL_W = 360
CELL_H = 640
HEADER_H = 48
META_STRIP_H = 46


@dataclass
class DeviceInfo:
    serial: str
    adb_state: str = "unknown"
    last_capture_ts: float = 0.0
    error: str = ""
    image_path: str = ""
    width: int = 0
    height: int = 0


class DashboardState:
    def __init__(self, rows: int, cols: int, data_dir: Path):
        self._lock = threading.Lock()
        self.rows = rows
        self.cols = cols
        self.focus_serials: Optional[List[str]] = None
        self.active_serial: Optional[str] = None
        self.last_action: str = ""
        self.all_serials: List[str] = []
        self.devices: Dict[str, DeviceInfo] = {}
        self.data_dir = data_dir

    def snapshot(self):
        with self._lock:
            return {
                "rows": self.rows,
                "cols": self.cols,
                "focus_serials": self.focus_serials,
                "active_serial": self.active_serial,
                "last_action": self.last_action,
                "all_serials": list(self.all_serials),
                "devices": {k: asdict(v) for k, v in self.devices.items()},
            }

    def set_layout(self, rows: int, cols: int):
        with self._lock:
            self.rows = max(1, rows)
            self.cols = max(1, cols)

    def set_focus(self, focus_serials: Optional[List[str]]):
        with self._lock:
            self.focus_serials = focus_serials if focus_serials else None

    def set_active_serial(self, serial: Optional[str]):
        with self._lock:
            self.active_serial = serial

    def set_last_action(self, message: str):
        with self._lock:
            self.last_action = message

    def set_all_serials(self, serials: List[str]):
        with self._lock:
            self.all_serials = serials
            for s in serials:
                if s not in self.devices:
                    self.devices[s] = DeviceInfo(serial=s)
            if self.active_serial and self.active_serial not in self.all_serials:
                self.active_serial = None

    def update_device(self, serial: str, **kwargs):
        with self._lock:
            if serial not in self.devices:
                self.devices[serial] = DeviceInfo(serial=serial)
            d = self.devices[serial]
            for k, v in kwargs.items():
                setattr(d, k, v)


def current_serials_for_grid_from_snapshot(snap: dict) -> List[str]:
    all_serials = list(snap.get("all_serials", []))
    focus = snap.get("focus_serials")
    if focus:
        focus_set = set(focus)
        return [s for s in all_serials if s in focus_set]
    return all_serials


def safe_name(serial: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_.-]", "_", serial)


def run(cmd: List[str], timeout: int = 20):
    return subprocess.run(cmd, capture_output=True, text=False, timeout=timeout, check=False)


def discover_serials(adb_bin: str) -> List[str]:
    p = run([adb_bin, "devices", "-l"], timeout=10)
    if p.returncode != 0:
        return []
    lines = (p.stdout or b"").decode("utf-8", errors="ignore").splitlines()
    serials = []
    for ln in lines[1:]:
        ln = ln.strip()
        if not ln:
            continue
        if ln.startswith("*"):
            continue
        parts = ln.split()
        if not parts:
            continue
        serial = parts[0]
        if serial == "List":
            continue
        serials.append(serial)
    return sorted(set(serials))


def adb_get_state(adb_bin: str, serial: str) -> str:
    p = run([adb_bin, "-s", serial, "get-state"], timeout=8)
    if p.returncode != 0:
        return "offline"
    out = (p.stdout or b"").decode("utf-8", errors="ignore").strip()
    return out or "unknown"


def adb_capture_png(adb_bin: str, serial: str) -> Optional[bytes]:
    p = run([adb_bin, "-s", serial, "exec-out", "screencap", "-p"], timeout=20)
    if p.returncode != 0:
        return None
    data = p.stdout or b""
    if not data.startswith(PNG_SIG):
        return None
    return data


def adb_shell(adb_bin: str, serial: str, args: List[str], timeout: int = 15):
    return run([adb_bin, "-s", serial, "shell"] + args, timeout=timeout)


def adb_input_tap(adb_bin: str, serial: str, x: int, y: int) -> bool:
    p = adb_shell(adb_bin, serial, ["input", "tap", str(max(0, x)), str(max(0, y))], timeout=10)
    return p.returncode == 0


def adb_input_swipe(adb_bin: str, serial: str, x1: int, y1: int, x2: int, y2: int, duration_ms: int) -> bool:
    p = adb_shell(
        adb_bin,
        serial,
        [
            "input",
            "swipe",
            str(max(0, x1)),
            str(max(0, y1)),
            str(max(0, x2)),
            str(max(0, y2)),
            str(max(1, duration_ms)),
        ],
        timeout=12,
    )
    return p.returncode == 0


def adb_input_keyevent(adb_bin: str, serial: str, key: str) -> bool:
    p = adb_shell(adb_bin, serial, ["input", "keyevent", key], timeout=10)
    return p.returncode == 0


def adb_input_text(adb_bin: str, serial: str, text: str) -> bool:
    # adb input text uses %s for spaces.
    safe = text.replace(" ", "%s")
    p = adb_shell(adb_bin, serial, ["input", "text", safe], timeout=15)
    return p.returncode == 0


def capture_loop(state: DashboardState, adb_bin: str, interval_sec: float):
    devices_dir = state.data_dir / "devices"
    devices_dir.mkdir(parents=True, exist_ok=True)

    while True:
        try:
            serials = discover_serials(adb_bin)
            state.set_all_serials(serials)
            now = time.time()

            for serial in serials:
                st = adb_get_state(adb_bin, serial)
                state.update_device(serial, adb_state=st)
                if st != "device":
                    state.update_device(serial, error=f"state={st}")
                    continue

                png = adb_capture_png(adb_bin, serial)
                if not png:
                    state.update_device(serial, error="capture_failed")
                    continue

                img_path = devices_dir / f"{safe_name(serial)}.png"
                tmp_path = img_path.with_suffix(".png.tmp")
                tmp_path.write_bytes(png)
                os.replace(tmp_path, img_path)

                try:
                    with Image.open(io.BytesIO(png)) as im:
                        w, h = im.size
                except Exception:
                    w, h = 0, 0

                state.update_device(
                    serial,
                    last_capture_ts=now,
                    error="",
                    image_path=str(img_path),
                    width=w,
                    height=h,
                )
        except Exception:
            # keep loop alive
            pass

        time.sleep(max(0.2, interval_sec))


def compose_grid(state: DashboardState, page: int) -> bytes:
    snap = state.snapshot()
    rows = max(1, int(snap["rows"]))
    cols = max(1, int(snap["cols"]))
    per_page = rows * cols

    serials = current_serials_for_grid_from_snapshot(snap)
    total = len(serials)
    pages = max(1, math.ceil(total / per_page))
    page = max(1, min(page, pages))

    start = (page - 1) * per_page
    page_serials = serials[start : start + per_page]

    canvas = Image.new("RGB", (cols * CELL_W, rows * CELL_H + HEADER_H), (18, 20, 28))
    draw = ImageDraw.Draw(canvas)
    title = f"Live Dashboard  devices={total}  page={page}/{pages}  layout={rows}x{cols}"
    draw.rectangle([(0, 0), (canvas.width, HEADER_H)], fill=(35, 39, 52))
    draw.text((10, 14), title, fill=(230, 235, 245))

    for idx in range(rows * cols):
        r = idx // cols
        c = idx % cols
        x0 = c * CELL_W
        y0 = HEADER_H + r * CELL_H
        x1 = x0 + CELL_W - 1
        y1 = y0 + CELL_H - 1
        draw.rectangle([(x0, y0), (x1, y1)], outline=(70, 80, 100), width=1)

        if idx >= len(page_serials):
            continue

        serial = page_serials[idx]
        dev = snap["devices"].get(serial, {})
        state_text = dev.get("adb_state", "unknown")
        err_text = dev.get("error", "")
        img_path = dev.get("image_path", "")

        if img_path and os.path.exists(img_path):
            try:
                with Image.open(img_path) as im:
                    im = im.convert("RGB")
                    # Reserve top metadata strip inside each cell.
                    box = (x0 + 4, y0 + META_STRIP_H, x1 - 4, y1 - 4)
                    fitted = ImageOps.contain(im, (box[2] - box[0], box[3] - box[1]))
                    px = box[0] + ((box[2] - box[0]) - fitted.width) // 2
                    py = box[1] + ((box[3] - box[1]) - fitted.height) // 2
                    canvas.paste(fitted, (px, py))
            except Exception:
                pass

        bar_fill = (28, 31, 42)
        if serial == snap.get("active_serial"):
            bar_fill = (36, 52, 40)
        draw.rectangle([(x0 + 2, y0 + 2), (x1 - 2, y0 + 42)], fill=bar_fill)
        draw.text((x0 + 8, y0 + 8), serial, fill=(220, 230, 245))
        draw.text((x0 + 8, y0 + 24), f"state={state_text}", fill=(150, 220, 170) if state_text == "device" else (240, 140, 120))
        if err_text:
            draw.text((x0 + 150, y0 + 24), err_text[:24], fill=(240, 140, 120))

    out = io.BytesIO()
    canvas.save(out, format="JPEG", quality=85)
    return out.getvalue()


def map_grid_point_to_device(state: DashboardState, page: int, nx: float, ny: float):
    snap = state.snapshot()
    rows = max(1, int(snap["rows"]))
    cols = max(1, int(snap["cols"]))
    per_page = rows * cols
    serials = current_serials_for_grid_from_snapshot(snap)
    total = len(serials)
    pages = max(1, math.ceil(max(1, total) / per_page))
    page = max(1, min(page, pages))
    start = (page - 1) * per_page
    page_serials = serials[start : start + per_page]

    try:
        nx = float(nx)
    except Exception:
        nx = 0.0
    try:
        ny = float(ny)
    except Exception:
        ny = 0.0
    nx = min(0.999999, max(0.0, nx))
    ny = min(0.999999, max(0.0, ny))
    canvas_w = cols * CELL_W
    canvas_h = rows * CELL_H + HEADER_H
    cx = int(nx * canvas_w)
    cy = int(ny * canvas_h)

    if cy < HEADER_H:
        return None, "click_on_header"

    row = (cy - HEADER_H) // CELL_H
    col = cx // CELL_W
    if row < 0 or row >= rows or col < 0 or col >= cols:
        return None, "out_of_grid"
    idx = row * cols + col
    if idx >= len(page_serials):
        return None, "empty_cell"

    serial = page_serials[idx]
    dev = snap["devices"].get(serial, {})
    img_path = str(dev.get("image_path", ""))
    if not img_path or not os.path.exists(img_path):
        return None, "image_not_ready"

    x0 = col * CELL_W
    y0 = HEADER_H + row * CELL_H
    x1 = x0 + CELL_W - 1
    y1 = y0 + CELL_H - 1
    box = (x0 + 4, y0 + META_STRIP_H, x1 - 4, y1 - 4)
    box_w = max(1, box[2] - box[0])
    box_h = max(1, box[3] - box[1])

    try:
        with Image.open(img_path) as im:
            im = im.convert("RGB")
            fitted = ImageOps.contain(im, (box_w, box_h))
            rs_w, rs_h = fitted.size
            src_w, src_h = im.size
    except Exception:
        return None, "image_open_failed"

    px = box[0] + (box_w - rs_w) // 2
    py = box[1] + (box_h - rs_h) // 2
    if cx < px or cx >= px + rs_w or cy < py or cy >= py + rs_h:
        return None, "outside_device_screen"

    dev_x = int((cx - px) * src_w / max(1, rs_w))
    dev_y = int((cy - py) * src_h / max(1, rs_h))
    dev_x = min(max(0, dev_x), max(0, src_w - 1))
    dev_y = min(max(0, dev_y), max(0, src_h - 1))
    return {"serial": serial, "x": dev_x, "y": dev_y}, ""


INDEX_HTML = """<!doctype html>
<html lang="zh">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Live Dashboard</title>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link href="https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;600;700&family=Syne:wght@400;700;800&display=swap" rel="stylesheet">
  <style>
    *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }

    :root {
      --bg:       #080b10;
      --surface:  #0d1117;
      --panel:    #111720;
      --border:   #1e2a3a;
      --accent:   #00e5ff;
      --accent2:  #7b61ff;
      --green:    #00ff8c;
      --red:      #ff4d6a;
      --amber:    #ffb740;
      --text:     #c9d8ee;
      --muted:    #4a6080;
      --glow:     rgba(0,229,255,0.15);
    }

    html, body { height: 100%; overflow: hidden; }

    body {
      font-family: 'JetBrains Mono', monospace;
      background: var(--bg);
      color: var(--text);
      display: flex;
      flex-direction: column;
      height: 100vh;
    }

    /* ── SCANLINE OVERLAY ─────────────────────────── */
    body::before {
      content: '';
      position: fixed; inset: 0;
      background: repeating-linear-gradient(
        0deg,
        transparent,
        transparent 2px,
        rgba(0,0,0,0.03) 2px,
        rgba(0,0,0,0.03) 4px
      );
      pointer-events: none;
      z-index: 9999;
    }

    /* ── HEADER BAR ───────────────────────────────── */
    #topbar {
      flex-shrink: 0;
      display: flex;
      align-items: center;
      gap: 0;
      background: var(--surface);
      border-bottom: 1px solid var(--border);
      height: 48px;
      padding: 0 16px;
      position: relative;
    }

    .logo {
      font-family: 'Syne', sans-serif;
      font-weight: 800;
      font-size: 15px;
      letter-spacing: 0.15em;
      color: var(--accent);
      text-transform: uppercase;
      display: flex;
      align-items: center;
      gap: 8px;
      white-space: nowrap;
    }
    .logo-dot {
      width: 8px; height: 8px;
      background: var(--green);
      border-radius: 50%;
      box-shadow: 0 0 8px var(--green);
      animation: pulse 2s infinite;
    }
    @keyframes pulse {
      0%,100% { opacity:1; box-shadow: 0 0 8px var(--green); }
      50%      { opacity:.5; box-shadow: 0 0 2px var(--green); }
    }

    .divider { width:1px; height:28px; background:var(--border); margin: 0 16px; flex-shrink:0; }

    /* ── STAT CHIPS ───────────────────────────────── */
    .stat-group { display: flex; align-items: center; gap: 8px; }
    .stat {
      display: flex; align-items: center; gap: 6px;
      font-size: 11px; color: var(--muted);
      background: var(--panel);
      border: 1px solid var(--border);
      border-radius: 4px;
      padding: 3px 8px;
    }
    .stat b { color: var(--text); font-weight: 600; }
    .stat.ok b  { color: var(--green); }
    .stat.info b { color: var(--accent); }

    /* ── STATUS TICKER ────────────────────────────── */
    #ticker {
      flex: 1;
      margin: 0 16px;
      font-size: 11px;
      color: var(--accent);
      overflow: hidden;
      white-space: nowrap;
      text-overflow: ellipsis;
      opacity: 0.8;
    }
    #ticker.flash { animation: tickflash .3s ease; }
    @keyframes tickflash {
      0%   { color: var(--green); opacity: 1; }
      100% { color: var(--accent); opacity: .8; }
    }

    /* ── CONTROL STRIP ────────────────────────────── */
    #controls {
      flex-shrink: 0;
      display: flex;
      align-items: stretch;
      gap: 0;
      background: var(--surface);
      border-bottom: 1px solid var(--border);
      height: 44px;
      overflow: hidden;
    }

    .ctrl-group {
      display: flex;
      align-items: center;
      gap: 6px;
      padding: 0 14px;
      border-right: 1px solid var(--border);
    }
    .ctrl-label {
      font-size: 10px;
      color: var(--muted);
      text-transform: uppercase;
      letter-spacing: .08em;
      white-space: nowrap;
    }

    input[type=number], input[type=text], select {
      background: var(--bg);
      color: var(--text);
      border: 1px solid var(--border);
      border-radius: 4px;
      padding: 4px 8px;
      font-family: inherit;
      font-size: 12px;
      outline: none;
      transition: border-color .15s;
    }
    input[type=number]:focus, input[type=text]:focus, select:focus {
      border-color: var(--accent);
    }
    input[type=number] { width: 54px; }

    button {
      background: var(--panel);
      color: var(--text);
      border: 1px solid var(--border);
      border-radius: 4px;
      padding: 5px 10px;
      font-family: inherit;
      font-size: 11px;
      cursor: pointer;
      transition: all .12s;
      white-space: nowrap;
    }
    button:hover {
      background: var(--border);
      border-color: var(--accent);
      color: var(--accent);
    }
    button:active { transform: scale(.97); }

    button.primary {
      background: rgba(0,229,255,.1);
      border-color: var(--accent);
      color: var(--accent);
    }
    button.primary:hover {
      background: rgba(0,229,255,.2);
    }

    .page-nav { display: flex; align-items: center; gap: 6px; }
    #pageLabel {
      font-size: 11px; color: var(--muted);
      min-width: 60px; text-align: center;
    }

    /* ── TOGGLE SWITCH ────────────────────────────── */
    .toggle-wrap { display: flex; align-items: center; gap: 8px; cursor: pointer; }
    .toggle-wrap .ctrl-label { cursor: pointer; }
    .toggle {
      position: relative; width: 32px; height: 17px;
    }
    .toggle input { display: none; }
    .toggle-slider {
      position: absolute; inset: 0;
      background: var(--panel);
      border: 1px solid var(--border);
      border-radius: 17px;
      transition: .2s;
    }
    .toggle-slider::before {
      content: '';
      position: absolute;
      width: 11px; height: 11px;
      left: 2px; top: 2px;
      background: var(--muted);
      border-radius: 50%;
      transition: .2s;
    }
    .toggle input:checked + .toggle-slider {
      background: rgba(0,229,255,.15);
      border-color: var(--accent);
    }
    .toggle input:checked + .toggle-slider::before {
      transform: translateX(15px);
      background: var(--accent);
      box-shadow: 0 0 6px var(--accent);
    }

    /* ── DEVICE PICKER DROPDOWN ───────────────────── */
    .devices-wrap { position: relative; }
    #devicesTrigger {
      display: flex; align-items: center; gap: 6px;
      cursor: pointer;
    }
    #deviceCount {
      font-size: 11px;
      background: rgba(0,229,255,.1);
      border: 1px solid var(--accent);
      border-radius: 3px;
      padding: 1px 6px;
      color: var(--accent);
    }
    #devicesDropdown {
      display: none;
      position: absolute;
      top: calc(100% + 4px);
      left: 0;
      z-index: 100;
      background: var(--panel);
      border: 1px solid var(--border);
      border-radius: 6px;
      padding: 8px;
      min-width: 220px;
      max-height: 240px;
      overflow-y: auto;
      box-shadow: 0 8px 24px rgba(0,0,0,.6);
    }
    #devicesDropdown.open { display: block; }
    #deviceList { display: flex; flex-direction: column; gap: 4px; }
    .dev-row {
      display: flex; align-items: center; gap: 8px;
      padding: 4px 6px; border-radius: 4px;
      font-size: 11px; cursor: pointer;
    }
    .dev-row:hover { background: var(--border); }
    .dev-status {
      width: 6px; height: 6px; border-radius: 50%; flex-shrink: 0;
    }
    .dev-status.online  { background: var(--green); box-shadow: 0 0 4px var(--green); }
    .dev-status.offline { background: var(--red); }

    /* ── KEY BUTTONS ──────────────────────────────── */
    .key-btn {
      display: flex; align-items: center; gap: 4px;
      font-size: 10px;
    }
    .key-icon { font-size: 14px; line-height: 1; }

    /* ── TEXT INPUT GROUP ─────────────────────────── */
    .text-input-row { display: flex; align-items: center; gap: 4px; }
    #textInput { width: 140px; }

    /* ── MAIN GRID AREA ───────────────────────────── */
    #gridArea {
      flex: 1;
      overflow: auto;
      display: flex;
      align-items: flex-start;
      justify-content: center;
      padding: 12px;
      background: var(--bg);
      position: relative;
    }

    #gridWrap {
      position: relative;
      cursor: crosshair;
    }
    .grid-img {
      display: block;
      max-width: 100%;
      border: 1px solid var(--border);
      border-radius: 6px;
      /* no transition — swapping handles smoothness */
    }
    /* double-buffer: back img sits on top, invisible until ready */
    #gridImgBack {
      position: absolute;
      top: 0; left: 0;
      opacity: 0;
      pointer-events: none;
    }

    /* ── LOADING SPINNER ──────────────────────────── */
    #spinner {
      position: absolute; inset: 0;
      display: flex; align-items: center; justify-content: center;
      pointer-events: none;
      opacity: 0;
      transition: opacity .2s;
    }
    #spinner.show { opacity: 1; }
    .spin-ring {
      width: 28px; height: 28px;
      border: 2px solid var(--border);
      border-top-color: var(--accent);
      border-radius: 50%;
      animation: spin .7s linear infinite;
    }
    @keyframes spin { to { transform: rotate(360deg); } }

    /* ── SWIPE GHOST ──────────────────────────────── */
    #swipeGhost {
      position: absolute;
      pointer-events: none;
      display: none;
    }
    #swipeLine {
      position: absolute;
      pointer-events: none;
      display: none;
      background: var(--accent);
      height: 2px;
      transform-origin: left center;
      box-shadow: 0 0 6px var(--accent);
      opacity: .8;
    }

    /* ── TOAST ────────────────────────────────────── */
    #toast {
      position: fixed;
      bottom: 20px; left: 50%;
      transform: translateX(-50%) translateY(60px);
      background: var(--panel);
      border: 1px solid var(--border);
      border-radius: 6px;
      padding: 8px 16px;
      font-size: 12px;
      color: var(--text);
      box-shadow: 0 4px 16px rgba(0,0,0,.5);
      transition: transform .25s cubic-bezier(.34,1.56,.64,1), opacity .25s;
      opacity: 0;
      z-index: 1000;
      white-space: nowrap;
    }
    #toast.show {
      transform: translateX(-50%) translateY(0);
      opacity: 1;
    }
    #toast.ok    { border-color: var(--green); color: var(--green); }
    #toast.err   { border-color: var(--red);   color: var(--red); }
    #toast.info  { border-color: var(--accent); color: var(--accent); }

    /* ── SCROLLBAR ────────────────────────────────── */
    ::-webkit-scrollbar { width: 6px; height: 6px; }
    ::-webkit-scrollbar-track { background: var(--bg); }
    ::-webkit-scrollbar-thumb { background: var(--border); border-radius: 3px; }
    ::-webkit-scrollbar-thumb:hover { background: var(--muted); }

    select { min-width: 160px; }

    /* active serial highlight */
    #activeSerial option { background: var(--bg); }
  </style>
</head>
<body>

<!-- ── TOP BAR ──────────────────────────────────────── -->
<div id="topbar">
  <div class="logo">
    <div class="logo-dot"></div>
    ADB&nbsp;MONITOR
  </div>
  <div class="divider"></div>
  <div class="stat-group">
    <div class="stat ok"><span>DEVICES</span><b id="s-total">—</b></div>
    <div class="stat ok"><span>ONLINE</span><b id="s-online">—</b></div>
    <div class="stat info"><span>LAYOUT</span><b id="s-layout">—</b></div>
  </div>
  <div id="ticker">waiting for data…</div>
</div>

<!-- ── CONTROL STRIP ─────────────────────────────────── -->
<div id="controls">

  <!-- Layout -->
  <div class="ctrl-group">
    <span class="ctrl-label">Layout</span>
    <input id="rows" type="number" min="1" value="2" title="Rows" oninput="layoutDirty=true">
    <span style="color:var(--muted);font-size:12px">×</span>
    <input id="cols" type="number" min="1" value="4" title="Cols" oninput="layoutDirty=true">
    <button class="primary" onclick="applyLayout()">Apply</button>
  </div>

  <!-- Pagination -->
  <div class="ctrl-group">
    <span class="ctrl-label">Page</span>
    <div class="page-nav">
      <button onclick="prevPage()">&#8249;</button>
      <span id="pageLabel">1 / 1</span>
      <button onclick="nextPage()">&#8250;</button>
    </div>
  </div>

  <!-- Refresh -->
  <div class="ctrl-group">
    <span class="ctrl-label">Refresh</span>
    <input id="refreshMs" type="number" min="300" value="1500" style="width:70px">
    <span style="font-size:10px;color:var(--muted)">ms</span>
  </div>

  <!-- Devices -->
  <div class="ctrl-group devices-wrap">
    <span class="ctrl-label">Focus</span>
    <div id="devicesTrigger" onclick="toggleDevicesDropdown()">
      <label class="toggle-wrap" style="pointer-events:none">
        <span class="toggle"><input type="checkbox" id="allDevices" checked><span class="toggle-slider"></span></span>
        <span class="ctrl-label" style="color:var(--text)">All</span>
      </label>
      <span id="deviceCount">0</span>
    </div>
    <div id="devicesDropdown">
      <div id="deviceList"></div>
      <div style="margin-top:8px;border-top:1px solid var(--border);padding-top:8px">
        <button class="primary" style="width:100%" onclick="applyFocus()">Apply Focus</button>
      </div>
    </div>
  </div>

  <!-- Interactive -->
  <div class="ctrl-group">
    <label class="toggle-wrap">
      <span class="toggle"><input type="checkbox" id="interactiveMode"><span class="toggle-slider"></span></span>
      <span class="ctrl-label" style="color:var(--text)">Interactive</span>
    </label>
  </div>

  <!-- Active device -->
  <div class="ctrl-group">
    <span class="ctrl-label">Active</span>
    <select id="activeSerial"></select>
  </div>

  <!-- Keys -->
  <div class="ctrl-group">
    <button class="key-btn" onclick="sendKey('KEYCODE_BACK')" title="Back"><span class="key-icon">&#9664;</span>Back</button>
    <button class="key-btn" onclick="sendKey('KEYCODE_HOME')" title="Home"><span class="key-icon">&#9711;</span>Home</button>
    <button class="key-btn" onclick="sendKey('KEYCODE_APP_SWITCH')" title="Recents"><span class="key-icon">&#9726;</span>Recent</button>
  </div>

  <!-- Text input -->
  <div class="ctrl-group text-input-row">
    <input id="textInput" type="text" placeholder="Send text…" onkeydown="if(event.key==='Enter')sendText()">
    <button class="primary" onclick="sendText()">Send</button>
  </div>

</div>

<!-- ── GRID ───────────────────────────────────────────── -->
<div id="gridArea" onclick="closeDropdowns()">
  <div id="gridWrap">
    <!-- double-buffer: front is visible, back loads silently then swaps -->
    <img id="gridImgFront" class="grid-img" src="/grid?page=1" alt="grid" draggable="false">
    <img id="gridImgBack"  class="grid-img" src="" alt="" draggable="false">
    <div id="spinner"><div class="spin-ring"></div></div>
    <div id="swipeGhost"></div>
    <div id="swipeLine"></div>
  </div>
</div>

<!-- ── TOAST ─────────────────────────────────────────── -->
<div id="toast"></div>

<script>
let page = 1;
let totalPages = 1;
let stateCache = null;
let dragStart = null;
let toastTimer = null;
let tickTimer = null;
let layoutDirty = false;

// ── TOAST ───────────────────────────────────
function showToast(msg, type='info', ms=2200) {
  const t = document.getElementById('toast');
  t.textContent = msg;
  t.className = 'show ' + type;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { t.className = ''; }, ms);
}

// ── STATE LOAD ──────────────────────────────
async function loadState() {
  const r = await fetch('/api/state');
  const s = await r.json();
  stateCache = s;

  // Only overwrite layout inputs if user hasn't manually changed them
  if (!layoutDirty) {
    document.getElementById('rows').value = s.rows;
    document.getElementById('cols').value = s.cols;
  }

  const monitored = s.focus_serials ? s.focus_serials.length : s.all_serials.length;
  const total = s.all_serials.length;
  const per = Math.max(1, s.rows * s.cols);
  totalPages = Math.max(1, Math.ceil(Math.max(1, monitored) / per));
  if (page > totalPages) page = totalPages;

  document.getElementById('pageLabel').textContent = `${page} / ${totalPages}`;
  document.getElementById('s-total').textContent  = total;
  document.getElementById('s-layout').textContent = `${s.rows}×${s.cols}`;

  // count online
  let online = 0;
  Object.values(s.devices || {}).forEach(d => { if (d.adb_state === 'device') online++; });
  document.getElementById('s-online').textContent = online;

  // ticker
  if (s.last_action) {
    const tk = document.getElementById('ticker');
    tk.textContent = '▸ ' + s.last_action;
    tk.classList.remove('flash');
    void tk.offsetWidth;
    tk.classList.add('flash');
  }

  // device count badge
  document.getElementById('deviceCount').textContent = total;

  renderDevices(s);
}

// ── RENDER DEVICE LIST ──────────────────────
function renderDevices(s) {
  const focus = s.focus_serials || [];
  const list  = document.getElementById('deviceList');
  list.innerHTML = s.all_serials.map(serial => {
    const dev     = (s.devices || {})[serial] || {};
    const online  = dev.adb_state === 'device';
    const checked = (!s.focus_serials || focus.includes(serial)) ? 'checked' : '';
    return `<div class="dev-row">
      <input class="dev-check" type="checkbox" value="${serial}" ${checked} style="accent-color:var(--accent)">
      <div class="dev-status ${online ? 'online' : 'offline'}"></div>
      <span style="flex:1">${serial}</span>
      <span style="font-size:10px;color:${online ? 'var(--green)' : 'var(--red)'}">${dev.adb_state || '?'}</span>
    </div>`;
  }).join('');

  document.getElementById('allDevices').checked = !s.focus_serials;
  document.querySelectorAll('.dev-check').forEach(c => c.disabled = !s.focus_serials && true);
  // actually enable when allDevices unchecked
  if (!s.focus_serials) {
    document.querySelectorAll('.dev-check').forEach(c => c.disabled = false);
  }

  const sel = document.getElementById('activeSerial');
  const cur = s.active_serial || '';
  sel.innerHTML = ['<option value="">(none)</option>'].concat(
    s.all_serials.map(sr => `<option value="${sr}" ${sr === cur ? 'selected' : ''}>${sr}</option>`)
  ).join('');
}

// ── DROPDOWN ────────────────────────────────
function toggleDevicesDropdown() {
  document.getElementById('devicesDropdown').classList.toggle('open');
}
function closeDropdowns() {
  document.getElementById('devicesDropdown').classList.remove('open');
}
document.getElementById('allDevices').addEventListener('change', function() {
  document.querySelectorAll('.dev-check').forEach(c => c.disabled = false);
});

// ── GRID IMAGE ──────────────────────────────
function refreshImage() {
  const front = document.getElementById('gridImgFront');
  const back  = document.getElementById('gridImgBack');
  const url   = `/grid?page=${page}&t=${Date.now()}`;
  document.getElementById('pageLabel').textContent = `${page} / ${totalPages}`;

  back.onload = () => {
    // atomic swap: copy size so layout doesn't jump
    front.src    = back.src;
    front.width  = back.naturalWidth  || front.width;
    back.src     = '';
    back.onload  = null;
    back.onerror = null;
  };
  back.onerror = () => {
    back.src     = '';
    back.onload  = null;
    back.onerror = null;
  };
  back.src = url;
}

// ── LAYOUT ──────────────────────────────────
async function applyLayout() {
  const rows = parseInt(document.getElementById('rows').value || '2');
  const cols = parseInt(document.getElementById('cols').value || '4');
  await fetch('/api/layout', {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({rows, cols})
  });
  layoutDirty = false;
  await loadState();
  refreshImage();
  showToast(`Layout set to ${rows}×${cols}`, 'ok');
}

// ── FOCUS ────────────────────────────────────
function selectedFocus() {
  const all = document.getElementById('allDevices').checked;
  if (all) return null;
  return Array.from(document.querySelectorAll('.dev-check:checked')).map(x => x.value);
}
async function applyFocus() {
  const focus = selectedFocus();
  await fetch('/api/focus', {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({focus_serials: focus})
  });
  page = 1;
  closeDropdowns();
  await loadState();
  refreshImage();
  showToast(focus ? `Focusing ${focus.length} device(s)` : 'Monitoring all devices', 'info');
}

// ── PAGINATION ───────────────────────────────
function nextPage() { page = Math.min(totalPages, page + 1); refreshImage(); }
function prevPage() { page = Math.max(1, page - 1); refreshImage(); }
document.addEventListener('keydown', e => {
  if (e.target.tagName === 'INPUT' || e.target.tagName === 'SELECT') return;
  if (e.key === 'ArrowRight') nextPage();
  if (e.key === 'ArrowLeft')  prevPage();
});

// ── COORD MAPPING ────────────────────────────
function clamp01(v) { return Math.max(0, Math.min(0.999999, v)); }
function posOnImage(ev) {
  const img  = document.getElementById('gridImgFront');
  const rect = img.getBoundingClientRect();
  return {
    nx: clamp01((ev.clientX - rect.left)  / Math.max(1, rect.width)),
    ny: clamp01((ev.clientY - rect.top)   / Math.max(1, rect.height)),
    cx: ev.clientX, cy: ev.clientY,
    rx: ev.clientX - rect.left, ry: ev.clientY - rect.top,
  };
}

// ── SWIPE VISUAL ─────────────────────────────
function showSwipeLine(p1, p2) {
  const line = document.getElementById('swipeLine');
  const dx = p2.rx - p1.rx, dy = p2.ry - p1.ry;
  const len = Math.sqrt(dx*dx + dy*dy);
  const ang = Math.atan2(dy, dx) * 180 / Math.PI;
  line.style.cssText = `display:block;left:${p1.rx}px;top:${p1.ry}px;width:${len}px;transform:rotate(${ang}deg)`;
  setTimeout(() => { line.style.display = 'none'; }, 400);
}

// ── INPUT POST ───────────────────────────────
async function postInput(payload) {
  const r = await fetch('/api/input', {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify(payload),
  });
  const j = await r.json();
  if (!j.ok) showToast(`Error: ${j.error || 'unknown'}`, 'err');
  else showToast(`${payload.type} → ${j.serial || '?'} (${j.x ?? ''},${j.y ?? ''})`, 'ok', 1400);
  await loadState();
}

// ── KEYS / TEXT ──────────────────────────────
async function sendKey(key) {
  const serial = document.getElementById('activeSerial').value || null;
  const r = await fetch('/api/keyevent', {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({key, serial}),
  });
  const j = await r.json();
  if (j.ok) showToast(`Key: ${key}`, 'ok', 1200);
  else showToast(`Key failed: ${j.error}`, 'err');
  await loadState();
}
async function sendText() {
  const serial = document.getElementById('activeSerial').value || null;
  const text   = document.getElementById('textInput').value || '';
  if (!text) { showToast('Enter text first', 'err', 1200); return; }
  const r = await fetch('/api/text', {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({text, serial}),
  });
  const j = await r.json();
  if (j.ok) { showToast(`Sent: "${text}"`, 'ok'); document.getElementById('textInput').value = ''; }
  else showToast(`Text failed: ${j.error}`, 'err');
  await loadState();
}

// ── INTERACTIVE MOUSE ────────────────────────
(function bindInteractive() {
  const img = document.getElementById('gridImgFront');
  img.addEventListener('mousedown', ev => {
    if (!document.getElementById('interactiveMode').checked) return;
    ev.preventDefault();
    dragStart = posOnImage(ev);
  });
  img.addEventListener('mouseup', async ev => {
    if (!document.getElementById('interactiveMode').checked) return;
    if (!dragStart) return;
    const end  = posOnImage(ev);
    const dx   = end.nx - dragStart.nx;
    const dy   = end.ny - dragStart.ny;
    const dist = Math.sqrt(dx*dx + dy*dy);
    if (dist < 0.012) {
      await postInput({type:'tap', page, nx: end.nx, ny: end.ny});
    } else {
      showSwipeLine(dragStart, end);
      await postInput({type:'swipe', page,
        nx1: dragStart.nx, ny1: dragStart.ny,
        nx2: end.nx, ny2: end.ny, duration_ms: 260});
    }
    dragStart = null;
    refreshImage();
  });
  img.addEventListener('mouseleave', () => { dragStart = null; });
})();

// ── TICK LOOP ────────────────────────────────
async function tick() {
  try {
    await loadState();
    refreshImage();
  } catch(e) {}
  const ms = Math.max(300, parseInt(document.getElementById('refreshMs').value || '1500'));
  tickTimer = setTimeout(tick, ms);
}

tick();
</script>
</body>
</html>"""


class Handler(BaseHTTPRequestHandler):
    state: DashboardState = None  # type: ignore
    adb_bin: str = "adb"

    def _json(self, obj, code=200):
        b = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def _html(self, text, code=200):
        b = text.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/":
            return self._html(INDEX_HTML)
        if parsed.path == "/api/state":
            return self._json(self.state.snapshot())
        if parsed.path == "/grid":
            q = parse_qs(parsed.query)
            page = int((q.get("page") or ["1"])[0])
            jpg = compose_grid(self.state, page)
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "image/jpeg")
            self.send_header("Content-Length", str(len(jpg)))
            self.end_headers()
            self.wfile.write(jpg)
            return
        self.send_error(404)

    def do_POST(self):
        parsed = urlparse(self.path)
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length) if length > 0 else b"{}"
        try:
            data = json.loads(body.decode("utf-8"))
        except Exception:
            data = {}

        if parsed.path == "/api/focus":
            focus = data.get("focus_serials")
            if not focus:
                self.state.set_focus(None)
            else:
                self.state.set_focus([str(x) for x in focus])
            return self._json({"ok": True})

        if parsed.path == "/api/layout":
            rows = int(data.get("rows", self.state.rows))
            cols = int(data.get("cols", self.state.cols))
            self.state.set_layout(rows, cols)
            return self._json({"ok": True})

        if parsed.path == "/api/input":
            kind = str(data.get("type", "")).strip().lower()
            page = int(data.get("page", 1))
            if kind == "tap":
                mapped, err = map_grid_point_to_device(self.state, page, data.get("nx", 0.0), data.get("ny", 0.0))
                if not mapped:
                    return self._json({"ok": False, "error": err}, code=400)
                serial = mapped["serial"]
                ok = adb_input_tap(self.adb_bin, serial, int(mapped["x"]), int(mapped["y"]))
                if not ok:
                    return self._json({"ok": False, "error": "adb_tap_failed"}, code=500)
                self.state.set_active_serial(serial)
                self.state.set_last_action(f"tap serial={serial} x={mapped['x']} y={mapped['y']}")
                return self._json({"ok": True, "serial": serial, "x": mapped["x"], "y": mapped["y"]})

            if kind == "swipe":
                m1, e1 = map_grid_point_to_device(self.state, page, data.get("nx1", 0.0), data.get("ny1", 0.0))
                m2, e2 = map_grid_point_to_device(self.state, page, data.get("nx2", 0.0), data.get("ny2", 0.0))
                if not m1:
                    return self._json({"ok": False, "error": e1}, code=400)
                if not m2:
                    return self._json({"ok": False, "error": e2}, code=400)
                if m1["serial"] != m2["serial"]:
                    return self._json({"ok": False, "error": "swipe_cross_device_not_supported"}, code=400)
                duration = int(data.get("duration_ms", 260))
                serial = m1["serial"]
                ok = adb_input_swipe(
                    self.adb_bin,
                    serial,
                    int(m1["x"]),
                    int(m1["y"]),
                    int(m2["x"]),
                    int(m2["y"]),
                    max(1, duration),
                )
                if not ok:
                    return self._json({"ok": False, "error": "adb_swipe_failed"}, code=500)
                self.state.set_active_serial(serial)
                self.state.set_last_action(
                    f"swipe serial={serial} ({m1['x']},{m1['y']}) -> ({m2['x']},{m2['y']}) {duration}ms"
                )
                return self._json({"ok": True, "serial": serial})

            return self._json({"ok": False, "error": "unsupported_input_type"}, code=400)

        if parsed.path == "/api/keyevent":
            key = str(data.get("key", "")).strip()
            serial = str(data.get("serial", "")).strip()
            if not serial:
                snap = self.state.snapshot()
                serial = str(snap.get("active_serial") or "")
                if not serial:
                    serials = current_serials_for_grid_from_snapshot(snap)
                    serial = serials[0] if serials else ""
            if not serial:
                return self._json({"ok": False, "error": "no_target_serial"}, code=400)
            if not key:
                return self._json({"ok": False, "error": "missing_key"}, code=400)
            ok = adb_input_keyevent(self.adb_bin, serial, key)
            if not ok:
                return self._json({"ok": False, "error": "adb_keyevent_failed"}, code=500)
            self.state.set_active_serial(serial)
            self.state.set_last_action(f"keyevent serial={serial} key={key}")
            return self._json({"ok": True})

        if parsed.path == "/api/text":
            text = str(data.get("text", ""))
            serial = str(data.get("serial", "")).strip()
            if not serial:
                snap = self.state.snapshot()
                serial = str(snap.get("active_serial") or "")
                if not serial:
                    serials = current_serials_for_grid_from_snapshot(snap)
                    serial = serials[0] if serials else ""
            if not serial:
                return self._json({"ok": False, "error": "no_target_serial"}, code=400)
            if text == "":
                return self._json({"ok": False, "error": "missing_text"}, code=400)
            ok = adb_input_text(self.adb_bin, serial, text)
            if not ok:
                return self._json({"ok": False, "error": "adb_text_failed"}, code=500)
            self.state.set_active_serial(serial)
            self.state.set_last_action(f"text serial={serial} len={len(text)}")
            return self._json({"ok": True})

        self.send_error(404)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=18080)
    ap.add_argument("--rows", type=int, default=2)
    ap.add_argument("--cols", type=int, default=4)
    ap.add_argument("--capture-interval", type=float, default=2.0)
    ap.add_argument("--data-dir", default="runs/live_dashboard")
    ap.add_argument("--adb-bin", default="adb")
    args = ap.parse_args()

    data_dir = Path(args.data_dir).resolve()
    data_dir.mkdir(parents=True, exist_ok=True)

    state = DashboardState(rows=max(1, args.rows), cols=max(1, args.cols), data_dir=data_dir)

    t = threading.Thread(
        target=capture_loop,
        args=(state, args.adb_bin, max(0.2, args.capture_interval)),
        daemon=True,
    )
    t.start()

    Handler.state = state
    Handler.adb_bin = args.adb_bin
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"LIVE_DASHBOARD_URL=http://{args.host}:{args.port}")
    print(f"DATA_DIR={data_dir}")
    server.serve_forever()


if __name__ == "__main__":
    main()
