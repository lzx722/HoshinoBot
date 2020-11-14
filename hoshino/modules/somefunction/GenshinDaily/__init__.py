from os import read
import pytz
from datetime import datetime
import os
import json
from hoshino import Service,priv
from hoshino.typing import CQEvent

# 注册服务
sv = Service('原神天赋本提醒',enable_on_default=False)

# 初始化
def Initialization():
    # 时间
    tz = pytz.timezone('Asia/Shanghai')
    now = datetime.now(tz)
    weekday = now.weekday() # 这玩意是从0到6的
    if now.hour < 4:
        weekday -= 1
    if weekday == -1:
        weekday = 6
    # 读json文件
    _config_path = os.path.join(os.path.dirname(__file__),'config.json')
    _config_file = open(_config_path, 'rb').read()
    _json = json.loads(_config_file)
    return now, weekday, _json


def Hello(now):
    msg = '哈喽'
    if now.hour < 6:
        msg = '凌晨了'
    elif now.hour < 11:
        msg = '早上好'
    elif now.hour < 13:
        msg = '中午好呀'
    elif now.hour < 17:
        msg = '下午好'
    elif now.hour < 18:
        msg = '傍晚了'
    else:
        msg = '晚上好呀'
    return msg

# 主要的函数
def CreatMessage(now, weekday, _json):
    one_day_book = _json["week"][weekday]
    all_character = _json["character"]

    weekStr = "一二三四五六日"
    msg = Hello(now)
    msg += f'，旅行者，今天是星期{weekStr[weekday]}，可以刷天赋素材'
    for book in one_day_book:
        msg += f'\n{book}：\n'
        for item in all_character[book]:
            if item == all_character[book][-1]:
                msg += item
            else:
                msg += f'{item}，'
    return msg


@sv.on_prefix(('原神日程','原神日常','ys日常','ys日程','ysrc'))
async def ys_daily(bot, ev: CQEvent):
    now, weekday, _json = Initialization()
    if weekday == 6:
        msg = Hello(now)
        msg += '，旅行者，今天是星期天，建议打周常boss哦'
    else:
        msg = CreatMessage(now, weekday, _json)
    await bot.send(ev,msg)

@sv.scheduled_job('cron', hour='8', minute='30')
async def ys_reminder():
    now, weekday, _json = Initialization()
    if weekday == 6:
        msg = Hello(now)
        msg += '，旅行者，今天是星期天，希望你今天也能过得快快乐乐哦'
    else:
        msg = CreatMessage(now, weekday, _json)
    await sv.broadcast(msg, '原神天赋本提醒', 0.2)
    