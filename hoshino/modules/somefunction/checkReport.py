from io import BytesIO
import os
import requests
from PIL import Image
from hoshino import Service,priv
from hoshino.typing import CQEvent
import matplotlib.pyplot as plt
import base64


sv = Service('checkReport')
@sv.on_prefix(('报刀'))
async def set_test(bot, ev: CQEvent):
    status = 0
    text = ev['message'][0]['data']['text']
    raw_message = ev['raw_message']
    if text == '':
        return
    try:
        report = int(text)
    except:
        status = -1
    if status == -1:
        return
    if raw_message[0] == ' ':
        return
    if len(raw_message) < 4:
        return
    if raw_message[3] == ' ':
        return

    msg1 = '警告！您报的伤害较小，请确定是否为尾刀。如果不是尾刀，请检查是否少报了一位数'
    msg2 = '警告！您报的伤害较大，请检查是否多报了一位数'
    msg3 = f'============\n此为辅助程序判断，并不影响您报刀'
    if report < 150000:
        await bot.send(ev, f'\n{msg1}\n{msg3}',at_sender=True)
    elif report > 1500000:
        await bot.send(ev, f'\n{msg2}\n{msg3}',at_sender=True)
    else:
        return