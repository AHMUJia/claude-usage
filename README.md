# Claude Usage Screen

**中文** · [English](README.en.md)
[v2](https://github.com/AHMUJia/claude-usage/blob/main/README.md) · **v1**

[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
![Python](https://img.shields.io/badge/Python-3.8%2B-blue.svg)
![PySide6](https://img.shields.io/badge/GUI-PySide6-41cd52.svg)
![Platform](https://img.shields.io/badge/Platform-Windows%20%7C%20macOS%20%7C%20Linux-lightgrey.svg)

一个极简的黑白**信息屏**，适合放在备用显示器或小尺寸 HDMI 屏上当桌面时钟。
它显示一个大号时钟、你的 **Claude Code 用量**（5 小时会话与每周额度，以「剩余 %」+
重置时间呈现）以及当前**天气**。

<p align="center">
  <img src="docs/device.jpg" width="420" alt="概念示意图">
</p>

> 上图为放在桌面上的形态概念图；下图是程序的**实际渲染界面**：

![实际界面](docs/screenshot.png)

基于 PySide6 —— 单个 Python 文件，无网页框架，可在 Windows / macOS / Linux 运行。

## 快速开始

```bash
git clone git@github.com:AHMUJia/claude-usage.git
cd claude-usage
pip install PySide6              # 想显示用量卡再装： pip install claude-usage-widget
python infoscreen.py --fullscreen
```

**快捷键：** `F` / `F11` 切换全屏 · `Esc` / `Q` 退出。

## 功能

- **大号时钟**，外加取景框式边框（支持 24 / 12 小时制）。
- **Claude 用量卡** —— `5H LIMIT`（当前 5 小时会话）与 `WEEK LIMIT`（本周·全模型）：
  显示**剩余百分比** + 进度条 + **重置时间**。数据来自
  [`claude-usage-widget`](https://pypi.org/project/claude-usage-widget/)。
- **天气**（右上角）：自绘图标（晴 / 多云 / 雨 / 雪 / 雷 / 雾）+ 温度 + 湿度，
  来自 [wttr.in](https://wttr.in)（无需 API key，仅用标准库）。
- 纯黑白、适合全屏、单文件自包含。

## 安装

```bash
pip install PySide6
# 可选，用于显示用量卡（需已安装并登录 Claude Code）：
pip install claude-usage-widget
```

## 运行

```bash
python infoscreen.py                 # 窗口模式
python infoscreen.py --fullscreen    # 全屏
python infoscreen.py --city Beijing  # 指定天气城市
```

## 配置

把 `config.example.json` 复制为 `config.json`（与 `infoscreen.py` 同目录）后编辑：

| 键 | 默认值 | 含义 |
|---|---|---|
| `weather_city` | `""` | wttr.in 城市名；留空则按 IP 自动判断 |
| `weather_refresh_seconds` | `900` | 天气刷新间隔（秒） |
| `claude_refresh_seconds` | `300` | 用量刷新间隔（秒） |
| `claude_usage_cmd` | `null` | 输出用量 JSON 的命令；`null` = `python -m claude_usage --once` |
| `time_24h` | `true` | 24 小时制 / 12 小时制 |
| `fullscreen` | `false` | 启动即全屏 |
| `frameless` | `false` | 无边框窗口 |
| `width` / `height` | `960` / `640` | 窗口尺寸 |
| `session_label` / `week_label` | `5H LIMIT` / `WEEK LIMIT` | 两张卡片标题 |
| `font_family` | `Bahnschrift` | Windows 下呈现紧凑字形，其它平台自动回退 |

也可用 `--config 路径/config.json` 指定配置文件。

## 用量数据从哪来？

两张 LIMIT 卡会运行 `python -m claude_usage --once` 并读取它打印的 JSON
（`session_utilization`、`weekly_utilization`、`session_reset`、`weekly_reset`）。
该工具读取**你本地的 Claude Code 数据**、复用 Claude Code 自身的登录态——本程序
**不会把任何数据发往别处**。若未安装 `claude-usage-widget`，卡片只显示 `--`，
时钟与天气照常工作。

> 提示：若你的 Claude 配置目录非默认位置，请按 `claude-usage-widget` 文档配置，
> 或用 `claude_usage_cmd` 指向你自己输出相同 JSON 的脚本。

## 许可

MIT —— 见 [LICENSE](LICENSE)。
