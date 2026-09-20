import asyncio
from collections import defaultdict
import re
from typing import List, Optional

from hoshino.service import Service
from hoshino.typing import CQEvent

from . import _config, _bili_sub, _bili_llm

sv = Service(
    'bilibili-summary',
    enable_on_default=False,
    visible=True,
    help_='''
【B站视频字幕 AI 总结服务】（群内默认关闭，群管发送「开启 bilibili-summary」启用）：
1. 默认速读版：群内发送 B 站视频链接、移动端短链（b23.tv）或 BV 号，自动生成 5~7 条精炼速读要点。
2. 详尽深度版：发送「详细总结 <BV/链接>」或「深度总结 <BV/链接>」，生成包含多小节拆解、对比表格与深度实操的详尽报告。
3. 主动指令支持：
   - 速读版：总结 / 视频总结 / b站总结 / 视频速读 / 速读总结 <链接/BV>
   - 详细版：详细总结 / 深度总结 / 详细视频总结 / b站详细总结 <链接/BV>
4. 磁盘缓存：各模式分别独立持久化缓存至磁盘，多次发送秒级秒回，不消耗额外 Token。
'''.strip()
)

# 群并发锁：同群同时仅处理 1 个总结任务
_group_locks = defaultdict(asyncio.Lock)

# 匹配 B 站视频链接、短链或 BV 号
BILI_TRIGGER_RE = re.compile(
    r"BV[A-Za-z0-9]{10}|b23\.tv/[A-Za-z0-9]+|bilibili\.com/video/BV[A-Za-z0-9]{10}",
    re.IGNORECASE
)

# 详尽深度版前缀列表
DETAILED_PREFIXES = (
    'b站详细总结', 'b站深度总结', 'b站视频详细总结', 'b站视频深度总结',
    '详细总结视频', '深度总结视频', '详细视频总结', '深度视频总结',
    '详细总结', '深度总结', '深度报告', '详细报告',
)

# 精炼速读版前缀列表
CONCISE_PREFIXES = (
    'b站总结', '总结b站', 'b站视频总结', 'b站速读总结', 'b站视频速读',
    '视频总结', '总结视频', '视频速读', '速读视频', '速读总结',
    '视频要点', '总结',
)

# 所有显式前缀汇总（用于在被动正则监听中排除，避免重复触发）
EXPLICIT_PREFIXES = DETAILED_PREFIXES + CONCISE_PREFIXES


# 单条消息字数上限：超过则切块分多条发送
SINGLE_MSG_LIMIT = 3000


