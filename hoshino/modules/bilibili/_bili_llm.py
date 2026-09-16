import asyncio
import re
from typing import Optional, Dict, Any, Tuple

import aiohttp

from . import _config

_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL)

# 无效字幕拦截哨兵：LLM 判定字幕与标题明显无关/为广告占位时，仅输出该标记
INVALID_SENTINEL = "[NO_VALID_CONTENT]"

_MUSIC_SYMBOL_RE = re.compile(r"[♪♫♬♩]")
_MUSIC_BRACKET_RE = re.compile(
    r"[（(【\[]\s*(?:纯|轻快|欢快|舒缓|动感|背景|激昂|悠扬)?\s*(?:音乐|伴奏|BGM)\s*[）)】\]]|\[\s*Music\s*\]",
    re.IGNORECASE,
)

SUMMARY_SYSTEM_CONCISE = (
    "你是资深的内容速读与要点提炼专家。你擅长以极高信息密度提炼音视频的核心结论，"
    "单条要点极其干练利落（一句话直击要害，严禁膨胀为段落），并根据内容动态裁剪模块，绝不无中生有。\n"
    "请直接输出结构紧凑、条理清晰的标准文本格式，严禁包裹整篇回答的代码块。"
)

SUMMARY_SYSTEM_DETAILED = (
    "你是资深的音视频内容深度提炼与结构化总结专家。你擅长主次分明的结构化深度拆解："
    "深入剖析核心机制、因果逻辑与实操步骤；对对比/规则/数据类信息自适应使用 Markdown 表格呈现；"
    "对次要/背景信息严格以单句概括，并自动裁剪无关模块。\n"
    "请直接输出层次分明、排版优美、信息结构化的标准文本深度报告，不要输出任何多余的寒暄或包裹整篇文档的代码块。"
)


def strip_leading_title(text: str) -> str:
    """去除总结文本开头可能存在的总标题行（如 # 标题），避免与消息头重复"""
    text = text.strip()
    return re.sub(r"^#\s+[^\n]*\n*", "", text).strip()


def _clean_markdown_response(raw: str) -> str:
    """清洗大模型回复文本：去除思考标签、外层代码块包裹及开头重复总标题"""
    text = _THINK_RE.sub("", raw).strip()
    if text.startswith("```") and text.endswith("```"):
        lines = text.splitlines()
        if len(lines) >= 2 and lines[0].startswith("```") and lines[-1].strip() == "```":
            text = "\n".join(lines[1:-1]).strip()
    return strip_leading_title(text)


def _is_music_segment(seg: str) -> bool:
    """判断单个字幕切片是否为音乐标记（如 ♪ 音乐 ♪、【音乐】）"""
    cleaned = _MUSIC_SYMBOL_RE.sub("", seg)
    cleaned = _MUSIC_BRACKET_RE.sub("", cleaned)
    cleaned = cleaned.strip(" \t，。、,.~～-—·")
    return len(cleaned) == 0 or (len(cleaned) <= 2 and cleaned in ("音乐", "伴奏"))


def detect_invalid_subtitle(text: str, min_effective: int = 60) -> Optional[str]:
    """本地预检字幕有效性：纯音乐标记或有效文本过少时直接拦截，不消耗 LLM Token。

    返回 None 表示通过；否则返回拦截原因（中文字符与英文单词均按 1 计）。
    """
    if not text or not text.strip():
        return "字幕内容为空"
    segs = [s for s in re.split(r"[\s\n]+", text) if s.strip()]
    if not segs:
        return "字幕内容为空"
    music_segs = [s for s in segs if _is_music_segment(s)]

    # 全文剥离音乐标记与音符，避免未切开的括号嵌入或无空格段落影响
    cleaned_full = _MUSIC_SYMBOL_RE.sub(" ", text)
    cleaned_full = _MUSIC_BRACKET_RE.sub(" ", cleaned_full)

    effective_segs = [s for s in segs if not _is_music_segment(s)]
    effective = " ".join(effective_segs)

    cjk_segs = sum(1 for ch in effective if "\u4e00" <= ch <= "\u9fff")
    cjk_full = sum(1 for ch in cleaned_full if "\u4e00" <= ch <= "\u9fff")
    cjk = min(cjk_segs, cjk_full)

    en_words = len(re.findall(r"[A-Za-z]+", effective))
    score = cjk + en_words

    if score < min_effective:
        total_music_len = sum(len(s) for s in music_segs) + len(_MUSIC_SYMBOL_RE.findall(text)) * 2
        if total_music_len >= len(effective.strip()) or (music_segs and score < 15):
            return "字幕几乎全为音乐标记"
        return f"有效文本过少（约{score}字）"
    return None


