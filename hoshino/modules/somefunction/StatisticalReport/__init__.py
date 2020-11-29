import json, os
from os import name, times
from hoshino import Service,priv
from hoshino.typing import CQEvent

all_dict = {}
single_dict = {'Name':'','Time':0}
single_dict_qun ={}


# 注册服务
sv = Service('报刀统计',enable_on_default=False)

def Read_Json():
    global all_dict
    _config_path = os.path.join(os.path.dirname(__file__),'config.json')
    _config_file = open(_config_path, 'r').read()
    all_dict = json.loads(_config_file)


def Write_Json():
    _config_path = os.path.join(os.path.dirname(__file__),'config.json')
    with open(_config_path,"w") as f:
        json.dump(all_dict,f)


# @sv.on_prefix(('报刀'))
async def StatisticalReport(bot, ev: CQEvent):
    name = str(ev['sender']['card'])
    qq = str(ev['user_id'])
    qun = str(ev['group_id'])
    Read_Json()
    if qun in all_dict:
        if qq in all_dict[qun]:
            num = all_dict[qun][qq]['Time']
            num += 1
            all_dict[qun][qq]['Time'] = num
            all_dict[qun][qq]['Name'] = name
        else:
            single_dict['Time'] = 1
            single_dict['Name'] = name
            all_dict[qun][qq] = single_dict
    else:
        single_dict['Time'] = 1
        single_dict['Name'] = name
        single_dict_qun[qq] = single_dict
        all_dict[qun] = single_dict_qun
    Write_Json()
    try:
        num = int(ev['message'][0]['data']['text'])
        if num > 3600000:
            msg = f'警告！您报的伤害较大，请检查是否多报了一位数\n============\n此为辅助程序判断，并不影响您报刀'
            await bot.send(ev, f'\n{msg}',at_sender=True)
    except Exception as e:
        print(e)


@sv.on_prefix(('报刀统计', '查看统计', '查询统计','统计报刀','查看报刀统计'))
async def ReadReports(bot, ev: CQEvent):
    qun = str(ev['group_id'])
    is_su = priv.check_priv(ev, priv.PRIVATE)
    if is_su:
        Read_Json()
        msg = '统计如下：'
        d = {} 
        for qq in all_dict[qun]:
            time = all_dict[qun][qq]['Time']
            name = all_dict[qun][qq]['Name']
            d[name] = time
        d = sorted(d.items(), key=lambda item:item[1], reverse=True)
        print(d)
        print(type(d))
        for item in d:    
            name = item[0]
            time = item[1]
            msg += f'\n{time}, {name}'
        await bot.send(ev,msg)
    else:
        await bot.send(ev,'无权查看！')


@sv.on_prefix(('删除统计', '清除统计', '清空统计'))
async def ReadReports(bot, ev: CQEvent):
    qun = str(ev['group_id'])
    is_su = priv.check_priv(ev, priv.ADMIN)
    if is_su:
        Read_Json()
        all_dict[qun] = {}
        Write_Json()
        await bot.send(ev,"成功！")
    else:
        await bot.send(ev,'权限不足！需要群主才能执行此操作！')