def _split_text(text: str, max_len: int = SINGLE_MSG_LIMIT, first_len: Optional[int] = None) -> List[str]:
    """按行切块，每块不超过 max_len 字；first_len 可单独限制首块容量（给头部标题留位）。

    先按最大容量估算块数，再据此设定每块的软目标长度，使各块长度尽量均分，
    避免出现末尾只有几十字的碎片消息。硬上限始终生效；单行本身超长时退化为硬切，
    保证不会出现被 QQ 拒发的超长消息。
    """
    lines = text.splitlines()
    if not lines:
        return []

    cap0 = max_len if first_len is None else first_len
    units = [len(line) + 1 for line in lines]  # +1 为换行符
    total = sum(units)

    # 估算块数：首块容量受限时可能多出一块
    n = max(1, -(-total // max_len))
    if cap0 < max_len:
        n = max(n, -(-(total + max_len - cap0) // max_len))
    target = total / n

    chunks: List[str] = []
    cur: List[str] = []
    cur_len = 0
    limit = cap0

    for line, unit in zip(lines, units):
        # 单行本身超过容量：先吐出已积累内容，再把该行均分为若干不超限的片段
        if len(line) > limit:
            if cur:
                chunks.append("\n".join(cur))
                cur, cur_len = [], 0
            k = -(-len(line) // limit)
            size = -(-len(line) // k)
            for i in range(0, len(line), size):
                chunks.append(line[i:i + size])
            limit = max_len
            continue

        if cur and (cur_len + unit > limit or cur_len >= target):
            chunks.append("\n".join(cur))
            cur, cur_len = [], 0
            limit = max_len

        cur.append(line)
        cur_len += unit

    if cur:
        chunks.append("\n".join(cur))
    return [c for c in chunks if c.strip()]


async def _send_summary_reply(
    bot,
    ev: CQEvent,
    title: str,
    uploader: str,
    mode_name: str,
    summary: str,
    from_cache: bool = False,
):
    """发送视频总结结果：短文本直接发送，长文本按 3000 字切块分条发送"""
    up_str = f"（UP主：{uploader}）" if uploader else ""
    cache_str = "（来自缓存）" if from_cache else ""
    # 标题/UP主异常长时会挤占正文容量，截断头部以保证首条至少留一半容量给正文
    prefix = f"🎬 B站视频{mode_name}{cache_str}："
    tail = "\n━━━━━━━━━━━━━━\n"
    head_body = f"《{title}》{up_str}"
    room = max(20, SINGLE_MSG_LIMIT // 2 - len(prefix) - len(tail))
    if len(head_body) > room:
        head_body = head_body[:room - 1] + "…"
    header = f"{prefix}{head_body}{tail}"

    body_limit = SINGLE_MSG_LIMIT - len(header)
    if len(summary) <= body_limit:
        await bot.send(ev, header + summary)
        return

    chunks = _split_text(summary, first_len=body_limit)
    for i, chunk in enumerate(chunks):
        if i:
            await asyncio.sleep(0.6)  # 降低连续发送触发风控的概率
        await bot.send(ev, header + chunk if i == 0 else chunk)


async def _do_summary(bot, ev: CQEvent, text: str, style: str = "concise"):
    """执行视频字幕抓取与总结核心流程"""
    gid = ev.group_id
    lock = _group_locks[gid]
    if lock.locked():
        # 当前群已有正在处理的任务，直接忽略避免刷屏与堆积
        return

    async with lock:
        # 1. 提取 BV 号
        bvid = await _bili_sub.resolve_bvid_from_text(text)
        if not bvid:
            return

        mode_name = "详细总结" if style == "detailed" else "速读总结"

        # 2. 检查磁盘持久化缓存（按风格独立缓存）
        cached = _config.get_summary_cache(bvid, style=style)
        if cached and cached.get("summary"):
            title = cached.get("title", bvid)
            uploader = cached.get("uploader", "")
            summary = _bili_llm.strip_leading_title(cached["summary"])
            await _send_summary_reply(
                bot, ev, title, uploader, mode_name, summary, from_cache=True
            )
            return

        # 3. 抓取视频信息与字幕文稿
        video_info, full_text, err = await _bili_sub.fetch_video_subtitle_text(bvid)

        if err == "NO_SUBTITLE":
            title = video_info.get("title", bvid) if video_info else bvid
            await bot.send(ev, f"ℹ️ 视频《{title}》未检测到官方或 AI 字幕，跳过总结。")
            return
        elif err:
            sv.logger.warning(f"获取 B 站视频 {bvid} 字幕失败: {err}")
            title = video_info.get("title", bvid) if video_info else bvid
            await bot.send(ev, f"⚠️ 视频《{title}》字幕获取失败: {err}")
            return

        if not video_info or not full_text:
            title = video_info.get("title", bvid) if video_info else bvid
            await bot.send(ev, f"⚠️ 视频《{title}》未能提取到有效字幕文本。")
            return

        title = video_info.get("title", bvid)
        uploader = video_info.get("uploader", "")

        # 4. 调用大模型生成对应风格的总结
        summary, llm_err = await _bili_llm.generate_video_summary(
            title, uploader, full_text, style=style
        )
        if llm_err:
            if llm_err.startswith("INVALID_SUBTITLE"):
                reason = llm_err.split(":", 1)[-1]
                sv.logger.info(f"拦截视频 {bvid} 的无效字幕总结: {reason}")
                if "音乐" in reason or "伴奏" in reason:
                    hint = "该视频可能为纯音乐、PV、手书或以画面为主，暂无充分对白提炼。"
                elif "文本过少" in reason or "为空" in reason:
                    hint = "有效对白过少，可能是无声视频，或 B 站 AI 字幕仍在后台转写中，建议稍后重试。"
                else:
                    hint = "视频字幕缺乏实质讨论内容，或为引流占位文本。"
                await bot.send(
                    ev,
                    f"ℹ️ 视频《{title}》未生成总结：{reason}。\n💡 {hint}"
                )
            else:
                sv.logger.error(f"总结视频 {bvid} 失败: {llm_err}")
                await bot.send(ev, f"❌ 视频《{title}》总结失败: {llm_err}")
            return

        if not summary:
            await bot.send(ev, f"❌ 视频《{title}》大模型提炼总结结果为空，请稍后重试。")
            return

        # 剥离可能存在的开头总标题，避免与机器人消息头重复
        summary = _bili_llm.strip_leading_title(summary)

        # 5. 写入对应风格的磁盘持久化缓存
        _config.save_summary_cache(bvid, title, uploader, summary, style=style)

        # 6. 发送总结结果（支持合并转发与分段降级）
        await _send_summary_reply(
            bot, ev, title, uploader, mode_name, summary, from_cache=False
        )


@sv.on_prefix(DETAILED_PREFIXES)
async def bilibili_detailed_summary_cmd(bot, ev: CQEvent):
    """显式指令触发：详尽深度版总结"""
    text = ev.message.extract_plain_text().strip()
    if not text:
        await bot.send(ev, "请附带需要总结的 B 站视频链接或 BV 号，例如：\n「详细总结 BV1xx411c7mD」")
        return
    await _do_summary(bot, ev, text, style="detailed")


@sv.on_prefix(CONCISE_PREFIXES)
async def bilibili_concise_summary_cmd(bot, ev: CQEvent):
    """显式指令触发：精炼速读版总结"""
    text = ev.message.extract_plain_text().strip()
    if not text:
        await bot.send(ev, "请附带需要总结的 B 站视频链接或 BV 号，例如：\n「总结 BV1xx411c7mD」")
        return
    await _do_summary(bot, ev, text, style="concise")


@sv.on_rex(BILI_TRIGGER_RE)
async def bilibili_auto_summary_listener(bot, ev: CQEvent):
    """被动监听触发：默认以精炼速读版总结"""
    text = ev.message.extract_plain_text().strip()
    if not text:
        return

    # 若匹配到显式指令前缀，交由显式 handler 处理，避免重复触发
    if any(text.startswith(p) for p in EXPLICIT_PREFIXES):
        return

    await _do_summary(bot, ev, text, style="concise")
