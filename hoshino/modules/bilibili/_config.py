import copy
import json
import logging
import os
import time
from typing import Dict, Any, Optional

logger = logging.getLogger(__name__)

CONFIG_PATH = os.path.join(os.path.dirname(__file__), 'config.json')
DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)

# 默认配置：新增配置项时在此追加，旧用户的 config.json 会在下次加载时自动补全
DEFAULT_CONFIG: Dict[str, Any] = {
    "sessdata": "",
    "bili_jct": "",
    "dedeuserid": "",
    "expires_at": 0,
    "user_agent": DEFAULT_UA,
    "llm": {
        "api_key": "",
        "base_url": "https://api.deepseek.com",
        "model": "deepseek-v4-flash",
        "temperature": 0.3,
        # 各总结模式独立配置（键缺失时使用代码内置默认 timeout=300 / enable_thinking=False，
        # 且自动补全机制通常会在加载时把缺失键写回；值非法则直接报错，不做静默纠正）
        # detailed.enable_thinking 实测：开思考约 190~220s/万字报告，关思考约 72s/4300 字，
        # 默认关以保证超时余量；需要更深报告可改 true 并适当调大 timeout
        "concise": {"timeout": 120, "enable_thinking": False},
        "detailed": {"timeout": 300, "enable_thinking": False},
    },
}

# 视频总结磁盘持久化缓存目录
CACHE_DIR = os.path.expanduser("~/.hoshino/bilibili_summary_cache/")
os.makedirs(CACHE_DIR, exist_ok=True)


def _merge_missing(defaults: Dict[str, Any], cfg: Dict[str, Any]) -> Dict[str, Any]:
    """以 defaults 为基底深合并 cfg（cfg 的同名键优先），用于给旧配置补全新增键"""
    merged = copy.deepcopy(defaults)
    for key, value in cfg.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _merge_missing(merged[key], value)
        else:
            merged[key] = value
    return merged


def load_config() -> Dict[str, Any]:
    """读取模块配置文件

    - 文件不存在（如新电脑部署）：按 DEFAULT_CONFIG 自动生成一份并提示填写；
    - 文件缺少新增默认键：自动补全写回，保证旧配置文件向后兼容；
    - 文件解析失败：本次以默认配置运行，且不覆盖原文件（保留用户手工修复的机会）。
    """
    if not os.path.exists(CONFIG_PATH):
        save_config(copy.deepcopy(DEFAULT_CONFIG))
        logger.warning(f"未找到配置文件，已自动生成 {CONFIG_PATH}，请填写 llm.api_key 后重启生效。")
        return copy.deepcopy(DEFAULT_CONFIG)

    try:
        with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
            cfg = json.load(f)
    except Exception as e:
        logger.warning(f"配置文件解析失败({e})，本次以默认配置运行（不会覆盖原文件）。")
        return copy.deepcopy(DEFAULT_CONFIG)

    merged = _merge_missing(DEFAULT_CONFIG, cfg if isinstance(cfg, dict) else {})
    if merged != cfg:
        save_config(merged)
        logger.info(f"配置文件缺少新增默认键，已自动补全写回 {CONFIG_PATH}")
    return merged


def save_config(cfg: Dict[str, Any]) -> None:
    """写入模块配置文件"""
    with open(CONFIG_PATH, 'w', encoding='utf-8') as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


def get_credentials() -> Dict[str, Any]:
    """获取当前的登录凭据"""
    cfg = load_config()
    return {
        "sessdata": cfg.get("sessdata", ""),
        "bili_jct": cfg.get("bili_jct", ""),
        "dedeuserid": str(cfg.get("dedeuserid", "")),
        "expires_at": cfg.get("expires_at", 0),
    }


def save_credentials(
    sessdata: str,
    bili_jct: str,
    dedeuserid: str,
    expires_at: Optional[int] = None
) -> None:
    """保存/更新凭据至配置文件"""
    cfg = load_config()
    cfg["sessdata"] = sessdata
    cfg["bili_jct"] = bili_jct
    cfg["dedeuserid"] = str(dedeuserid)
    cfg["expires_at"] = expires_at if expires_at else int(time.time() * 1000) + 30 * 24 * 60 * 60 * 1000
    save_config(cfg)


