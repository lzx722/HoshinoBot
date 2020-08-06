import re
import os
import asyncio
import time
import json
import traceback
import nonebot
import threading 

from queue import Queue
from .api import get_final_setu,get_final_setu_async
from .data_source import SetuWarehouse,get_random_pic,save_config,load_config,send_setus,my_delete_msg,add_delay_job
from .config import *
from hoshino import Service, priv, util

g_is_online = DEFAULT_ONLINE
g_with_url = WITH_URL

g_config = load_config()
g_r18_groups = set(g_config.get('r18_groups',[]))
g_delete_groups = set(g_config.get('delete_groups',[]))

g_msg_to_delete = {}

#初始化色图仓库
nr18_path = NR_PATH #存放非r18图片
r18_path = R18_PATH#存放r18图片
search_path = SEARCH_PATH #存放搜索图片
if not os.path.exists(search_path):
    os.mkdir(search_path)
wh = SetuWarehouse(nr18_path)
r18_wh = SetuWarehouse(r18_path,r18=1)

#启动一个线程一直补充色图
print('线程启动')
thd = threading.Thread(target=wh.keep_supply)
thd.start()

#启动一个线程一直补充r18色图
print('r18线程启动')
thd_r18 = threading.Thread(target=r18_wh.keep_supply)
thd_r18.start()

config_path = os.path.dirname(__file__)+'/config.json'
help_path = os.path.dirname(__file__)+'/help.txt'
sv = Service('涩图')
@sv.on_message(event=None)
async def _(bot,ctx):
    global g_msg_to_delete
    message = ctx['raw_message']
    uid = ctx['user_id']
    self_id = ctx['self_id']
    gid = ctx.get('group_id',0)
    user_priv = priv.get_user_priv
    is_to_delete = True if gid in g_delete_groups else False
    if COMMON_RE.match(message):
        if not g_is_online:
            print('发送本地涩图')
            pic = get_random_pic(nr18_path)
            folder = nr18_path.split('/')[-1]
            pic = f'[CQ:image,file={folder}/{pic}]'
            print(pic)
            ret = await bot.send(ctx,pic)
            msg_id = ret['message_id']
            if is_to_delete:
                #30秒后删除
                add_delay_job(my_delete_msg,delay_time=60,args=[bot,self_id,msg_id])
            return

        robj = COMMON_RE.match(message)
        try:
            num = int(robj.group(1))
        except:
            num = 1
        keyword = robj.group(2)
        if keyword:
            while keyword[0] == ' ':
                keyword = keyword[1:]
            print(f"含有关键字{keyword}")
            if priv.get_user_priv(ctx) < priv.SUPERUSER:
                sv.add_black_user(uid,60)
                setus = await get_final_setu_async(search_path,keyword=keyword,r18=0)
            else:
                setus = await get_final_setu_async(search_path,keyword=keyword,r18=2) 
            await send_setus(bot,ctx,search_path,setus,g_with_url,is_to_delete)
            

        else:
            setus = wh.fetch(num)
            if not send_setus:#send_setus为空
                await bot.send(ctx,'色图库正在补充，下次再来吧',at_sender=False)
            else:
                await send_setus(bot,ctx,nr18_path,setus,g_with_url,is_to_delete)

    elif SECRET_RE.match(message):
        if gid not in g_r18_groups and gid!= 0:
            await bot.send(ctx,'本群未开启r18色图，请私聊机器人,但注意不要过分请求，否则拉黑')
            return

        if not g_is_online:
            print('发送本地涩图')
            pic = get_random_pic(r18_path)
            folder = r18_path.split('/')[-1]
            pic = f'[CQ:image,file={folder}/{pic}]'
            ret = await bot.send(ctx,pic)
            msg_id = ret['message_id']
            if is_to_delete:
                add_delay_job(my_delete_msg,delay_time=60,args=[bot,self_id,msg_id])
            return   

        try:
            num = int(SECRET_RE.match(message).group(1))
        except:
            num = 1
        if Service.user_priv < priv.SUPERUSER:
            num = 1

        setus = r18_wh.fetch(num)
        print("发送r18")
        await send_setus(bot,ctx,r18_path,setus,g_with_url,is_to_delete)
        if priv.get_user_priv(ctx) < priv.SUPERUSER:
            sv.set_block_user(uid,10)
    else:
        print('无关消息')

@sv.on_message()
async def switch(bot,ctx):
    global g_is_online
    global g_delete_groups
    global g_config
    global g_r18_groups
    msg = ctx['raw_message']
    gid = ctx['group_id']
    if priv.get_user_priv(ctx) < priv.SUPERUSER:
        return
    if msg == '选择在线涩图':
        g_is_online = True
        await bot.send(ctx,'已选择在线涩图',at_sender=True)
    elif msg == '选择本地涩图':
        g_is_online = False
        await bot.send(ctx,'已选择本地涩图',at_sender=True)

    elif msg == '本群涩图撤回':
        g_delete_groups.add(gid)
        g_config['delete_groups'] = list(g_delete_groups)
        save_config(g_config)
        await bot.send(ctx,'本群涩图撤回',at_sender=True)

    elif msg == '本群涩图不撤回':
        g_delete_groups.discard(gid)
        g_config['delete_groups'] = list(g_delete_groups)
        save_config
        await bot.send(ctx,'本群涩图不撤回',at_sender=True)

    elif re.match(r'群(\d{5,12})?涩图撤回',msg):
        gid = int(re.match(r'群(\d{5,12})?涩图撤回',msg).group(1))
        g_delete_groups.add(gid)
        g_config['delete_groups'] = list(g_delete_groups)
        save_config(g_config)
        await bot.send(ctx,f'群{gid}涩图撤回')

    elif re.match(r'群(\d{5,12})?涩图不撤回',msg):
        gid = int(re.match(r'群(\d{5,12})?涩图不撤回',msg).group(1))
        g_delete_groups.discard(gid)
        g_config['delete_groups'] = list(g_delete_groups)
        save_config(g_config)
        await bot.send(ctx,f'群{gid}涩图不撤回')

    elif msg == '本群r18开启':
        g_r18_groups.add(gid)
        g_config['r18_groups'] = list(g_r18_groups)
        save_config(g_config)
        await bot.send(ctx,'本群r18开启',at_sender=True)

    elif msg == '本群r18关闭':
        g_r18_groups.discard(gid)
        g_config['r18_groups'] = list(g_r18_groups)
        save_config(g_config)
        await bot.send(ctx,'本群r18关闭',at_sender=True)

    elif re.match(r'群(\d{5,12})r18开启',msg):
        gid = int(re.match(r'群(\d{5,12})r18开启',msg).group(1))
        g_r18_groups.add(gid)
        g_config['r18_groups'] = list(g_r18_groups)
        save_config(g_config)
        await bot.send(ctx,f'群{gid}r18开启')

    elif re.match(r'群(\d{5,12})r18关闭',msg):
        gid = int(re.match(r'群(\d{5,12})r18关闭',msg).group(1))
        g_r18_groups.discard(gid)
        g_config['r18_groups'] = list(g_r18_groups)
        save_config(g_config)
        await bot.send(ctx,f'群{gid}r18关闭')

    else:
        pass




            
        

            
