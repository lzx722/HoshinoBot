from hoshino import Service, priv
from hoshino.typing import CQEvent

sv = Service('_help_', manage_priv=priv.SUPERUSER, visible=False)

UNION_WAR_MANUAL = '''
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
