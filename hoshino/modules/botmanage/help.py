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

TOP_MANUAL = '''
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

def gen_bundle_manual(bundle_name, service_list, gid):
    manual = [bundle_name]
    service_list = sorted(service_list, key=lambda s: s.name)
    for sv in service_list:
        if sv.visible:
            spit_line = '=' * max(0, 18 - len(sv.name))
            manual.append(f"|{'○' if sv.check_enabled(gid) else '×'}| {sv.name} {spit_line}")
            if sv.help:
                manual.append(sv.help)
    return '\n'.join(manual)


@sv.on_prefix(('help', '帮助', '幫助'))
async def send_help(bot, ev: CQEvent):
    bundle_name = ev.message.extract_plain_text().strip()
    bundles = Service.get_bundles()
    if not bundle_name:
        await bot.send(ev, TOP_MANUAL)
    elif bundle_name in bundles:
        msg = gen_bundle_manual(bundle_name, bundles[bundle_name], ev.group_id)
        await bot.send(ev, msg)
    # else: ignore

@sv.on_fullmatch(('bot模块', '机器人模块', 'bot模块查看', '机器人模块查看', 'bot模块查询', '机器人模块查询'))
async def send_pcrhelp(bot, ev: CQEvent):
    await bot.send(ev, BOT_MODULE_MANUAL)

@sv.on_fullmatch(('公会战帮助','公会战说明','公会战菜单','公会战管理','公会战指令', '工会战帮助','工会战说明','工会战菜单','工会战管理', '工会战指令'))
async def send_union_war_help(bot, ev: CQEvent):
    await bot.send(ev, UNION_WAR_MANUAL)
