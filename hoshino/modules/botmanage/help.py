from hoshino import Service, priv
from hoshino.typing import CQEvent

sv = Service('_help_', manage_priv=priv.SUPERUSER, visible=False)

UNION_WAR_MANUAL = '''
- 公主连结Re:Dive -
==================
管理员指令：
[创建国服公会] 必须
[加入全部成员] 看情况
[撤销] 撤销上一次报刀
[锁定:留言] 锁定boss
[解锁] 让boss可以挑战

普通成员常用指令：
[申请出刀] 防止撞刀
[报刀+伤害]
[尾刀] 打死boss时用

普通成员不常用指令：
[撤销] 只能撤销自己的
[SL] 每天只能一次SL
[查刀] 很棒的功能
[加入公会]
[查看全部指令]
[手册] 如果不理解指令
[状态] 显示boss状态
[预约2] 预约2号boss
[登陆] 要私聊机器人

部分功能可以@某人实现
'''.strip()

TOP_MANUAL1 = '''
==================
- 公主连结Re:Dive -
==================
常用功能：
[@机器人 一井] 模拟抽奖
[@机器人 签到] 模拟签到
[怎么打+5个角色] jjc查询
[来张涩图] 注意不是色图
[搜图+名字] 需要等一会儿
[公会战帮助] 查看公会战相关的指令
[bot模块] 查看全部功能

不常用功能：
[日程表] 查看一周日程
[brank] 查询国服rank表
[黄骑充电表]
[翻译+要翻译的内容]
[谁是+要查的角色别名]
[挖矿+你的jjc最高名次]
[切噜一下+要转换的话] 复制粘贴就能转换回来
[精致睡眠] 8小时精致睡眠

※搜图功能要等一小会儿
※图片出不来是玄学问题
※除此之外，另有其他很不常用的隐藏功能:)
'''.strip()

BOT_MODULE_MANUAL = '''
发送以下关键词查看更多：
[帮助pcr查询]
[帮助pcr娱乐]
[帮助pcr订阅]
[帮助pcrbox统计]
[帮助通用]
'''.strip()

TOP_MANUAL = '''
====================
= HoshinoBot使用说明 =
====================
发送[]内的关键词触发

==== 查看详细说明 ====
[帮助pcr会战][帮助pcr查询]
[帮助pcr娱乐][帮助pcr订阅]
[帮助artist][帮助kancolle]
[帮助umamusume]
[帮助通用]

====== 常用指令 ======
[启用会战] 选择会战版本
[怎么拆日和] 竞技场查询
[pcr速查] 常用网址
[星乃来发十连] 转蛋模拟
[官漫286] 官方四格(日文)
[猜头像][猜角色] 小游戏
[.rd] roll点

[切噜一下+待加密文本]
▲切噜语转换
[来杯咖啡+反馈内容]
▲联系维护组

====== 被动技能 ======
pcr推特转发(日)
pcr台B服官网公告推送
pcr四格漫画(日)更新推送
pcr竞技场背刺时间提醒*
赛马娘推特转发*
URA9因子嗅探者*
萌系画师推特转发*
撤回终结者*
入群欢迎* & 退群通知
番剧字幕组更新推送*°
*: 默认关闭
°: 不支持自定义

====== 模块开关 ======
※限群管理/群主控制※
[lssv] 查看各模块开关状态
[启用+空格+service]
[禁用+空格+service]
 
=====================
※Hoshino开源Project：
github.com/Ice-Cirno/HoshinoBot
您对项目作者的支持与Star是本bot更新维护的动力
💰+⭐=❤
'''.strip()
# 魔改请保留 github.com/Ice-Cirno/HoshinoBot 项目地址


def gen_service_manual(service: Service, gid: int):
    spit_line = '=' * max(0, 18 - len(service.name))
    manual = [f"|{'○' if service.check_enabled(gid) else '×'}| {service.name} {spit_line}"]
    if service.help:
        manual.append(service.help)
    return '\n'.join(manual)


def gen_bundle_manual(bundle_name, service_list, gid):
    manual = [bundle_name]
    service_list = sorted(service_list, key=lambda s: s.name)
    for s in service_list:
        if s.visible:
            manual.append(gen_service_manual(s, gid))
    return '\n'.join(manual)


@sv.on_prefix('help', '帮助')
async def send_help(bot, ev: CQEvent):
    gid = ev.group_id
    arg = ev.message.extract_plain_text().strip()
    bundles = Service.get_bundles()
    services = Service.get_loaded_services()
    if not arg:
        await bot.send(ev, TOP_MANUAL)
    elif arg in bundles:
        msg = gen_bundle_manual(arg, bundles[arg], gid)
        await bot.send(ev, msg)
    elif arg in services:
        s = services[arg]
        msg = gen_service_manual(s, gid)
        await bot.send(ev, msg)
    # else: ignore

@sv.on_fullmatch(('bot模块', '机器人模块', 'bot模块查看', '机器人模块查看', 'bot模块查询', '机器人模块查询'))
async def send_pcrhelp(bot, ev: CQEvent):
    await bot.send(ev, BOT_MODULE_MANUAL)

@sv.on_fullmatch(('公会战帮助','公会战说明','公会战菜单','公会战管理','公会战指令', '工会战帮助','工会战说明','工会战菜单','工会战管理', '工会战指令'))
async def send_union_war_help(bot, ev: CQEvent):
    await bot.send(ev, UNION_WAR_MANUAL)
