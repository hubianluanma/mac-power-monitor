#!/usr/bin/env python3
"""
power-monitor Web 仪表盘
- Flask 单文件服务
- 前端：Chart.js + 原生 CSS，无框架依赖
- 端口 7654，只监听 127.0.0.1（不暴露公网，通过 nginx 反代外网访问）

页面：
  /                  总览：今日曲线 + 周趋势 + 累计成本
  /api/summary        JSON 摘要（支持电价档位）
  /api/samples?hours=  原始数据
  /api/hourly         按小时聚合
  /api/daily          按天聚合
  /api/monthly        按月聚合（用于阶梯电价）
"""
import sqlite3
import os
from datetime import datetime, timezone, timedelta
from pathlib import Path
from flask import Flask, jsonify, render_template, request

BASE = Path(__file__).resolve().parent.parent
DB_PATH = BASE / "data" / "power.db"
PORT = int(os.environ.get("PORT", "7654"))

# 系统外设功耗估算 (W)，跟 collector.py 保持一致
SYSTEM_BIAS_W = float(os.environ.get("SYSTEM_BIAS_W", "7.0"))

# URL 前缀：如果你用 nginx 反代 /power/ 路径访问，保持 /power 默认
# 单机直接访问改成 "" 即可
SCRIPT_PREFIX = os.environ.get("SCRIPT_PREFIX", "/power")

CST = timezone(timedelta(hours=8))

# ============= 电价表 =============
# 中国主要城市居民阶梯电价（2026 年现行，单位：元/kWh）
# 档位按"年累计用电量"分：
#   一档：基数以内（普通家庭主要落在这里）
#   二档：超基数 1 倍以内
#   三档：超基数 2 倍以上
#
# 北京档位基准：2520 / 4800 kWh/年（约 210 / 400 度/月）
# 上海档位基准：3120 / 4800 kWh/年（夏冬有季节性调整，这里简化为全年均值）
# 广州/深圳档位基准基本一致，约 2600 / 4600 kWh/年

PRICE_TIERS = {
    "beijing": {
        "name": "北京",
        "tiers": [
            {"limit": 2520, "price": 0.5469},
            {"limit": 4800, "price": 0.5969},
            {"limit": None, "price": 0.8469},  # None = 无上限
        ],
        "default": 0.5469,
    },
    "shanghai": {
        "name": "上海",
        "tiers": [
            {"limit": 3120, "price": 0.6170},
            {"limit": 4800, "price": 0.6670},
            {"limit": None, "price": 0.9170},
        ],
        "default": 0.6170,
    },
    "guangzhou": {
        "name": "广州",
        "tiers": [
            {"limit": 2600, "price": 0.5898},
            {"limit": 4600, "price": 0.6398},
            {"limit": None, "price": 0.8898},
        ],
        "default": 0.5898,
    },
    "shenzhen": {
        "name": "深圳",
        "tiers": [
            {"limit": 2600, "price": 0.5956},
            {"limit": 4600, "price": 0.6456},
            {"limit": None, "price": 0.8956},
        ],
        "default": 0.5956,
    },
    "flat": {
        "name": "统一价（0.6 元）",
        "tiers": [
            {"limit": None, "price": 0.6},
        ],
        "default": 0.6,
    },
}


def calc_tier_cost(year_kwh: float, city: str) -> dict:
    """
    根据年度累计度数和城市档位算成本
    返回 {total_cost, breakdown: [{tier, kwh, price, cost}], average_price}
    """
    tiers = PRICE_TIERS.get(city, PRICE_TIERS["beijing"])["tiers"]
    remaining = year_kwh
    total_cost = 0.0
    breakdown = []
    consumed = 0

    for t in tiers:
        if remaining <= 0:
            break
        cap = t["limit"]
        if cap is None:
            chunk = remaining
        else:
            chunk = max(0, min(remaining, cap - consumed))
        cost = chunk * t["price"]
        total_cost += cost
        breakdown.append({
            "tier": len(breakdown) + 1,
            "kwh": round(chunk, 3),
            "price": t["price"],
            "cost": round(cost, 4),
        })
        consumed += chunk
        remaining -= chunk

    return {
        "total_cost": round(total_cost, 4),
        "breakdown": breakdown,
        "average_price": round(total_cost / year_kwh, 4) if year_kwh > 0 else tiers[0]["price"],
    }


