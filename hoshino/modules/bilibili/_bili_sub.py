import hashlib
import re
import time
from typing import Optional, Dict, Any, List, Tuple
from urllib.parse import urlparse

import aiohttp

from . import _config

API_BASE = "https://api.bilibili.com"
SUBTITLE_HOSTS = {"aisubtitle.hdslb.com", "subtitle.bilibili.com"}

# 与 summary.py 的触发正则保持一致的大小写不敏感匹配，避免小写 bv 链接能触发却提取不到
BILIBILI_BV_RE = re.compile(r"BV[A-Za-z0-9]{10}", re.IGNORECASE)
B23_RE = re.compile(r"(?:https?://)?b23\.tv/[A-Za-z0-9]+", re.IGNORECASE)


def _normalize_bvid(raw: str) -> str:
    """BV 号主体为 base58、大小写敏感，仅将前缀统一为大写 BV"""
    return "BV" + raw[2:] if raw[:2].upper() == "BV" else raw
WBI_KEY_RE = re.compile(r"([^/_]+)(?=\.[a-zA-Z]+$)")

MIXIN_KEY_ENC_TAB = [
    46, 47, 18, 2, 53, 8, 23, 32, 15, 50, 10, 31, 58, 3, 45, 35, 27, 43, 5,
    49, 33, 9, 42, 19, 29, 28, 14, 39, 12, 38, 41, 13, 37, 48, 7, 16, 24, 55,
    40, 61, 26, 17, 0, 1, 60, 51, 30, 4, 22, 25, 54, 21, 56, 59, 6, 63, 57,
    62, 11, 36, 20, 34, 44, 52,
]


async def resolve_bvid_from_text(text: str) -> Optional[str]:
    """从消息文本中提取 BV 号，支持纯 BV 号、网页链接及 b23.tv 短链重定向"""
    # 1. 优先直接匹配 BV 号
    direct_match = BILIBILI_BV_RE.search(text)
    if direct_match:
        return _normalize_bvid(direct_match.group(0))

    # 2. 检查 b23.tv 短链（协议头可选）
    b23_match = B23_RE.search(text)
    if b23_match:
        short_url = b23_match.group(0)
        if not short_url.lower().startswith("http"):
            short_url = "https://" + short_url
        try:
            headers = _config.get_headers()
            async with aiohttp.ClientSession(headers=headers) as session:
                async with session.get(short_url, allow_redirects=True, timeout=aiohttp.ClientTimeout(total=8)) as resp:
                    target_url = str(resp.url)
                    match = BILIBILI_BV_RE.search(target_url)
                    if match:
                        return _normalize_bvid(match.group(0))
        except Exception:
            pass

    return None


def _wbi_sign(params: Dict[str, Any], mixin_key: str) -> str:
    """计算 WBI 签名哈希值"""
    filtered = {
        k: str(v).translate(str.maketrans("", "", "!'()*"))
        for k, v in params.items()
    }
    query = "&".join(f"{k}={filtered[k]}" for k in sorted(filtered))
    return hashlib.md5((query + mixin_key).encode("utf-8")).hexdigest()


async def _get_wbi_mixin_key(session: aiohttp.ClientSession) -> Optional[str]:
    """从 nav 接口动态提取 WBI mixin key"""
    try:
        async with session.get(f"{API_BASE}/x/web-interface/nav", timeout=aiohttp.ClientTimeout(total=8)) as resp:
            data = await resp.json()
            if data.get("code") != 0:
                return None
            wbi_img = (data.get("data") or {}).get("wbi_img") or {}
            img_url = wbi_img.get("img_url", "")
            sub_url = wbi_img.get("sub_url", "")
            img_match = WBI_KEY_RE.search(img_url)
            sub_match = WBI_KEY_RE.search(sub_url)
            if not img_match or not sub_match:
                return None
            img_key = img_match.group(1)
            sub_key = sub_match.group(1)
            mixed = img_key + sub_key
            return "".join(mixed[i] for i in MIXIN_KEY_ENC_TAB)
    except Exception:
        return None


