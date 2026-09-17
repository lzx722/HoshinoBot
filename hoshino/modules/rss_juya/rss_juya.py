import time
from asyncio import Lock

from hoshino import Service, util
from ._spider import JuyaRssSpider

sv = Service(
    'rss_juya',
    bundle='RSS订阅',
    help_=(
        '橘鸦AI早报 RSS 推送\n'
        '每天 8 点起每 30 分钟扫描更新\n'
        '发送「AI早报」查看/刷新最新一期'
    ),
    enable_on_default=False,
)

lmt = util.FreqLimiter(60)
# group_id -> (上次推送日期串, 上次推送时间戳)
last_push = {}
_push_lock = Lock()

PUSH_INTERVAL = 30 * 60  # 同群同日期的完整早报至少间隔 30 分钟


async def rss_poller(sv: Service, spider, TAG):
    news = await spider.refresh()
    if not news:
        sv.logger.info(f'未检索到{TAG}更新')
        return
    sv.logger.info(f'检索到{len(news)}条{TAG}更新！')
    randomizer = util.randomizer(spider.src_name)
    await sv.broadcast(spider.format_items(news), TAG, 0.5, randomizer)


@sv.scheduled_job('cron', hour='8-23', minute='0,30', jitter=60)
async def juya_rss_poller():
    await rss_poller(sv, JuyaRssSpider, '橘鸦AI早报')


@sv.on_fullmatch('AI早报', 'ai早报')
async def send_rss(bot, ev):
    uid = ev.user_id
    if not lmt.check(uid):
        await bot.finish(ev, f'查询冷却中(剩余 {int(lmt.left_time(uid)) + 1}秒)', at_sender=True)
    lmt.start_cd(uid)

    try:
        async with _push_lock:
            # 只取内容不登记已推送，否则会吃掉当天的定时群推送
            updates = await JuyaRssSpider.fetch()
            if updates:
                sv.logger.info(f'手动刷新检索到{len(updates)}条更新')
    except Exception as e:
        sv.logger.exception(e)
        if not JuyaRssSpider.item_cache:
            await bot.send(ev, f'早报获取失败：{e}', at_sender=True)
            return
        await bot.send(ev, '早报获取失败，发送缓存的最新一期', at_sender=True)

    if not JuyaRssSpider.item_cache:
        await bot.send(ev, '暂未获取到早报内容', at_sender=True)
        return

    latest = JuyaRssSpider.item_cache[0]
    date_str = JuyaRssSpider.item_date(latest)
    now = time.time()
    gid = getattr(ev, 'group_id', None)
    if gid is not None:
        last_date, last_ts = last_push.get(gid, ('', 0))
        if date_str == last_date and now - last_ts < PUSH_INTERVAL:
            remain = int((PUSH_INTERVAL - (now - last_ts)) // 60) + 1
            await bot.finish(ev, f'{date_str} 的早报刚发过，{remain} 分钟后可再次查询', at_sender=True)
        last_push[gid] = (date_str, now)

    chunks = JuyaRssSpider.format_items([latest])
    for chunk in chunks:
        await bot.send(ev, chunk)
