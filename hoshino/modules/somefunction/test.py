from io import BytesIO
import os, json
import requests
from PIL import Image
from hoshino import Service,priv
from hoshino.typing import CQEvent
import matplotlib.pyplot as plt
import base64

yobot_url = 'http://39.108.125.221/' #请修改为你的yobot网址
# 开始改写
sv = Service('test')
current_folder = os.path.dirname(__file__) #本地文件路径
# @sv.on_fullmatch('离职报告')
# async def create_resignation_report(bot, event):
@sv.on_prefix(('tset','test'))
async def set_test(bot, ev: CQEvent):
    uid = ev['message'][0]['data']['text']
    await bot.send(ev, f'{uid}')





    