def clear_credentials() -> None:
    """清空登录凭据"""
    cfg = load_config()
    cfg["sessdata"] = ""
    cfg["bili_jct"] = ""
    cfg["dedeuserid"] = ""
    cfg["expires_at"] = 0
    save_config(cfg)


def get_cookie_str() -> str:
    """拼装 Cookie 请求头字符串"""
    creds = get_credentials()
    if not creds["sessdata"]:
        return ""
    return f"SESSDATA={creds['sessdata']}; bili_jct={creds['bili_jct']}; DedeUserID={creds['dedeuserid']}"


def get_headers(referer: str = "https://www.bilibili.com/") -> Dict[str, str]:
    """获取预设好 User-Agent、Referer 及已登录 Cookie 的通用请求头"""
    cfg = load_config()
    headers = {
        "User-Agent": cfg.get("user_agent", DEFAULT_UA),
        "Referer": referer,
    }
    cookie = get_cookie_str()
    if cookie:
        headers["Cookie"] = cookie
    return headers


# 代码内置默认值：仅当 config.json 中对应键缺失时使用
_DEFAULT_TIMEOUT = 300


def get_llm_config(style: Optional[str] = None) -> Dict[str, Any]:
    """获取 LLM 调用配置

    timeout 与 enable_thinking 按总结风格从 llm.<style> 子节点读取；
    配置值非法时抛出 ValueError（消息中含节点路径），由调用方转为群内错误提示。
    """
    cfg = load_config()
    llm = cfg.get("llm", {})
    style_cfg = llm.get(style) if isinstance(llm.get(style), dict) else {}
    style_label = f"llm.{style}" if style else "llm"
    # enable_thinking: true/false 显式下发该字段；null 表示不发字段、由服务端决定默认行为
    thinking_raw = style_cfg.get("enable_thinking", False)
    try:
        timeout = int(float(style_cfg.get("timeout", _DEFAULT_TIMEOUT)))
    except (TypeError, ValueError):
        raise ValueError(f"{style_label}.timeout 配置无效: {style_cfg.get('timeout')!r}，应为正整数秒数")
    if timeout <= 0:
        raise ValueError(f"{style_label}.timeout 配置无效: {style_cfg.get('timeout')!r}，应为正整数秒数")
    try:
        temperature = float(llm.get("temperature", 0.3))
    except (TypeError, ValueError):
        raise ValueError(f"llm.temperature 配置无效: {llm.get('temperature')!r}，应为数字")
    return {
        "api_key": llm.get("api_key", "").strip(),
        "base_url": llm.get("base_url", "https://api.deepseek.com").strip().rstrip("/"),
        "model": llm.get("model", "deepseek-v4-flash").strip(),
        "temperature": temperature,
        "timeout": timeout,
        "enable_thinking": None if thinking_raw is None else bool(thinking_raw),
    }


def get_summary_cache(bvid: str, style: str = "concise") -> Optional[Dict[str, Any]]:
    """读取指定 BV 号和风格的磁盘持久化总结缓存"""
    cache_file = os.path.join(CACHE_DIR, f"{bvid}_{style}.json")
    if not os.path.exists(cache_file):
        # 兼容此前未带风格后缀的历史缓存文件
        if style == "concise":
            legacy_file = os.path.join(CACHE_DIR, f"{bvid}.json")
            if os.path.exists(legacy_file):
                cache_file = legacy_file
            else:
                return None
        else:
            return None
    try:
        with open(cache_file, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def save_summary_cache(
    bvid: str,
    title: str,
    uploader: str,
    summary: str,
    style: str = "concise"
) -> None:
    """写入指定 BV 号和风格的磁盘持久化总结缓存"""
    cache_file = os.path.join(CACHE_DIR, f"{bvid}_{style}.json")
    payload = {
        "bvid": bvid,
        "title": title,
        "uploader": uploader,
        "summary": summary,
        "style": style,
        "created_at": int(time.time()),
    }
    try:
        with open(cache_file, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
    except Exception:
        pass