def get_con():
    return sqlite3.connect(DB_PATH)


def query(sql, params=()):
    con = get_con()
    con.row_factory = sqlite3.Row
    cur = con.cursor()
    cur.execute(sql, params)
    rows = cur.fetchall()
    con.close()
    return rows


def now_local():
    return datetime.now(CST)


def _integrate(rows) -> float:
    """梯形积分：rows 是 [{ts_local, total_w}, ...]，返回焦耳数"""
    if len(rows) < 2:
        return 0
    ts_data = []
    for r in rows:
        try:
            ts = datetime.strptime(r["ts_local"], "%Y-%m-%d %H:%M:%S").timestamp()
            ts_data.append((ts, r["total_w"]))
        except Exception:
            pass
    ts_data.sort()
    integral = 0.0
    for i in range(1, len(ts_data)):
        dt = ts_data[i][0] - ts_data[i-1][0]
        if dt > 7200:  # 跳过超过 2 小时的间隔
            continue
        p_avg = (ts_data[i][1] + ts_data[i-1][1]) / 2
        integral += p_avg * dt
    return integral


app = Flask(__name__, template_folder=str(BASE / "templates"))


@app.after_request
def add_no_cache(response):
    """实时数据，禁止 Cloudflare/CDN 缓存"""
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response.headers["Pragma"] = "no-cache"
    return response


# ============= API =============

@app.route(f"{SCRIPT_PREFIX}/api/summary")
def api_summary():
    city = request.args.get("city", "beijing")
    if city not in PRICE_TIERS:
        city = "beijing"

    today = now_local().strftime("%Y-%m-%d")
    this_month_start = now_local().strftime("%Y-%m-01")
    this_year_start = now_local().strftime("%Y-01-01")

    # 当前（最后）瓦数
    last_row = query("SELECT total_w, ts_local FROM samples ORDER BY ts DESC LIMIT 1")
    current_w = last_row[0]["total_w"] if last_row else 0

    # 今日
    today_rows = query("SELECT total_w, ts_local FROM samples WHERE ts_local LIKE ?",
                       (f"{today}%",))
    today_joules = _integrate(today_rows)
    # 加上尾巴（最后采样到现在的时间，默认功率）
    if today_rows:
        try:
            last_ts = datetime.strptime(today_rows[-1]["ts_local"], "%Y-%m-%d %H:%M:%S").timestamp()
            now_ts = now_local().timestamp()
            tail = min(now_ts - last_ts, 7200)
            if tail > 0:
                today_joules += current_w * tail
        except Exception:
            pass
    today_kwh = today_joules / 3_600_000
    avg_w = sum(r["total_w"] for r in today_rows) / len(today_rows) if today_rows else 0

    # 本月累计（用于阶梯判断）
    month_rows = query("""SELECT total_w, ts_local FROM samples
                          WHERE ts_local >= ?
                          ORDER BY ts""", (this_month_start,))
    # 因为采样器只有当天数据，本月之前的部分用"近 30 天均值"估算
    if month_rows:
        days_in_month = now_local().day
        # 当前采集天数
        days_collected = len(set(r["ts_local"][:10] for r in month_rows))
        if days_collected > 0:
            avg_per_day = today_joules / 3_600_000 if days_collected >= 1 else 0
            # 简单估算：本月度数 ≈ 今日度数 + (今日度数 × (month_day - 1))
            # 更好的做法：用本月所有日的样本平均 × 月天数
            # 这里用更直接的方法：本月样本平均值 × 24 × month_day
            month_avg_w = sum(r["total_w"] for r in month_rows) / len(month_rows)
            month_kwh = month_avg_w * 24 * days_in_month / 1000
        else:
            month_kwh = 0
    else:
        month_kwh = 0

    # 本年累计（用于阶梯判断）
    year_rows = query("""SELECT total_w, ts_local FROM samples
                         WHERE ts_local >= ?
                         ORDER BY ts""", (this_year_start,))
    if year_rows:
        days_in_year = now_local().timetuple().tm_yday
        days_collected = len(set(r["ts_local"][:10] for r in year_rows))
        if days_collected > 0:
            year_avg_w = sum(r["total_w"] for r in year_rows) / len(year_rows)
            year_kwh = year_avg_w * 24 * days_in_year / 1000
        else:
            year_kwh = 0
    else:
        year_kwh = 0

    tier = calc_tier_cost(year_kwh, city)

    # 7 日每日均值
    week_ago = (now_local() - timedelta(days=7)).strftime("%Y-%m-%d")
    daily_rows = query("""
        SELECT date(ts_local) as day,
               AVG(total_w) as avg_w,
               COUNT(*) as n
        FROM samples
        WHERE date(ts_local) >= date(?)
        GROUP BY day ORDER BY day
    """, (week_ago,))
    daily_data = []
    for r in daily_rows:
        daily_data.append({
            "day": r["day"],
            "avg_w": round(r["avg_w"], 2),
            "kwh_est": round(r["avg_w"] * 24 / 1000, 3),
        })

    return jsonify({
        "current_w": round(current_w, 2),
        "city": city,
        "city_name": PRICE_TIERS[city]["name"],
        "today": {
            "samples": len(today_rows),
            "avg_w": round(avg_w, 2),
            "kwh": round(today_kwh, 3),
            "cost": round(today_kwh * tier["average_price"], 4),
        },
        "month": {
            "kwh_est": round(month_kwh, 3),
            "cost_est": round(month_kwh * tier["average_price"], 4),
        },
        "year": {
            "kwh_est": round(year_kwh, 3),
            "tier": tier,
        },
        "weekly_data": daily_data,
        "price_tiers": PRICE_TIERS,
        "system_bias_w": SYSTEM_BIAS_W,
        "updated_at": now_local().strftime("%Y-%m-%d %H:%M:%S"),
    })