def _build_concise_prompt(title: str, uploader: str, text_content: str) -> str:
    """构建精炼速读 Prompt（5~7 条硬核要点，杜绝冗余段落膨胀）"""
    header = f"【视频标题】：{title}（UP主：{uploader}）" if uploader else f"【视频标题】：{title}"
    return f"""{header}
【视频字幕全文】：
{text_content}

---

请基于上述视频字幕，输出一份直击核心、干练紧凑的速读总结。

【提炼与裁剪原则】：
1. **禁止输出总标题**：外层消息已展示视频标题，正文严禁输出任何形式的总标题或一级标题（不要输出 # 标题），直接从【> ⚡ 一句话速览】开始输出；
2. **单条要点字数硬约束**：每条要点严格限制在 1~2 句话内（25~50 字），必须为【明确结论 + 关键数据/直接依据】，严禁膨胀为多行小段落；
3. **动态裁剪总则**：除一句话速览与核心要点外，标注（按需）的模块仅当存在关键限制条件、关键对比数据等明确内容时输出（重要补充限 1~2 条），无则整体省略，严禁硬凑。

【最高优先级拦截准则】：
1. 仅在以下两种异常情况下输出 {INVALID_SENTINEL}，禁止输出其他任何字符：
   - 【恶意引流/占位】：字幕通篇为纯欺诈引流（如博彩、兼职刷单等广告）、乱码、无意义字符或机械重复；
   - 【主题客体严重互斥（字幕串台）】：视频标题含有【明确的特定具体客体/专属领域】（如特定人物、具体产品、专门学科或特定专属事件），而字幕全文【完全聚焦于另一个毫不相干的独立具体实体】，全文对标题所指实体及其所属领域零提及、零关联（疑似平台字幕调度错误或占位串台）。
2. 以下情况均属正常视频生态，【严禁拦截，必须正常总结】：
   - 标题为抽象情绪、感叹或非特指梗词（如《震惊》《蚌埠住了》《我真服了》《这不合理》等未指定具体专属客体的标题）；
   - 正文包含商业赞助、广告带货、漫谈发散或题外闲聊，但仍有实质表达内容；
   - UP 主采用隐喻、比喻或反讽手法。

【按以下结构直接输出】：

> ⚡ 一句话速览：1~2 句话直击视频最核心的定性结论与核心观点。

💡 核心要点速览（照此样式提炼 5~7 条）：
- 【要点核心词】：明确论断，紧跟支撑该结论的关键数据或直接依据。

🔑 重要补充与关键前提（按需输出，无则完全省略本模块）：
- 前提条件 / 限制范围：方案生效的具体前提、适用场景或限制条件（如有）。

🎯 UP 主核心建议与结论（按需输出，无则省略）：
- 推荐方案 / 最优解：UP 主推荐的最优选择或实操建议。
- 避坑提示：核心避坑点或不推荐的做法（如有）。
"""


