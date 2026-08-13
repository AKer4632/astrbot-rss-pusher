# AstrBot RSS Pusher

RSS订阅及自动推送插件

## 功能

- 群聊/私聊订阅RSS源
- 定时轮询检测更新
- 新条目自动推送到对应会话
- 支持管理订阅（增删查改）

## 命令

- `rss add <url> [别名]` — 添加订阅
- `rss del <别名/url>` — 删除订阅
- `rss list` — 查看当前订阅
- `rss refresh <别名>` — 手动刷新
- `rss set <别名> <key> <value>` — 配置单源（enabled/interval）
- `rss_clean` — 清理过期记录

## 安装

将本插件放入 AstrBot 的 `plugins/` 目录，重启即可。

## 配置

在 `data/rss_pusher/config.json` 中可调整：

- `check_interval`: 轮询间隔（秒，默认600）
- `max_summary_length`: 摘要最大长度（默认200）
- `retention_days`: 记录保留天数（默认7）
- `default_template`: 推送消息模板
