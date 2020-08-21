from hoshino import Service, priv
from hoshino.typing import CQEvent
import requests,json,time
from hoshino import util
from hoshino.util import FreqLimiter
from hoshino.typing import CQEvent

url_first = "https://service-kjcbcnmw-1254119946.gz.apigw.tencentcs.com/"

sv1 = Service('定时查询1会', enable_on_default=False, help_='自动查询1会公会排名', bundle='pcr订阅')
sv2 = Service('定时查询2会', enable_on_default=False, help_='自动查询2会公会排名', bundle='pcr订阅')
sv3 = Service('定时查询3会', enable_on_default=False, help_='自动查询3会公会排名', bundle='pcr订阅')

async def get_rank(info,info_type):
    """
    获取公会排名(25010之前),刷新时间为30分钟一次,相比于游戏内排名有约30分钟延迟\n
    info_type可能的值"leader"(按会长名),"name"(按公会名),"score"(按分数),"rank"(按排名)\n
    info是待搜索信息,不必填完整,可以以部分信息来搜索\n
    目前只能返回第一页数据,也就是前10个数据
    """
    url = url_first + info_type
    url += '/'
    headers = {"Custom-Source":"did","Content-Type": "application/json","Referer": "https://kengxxiao.github.io/Kyouka/"}
    
    if info_type == "name":
        url += '-1'
        content = json.dumps({"history":"0","clanName": info})
    elif info_type == "leader":
        url += '-1'
        content = json.dumps({"history":"0","leaderName": info})
    elif info_type == "score":
        # 无需额外请求头
        url += info
        content = json.dumps({"history":"0"})
    elif info_type == "rank":
        url += info
        content = json.dumps({"history":"0"})
    else:
        # 这都能填错?爪巴!
        msg = '内部错误，请联系维护人员'
        return msg
    r = requests.post(url, data=content, headers=headers)
    r_dec = json.loads(r.text)

    if r_dec['code'] != 0:
        # Bad request
        msg = f"查询失败,错误代码{r_dec['code']},错误信息{r_dec['msg']}请联系维护人员"
        return msg

    msg = ">>>公会战排名查询\n"
    queryTime = time.localtime(r_dec['ts'])
    formatTime = time.strftime('%Y-%m-%d %H:%M', queryTime)
    msg += f'数据更新时间{formatTime}\n'
    # 查询不到结果
    result = len(r_dec['data'])
    if result == 0:
        msg += "没有查询结果,当前仅能查询前25010名公会,排名信息30分钟更新一次,相比于游戏内更新有10分钟左右延迟"
        return msg
    for i in range(result):
        clanname = r_dec['data'][i]['clan_name']
        rank = r_dec['data'][i]['rank']
        damage = r_dec['data'][i]['damage']
        leader = r_dec['data'][i]['leader_name']
        num = r_dec['data'][i]['member_num']
        msg_new = f"第{i+1}条信息:\n公会名称：{clanname}\n会长：{leader}\n成员数量：{num}\n目前排名：{rank}\n造成伤害：{damage}\n\n"
        msg += msg_new
    return msg

@sv1.scheduled_job('cron', hour='5', minute='18')
async def pcr_timing_1():
    msg = await get_rank('重生之来',"name")
    msg += f"此为自动查询5点时的排名"
    await sv1.broadcast(msg, 'pcr_timing_1', 0.2)

@sv2.scheduled_job('cron', hour='5', minute='21')
async def pcr_timing_2():
    msg = await get_rank('妈，',"name")
    msg += f"此为自动查询5点时的排名"
    await sv2.broadcast(msg, 'pcr_timing_2', 0.2)

@sv3.scheduled_job('cron', hour='5', minute='24')
async def pcr_timing_3():
    msg = await get_rank('美食殿堂养生',"name")
    msg += f"此为自动查询5点时的排名"
    await sv2.broadcast(msg, 'pcr_timing_3', 0.2)