@app.route(f"{SCRIPT_PREFIX}/api/samples")
def api_samples():
    hours = int(request.args.get("hours", 24))
    since = (now_local() - timedelta(hours=hours)).strftime("%Y-%m-%d %H:%M:%S")
    rows = query("""SELECT ts_local, total_w, cpu_mw, gpu_mw, ane_mw FROM samples
                    WHERE ts_local >= ? ORDER BY ts""", (since,))
    return jsonify([{
        "ts": r["ts_local"], "total_w": r["total_w"],
        "cpu_mw": r["cpu_mw"], "gpu_mw": r["gpu_mw"], "ane_mw": r["ane_mw"],
    } for r in rows])


@app.route(f"{SCRIPT_PREFIX}/api/hourly")
def api_hourly():
    today = now_local().strftime("%Y-%m-%d")
    rows = query("""SELECT substr(ts_local, 12, 2) as hour,
                           AVG(total_w) as avg_w, MAX(total_w) as max_w, COUNT(*) as n
                    FROM samples WHERE ts_local LIKE ?
                    GROUP BY hour ORDER BY hour""", (f"{today}%",))
    result = {f"{h:02d}": None for h in range(24)}
    for r in rows:
        result[r["hour"]] = {"avg_w": round(r["avg_w"], 2), "max_w": round(r["max_w"], 2), "n": r["n"]}
    return jsonify(result)


@app.route(f"{SCRIPT_PREFIX}/api/cities")
def api_cities():
    """电价表，前端用来渲染下拉菜单"""
    return jsonify({k: {"name": v["name"], "default": v["default"], "tiers": v["tiers"]}
                    for k, v in PRICE_TIERS.items()})


@app.route(f"{SCRIPT_PREFIX}/")
@app.route(f"{SCRIPT_PREFIX}")
@app.route("/")  # 兼容无前缀模式
def index():
    return render_template("index.html", script_prefix=SCRIPT_PREFIX)


if __name__ == "__main__":
    print(f"[web] starting on http://127.0.0.1:{PORT}  (prefix={SCRIPT_PREFIX})")
    app.run(host="127.0.0.1", port=PORT, debug=False)