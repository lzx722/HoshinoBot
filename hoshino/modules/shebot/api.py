import asyncio
import os
import sqlite3
from io import BytesIO

import aiohttp
import requests
from PIL import Image

from hoshino import Service, priv, util, config


class Setu:
    def __init__(self,pid,title,url,r18,tags,author):
        self.pid = pid
        self.title = title
        self.url = url
        self.r18 = r18
        self.tags = tags
        self.author = author

def __get_auth_key():
    return config.shebot.apikey.API_KEY

def get_setu(r18,keyword,num,size1200):
    apikey = __get_auth_key()
    apiPath=r'https://api.lolicon.app/setu'
    params = {'apikey':apikey,'r18':r18,'keyword':keyword,'num':num,'size1200':size1200}
    try:
        setu_list=[]
        with requests.get(apiPath,params=params,timeout=20) as resp:
            res = resp.json()
            data = res
            for item in data['data']:
                pid = item['pid']
                title = item['title']
                url = item['url']
                r18 = item['r18']
                tags = item['tags']
                author = item['author']
                setu = Setu(pid,title,url,r18,tags,author)
                setu_list.append(setu)
            #insert(setu_list) #这行代码会把数据保存数据库，有需要可以取消注释
            return setu_list
    except Exception as ex:
        print(ex)
        print("error")
    finally:
        return setu_list

async def Task (setu,storePath,PICTURES):
    url = setu.url
    r18 = setu.r18
    fileName = str(setu.pid)
    try:
        timeout = aiohttp.ClientTimeout(total=30)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            print(f'当前url{url}')
            async with session.get(url) as resp:
                content = await resp.read()
                print('一张下载完成')
                filePath = os.path.join(storePath,fileName)
                with open(filePath,'wb') as f:
                    if r18==0:
                        f.write(content)
                        f.close()
                    elif r18==1:
                        img = Image.open(BytesIO(content))
                        img = img.convert('RGB')
                        #img = img.transpose(Image.ROTATE_90)
                        out = BytesIO()
                        img.save(out,format='JPEG')
                        f.write(out.getvalue())
                        f.close()
                    PICTURES.append(setu)
    except Exception as ex:
        print(ex)

async def task_creator(setu_list,storePath,PICTURES):
    tasks = []
    for setu in setu_list:
        task = asyncio.create_task(Task(setu,storePath,PICTURES))
        tasks.append(task)
    await asyncio.gather(*tasks)

def get_final_setu(storePath,num=1,r18=2,keyword='',size1200='false'):
    PICTURES=[]
    setu_list = get_setu(r18=r18,num=num,keyword=keyword,size1200=size1200)
    asyncio.run(task_creator(setu_list,storePath,PICTURES))
    return PICTURES
    
async def get_final_setu_async(storePath,num=1,r18=2,keyword='',size1200='false'):
    PICTURES=[]
    setu_list = get_setu(r18=r18,num=num,keyword=keyword,size1200=size1200)
    await task_creator(setu_list,storePath,PICTURES)
    #这是测试代码
    return PICTURES
    # return setu_list

def insert(setus:list):
    with sqlite3.connect(os.path.join(os.path.dirname(__file__),'setu.db3')) as conn:
        for setu in setus:
            pid = setu.pid
            author = setu.author
            title = setu.title
            url = setu.url
            r18 = setu.r18
            tags = str(setu.tags)
            cur = conn.cursor()
            sql = f'insert into setu (pid,author,title,url,r18,tags) values(?,?,?,?,?,?)'
            data = [pid,author,title,url,r18,tags]
            #print(sql)
            cur.execute(sql,data)
    conn.commit()
    cur.close()
    conn.close()
