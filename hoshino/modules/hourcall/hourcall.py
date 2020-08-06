import pytz
from datetime import datetime
import hoshino
from hoshino import Service
import random
from hoshino.res import R

sv = Service('hourcall', enable_on_default=False, help_='时报')
tz = pytz.timezone('Asia/Shanghai')

def get_hour_call():
    """挑出一组时报，每日更换，一日之内保持相同"""
    cfg = hoshino.config.hourcall
    now = datetime.now(tz)
    hc_groups = cfg.HOUR_CALLS_ON
    g = hc_groups[ now.day % len(hc_groups) ]
    return cfg.HOUR_CALLS[g]


@sv.scheduled_job('cron', hour='*')
async def hour_call():
    now = datetime.now(tz)
    if now.hour % 6 == 0:
        pic = R.img(f"提醒买药{random.randint(1, 5)}.jpg").cqcode
        await sv.broadcast(f"{pic}", 'hourcall', 0)
        #await sv.broadcast(str(R.img('提醒买药.jpg').cqcode), 'hourcall', 0)

    # if 2 <= now.hour <= 4:
    #     return  # 宵禁 免打扰
    # msg = get_hour_call()[now.hour]
    # await sv.broadcast(msg, 'hourcall', 0)
