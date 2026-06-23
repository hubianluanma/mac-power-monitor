# mac-power-monitor

**Real-time Mac power consumption dashboard.** Sample `powermetrics` every minute, store in SQLite, visualize in browser via Flask + Chart.js. Estimate electricity cost with tiered Chinese residential pricing.

把 Mac 的实时功耗数据变成可外网访问的可视化仪表盘，按北京/上海/广州阶梯电价估算电费。

[中文文档](./README.zh.md) | [Live demo](https://hubianluanma.com/power/) | [Blog post](https://blog.hubianluanma.com/posts/mac-power-monitor/)

## Screenshots

**Main dashboard** — KPIs, 24h realtime curve, hourly distribution, 7-day trend:

<div align="center">
  <img src="./docs/screenshots/dashboard.png" alt="Main dashboard" width="900">
</div>

**KPI cards are clickable** — click any of the 4 highlighted cards to switch the realtime chart series (current power, today's cumulative kWh, 5-point moving average, monthly cumulative kWh). The other 2 cards (no time-series) show a toast on click:

<div align="center">
  <img src="./docs/screenshots/kpi-click.gif" alt="KPI click interaction" width="900">
</div>

**Tiered electricity pricing breakdown** — annual cumulative kWh maps to the appropriate tier automatically (Beijing/Shanghai/Guangzhou/Shenzhen):

<div align="center">
  <img src="./docs/screenshots/tier-pricing.png" alt="Tiered pricing breakdown" width="900">
</div>

## Features

- ⚡ **Real-time sampling** — `powermetrics` every 60s, parses CPU/GPU/ANE power
- 📊 **Web dashboard** — 24h curve, hourly distribution, 7-day trend (Chart.js, single HTML file)
- 💰 **Tiered pricing** — Beijing/Shanghai/Guangzhou/Shenzhen tiered residential electricity
- 🌐 **External access** — Optional nginx reverse proxy + Cloudflare Tunnel setup
- 📦 **Single binary** — No database server, just SQLite; no build step
- 🪶 **Lightweight** — ~800 lines of Python + HTML combined

## Quick Start

```bash
# 1. Install dependencies
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# 2. Configure passwordless sudo for powermetrics (one-time setup)
#    See "Installation" below for full instructions

# 3. Start the collector (samples every minute)
python3 scripts/collector.py

# 4. Start the web dashboard (in another terminal)
python3 scripts/web.py

# 5. Open http://127.0.0.1:7654/power/
```

## Installation

### Requirements

- **macOS** (Apple Silicon recommended; powermetrics is macOS-only)
- **Python 3.9+** (tested on 3.11 and 3.14)
- **`sudo`** access (for one-time powermetrics configuration)

### Step 1: Configure passwordless sudo for `powermetrics`

`powermetrics` requires root. To run it every minute without password prompts:

```bash
# Create a sudoers drop-in file
sudo tee /etc/sudoers.d/powermetrics <<EOF
$(whoami) ALL=(root) NOPASSWD: /usr/bin/powermetrics
Defaults!/usr/bin/powermetrics !logfile, !syslog
EOF

# Lock down permissions
sudo chmod 0440 /etc/sudoers.d/powermetrics

# Verify (should run without password)
sudo -n /usr/bin/powermetrics -i 1000 -n 1 --hide-cpu-duty-cycle | head -20
```

> ⚠️ **Security note**: The `!logfile, !syslog` defaults prevent syslog spam. The `NOPASSWD` is scoped to `powermetrics` only, not full root.

### Step 2: Configure your system bias

`powermetrics` reports only SoC package power (CPU+GPU+ANE), excluding peripherals (screen, SSD, USB, Wi-Fi, DRAM, PSU losses). You need to add a `SYSTEM_BIAS_W` estimate:

| Machine | SYSTEM_BIAS_W |
|---|---|
| M4 Mac mini (desktop, no screen) | 7 W |
| M4 MacBook Pro 14" idle | 10-12 W |
| M4 MacBook Air idle | 8-10 W |

Override with environment variable:
```bash
export SYSTEM_BIAS_W=10
```

### Step 3: Run it

```bash
# Terminal 1 — collector
SYSTEM_BIAS_W=7 python3 scripts/collector.py

# Terminal 2 — web dashboard
SYSTEM_BIAS_W=7 python3 scripts/web.py
# → open http://127.0.0.1:7654/power/
```

## Configuration

All settings are environment variables:

| Variable | Default | Description |
|---|---|---|
| `SYSTEM_BIAS_W` | `7.0` | System peripheral power estimate (W) |
| `PORT` | `7654` | Web server port |
| `SCRIPT_PREFIX` | `/power` | URL prefix (set to `""` for root deployment) |

## Architecture

```
┌─────────────────────┐
│ powermetrics (sudo) │  ← samples every 60s
└──────────┬──────────┘
           ↓
┌─────────────────────┐
│ SQLite single file  │  ← data/power.db
└──────────┬──────────┘
           ↓
┌─────────────────────┐
│ Flask @ 127.0.0.1   │  ← port 7654
└──────────┬──────────┘
           ↓ proxy_pass /power/
┌─────────────────────┐
│ nginx (optional)    │  ← reverse proxy
└──────────┬──────────┘
           ↓
┌─────────────────────┐
│ Cloudflare Tunnel   │  ← HTTPS termination
└──────────┬──────────┘
           ↓
     Browser (mobile / desktop)
```

## API Endpoints

All endpoints return JSON, all under `SCRIPT_PREFIX` (default `/power`):

| Endpoint | Description |
|---|---|
| `GET /power/api/summary?city=beijing` | KPI summary (current power, today/month/year kWh + cost) |
| `GET /power/api/samples?hours=24` | Raw sample points (last N hours) |
| `GET /power/api/hourly` | Today's hourly distribution |
| `GET /power/api/cities` | Available cities and pricing tiers |

## External Access (Optional)

See [`examples/nginx.conf`](./examples/nginx.conf) for a complete nginx configuration. If using Cloudflare, **add a Cache Rule** for `/power/api/*` to bypass CDN caching — otherwise the dashboard will show stale data.

## Limitations

- **macOS only** — `powermetrics` is not available on Windows or Linux
- **Approximate cost** — `powermetrics` doesn't report screen/peripheral power, so a `SYSTEM_BIAS_W` correction is needed. Total ±15% accuracy vs. real wattmeter.
- **No authentication** — If exposing externally, add authentication at the nginx layer or use Cloudflare Access
- **SQLite is fine for personal use** — For multi-user deployments, migrate to PostgreSQL/TimescaleDB

## Project Background

This started as a personal tool to estimate the daily electricity cost of an always-on M4 Mac mini. After solving the problem for myself, I packaged it as an open-source project.

📖 Full implementation story (in Chinese): [从 powermetrics 到外网可视化仪表盘](https://blog.hubianluanma.com/posts/mac-power-monitor/)

## License

MIT © 2026 [Spiral](https://github.com/hubianluanma)