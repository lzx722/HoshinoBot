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
    if text == '':
        return
    try:
        report = int(text)
    except:
        status = -1
    if status == -1:
        return

    if report < 250000:
        await bot.send(ev, f'\n您报告的伤害较小，请检查是否少报了一位数',at_sender=True)
    elif report > 2500000:
        await bot.send(ev, f'\n您报告的伤害较大，请检查是否多报了一位数',at_sender=True)
    else:
        return