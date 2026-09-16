import asyncio
from collections import defaultdict
import re
from typing import List, Dict, Any

from aiocqhttp.exceptions import ActionFailed

import hoshino
from hoshino import priv
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


def _build_forward_nodes(ev: CQEvent, header: str, summary: str) -> List[Dict[str, Any]]:
    """构造 QQ 合并转发节点（将超长总结按章节分块放入聊天记录卡片）"""
    bot_name = hoshino.config.NICKNAME[0] if getattr(hoshino.config, "NICKNAME", None) else "B站视频总结"
    uin = str(ev.self_id)

    # 按照二级标题 "## " 切分章节
    sections = re.split(r"\n(?=##\s+)", summary.strip())
    nodes: List[Dict[str, Any]] = []

    # 首节点：放置头部标题 + 第一部分（如主旨概览）
    first_part = sections[0].strip() if sections else summary.strip()
    nodes.append({
        "type": "node",
        "data": {
            "name": bot_name,
            "uin": uin,
            "content": f"{header}\n━━━━━━━━━━━━━━\n{first_part}"
        }
    })

    # 后续节点：放置各个二级章节
    for sec in sections[1:]:
        sec = sec.strip()
        if not sec:
            continue
        # 如果单个章节较长（>1000字），进一步按段落拆分
        if len(sec) > 1000:
            sub_chunks = [sec[i:i + 900] for i in range(0, len(sec), 900)]
            for chunk in sub_chunks:
                nodes.append({
                    "type": "node",
                    "data": {
                        "name": bot_name,
                        "uin": uin,
                        "content": chunk.strip()
                    }
                })
        else:
            nodes.append({
                "type": "node",
                "data": {
                    "name": bot_name,
                    "uin": uin,
                    "content": sec
                }
            })

    return nodes


async def _send_chunks(bot, ev: CQEvent, header: str, summary: str):
    """分段发送降级兜底（防止单条消息文本过长触发 QQ code=34 拒绝）"""
    full_text = f"{header}\n━━━━━━━━━━━━━━\n{summary}"
    if len(full_text) <= 600:
        await bot.send(ev, full_text)
        return

    lines = full_text.splitlines()
    chunk: List[str] = []
    chunk_len = 0
    for line in lines:
        if chunk_len + len(line) > 600 and chunk:
            await bot.send(ev, "\n".join(chunk))
            await asyncio.sleep(0.6)
            chunk = [line]
            chunk_len = len(line)
        else:
            chunk.append(line)
            chunk_len += len(line) + 1

    if chunk:
        await bot.send(ev, "\n".join(chunk))


async def _send_summary_reply(
    bot,
    ev: CQEvent,
    title: str,
    uploader: str,
    mode_name: str,
    summary: str,
    style: str,
    from_cache: bool = False,
):
    """安全智能发送视频总结结果：
    - 精炼速读版（短文本）：优先直接发送单条消息，失败则降级
    - 详尽深度版（长文本）：优先采用群合并转发卡片发送，彻底避开 QQ code=34（消息超长拒发）
    - 自动异常捕获与分段降级兜底
    """
    up_str = f"（UP主：{uploader}）" if uploader else ""
    cache_str = "（来自缓存）" if from_cache else ""
    header = f"🎬 B站视频{mode_name}{cache_str}：《{title}》{up_str}"
    full_reply = f"{header}\n━━━━━━━━━━━━━━\n{summary}"

    # 1. 若非详细版且字数较短，直接尝试普通单条消息发送
    if style != "detailed" and len(full_reply) < 800:
        try:
            await bot.send(ev, full_reply)
            return
        except ActionFailed as e:
            sv.logger.warning(f"单条消息发送失败({e})，准备尝试合并转发/分段降级")

    # 2. 详尽深度版（或长消息）：在群聊环境下优先采用合并转发卡片
    if getattr(ev, "group_id", None):
        try:
            nodes = _build_forward_nodes(ev, header, summary)
            await bot.send_group_forward_msg(group_id=ev.group_id, messages=nodes)
            return
        except ActionFailed as e:
            sv.logger.warning(f"合并转发发送失败({e})，准备降级为分段发送")
        except Exception as e:
            sv.logger.exception(f"合并转发未知异常: {e}")

    # 3. 兜底方案：分段普通消息发送
    try:
        await _send_chunks(bot, ev, header, summary)
    except Exception as e:
        sv.logger.exception(f"分段发送总结依然失败: {e}")
        await bot.send(ev, f"❌ 视频总结已生成，但由于平台风控或消息限制发送失败: {e}")


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
                bot, ev, title, uploader, mode_name, summary, style=style, from_cache=True
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
            bot, ev, title, uploader, mode_name, summary, style=style, from_cache=False
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