def _build_detailed_prompt(title: str, uploader: str, text_content: str) -> str:
    """构建详尽深度版 Prompt（去重复层 + 表格化触发 + 去模式化 + 动态裁剪）"""
    header = f"【视频标题】：{title}（UP主：{uploader}）" if uploader else f"【视频标题】：{title}"
    return f"""{header}
【视频字幕全文】：
{text_content}

---

请基于上述视频全文，输出一份主次分明、深度充实且信息结构化的深度总结报告。

【提炼与排版原则】：
1. **禁止输出总标题**：外层消息已展示视频标题，正文严禁输出任何形式的总标题或一级标题（不要输出 # 标题），直接从【> 📌 视频主旨与背景概览】开始输出；
2. **去冗余，去重复层**：无需前置重复罗列要点列表，主旨概览后直接深入各主题拆解；
3. **规则/数据/对比自适应表格化**：凡涉及【卡池抽取优先级 / 数值与配置对比 / 命座收益 / 规则与奖励明细 / 方案优劣对比】等信息，必须优先使用标准 Markdown 表格呈现，严禁段落堆砌；
4. **深入解析与去模式化**：各主题小节根据内容自然展开，不生搬硬套死板子项；
5. **次要信息单句化**：背景铺垫或次要旁支统一归集，每点严格用一句话高度概括；
6. **动态模块裁剪**：标注（按需）的模块若视频中无相关内容，必须彻底裁撤，不留空话。

【最高优先级拦截准则】：
1. 仅在以下两种异常情况下输出 {INVALID_SENTINEL}，禁止输出其他任何字符：
   - 【恶意引流/占位】：字幕通篇为纯欺诈引流（如博彩、兼职刷单等广告）、乱码、无意义字符或机械重复；
   - 【主题客体严重互斥（字幕串台）】：视频标题含有【明确的特定具体客体/专属领域】（如特定人物、具体产品、专门学科或特定专属事件），而字幕全文【完全聚焦于另一个毫不相干的独立具体实体】，全文对标题所指实体及其所属领域零提及、零关联（疑似平台字幕调度错误或占位串台）。
2. 以下情况均属正常视频生态，【严禁拦截，必须正常总结】：
   - 标题为抽象情绪、感叹或非特指梗词（如《震惊》《蚌埠住了》《我真服了》《这不合理》等未指定具体专属客体的标题）；
   - 正文包含商业赞助、广告带货、漫谈发散或题外闲聊，但仍有实质表达内容；
   - UP 主采用隐喻、比喻或反讽手法。

【按以下结构直接输出】：

> 📌 视频主旨与背景概览：用 2~3 句话交代视频核心主题、讨论背景及最核心的总体定调。

## 📋 重点内容深度解析（按主题展开）

### 1. 【核心主题/模块标题】
- 核心逻辑与机制：深入阐述该部分的主要观点、设计逻辑或底层机制。
- 具体细节与论据：详细交代具体数值对比、实操步骤、因果推导或测试结论。
- 实操/方案建议：具体的做法、配置选择或操作技巧。

### 2. 【核心主题/模块标题】
- ...

## 📊 核心数据与规则对比表（按需输出，若已在上方章节表格化或无数据则省略）
| 维度 / 对象 | 核心表现 / 机制规则 | 推荐度 / 评价 | 关键数值 / 备注 |
|---|---|---|---|

## 🎯 UP 主深度实操建议与避坑指南（按需输出，无则省略）
- 推荐方案 / 最优解：UP 主推荐的最优选择、优先级或选型建议。
- 避坑要点：容易踩坑的误区、不推荐的做法或注意事项。

## 📌 次要信息与补充背景速览（按需，每点一句话）

## 🏷️ 关键术语与概念字典（按需，一句话通俗解释视频中的专有名词，无则省略）
"""


