#!/usr/bin/env python3
"""
power-monitor 采样器
- 每 60s 调一次 sudo powermetrics，采样 5s
- 解析出 CPU / GPU / ANE 三段功耗（mW）
- 写入 SQLite（按 ts 唯一，幂等）

设计：单进程循环，daemon 化由 LaunchAgent 处理。
"""
import sqlite3
import subprocess
import re
import time
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

# ---------- 配置 ----------
BASE = Path(__file__).resolve().parent.parent
DB_PATH = BASE / "data" / "power.db"

# 系统外设功耗估算 (W)
# powermetrics 只给 SoC package 数 (CPU+GPU+ANE)，不含屏幕/SSD/USB/Wi-Fi/DRAM/PSU 损耗
# 这是个粗略估算——你机器的整机真实瓦数 = powermetrics 报的 combined + SYSTEM_BIAS
# 推荐值（实测参考）：
#   M4 Mac mini 桌面机（无屏幕）：7 W
#   M4 MacBook Pro 14" 闲置：    10-12 W
#   M4 MacBook Air 闲置：         8-10 W
#   M4 满载推理：                  5-10 W（外加屏幕 12 W）
# 用环境变量覆盖：SYSTEM_BIAS_W=10 python3 collector.py
SYSTEM_BIAS_W = float(os.environ.get("SYSTEM_BIAS_W", "7.0"))

CST = timezone(timedelta(hours=8))  # 中国标准时间

# ---------- DB 初始化 ----------
def init_db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS samples (
            ts INTEGER PRIMARY KEY,        -- UTC 秒
            ts_local TEXT NOT NULL,        -- 本地时间 ISO 格式
            cpu_mw REAL NOT NULL,
            gpu_mw REAL NOT NULL,
            ane_mw REAL NOT NULL,
            combined_mw REAL NOT NULL,     -- CPU+GPU+ANE
            total_w REAL NOT NULL,         -- combined + SYSTEM_BIAS
            note TEXT
        )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_ts_local ON samples(ts_local)")
    con.commit()
    return con


# ---------- 解析 ----------
# 输出示例：
#   CPU Power: 123 mW
#   GPU Power: 45 mW
#   ANE Power: 0 mW
#   Combined Power (CPU + GPU + ANE): 168 mW
FIELD_RE = {
    "cpu":    re.compile(r"CPU Power:\s+([\d.]+)\s+mW"),
    "gpu":    re.compile(r"GPU Power:\s+([\d.]+)\s+mW"),
    "ane":    re.compile(r"ANE Power:\s+([\d.]+)\s+mW"),
    "combined": re.compile(r"Combined Power \(CPU \+ GPU \+ ANE\):\s+([\d.]+)\s+mW"),
}


def sample_once(sampler_seconds: int = 5) -> dict | None:
    """跑一次 powermetrics，返回解析后的 dict；失败返回 None。"""
    try:
        # -i 1000 1s 一次，-n sampler_seconds 跑 N 次
        result = subprocess.run(
            [
                "sudo", "-n", "/usr/bin/powermetrics",
                "-i", "1000",
                "-n", str(sampler_seconds),
                "--hide-cpu-duty-cycle",
            ],
            capture_output=True, text=True, timeout=sampler_seconds + 10,
        )
        if result.returncode != 0:
            print(f"[ERR] powermetrics rc={result.returncode}: {result.stderr[:200]}", file=sys.stderr)
            return None

        text = result.stdout
        # 每个采样周期都会重打印 CPU/GPU/ANE Power，取最后一次（最稳定）
        # 用 findall 取所有匹配，[-1] 是最新
        cpu_mw = float(FIELD_RE["cpu"].findall(text)[-1]) if FIELD_RE["cpu"].findall(text) else 0.0
        gpu_mw = float(FIELD_RE["gpu"].findall(text)[-1]) if FIELD_RE["gpu"].findall(text) else 0.0
        ane_mw = float(FIELD_RE["ane"].findall(text)[-1]) if FIELD_RE["ane"].findall(text) else 0.0
        combined_mw = cpu_mw + gpu_mw + ane_mw

        total_w = combined_mw / 1000.0 + SYSTEM_BIAS_W

        return {
            "cpu_mw": cpu_mw,
            "gpu_mw": gpu_mw,
            "ane_mw": ane_mw,
            "combined_mw": combined_mw,
            "total_w": round(total_w, 3),
        }
    except subprocess.TimeoutExpired:
        print("[ERR] powermetrics timeout", file=sys.stderr)
        return None
    except Exception as e:
        print(f"[ERR] sample exception: {e}", file=sys.stderr)
        return None


# ---------- 主循环 ----------
def store_sample(con, data: dict):
    now = datetime.now(timezone.utc)
    local = now.astimezone(CST)
    cur = con.cursor()
    try:
        cur.execute("""
            INSERT OR IGNORE INTO samples
            (ts, ts_local, cpu_mw, gpu_mw, ane_mw, combined_mw, total_w, note)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            int(now.timestamp()),
            local.strftime("%Y-%m-%d %H:%M:%S"),
            data["cpu_mw"], data["gpu_mw"], data["ane_mw"],
            data["combined_mw"], data["total_w"],
            None,
        ))
        con.commit()
        return cur.rowcount == 1
    except sqlite3.Error as e:
        print(f"[ERR] db insert: {e}", file=sys.stderr)
        return False


def main():
    interval_s = 60  # 每分钟一次
    print(f"[collector] starting, interval={interval_s}s, db={DB_PATH}")
    con = init_db()
    last_sample_minute = -1
    while True:
        now = datetime.now()
        # 整分钟对齐
        sleep_to_next_min = 60 - now.second
        time.sleep(sleep_to_next_min)
        # 采样
        data = sample_once(sampler_seconds=5)
        if data:
            inserted = store_sample(con, data)
            local_str = datetime.now(CST).strftime("%H:%M:%S")
            marker = "✓" if inserted else "·"  # · 表示重复分钟
            print(f"[{local_str}] {marker} CPU={data['cpu_mw']:.0f}mW GPU={data['gpu_mw']:.0f}mW ANE={data['ane_mw']:.0f}mW total={data['total_w']:.2f}W")
        else:
            print(f"[{datetime.now(CST).strftime('%H:%M:%S')}] sample failed", file=sys.stderr)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n[collector] stopped")