def _pick_best_subtitle(subtitles: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """从候选字幕中挑选最优语言与版本（中文优先，手动 CC > AI）"""
    lang_rank = {
        "zh-hans": 0, "zh-cn": 0, "zh": 0,
        "zh-hant": 1, "zh-tw": 1, "zh-hk": 1,
        "en": 2,
    }

    def rank_key(entry: Dict[str, Any]) -> Tuple[int, int]:
        lan = str(entry.get("lan") or "").lower()
        is_ai = lan.startswith("ai-")
        base = lan[3:] if is_ai else lan
        return (lang_rank.get(base, 3), 1 if is_ai else 0)

    candidates = [s for s in subtitles if str(s.get("subtitle_url") or "").strip()]
    if not candidates:
        return None
    return min(candidates, key=rank_key)


def _is_cjk(char: str) -> bool:
    """判断是否为汉字或中文字符"""
    if not char:
        return False
    cp = ord(char)
    return (
        0x4E00 <= cp <= 0x9FFF
        or 0x3400 <= cp <= 0x4DBF
        or 0x3000 <= cp <= 0x303F
        or 0xFF00 <= cp <= 0xFFEF
        or 0x20000 <= cp <= 0x2A6DF
    )


def _join_segments(segments: List[str]) -> str:
    """智能连接语句切片：中文紧凑拼接，英文单词间留空格"""
    if not segments:
        return ""
    joined: List[str] = []
    cjk_punct = "，。！？；：、“”‘’（）《》【】…—～·"
    for seg in segments:
        text = seg.strip()
        if not text:
            continue
        if not joined:
            joined.append(text)
            continue
        prev = joined[-1]
        prev_last = prev[-1] if prev else ""
        curr_first = text[0] if text else ""
        if (_is_cjk(prev_last) and _is_cjk(curr_first)) or prev_last in cjk_punct or curr_first in cjk_punct:
            joined.append(text)
        else:
            joined.append(" " + text)
    return "".join(joined)


def _cues_to_text(body: List[Dict[str, Any]], pause_threshold_s: float = 3.0) -> str:
    """将字幕切片列表规整合并为纯文本文稿（支持中英文智能拼接与停顿分段）"""
    if not body:
        return ""

    paragraphs: List[str] = []
    current_para: List[str] = []
    last_end = 0.0

    for item in body:
        text = str(item.get("content", "")).strip()
        if not text:
            continue
        start = float(item.get("from", 0.0))
        end = float(item.get("to", start))

        # 停顿超时或累积长度足够，断段换行
        if current_para and (start - last_end > pause_threshold_s or sum(len(s) for s in current_para) > 300):
            paragraphs.append(_join_segments(current_para))
            current_para = []

        current_para.append(text)
        last_end = max(end, start)

    if current_para:
        paragraphs.append(_join_segments(current_para))

    return "\n\n".join(p for p in paragraphs if p.strip())


async def fetch_video_subtitle_text(bvid: str) -> Tuple[Optional[Dict[str, Any]], Optional[str], Optional[str]]:
    """抓取 B 站视频字幕文稿
    
    返回: (video_info, full_text, error_message)
    若无字幕: error_message 为 "NO_SUBTITLE"
    """
    headers = _config.get_headers()

    try:
        async with aiohttp.ClientSession(headers=headers) as session:
            # 1. 获取视频基本信息
            async with session.get(
                f"{API_BASE}/x/web-interface/view",
                params={"bvid": bvid},
                timeout=aiohttp.ClientTimeout(total=10)
            ) as view_resp:
                view_data = await view_resp.json()
                if view_data.get("code") != 0 or not view_data.get("data"):
                    return None, None, f"获取视频信息失败: {view_data.get('message', '未知错误')}"
                v = view_data["data"]
                video_info = {
                    "bvid": bvid,
                    "title": v.get("title", ""),
                    "uploader": (v.get("owner") or {}).get("name", ""),
                    "duration": v.get("duration", 0),
                    "cid": v.get("cid", 0),
                }

            cid = video_info["cid"]
            if not cid:
                # 若 view 没带 cid，从 pagelist 获取第一 P
                async with session.get(
                    f"{API_BASE}/x/player/pagelist",
                    params={"bvid": bvid},
                    timeout=aiohttp.ClientTimeout(total=8)
                ) as page_resp:
                    page_data = await page_resp.json()
                    pages = page_data.get("data") or []
                    if not pages:
                        return video_info, None, "获取视频分 P 列表失败"
                    cid = int(pages[0].get("cid", 0))
                    video_info["cid"] = cid

            # 2. 获取字幕列表（WBI 优先，普通接口降级）
            subtitles: List[Dict[str, Any]] = []
            mixin_key = await _get_wbi_mixin_key(session)
            if mixin_key:
                wbi_params: Dict[str, Any] = {"bvid": bvid, "cid": cid, "wts": int(time.time())}
                wbi_params["w_rid"] = _wbi_sign(wbi_params, mixin_key)
                try:
                    async with session.get(
                        f"{API_BASE}/x/player/wbi/v2",
                        params=wbi_params,
                        timeout=aiohttp.ClientTimeout(total=10)
                    ) as wbi_resp:
                        wbi_data = await wbi_resp.json()
                        subtitles = ((wbi_data.get("data") or {}).get("subtitle") or {}).get("subtitles") or []
                except Exception:
                    pass

            if not subtitles:
                try:
                    async with session.get(
                        f"{API_BASE}/x/player/v2",
                        params={"bvid": bvid, "cid": cid},
                        timeout=aiohttp.ClientTimeout(total=8)
                    ) as p2_resp:
                        p2_data = await p2_resp.json()
                        subtitles = ((p2_data.get("data") or {}).get("subtitle") or {}).get("subtitles") or []
                except Exception:
                    pass

            if not subtitles:
                return video_info, None, "NO_SUBTITLE"

            # 3. 挑选最优字幕并下载
            best_sub = _pick_best_subtitle(subtitles)
            if not best_sub or not best_sub.get("subtitle_url"):
                return video_info, None, "NO_SUBTITLE"

            sub_url = str(best_sub["subtitle_url"])
            if sub_url.startswith("//"):
                sub_url = "https:" + sub_url

            parsed = urlparse(sub_url)
            if parsed.hostname not in SUBTITLE_HOSTS:
                return video_info, None, "字幕下载地址不受信任"

            async with session.get(sub_url, timeout=aiohttp.ClientTimeout(total=10)) as sub_resp:
                raw_json = await sub_resp.json(content_type=None)
                body = raw_json.get("body") or []
                full_text = _cues_to_text(body)

                if not full_text.strip():
                    return video_info, None, "NO_SUBTITLE"

                return video_info, full_text, None

    except Exception as e:
        return None, None, f"抓取字幕异常: {e}"
