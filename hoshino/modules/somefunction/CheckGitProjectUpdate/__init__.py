# pip install PyGithub
# pip install retrying
from retrying import retry  # => 引入retry装饰器
from github import Github
import json, os, time
from hoshino import Service,priv,config
from hoshino.typing import CQEvent

g = Github(config.somefunction.apikey.TOKEN_KEY)
all_dict = {}
single_dict = {'Commit_Sha':'','Branch':''}

# 注册服务
sv = Service('git项目追踪',enable_on_default=False)


def Read_Json():
    global all_dict
    _config_path = os.path.join(os.path.dirname(__file__),'config.json')
    _config_file = open(_config_path, 'r').read()
    all_dict = json.loads(_config_file)


def Write_Json():
    _config_path = os.path.join(os.path.dirname(__file__),'config.json')
    with open(_config_path,"w") as f:
        json.dump(all_dict,f)


# 获取git项目最新commit的sha值, 以及跟新说明。如果错误，sha为空，message为错误消息
@retry(stop_max_attempt_number=30, wait_fixed=50)   # 重试30次，每次间隔50ms (0.05s)
def Get_Commit(project, branch):
    try:
        branch = g.get_repo(project).get_branch(branch)
        sha = branch.commit.sha
        message = branch.commit.commit.message
        return sha[:7], message
    except Exception as message:
        sha = ''
        return sha, str(message)


# 获取git项目的branch, 404就返回空
@retry(stop_max_attempt_number=30, wait_fixed=50)   # 重试30次，每次间隔50ms (0.05s)
def Get_Branch(project):
    try:
        repo = g.get_repo(project)
        for items in list(repo.get_branches()):
            if items.name == 'master' or items.name == 'main':
                return items.name
    except Exception as e:
        print(e)
        return ''

# 全部项目追踪一次
def Check_Updates():
    msg = ''
    Read_Json()
    for project in all_dict:
        time.sleep(5)
        branch = all_dict[project]['Branch']
        sha, message = Get_Commit(project, branch)
        if sha == '':
            msg += f'项目{project}无法获取更新情况\n'
        else:
            if sha == all_dict[project]['Commit_Sha']:
                print(f'git项目{project}没有发现更新')
                pass
            else:
                all_dict[project]['Commit_Sha'] = sha
                Write_Json()
                msg += f'项目{project}有更新，该项目目前的最新的版本为：{sha[:7]}，更新说明是：\n{message}\n'
    if msg == '':
        print('所有追踪的git项目都没有发现更新')
    return msg
        


@sv.on_prefix(('新建追踪','建立追踪'))
async def NewTrack(bot, ev: CQEvent):
    project = ev['message'][0]['data']['text']
    print(f'project = {project}')
    Read_Json()
    if project in all_dict:
        msg = '失败，该项目已经在追踪了'
        await bot.send(ev,msg)
    else:
        branch = Get_Branch(project)
        if branch == '':
            msg = '获取失败，可能是因为作者或项目名或者分支名错误'
            await bot.send(ev,msg)
        else:
            sha, message = Get_Commit(project,branch)
            if sha == '':
                msg = '网络不好，commit信息获取失败'
                await bot.send(ev,msg)
            else:
                single_dict['Commit_Sha'] = sha
                single_dict['Branch'] = branch
                all_dict[project] = single_dict
                Write_Json()
                msg = f'项目已经成功建立追踪！\n该项目目前的最新的版本为：{sha[:7]}，更新说明是：\n{message}'
                await bot.send(ev,msg)


@sv.scheduled_job('cron', hour='10', minute='50')
async def Timing_Check_Updates():
    msg = Check_Updates()
    if msg != '':
        await sv.broadcast(msg, 'git项目追踪', 0.2)


@sv.on_prefix(('立刻追踪','现在追踪'))
async def Now_Check_Updates(bot, ev: CQEvent):
    msg = Check_Updates()
    if msg != '':
        await bot.send(ev,msg)


@sv.on_prefix(('检查追踪','查看追踪'))
async def Show_Project(bot, ev: CQEvent):
    Read_Json()
    msg = '正在追踪以下项目：'
    for project in all_dict:
        msg += f'\n{project}'
    await bot.send(ev,msg)


@sv.on_prefix(('删除追踪','停止追踪'))
async def Delete_Project(bot, ev: CQEvent):
    project = ev['message'][0]['data']['text']
    Read_Json()
    if project in all_dict:
        del all_dict[project]
        Write_Json()
        msg = '删除成功！'
        await bot.send(ev,msg)
    else:
        msg = '失败，没有这个项目'
        await bot.send(ev,msg)


@sv.on_prefix(('追踪帮助','git追踪帮助','git项目追踪帮助'))
async def CheckGitHelp(bot, ev: CQEvent):
    msg = '[新建追踪]\n[检查追踪]\n[删除追踪]'
    await bot.send(ev,msg)

@sv.on_prefix(('升级追踪'))
async def UpdaCheckGitHelp(bot, ev: CQEvent):
    Read_Json()
    for project in all_dict:
        if all_dict[project]['Branch']=='':
            all_dict[project]['Branch'] = 'master'
    Write_Json()
    await bot.send(ev,'cg!')
