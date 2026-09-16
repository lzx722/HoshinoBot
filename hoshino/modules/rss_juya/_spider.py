import re
import time
from dataclasses import dataclass
from typing import List, Union
from xml.etree import ElementTree

from bs4 import BeautifulSoup
from hoshino import aiorequests

CONTENT_NS = 'http://purl.org/rss/1.0/modules/content/'


REQUEST_TIMEOUT = 60


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
    last_refresh_ts = 0.0
    REFRESH_WINDOW = 60

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
    def has_today(cls) -> bool:
        """缓存里最新一条的日期是否已等于本地今天。"""
        if not cls.item_cache:
            return False
        return cls.item_date(cls.item_cache[0]) == cls.today_str()

    @staticmethod
    def today_str() -> str:
        return time.strftime('%Y-%m-%d')

    @classmethod
    async def refresh(cls) -> List[Item]:
        """统一拉取入口。

        当日早报已入库则不再请求（一天只有一期）；
        否则受 60 秒全局窗口限制，窗口内不重复请求。
        """
        if cls.has_today():
            return []
        now = time.monotonic()
        if now - cls.last_refresh_ts < cls.REFRESH_WINDOW:
            return []
        cls.last_refresh_ts = now
        try:
            resp = await cls.get_response()
            items = await cls.get_items(resp)
        except Exception:
            cls.last_refresh_ts = 0.0
            raise
        updates = [i for i in items if i not in cls.item_cache]
        if updates:
            cls.item_cache = items
        return updates

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
