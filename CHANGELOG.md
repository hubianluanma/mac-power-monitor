# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.1.0] - 2026-06-23

### Added
- KPI 卡片点击交互:点选卡片切换"实时功率曲线"显示的指标序列。
  - 4 张卡片可交互 → 切换 series:
    - `当前功耗` → 整机功耗 (W)
    - `今日耗电` → 今日累计度数 (kWh,梯形积分)
    - `今日平均` → 5 点滑动平均 (W,平滑趋势)
    - `本月估算` → 本月累计度数 (kWh,基线 + 今日累计)
  - 2 张卡片无时间序列数据 → 点击触发 toast 提示:
    - `年均电价`、`系统估算开销`
- KPI 卡片键盘可达性:`role="button"`、`tabindex="0"`、`Enter`/`Space` 触发。
- KPI 选中状态持久化:`localStorage.power-active-kpi` 记住上次选择。
- Toast 反馈组件:页面底部居中,自动 2.2s 消失。
- 图表自适应:切换指标时同步更新曲线颜色、Y 轴单位、meta 文本。

### Changed
- 移除 KPI 卡片硬编码的 `.primary` 高亮,改为由 JS 注入 `.selected` class。
- 实时曲线渲染逻辑抽离到 `renderRealtimeChart(samples)`,支持按 KPI 切换数据序列。

## [1.0.0] - 2026-06-22

### Added
- 初始发布:M4 Mac powermetrics 实时功耗监测仪表盘。
- Flask 后端 + Chart.js 前端单页架构,SQLite 时序存储。
- 阶梯电价拆解(支持北京/上海/广州/深圳/统一价)。
- nginx 反代 + Cloudflare Tunnel 外网访问。
- NOPASSWD sudoers 配置说明、LaunchAgent 后台常驻。