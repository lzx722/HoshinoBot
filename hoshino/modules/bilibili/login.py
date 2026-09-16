import asyncio
from datetime import datetime
from io import BytesIO
from typing import Optional, Tuple, Dict, Any
from urllib.parse import quote

import aiohttp
from nonebot import MessageSegment
from PIL import Image

try:
    import qrcode
except ImportError:
    qrcode = None

import hoshino
from hoshino import priv, util
from hoshino.service import Service
from hoshino.typing import CQEvent

from . import _config

sv = Service(
    'bilibili-login',
    enable_on_default=False,
    manage_priv=priv.ADMIN,
    visible=True,
    help_='''
【B站扫码登录服务】（群内默认关闭，群管发送「开启 bilibili-login」启用）：
- 【b站登录 / 扫码登录】：生成手机B站扫码登录二维码
- 【b站取消登录 / 取消登录】：取消当前正在进行的扫码登录
- 【b站登录状态 / 登录状态】：查看当前B站账号登录状态与有效性
- 【b站退出登录 / 退出登录】：清除当前保存的B站登录凭据
'''.strip()
)

_login_task: Optional[asyncio.Task] = None
_login_lock = asyncio.Lock()


def _check_admin_priv(ev: CQEvent) -> bool:
    """检查是否具有群管或超级管理员权限"""
    if ev.user_id in hoshino.config.SUPERUSERS:
        return True
    return priv.check_priv(ev, priv.ADMIN)


