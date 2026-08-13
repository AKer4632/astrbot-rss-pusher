import os
import json
import sqlite3
import asyncio
import aiohttp
import feedparser
from datetime import datetime, timedelta
from typing import Dict, List, Optional

from astrbot.api.all import *
from astrbot.api.event import filter

DATA_DIR = os.path.join("data", "rss_pusher")
DB_PATH = os.path.join(DATA_DIR, "rss.db")

class RSSPusherPlugin(Star):
    def __init__(self, context: Context):
        super().__init__(context)
        self.config = context.get_config()
        self.interval = self.config.get("check_interval", 600)
        self.max_summary = self.config.get("max_summary_length", 200)
        self.retention = self.config.get("retention_days", 7)
        self.template = self.config.get("default_template", "[RSS] {title}\n{link}\n{summary}")
        os.makedirs(DATA_DIR, exist_ok=True)
        self._init_db()
        self.task = None
        self.running = False

    def _init_db(self):
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute('''CREATE TABLE IF NOT EXISTS subscriptions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            url TEXT NOT NULL,
            alias TEXT,
            chat_type TEXT,
            chat_id TEXT,
            last_guid TEXT,
            last_check REAL,
            interval INTEGER DEFAULT 600,
            enabled INTEGER DEFAULT 1,
            UNIQUE(url, chat_id)
        )''')
        c.execute('''CREATE TABLE IF NOT EXISTS history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            sub_id INTEGER,
            guid TEXT,
            title TEXT,
            link TEXT,
            published REAL,
            FOREIGN KEY(sub_id) REFERENCES subscriptions(id)
        )''')
        conn.commit()
        conn.close()

    async def activate(self):
        self.running = True
        self.task = asyncio.create_task(self._poll_loop())

    async def deactivate(self):
        self.running = False
        if self.task:
            self.task.cancel()

    async def _poll_loop(self):
        while self.running:
            try:
                await self._check_all()
            except Exception as e:
                logger.error(f"[RSS] poll error: {e}")
            await asyncio.sleep(self.interval)

    async def _check_all(self):
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("SELECT * FROM subscriptions WHERE enabled=1")
        subs = c.fetchall()
        conn.close()

        for sub in subs:
            sub_id, url, alias, chat_type, chat_id, last_guid, last_check, interval, enabled = sub
            try:
                await self._fetch_and_push(sub_id, url, chat_type, chat_id, last_guid)
            except Exception as e:
                logger.error(f"[RSS] fetch {url} error: {e}")

    async def _fetch_and_push(self, sub_id: int, url: str, chat_type: str, chat_id: str, last_guid: str):
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                text = await resp.text()

        feed = feedparser.parse(text)
        if not feed.entries:
            return

        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        new_items = []

        for entry in feed.entries:
            guid = entry.get("id") or entry.get("link") or entry.get("title")
            if not guid:
                continue
            c.execute("SELECT 1 FROM history WHERE sub_id=? AND guid=?", (sub_id, guid))
            if c.fetchone():
                continue

            title = entry.get("title", "无标题")
            link = entry.get("link", "")
            summary = entry.get("summary", "") or entry.get("description", "")
            summary = summary[:self.max_summary] + "..." if len(summary) > self.max_summary else summary

            c.execute("INSERT INTO history (sub_id, guid, title, link, published) VALUES (?,?,?,?,?)",
                      (sub_id, guid, title, link, datetime.now().timestamp()))
            new_items.append((title, link, summary))

        c.execute("UPDATE subscriptions SET last_check=? WHERE id=?", (datetime.now().timestamp(), sub_id))
        conn.commit()
        conn.close()

        if new_items:
            for title, link, summary in reversed(new_items):
                msg = self.template.format(title=title, link=link, summary=summary)
                await self._send(chat_type, chat_id, msg)

    async def _send(self, chat_type: str, chat_id: str, msg: str):
        try:
            if chat_type == "group":
                await self.context.send_message(chat_id, MessageChain().plain(msg))
            else:
                await self.context.send_message(chat_id, MessageChain().plain(msg))
        except Exception as e:
            logger.error(f"[RSS] send error: {e}")

    @filter.command("rss")
    async def rss_cmd(self, event: AstrMessageEvent, *args):
        if not args:
            yield event.plain_result("用法: rss add <url> [别名] | del <别名/url> | list | refresh <别名> | set <别名> <key> <value>")
            return

        action = args[0]
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        chat_type = "group" if event.is_group_chat() else "private"
        chat_id = str(event.get_group_id() if event.is_group_chat() else event.get_sender_id())

        if action == "add":
            if len(args) < 2:
                yield event.plain_result("用法: rss add <url> [别名]")
                conn.close()
                return
            url = args[1]
            alias = args[2] if len(args) > 2 else url.split("/")[2]
            try:
                c.execute("INSERT INTO subscriptions (url, alias, chat_type, chat_id) VALUES (?,?,?,?)",
                          (url, alias, chat_type, chat_id))
                conn.commit()
                yield event.plain_result(f"已订阅: {alias} ({url})")
            except sqlite3.IntegrityError:
                yield event.plain_result("该RSS已在此会话订阅")

        elif action == "del":
            if len(args) < 2:
                yield event.plain_result("用法: rss del <别名/url>")
                conn.close()
                return
            target = args[1]
            c.execute("DELETE FROM subscriptions WHERE (alias=? OR url=?) AND chat_id=?", (target, target, chat_id))
            conn.commit()
            yield event.plain_result(f"已删除订阅: {target}")

        elif action == "list":
            c.execute("SELECT alias, url, enabled FROM subscriptions WHERE chat_id=?", (chat_id,))
            rows = c.fetchall()
            if not rows:
                yield event.plain_result("当前无订阅")
            else:
                lines = [f"{i+1}. {a} ({u}) {'[开]' if e else '[关]'}" for i, (a, u, e) in enumerate(rows)]
                yield event.plain_result("订阅列表:\n" + "\n".join(lines))

        elif action == "refresh":
            if len(args) < 2:
                yield event.plain_result("用法: rss refresh <别名>")
                conn.close()
                return
            alias = args[1]
            c.execute("SELECT id, url, chat_type, chat_id, last_guid FROM subscriptions WHERE alias=? AND chat_id=?", (alias, chat_id))
            row = c.fetchone()
            if not row:
                yield event.plain_result("未找到该订阅")
            else:
                yield event.plain_result(f"正在刷新 {alias}...")
                await self._fetch_and_push(row[0], row[1], row[2], row[3], row[4])
                yield event.plain_result("刷新完成")

        elif action == "set":
            if len(args) < 4:
                yield event.plain_result("用法: rss set <别名> <key> <value>\nkey: enabled(0/1), interval(秒)")
                conn.close()
                return
            alias, key, value = args[1], args[2], args[3]
            if key == "enabled":
                c.execute("UPDATE subscriptions SET enabled=? WHERE alias=? AND chat_id=?", (int(value), alias, chat_id))
            elif key == "interval":
                c.execute("UPDATE subscriptions SET interval=? WHERE alias=? AND chat_id=?", (int(value), alias, chat_id))
            conn.commit()
            yield event.plain_result(f"已更新 {alias}: {key}={value}")

        else:
            yield event.plain_result("未知命令")

        conn.close()

    @filter.command("rss_clean")
    async def rss_clean(self, event: AstrMessageEvent):
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        cutoff = (datetime.now() - timedelta(days=self.retention)).timestamp()
        c.execute("DELETE FROM history WHERE published < ?", (cutoff,))
        conn.commit()
        count = c.rowcount
        conn.close()
        yield event.plain_result(f"已清理 {count} 条过期记录")
