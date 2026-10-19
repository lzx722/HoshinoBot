import ast
import os
import random
import tempfile

import hoshino
from hoshino import Service, aiorequests, priv, sucmd
from hoshino.config import SUPERUSERS
from hoshino.typing import CommandSession

from . import chara

sv = Service("pcr-data-updater", use_priv=priv.SU, manage_priv=priv.SU, visible=False)

# 花名册数据源，按优先级排列。
# GitHub raw 在国内网络下经常在 TLS 握手阶段被中间设备 RST（WinError 10054），
# 单次请求失败率很高，因此保留 jsDelivr / gh-proxy 镜像兜底，任一源可用即可完成更新。
DATA_SOURCES = (
    'https://raw.githubusercontent.com/Ice9Coffee/LandosolRoster/master/_pcr_data.py',
    'https://cdn.jsdelivr.net/gh/Ice9Coffee/LandosolRoster@master/_pcr_data.py',
    'https://fastly.jsdelivr.net/gh/Ice9Coffee/LandosolRoster@master/_pcr_data.py',
    'https://gh-proxy.com/https://raw.githubusercontent.com/Ice9Coffee/LandosolRoster/master/_pcr_data.py',
)
RETRIES_PER_SOURCE = 2
REQUEST_TIMEOUT = (10, 40)      # (连接, 读取) 秒；避免坏源把定时任务卡住几分钟
MIN_FILE_SIZE = 50 * 1024       # 正常数据约 87KB，明显偏小说明拿到的是错误页
MIN_CHARA_ENTRIES = 100         # 正常 CHARA_NAME 有 300+ 条，少于这个数基本是残文件
REQUIRED_MARKERS = ('CHARA_NAME', 'CHARA_PROFILE')


def _validate(content: str) -> None:
    """确认拿到的是完整可用的花名册，而不是镜像返回的错误页/半截内容。

    只做静态检查（不执行下载到的代码）：大小 → 语法 → 顶层确实定义了
    CHARA_NAME / CHARA_PROFILE 这两个字典，且条目数量像正常花名册。
    """
    if len(content) < MIN_FILE_SIZE:
        raise ValueError(f'文件过小（{len(content)} 字节）')
    try:
        tree = ast.parse(content)
    except SyntaxError as e:
        raise ValueError(f'语法错误（疑似下载不完整）：{e}')

    assigned = {}
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    assigned[target.id] = node.value
    missing = [m for m in REQUIRED_MARKERS if m not in assigned]
    if missing:
        raise ValueError(f'缺少定义：{", ".join(missing)}')
    for marker in REQUIRED_MARKERS:
        if not isinstance(assigned[marker], ast.Dict):
            raise ValueError(f'{marker} 不是字典定义')
    if len(assigned['CHARA_NAME'].keys) < MIN_CHARA_ENTRIES:
        raise ValueError(f'CHARA_NAME 条目过少（{len(assigned["CHARA_NAME"].keys)}）')


def _source_name(url: str) -> str:
    return url.split('/')[2]


async def _fetch_pcr_data() -> str:
    """按优先级逐个尝试数据源，每个源重试若干次；全部失败才抛异常。"""
    errors = []
    for url in DATA_SOURCES:
        for attempt in range(1, RETRIES_PER_SOURCE + 1):
            try:
                rsp = await aiorequests.get(url, timeout=REQUEST_TIMEOUT)
                rsp.raise_for_status()
                # 显式按 UTF-8 解码：镜像的 Content-Type 不一定带 charset，
                # 交给 requests 猜编码可能把角色名解成乱码（内容仍是合法 Python，校验拦不住）
                raw = await rsp.content
                try:
                    content = raw.decode('utf-8-sig')
                except UnicodeDecodeError as e:
                    raise ValueError(f'内容不是 UTF-8 编码：{e}')
                _validate(content)
                sv.logger.info(f'花名册下载成功：{_source_name(url)}（第 {attempt} 次尝试）')
                return content
            except Exception as e:
                # 单个源/单次失败是常态（连接被重置等），记录后继续尝试下一个
                errors.append(f'{_source_name(url)} 第{attempt}次：{e}')
                sv.logger.warning(f'花名册数据源失败（{_source_name(url)} 第{attempt}次）：{e}')
    raise RuntimeError('所有数据源均不可用：\n' + '\n'.join(errors))


def _write_atomic(filename: str, content: str) -> None:
    """先写临时文件再原子替换，避免中途失败留下半个文件导致 priconne 无法导入。"""
    tmp_path = None
    try:
        fd, tmp_path = tempfile.mkstemp(
            dir=os.path.dirname(filename), prefix='._pcr_data_', suffix='.tmp')
        with os.fdopen(fd, 'w', encoding='utf8') as f:
            f.write(content)
        os.replace(tmp_path, filename)
        tmp_path = None
    finally:
        if tmp_path and os.path.exists(tmp_path):
            os.remove(tmp_path)


async def report_to_su(sess, msg_with_sess, msg_wo_sess):
    if sess:
        await sess.send(msg_with_sess)
    else:
        bot = hoshino.get_bot()
        sid = bot.get_self_ids()
        if len(sid) > 0:
            sid = random.choice(sid)
            await bot.send_private_msg(self_id=sid, user_id=SUPERUSERS[0], message=msg_wo_sess)


async def pull_chara(sess: CommandSession = None):
    try:
        content = await _fetch_pcr_data()
        filename = os.path.join(os.path.dirname(__file__), '_pcr_data.py')
        _write_atomic(filename, content)
        result = chara.roster.update()
    except Exception as e:
        sv.logger.exception(e)
        await report_to_su(sess, f'Error: {e}', f'pcr_data定时更新时遇到错误：\n{e}')
        return

    result = f"角色别称导入成功 {result['success']}，重名 {result['duplicate']}"
    if sess:
        await report_to_su(sess, result, f'pcr_data定时更新：\n{result}')


sucmd('update-pcr-chara', force_private=False, aliases=('重载花名册', '更新花名册'))(pull_chara)
sv.scheduled_job('cron', hour=5, jitter=300)(pull_chara)