async def _generate_qr_image(url: str) -> Optional[Image.Image]:
    """生成二维码 PIL 图片（优先使用本地 qrcode 库，未安装时通过网络 API 自动降级）"""
    if qrcode is not None:
        try:
            qr = qrcode.QRCode(
                version=None,
                error_correction=qrcode.constants.ERROR_CORRECT_L,
                box_size=8,
                border=3,
            )
            qr.add_data(url)
            qr.make(fit=True)
            return qr.make_image(fill_color="black", back_color="white").convert('RGBA')
        except Exception as e:
            sv.logger.warning(f"本地 qrcode 生成失败，尝试网络 API 降级: {e}")

    # 降级：通过公开 QR 接口获取图片（即使新电脑未安装 qrcode 也能正常显示）
    api_url = f"https://api.qrserver.com/v1/create-qr-code/?size=300x300&margin=15&data={quote(url)}"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(api_url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status == 200:
                    data = await resp.read()
                    return Image.open(BytesIO(data)).convert('RGBA')
    except Exception as e:
        sv.logger.error(f"网络 API 获取二维码图片失败: {e}")
    return None


async def _fetch_nav_info() -> Dict[str, Any]:
    """获取 B 站账号个人导航信息（校验登录态）"""
    url = "https://api.bilibili.com/x/web-interface/nav"
    headers = _config.get_headers()
    try:
        async with aiohttp.ClientSession(headers=headers) as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                data = await resp.json()
                return data
    except Exception as e:
        sv.logger.exception(f"请求 B 站 nav 接口失败: {e}")
        return {"code": -999, "message": str(e)}


async def _generate_qrcode_data() -> Tuple[Optional[str], Optional[str]]:
    """向 B 站申请登录二维码链接与 qrcode_key"""
    api = "https://passport.bilibili.com/x/passport-login/web/qrcode/generate"
    headers = _config.get_headers()
    try:
        async with aiohttp.ClientSession(headers=headers) as session:
            async with session.get(api, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                res = await resp.json()
                if res.get("code") == 0 and res.get("data"):
                    return res["data"]["url"], res["data"]["qrcode_key"]
    except Exception as e:
        sv.logger.exception(f"获取 B 站二维码接口失败: {e}")
    return None, None


async def _do_qr_login(bot, ev: CQEvent):
    """后台异步轮询扫码登录任务"""
    try:
        qr_url, qrcode_key = await _generate_qrcode_data()
        if not qr_url or not qrcode_key:
            await bot.send(ev, "❌ 获取 B 站登录二维码失败，请稍后重试。")
            return

        # 生成二维码图片并发送
        qr_img = await _generate_qr_image(qr_url)
        if not qr_img:
            await bot.send(ev, "❌ 生成二维码图片失败，请重试。")
            return
        img_b64 = util.pic2b64(qr_img)
        msg = (
            f"{MessageSegment.image(img_b64)}\n"
            f"📱 请打开【手机哔哩哔哩 APP】扫一扫登录并在手机端确认。\n"
            f"⏳ 二维码有效期 180 秒（发送「b站取消登录」可随时中止）。"
        )
        await bot.send(ev, msg)

        # 轮询扫码结果（最多等待 180 秒，每 2 秒一次）
        poll_url = f"https://passport.bilibili.com/x/passport-login/web/qrcode/poll?qrcode_key={qrcode_key}"
        poll_headers = _config.get_headers()

        async with aiohttp.ClientSession(headers=poll_headers) as session:
            for _ in range(90):
                await asyncio.sleep(2)
                try:
                    async with session.get(poll_url, timeout=aiohttp.ClientTimeout(total=8)) as poll_resp:
                        poll_json = await poll_resp.json()
                        data = poll_json.get("data", {})
                        code = data.get("code")

                        if code == 86101:
                            # 未扫码，继续安静等待
                            continue
                        elif code == 86090:
                            # 已扫码未确认，继续安静等待
                            continue
                        elif code == 86038:
                            # 二维码已失效
                            await bot.send(ev, "❌ 二维码已失效或超时，扫码登录已取消。")
                            return
                        elif code == 0:
                            # 扫码成功
                            cookies: Dict[str, str] = {}
                            # 优先从 response.cookies 读取
                            for name, morsel in poll_resp.cookies.items():
                                cookies[name] = morsel.value
                            # 补充解析原始 Set-Cookie 头部
                            for header in poll_resp.headers.getall("Set-Cookie", []):
                                parts = header.split(";")
                                first = parts[0].strip()
                                if "=" in first:
                                    k, v = first.split("=", 1)
                                    if k not in cookies:
                                        cookies[k] = v

                            sessdata = cookies.get("SESSDATA", "")
                            bili_jct = cookies.get("bili_jct", "")
                            dedeuserid = cookies.get("DedeUserID", "")

                            if not sessdata or not bili_jct:
                                await bot.send(ev, "❌ 扫码成功但未能提取完整 Cookie 凭据，请重试。")
                                return

                            # 保存凭据
                            _config.save_credentials(
                                sessdata=sessdata,
                                bili_jct=bili_jct,
                                dedeuserid=dedeuserid,
                            )

                            # 验证登录结果并获取用户名
                            nav_res = await _fetch_nav_info()
                            if nav_res.get("code") == 0 and nav_res.get("data", {}).get("isLogin"):
                                u = nav_res["data"]
                                uname = u.get("uname", "未知")
                                mid = u.get("mid", dedeuserid)
                                level = u.get("level_info", {}).get("current_level", 0)
                                money = u.get("money", 0)
                                vip_type = u.get("vipType", 0)
                                vip_status = u.get("vipStatus", 0)
                                vip_str = "年度大会员" if vip_type == 2 and vip_status == 1 else ("大会员" if vip_status == 1 else "普通用户")

                                reply = (
                                    f"🎉 B 站账号扫码登录成功！\n"
                                    f"━━━━━━━━━━━━━━\n"
                                    f"👤 账号：{uname} (UID: {mid})\n"
                                    f"⭐ 等级：Lv{level} | 🪙 硬币：{money}\n"
                                    f"👑 会员：{vip_str}\n"
                                    f"━━━━━━━━━━━━━━\n"
                                    f"凭据已自动写入本地配置文件。"
                                )
                            else:
                                reply = "✅ 登录成功，凭据已保存至本地配置文件！"
                            await bot.send(ev, reply)
                            return
                        else:
                            sv.logger.warning(f"未知 B 站扫码返回码: {code}, 消息: {data.get('message')}")
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    sv.logger.warning(f"轮询 B 站扫码登录时网络波动: {e}")

            await bot.send(ev, "⌛ 扫码超时，请重新发送「b站登录」。")

    except asyncio.CancelledError:
        sv.logger.info("B 站扫码登录任务已被主动取消。")
    except Exception as e:
        sv.logger.exception(f"执行扫码登录异常: {e}")
        await bot.send(ev, f"❌ 扫码登录发生异常: {e}")
    finally:
        global _login_task
        _login_task = None


@sv.on_fullmatch('b站登录', 'bilibili登录', 'B站登录', '扫码登录', 'b站扫码登录', 'B站扫码登录', 'b站扫码')
async def bilibili_login(bot, ev: CQEvent):
    if not _check_admin_priv(ev):
        return

    global _login_task
    async with _login_lock:
        if _login_task and not _login_task.done():
            await bot.send(ev, "⚠️ 当前已有正在进行的 B 站扫码登录任务，请先扫码或发送「取消登录」。")
            return
        _login_task = asyncio.create_task(_do_qr_login(bot, ev))


@sv.on_fullmatch('b站取消登录', '取消b站登录', 'bilibili取消登录', '取消登录', '取消扫码')
async def bilibili_cancel_login(bot, ev: CQEvent):
    if not _check_admin_priv(ev):
        return

    global _login_task
    async with _login_lock:
        if _login_task and not _login_task.done():
            _login_task.cancel()
            _login_task = None
            await bot.send(ev, "🛑 已取消当前的 B 站扫码登录任务。")
        else:
            await bot.send(ev, "当前没有正在进行的 B 站扫码登录任务。")


@sv.on_fullmatch('b站登录状态', 'b站账号信息', 'b站凭据状态', 'bilibili登录状态', '登录状态', '账号信息', 'b站状态', 'B站状态')
async def bilibili_status(bot, ev: CQEvent):
    if not _check_admin_priv(ev):
        return

    creds = _config.get_credentials()
    if not creds.get("sessdata"):
        await bot.send(ev, "ℹ️ 当前未保存任何 B 站登录凭据，请发送「扫码登录」完成绑定。")
        return

    nav_res = await _fetch_nav_info()
    if nav_res.get("code") == 0 and nav_res.get("data", {}).get("isLogin"):
        u = nav_res["data"]
        uname = u.get("uname", "未知")
        mid = u.get("mid", creds.get("dedeuserid"))
        level = u.get("level_info", {}).get("current_level", 0)
        money = u.get("money", 0)
        vip_type = u.get("vipType", 0)
        vip_status = u.get("vipStatus", 0)
        vip_str = "年度大会员" if vip_type == 2 and vip_status == 1 else ("大会员" if vip_status == 1 else "普通用户")

        expires_at = creds.get("expires_at", 0)
        expires_str = (
            datetime.fromtimestamp(expires_at / 1000).strftime("%Y-%m-%d %H:%M:%S")
            if expires_at > 0
            else "未知"
        )

        msg = (
            f"✅ B 站登录凭据有效！\n"
            f"━━━━━━━━━━━━━━\n"
            f"👤 账号：{uname} (UID: {mid})\n"
            f"⭐ 等级：Lv{level} | 🪙 硬币：{money}\n"
            f"👑 会员：{vip_str}\n"
            f"📅 预计到期：{expires_str}\n"
            f"━━━━━━━━━━━━━━"
        )
    else:
        msg = (
            f"❌ B 站登录凭据已失效或过期！\n"
            f"返回信息: {nav_res.get('message', '未知错误')}\n"
            f"建议重新发送「扫码登录」更新凭据。"
        )

    await bot.send(ev, msg)


@sv.on_fullmatch('b站退出登录', 'b站注销', 'bilibili退出登录', '退出登录', '注销登录', 'b站登出')
async def bilibili_logout(bot, ev: CQEvent):
    if not _check_admin_priv(ev):
        return

    _config.clear_credentials()
    await bot.send(ev, "🗑️ 已清空 B 站登录凭据。")
