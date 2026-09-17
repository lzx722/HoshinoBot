import json
import os
import re
import time
from dataclasses import dataclass
from typing import List, Union
from xml.etree import ElementTree

import hoshino
from bs4 import BeautifulSoup
from hoshino import aiorequests

CONTENT_NS = 'http://purl.org/rss/1.0/modules/content/'


REQUEST_TIMEOUT = 60

STATE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'db.json')
MAX_SEEN_GUIDS = 200


@dataclass
class Item:
    idx: Union[str, int]
    content: Union[str, List[str]] = ""

    def __eq__(self, other):
        return self.idx == other.idx


def _local_name(tag: str) -> str:
    return tag.rsplit('}', 1)[-1]


class JuyaRssSpider:
    url = "https://daily.juya.uk/rss.xml"
    src_name = "橘鸦AI早报"
    header = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    }
    item_cache = []
    # 已推送过的条目 guid（新->旧）与最近一次推送的期号日期，持久化到 db.json
    seen_guids = []
    last_push_date = ''
    last_refresh_ts = 0.0
    REFRESH_WINDOW = 60
    # 一天只有一期，单次推送最多一期，其余新条目直接登记跳过
    MAX_PUSH_ITEMS = 1

    _state_loaded = False

    @classmethod
    def _load_state(cls) -> None:
        """启动后首次调用时载入已推送记录；读不到即视为全新安装。"""
        if cls._state_loaded:
            return
        cls._state_loaded = True
        try:
            with open(STATE_PATH, encoding='utf8') as f:
                data = json.load(f)
        except FileNotFoundError:
            hoshino.logger.info(f'{cls.src_name} 未找到历史记录，本次只建立基线不推送历史')
        except Exception as e:
            hoshino.logger.error(f'{cls.src_name} 历史记录读取失败，按全新安装处理：{type(e)} {e}')
        else:
            guids = data.get('seen_guids') or []
            if isinstance(guids, list):
                cls.seen_guids = [g for g in guids if isinstance(g, str)]
            date = data.get('last_push_date')
            cls.last_push_date = date if isinstance(date, str) else ''
            hoshino.logger.info(
                f'{cls.src_name} 已载入 {len(cls.seen_guids)} 条推送记录'
                f'，最近推送日期 {cls.last_push_date or "无"}'
            )

    @classmethod
    def _save_state(cls) -> None:
        try:
            with open(STATE_PATH, 'w', encoding='utf8') as f:
                json.dump(
                    {
                        'seen_guids': cls.seen_guids,
                        'last_push_date': cls.last_push_date,
                        'updated_at': time.strftime('%Y-%m-%d %H:%M:%S'),
                    },
                    f, ensure_ascii=False, indent=2,
                )
        except Exception as e:
            hoshino.logger.error(f'{cls.src_name} 推送记录保存失败：{type(e)} {e}')

    @classmethod
    def _mark_seen(cls, items, pushed) -> None:
        """登记已推送条目：pushed 计入当日推送，items 全部登记避免重放。"""
        fresh = [i.idx for i in items]
        known = set(fresh)
        cls.seen_guids = (fresh + [g for g in cls.seen_guids if g not in known])[:MAX_SEEN_GUIDS]
        if pushed:
            cls.last_push_date = cls.item_date(pushed[0])
        cls._save_state()

    @classmethod
    async def get_response(cls) -> aiorequests.AsyncResponse:
        resp = await aiorequests.get(cls.url, headers=cls.header, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        return resp

    @staticmethod
    def _clean_text(text: str) -> str:
        text = re.sub(r'\{([^|{}]*)\|([^{}]*)\}', r'\1', text)
        return ' '.join(text.split())

    @classmethod
    def _extract_news(cls, html: str) -> List[str]:
        soup = BeautifulSoup(html, 'lxml')
        items = []
        current_cat = None
        in_detail = False

        for el in soup.find_all(['h2', 'h3']):
            if el.name == 'h2':
                cat = el.get_text(' ', strip=True)
                if cat == '概览':
                    in_detail = False
                    current_cat = None
                else:
                    in_detail = True
                    current_cat = cat
                continue

            if not in_detail or not current_cat:
                continue

            num_tag = el.find('code')
            num = num_tag.get_text(strip=True) if num_tag else ''

            title_soup = BeautifulSoup(str(el), 'lxml').find('h3')
            if title_soup.find('code'):
                title_soup.find('code').decompose()
            item_title = cls._clean_text(title_soup.get_text(' ', strip=True))

            heading = f'{num} {item_title}'.strip() if num else item_title

            blockquote = el.find_next_sibling('blockquote')
            abstract = cls._clean_text(blockquote.get_text()) if blockquote else ''
            if len(abstract) > 300:
                abstract = abstract[:300] + '…'

            news_text = f'{heading}\n{abstract}' if abstract else heading
            items.append((current_cat, news_text))

        news_msgs = []
        last_cat = None
        for cat, news_text in items:
            if cat != last_cat:
                news_msgs.append(f'【{cat}】\n{news_text}')
                last_cat = cat
            else:
                news_msgs.append(news_text)

        return news_msgs

    @classmethod
    async def get_items(cls, resp: aiorequests.AsyncResponse) -> List[Item]:
        root = ElementTree.fromstring(await resp.text)
        items = []
        for node in root.iter():
            if _local_name(node.tag) != 'item':
                continue
            data = {}
            for child in node:
                data[_local_name(child.tag)] = (child.text or '').strip()

            title = data.get('title') or ''
            link = data.get('link') or ''
            guid = data.get('guid') or link or title
            if not guid:
                continue

            msgs = [f'【{title}】橘鸦AI早报\n{link}']
            encoded = data.get('encoded') or ''
            if encoded:
                msgs.extend(cls._extract_news(encoded))
            items.append(Item(idx=guid, content=msgs))
        return items

    @classmethod
    def has_pushed_today(cls) -> bool:
        """今天这一期是否已经推送过。依据持久化记录判断，重启后依然有效。"""
        return bool(cls.last_push_date) and cls.last_push_date == cls.today_str()

    @staticmethod
    def today_str() -> str:
        return time.strftime('%Y-%m-%d')

    @classmethod
    async def fetch(cls) -> List[Item]:
        """拉取 feed 并刷新 item_cache，供手动查询使用。

        60 秒窗口内的重复调用直接复用缓存，不重复请求。
        """
        cls._load_state()
        now = time.monotonic()
        if now - cls.last_refresh_ts < cls.REFRESH_WINDOW:
            return cls.item_cache
        cls.last_refresh_ts = now
        try:
            resp = await cls.get_response()
            items = await cls.get_items(resp)
        except Exception:
            cls.last_refresh_ts = 0.0
            raise
        cls.item_cache = items
        return items

    @classmethod
    async def refresh(cls) -> List[Item]:
        """定时推送入口，返回值即本次要推送的条目，至多 MAX_PUSH_ITEMS 条。

        当天已推送过则直接返回，不再请求（一天只有一期）。
        拉取到的全部条目都会登记进 seen_guids，每期至多外发一次，
        避免广播部分失败后下次轮询重复刷屏（代价是失败的那期不重试）。
        """
        cls._load_state()
        if cls.has_pushed_today():
            return []
        items = await cls.fetch()
        if not items:
            return []

        if not cls.seen_guids:
            # 全新安装：整份 feed 只作基线，仅补推最新一期，存量历史不外发
            pushed = items[:cls.MAX_PUSH_ITEMS]
            cls._mark_seen(items, pushed)
            hoshino.logger.info(
                f'{cls.src_name} 首次运行，登记 {len(items)} 期基线，'
                f'仅推送最新一期 {cls.item_date(pushed[0])}'
            )
            return pushed

        seen = set(cls.seen_guids)
        updates = [i for i in items if i.idx not in seen]
        if not updates:
            return []

        dropped = updates[cls.MAX_PUSH_ITEMS:]
        if dropped:
            hoshino.logger.warning(
                f'{cls.src_name} 积压 {len(updates)} 期更新，本次仅推送最新一期 '
                f'{cls.item_date(updates[0])}，其余 {len(dropped)} 期已登记跳过：'
                f'{", ".join(cls.item_date(i) for i in dropped)}'
            )
        pushed = updates[:cls.MAX_PUSH_ITEMS]
        cls._mark_seen(items, pushed)
        return pushed

    @classmethod
    def item_date(cls, item: Item) -> str:
        """从条目标题提取日期串，如 '2026-09-16'；解析失败返回空串。"""
        m = re.search(r'\d{4}-\d{2}-\d{2}', (item.content[0] if item.content else ''))
        return m.group(0) if m else ''

    @staticmethod
    def _chunk(parts: List[str], max_len: int = 3000) -> List[str]:
        chunks = []
        cur = ''
        for p in parts:
            if cur and len(cur) + len(p) + 2 > max_len:
                chunks.append(cur)
                cur = p
            else:
                cur = f'{cur}\n\n{p}' if cur else p
        if cur:
            chunks.append(cur)
        return chunks

    @classmethod
    def format_items(cls, items) -> List[str]:
        msgs = []
        for item in items:
            msgs.extend(cls._chunk(item.content))
        return msgs