async def generate_video_summary(
    title: str,
    uploader: str,
    text_content: str,
    style: str = "concise",
) -> Tuple[Optional[str], Optional[str]]:
    """调用 OpenAI 兼容接口（如 DeepSeek）生成总结
    
    style 可选:
      - "concise": 精炼速读版（5~7 条硬核要点，约 300~500 字）
      - "detailed": 详尽深度版（深度小节拆解、表格化对比，约 1000~2000 字）
      
    返回: (summary_text, error_message)
    llm_err 为 "INVALID_SUBTITLE:<原因>" 时表示字幕被拦截（无效/与标题不符），
    上层应向用户友好提示而非按失败报错，且不应写入缓存。
    """
    # 本地预检：纯音乐标记/有效文本过少的字幕直接拦截，不消耗 LLM Token
    invalid_reason = detect_invalid_subtitle(text_content)
    if invalid_reason:
        return None, f"INVALID_SUBTITLE:{invalid_reason}"

    try:
        llm_cfg = _config.get_llm_config(style)
    except ValueError as e:
        # 配置填错不静默纠正，直接在群内报出来让用户自己修
        return None, f"配置错误: {e}"
    api_key = llm_cfg.get("api_key")
    if not api_key:
        return None, "未配置 LLM API Key，请在 config.json 中的 llm 节点填写 api_key。"

    base_url = llm_cfg.get("base_url", "https://api.deepseek.com").rstrip("/")
    if not base_url.endswith("/chat/completions"):
        completions_url = f"{base_url}/chat/completions"
    else:
        completions_url = base_url

    model = llm_cfg.get("model", "deepseek-v4-flash")
    temperature = llm_cfg.get("temperature", 0.3)
    timeout = llm_cfg.get("timeout", 300)
    enable_thinking = llm_cfg.get("enable_thinking")

    # 长度兜底防护：当字幕超过 3.8 万字（约 2.6 万~3 万 tokens）时做安全截断，
    # 确保稳定落在主流 LLM 厂商的 32K 低费率阶梯内，并杜绝极端超长视频超时
    MAX_SUBTITLE_CHARS = 38000
    if len(text_content) > MAX_SUBTITLE_CHARS:
        text_content = (
            text_content[:MAX_SUBTITLE_CHARS]
            + "\n\n[注：字幕全文过长，已自动截取前 3.8 万字进行总结以避免超时与费用激增]"
        )

    if style == "detailed":
        system_prompt = SUMMARY_SYSTEM_DETAILED
        user_prompt = _build_detailed_prompt(title, uploader, text_content)
    else:
        system_prompt = SUMMARY_SYSTEM_CONCISE
        user_prompt = _build_concise_prompt(title, uploader, text_content)

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": temperature,
    }
    # enable_thinking 为 null 时不发该字段；qwen3 系（如百炼）默认开思考，
    # 显式 false 可跳过隐藏思考 Token，大幅降低长字幕总结的耗时
    if enable_thinking is not None:
        payload["enable_thinking"] = enable_thinking

    try:
        async with aiohttp.ClientSession(headers=headers) as session:
            async with session.post(
                completions_url,
                json=payload,
                timeout=aiohttp.ClientTimeout(total=timeout)
            ) as resp:
                if resp.status != 200:
                    err_text = await resp.text()
                    return None, f"LLM 接口返回错误 (HTTP {resp.status}): {err_text[:120]}"
                res_json = await resp.json()
                choices = res_json.get("choices") or []
                if not choices:
                    return None, "LLM 返回内容为空"
                msg = choices[0].get("message") or {}
                content = msg.get("content") or msg.get("reasoning_content") or ""
                summary = _clean_markdown_response(content)
                clean_strip = summary.strip()
                first_line = clean_strip.splitlines()[0].strip() if clean_strip else ""
                is_sentinel = (
                    clean_strip == INVALID_SENTINEL
                    or clean_strip.startswith(INVALID_SENTINEL)
                    or first_line.strip("`*# ") == INVALID_SENTINEL
                )
                if is_sentinel:
                    return None, "INVALID_SUBTITLE:字幕内容与视频标题不符或为广告占位文本"
                if not clean_strip:
                    return None, "LLM 提炼总结内容为空"
                return summary, None
    except asyncio.TimeoutError:
        return None, f"大模型响应超时（超过 {timeout} 秒），可调大 config.json 中 llm.timeout 后重试"
    except aiohttp.ClientConnectorError as e:
        return None, f"连接大模型服务器失败: {e}"
    except Exception as e:
        err_msg = str(e).strip() or type(e).__name__
        return None, f"调用大模型异常: {err_msg}"
