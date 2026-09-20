# AGENTS.md

本文件面向在本仓库工作的 AI 编码助手（以及人类协作者），记录**不写下来就会反复踩的约定与陷阱**。
通用常识（Python 语法、git 基础操作）不在此赘述。

---

## 1. 项目概览

HoshinoBot：基于 [nonebot 1.x](https://github.com/nonebot/nonebot) + aiocqhttp 的 QQ 群机器人框架。
通过 OneBot 协议与 QQ 客户端通信。

- **入口**：`run.py`（`hoshino.init()` 返回 bot，`bot.run()` 启动，监听 8080 端口）
- **配置**：`hoshino/config/`（含 `__bot__.py`，见下文）
- **源码**：`hoshino/`（框架本体）、`hoshino/modules/`（业务插件）
- **日志**：`log/error.log`、`log/critical.log`

### Python 环境

**目标运行时是 Python 3.10** —— 两台部署机器（详见第 5 节）用的都是 3.10。
因此：**不要使用 3.11+ / 3.12+ 才有的语法或标准库特性**（如 `Self` 类型、`ExceptionGroup`、`tomllib` 等）。语法检查与测试统一用 3.10 执行。

本机可能另装有更高版本的 Python，但那只是额外安装，**不是目标环境**，不要用它跑测试或验证。

调用 3.10 的方式（Windows，Git Bash）：

```bash
py -3.10 -m py_compile path/to/file.py    # 推荐，版本选择明确
```

依赖清单用 `requirements_python310.txt`（3.10 用，含 autopcr 依赖，已用 `pip check` 验证过）。`requirements.txt` 是历史版本，保留但不用。

---

## 2. 插件开发

### 2.1 目录与命名约定（重要）

插件放在 `hoshino/modules/<插件名>/`。**文件名前缀有严格语义**，这不是风格问题，而是框架加载机制决定的：

| 文件形态 | 语义 | 加载器行为 |
|---|---|---|
| `xxx.py`（无下划线） | **入口模块**，注册 Service 与 handler | ✅ 启动时被导入 |
| `_xxx.py`（下划线开头） | **内部实现模块**，被入口 import | ❌ 跳过 |
| `__init__.py` | 包标记（可选，见下） | ✅ 被导入 |
| `test/`（无 `__init__.py`） | 子目录 | ❌ 跳过 |
| `test/`（含 `__init__.py`） | 子目录 | ✅ **会被导入**（陷阱） |

`nonebot.load_plugins` 用 `os.listdir` **非递归**扫描插件目录，规则是：

1. 文件：跳过以 `_` 开头、或不以 `.py` 结尾的
2. 目录：跳过以 `_` 开头、或**不含 `__init__.py`** 的

**由此得出两条硬性要求：**

- 插件目录下**任何不以 `_` 开头的 `.py` 文件都会被当作插件导入**。因此测试脚本、临时工具、一次性脚本**绝对不能**直接放在插件目录里——否则每次启动 bot 都会 import 它们（若 import 失败，`load_plugin` 会捕获异常并写 error 日志，不会崩溃，但会持续刷屏）。
- 内部模块**必须**加 `_` 前缀，否则会被重复导入为独立插件。仓库已有先例：commit `9837179`（`重构setu_renew：入口改为setu_renew.py，内部模块加下划线前缀`）就是把 `acggov.py` → `_acggov.py`、`base.py` → `_base.py`。

当前 `hoshino/modules/bilibili/` 就是标准样例：

```
bilibili/
├── summary.py          # 入口：速读/详细总结
├── login.py            # 入口：扫码登录
├── _config.py          # 内部：配置读写 + 磁盘缓存
├── _bili_sub.py        # 内部：字幕抓取
├── _bili_llm.py        # 内部：大模型调用
├── config.json         # 实际配置（gitignore）
└── config.json.example # 配置模板（入库）
```

> 注：插件目录**不需要** `__init__.py`。现有 `bilibili/`、`rss_juya/`、`setu_renew/` 都没有，靠 namespace package 工作。而 `priconne/`、`pcrclanbattle/` 有，那是历史遗留，不必模仿也不必清理。

### 2.2 注册插件：Service

一律通过 `hoshino.Service` 注册，不要直接用 nonebot 的裸装饰器（那样会绕过分群权限与服务开关）：

```python
from hoshino import Service, priv, util
from hoshino.typing import CQEvent

sv = Service(
    'rss_juya',                    # 服务名，唯一标识，用于分群开关
    bundle='RSS订阅',              # 可选，用于归组
    help_='...',                   # 帮助文本
    enable_on_default=False,       # 默认是否在本群启用
    visible=True,                  # 是否出现在服务列表
)
```

`Service` 构造参数（取自 `hoshino/service.py`）：

| 参数 | 说明 |
|---|---|
| `name` | 服务名，**必填且全局唯一** |
| `use_priv` / `manage_priv` | 使用 / 管理所需权限，默认 `priv.NORMAL` / `priv.ADMIN` |
| `enable_on_default` | 新群默认是否启用 |
| `visible` | 是否在服务列表中可见 |
| `help_` | 帮助文本 |
| `bundle` | 归组名 |

**服务配置持久化在** `~/.hoshino/service_config/<服务名>.json`，不在仓库里。

群内开关服务的指令由 `botmanage` 提供：

```
开启 <服务名>    # aliases: 启用 / 打开
关闭 <服务名>    # aliases: 禁用
```

### 2.3 触发器

`Service` 提供的装饰器（均在 `hoshino/service.py`）：

| 装饰器 | 用途 |
|---|---|
| `@sv.on_prefix(*prefix)` | 前缀匹配 |
| `@sv.on_suffix(*suffix)` | 后缀匹配 |
| `@sv.on_fullmatch(*word)` | 完整匹配 |
| `@sv.on_keyword(*kw)` | 关键词匹配 |
| `@sv.on_rex(rex)` | 正则匹配（接受 str 或 `re.Pattern`） |
| `@sv.on_message('group')` | 所有消息 |
| `@sv.on_command(...)` | nonebot 命令式（带参数解析） |
| `@sv.on_natural_language(...)` | 自然语言 |
| `@sv.scheduled_job(...)` | 定时任务（APScheduler，已默认 `Asia/Shanghai` 时区） |
| `@sv.on_request(...)` / `@sv.on_notice(...)` | 请求 / 通知事件 |

handler 签名统一为 `async def func(bot, ev: CQEvent)`。

`broadcast` 用于向所有启用了该服务的群推送：

```python
await sv.broadcast(msgs, '任务名', 0.5, randomizer)  # msgs 可为 str 或 list
```

`broadcast` 内部已有 try/except 逐群隔离，单个群失败不会影响其它群。

### 2.4 权限

`hoshino/priv.py` 定义的等级：

| 常量 | 值 | 含义 |
|---|---|---|
| `priv.BLACK` | -999 | 黑名单 |
| `priv.NORMAL` | 1 | 普通成员 |
| `priv.ADMIN` | 21 | 群管理 |
| `priv.OWNER` | 22 | 群主 |
| `priv.WHITE` | 51 | 白名单 |
| `priv.SUPERUSER` | 999 | 超级用户（`__bot__.py` 的 `SUPERUSERS`） |

检查当前事件：`priv.check_priv(ev, priv.SUPERUSER)`。
注意：`hoshino/config/__bot__.py` 里 `SUPERUSERS` 是**硬编码的 QQ 号列表**，`NICKNAME` 为空字符串而非列表（新写的代码取值前先 `getattr` 判空）。

### 2.5 配置

**约定：`config.py` / `config.json` 一律不进版本控制**（`.gitignore` 第 2–5 行）。因此新插件需要配置时：

1. 在插件目录放一份 **`config.json.example`**（或 `config.template.json`）作为模板并提交
2. 真实配置读 `config.json`，运行时不存在就在代码里兜底

`bilibili/_config.py` 是一个成熟范例：内置 `DEFAULT_CONFIG`，加载时与用户配置**深合并**，缺失的新键自动补全写回（保证老用户升级后不炸），解析失败则以默认配置运行且**不覆盖原文件**（保留用户手工修复的机会）。

配置路径写法参考：

```python
import os
CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'config.json')
```

### 2.6 常用工具

- **HTTP**：`from hoshino import aiorequests`（aiohttp 的封装，提供 `get/post/...` 返回 `AsyncResponse`）。插件里也常直接用 `aiohttp`。
- **限流**：`util.FreqLimiter(second)`（个人冷却）、`util.DailyNumberLimiter(n)`（每日次数）
- **用户数据目录**：`~/.hoshino/`（框架启动时自动创建）。插件自定义的持久化数据建议放 `~/.hoshino/<插件名>_*/`，如 bilibili 的总结缓存放在 `~/.hoshino/bilibili_summary_cache/`。
- **图片**：`util.pic2b64` / `util.fig2b64` / `util.concat_pic`
- **文本**：`util.normalize_str` / `util.filt_message` / `util.render_list`

### 2.7 启用插件

新插件写好**不会自动生效**，必须把插件目录名加进 `hoshino/config/__bot__.py` 的 `MODULES_ON`：

```python
MODULES_ON = {
    'bilibili',
    'rss_juya',
    # ...
}
```

`hoshino/config/` 整个目录在 gitignore 里，所以这是**每台部署机器各自维护**的本地配置。

### 2.8 代码风格

- **面向中文用户**：注释、日志、群内回复一律用中文
- 群内错误提示要**具体可行动**，例如 bilibili 插件会明确告诉用户「未检测到官方或 AI 字幕，跳过总结」而不是笼统的「失败」
- 长文本发送注意 QQ 单条消息长度限制，参考第 5 节

---

## 3. 测试脚本放在哪里

### 3.1 位置：`test/<插件名>/`

```
test/
├── bilibili/                 # 与 hoshino/modules/bilibili/ 对应
│   ├── sim_bootstrap.py      # 共享引导模块
│   ├── simulate_summary.py   # 全流程模拟
│   ├── diagnose_*.py         # 诊断脚本
│   ├── test_*.py             # 断言式测试
│   └── outputs/              # 测试输出存档
└── autopcr/                  # 与 hoshino/modules/autopcr/ 对应
    ├── README.md             # 两个修复工具的详细说明
    ├── autopcr_ocr_fix.py    # 修识图漏格（手动运行）
    └── autopcr_add_icons.py  # 补全识图模板库（手动运行）
```

`test/autopcr/` 下是两个 autopcr 修复工具，与 `test/bilibili/` 的测试脚本性质不同：
它们不为验证代码，而是**修改** autopcr 的文件（修复运行中的实际问题），因此是手动按需运行的一次性工具，
不使用 `sim_bootstrap` 那套加载机制。用法见该目录的 `README.md`。

**为什么不能放进 `hoshino/modules/<插件>/`**：见 2.1 节——插件目录下非下划线开头的 `.py` 会在 bot 启动时被当作插件导入。若确实要在插件目录内放子目录，命名必须以下划线开头（如 `_test/`），或保证目录内没有 `__init__.py`（但后者很脆弱，加个 `__init__.py` 就会开始被导入）。

`test/` 目录本身在 `.gitignore` 里（规则 `/test/`，已锚定仓库根目录，不会误伤模块内的同名子目录）。

### 3.2 编写方式

本仓库**没有 pytest**（未安装，也无 `pytest.ini` / `pyproject.toml`）。测试是**独立可执行脚本**：

```python
"""一句话说明这个脚本验证什么，以及怎么用。

用法: python test/bilibili/test_xxx.py [参数]
"""
import asyncio

def main():
    # 每个用例自己打印结果
    print("[PASS] 用例1: ...")

if __name__ == "__main__":
    asyncio.run(main())
```

约定：用 `print("[PASS] ...")` / `[FAIL]` 输出结果，失败时用 `assert` 让退出码非零。

### 3.3 运行方式

在**仓库根目录**执行即可（Python 会把脚本所在目录加入 `sys.path`，因此脚本间可直接 `from sim_bootstrap import ...`）：

```bash
python test/bilibili/test_config_bootstrap.py
python test/bilibili/simulate_summary.py "总结 BV1xx411c7mD"
```

### 3.4 关键模式：绕开 `import hoshino`

插件模块顶层通常 `from hoshino import Service`，而 `import hoshino` 会拉起 nonebot、Quart 等重依赖。测试里为了单独加载插件源码，用 `test/bilibili/sim_bootstrap.py` 的做法：

1. 把插件目录注册成内存中的独立包（如 `bilisim`），插件源文件**零改动**
2. `load_plugin_modules()` 返回 `_config` / `_bili_sub` / `_bili_llm` 等内部模块
3. `load_summary_module()` 进一步用最小桩替换 `hoshino.service` / `hoshino.typing`，从而加载入口模块本体，拿到其中函数的**真实实现**

**原则：优先调用插件的真实函数，不要在测试里手抄一份逻辑。** 手抄的副本会随插件改动悄悄过期（`simulate_summary.py` 就曾复刻了一份已被删除的发送逻辑）。若必须验证行为，用假对象（如 `_FakeBot` 收集 `send()` 的消息）驱动真实函数，而不是重写一遍。

> `sim_bootstrap.py` 的 `PLUGIN_DIR` 由本文件位置自动推导（`test/bilibili/` → 仓库根 → `hoshino/modules/bilibili`），换机器或移动仓库位置均无需修改。

---

## 4. 版本控制与 `.gitignore`

以下路径**不进版本控制**，改动它们不会被 git 记录，也**无法通过 git 找回**：

| 路径 | 原因 |
|---|---|
| `hoshino/config/` | 本地部署配置（含 `__bot__.py`、各插件 config） |
| `config.py` / `config.json` | 同上 |
| `hoshino/modules/autopcr/` | 整个 autopcr 模块被排除（**有意隔离**，上游有更新的 autopcr 会整体覆盖，不参与本仓库版本控制，勿清理此规则） |
| `/test/` | 测试脚本，**有意保持本地**（见下） |
| `log/` | 日志 |
| `res/`、`img/`、`image/` 等 | 资源 |
| `*.db`、`db.json` | 数据库 |
| `__pycache__/`、`*.py[cod]` | 字节码 |

**注意**：`hoshino/modules/bilibili/config.json` 这类文件因为 `config.json` 规则被忽略，但**同目录的 `config.json.example` 会被正常跟踪**——这正是模板文件存在的意义。

### 关于 `test/` 不纳入版本控制的决定

`test/` 有意保持在本地，不提交到仓库（本仓库是 `Ice9Coffee/HoshinoBot` 的公开 fork）。原因：

- 测试脚本绑定了具体的 B 站视频 ID 与本机环境 workaround（如 aiodns 解析器问题），对上游无参考价值
- `test/bilibili/outputs/` 下的运行存档含 API Key 前缀与 base_url，不宜进公开仓库

**代价**：`test/` 下的文件不受版本控制保护，删除或损坏后**无法通过 git 找回**（2026-09 已发生过 `tmp/` 下 3 个文件无声消失、无法恢复的情况）。有长期价值的脚本请另做备份。

---

## 5. 部署环境差异（重要陷阱）

本项目的部署**不止一台机器**。**两台都运行 Python 3.10**，代码层面无差异；差异只在 **OneBot 协议端实现**，这会导致「同一份插件代码，在一台正常、另一台报错」。

| | 本机（`E:\AI\HoshinoBot`） | 另一台 |
|---|---|---|
| Python | 3.10.11 | 3.10（与本机一致） |
| OneBot 实现 | **NapCat**（`C:\Windows\System32\NapCatQQ\napcat.mjs`） | **LLOneBot / LuckyLilliaBot** |
| 发送特性 | 无 self-echo 等待 | 群聊发送后等 QQ 回声，超时 10s 抛错 |

因此排查问题时**不要往 Python 版本差异上想**，协议端才是两台的真正分界。

**`waitForSelfEcho timeout` 是 LLOneBot 独有**（源码 `src/ntqqapi/api/msg.ts`），NapCat 里 grep 不到这个字符串。它的含义是：消息**已经发出**，只是没在 10 秒内等到 QQ 服务器的回声推送，LLOneBot 仍会返回 `retcode=1200` 报错。也就是说——**报错不等于发送失败**。

排查发送类问题时，**先确认对方用的是哪个协议端**，再去怀疑插件代码。建议 LLOneBot 升级到 v8.2.1+（v8.2.0 修复过「假在线：显示在线但收不到消息、发消息全部超时」），或统一换用 NapCat。

### 长文本发送

QQ 单条消息有长度上限，超长需要自行切块。现有两处实现可参考：

- `hoshino/modules/rss_juya/_spider.py:_chunk` — 阈值 **3000 字**，按预切分好的段（每个新闻条目一段）贪心聚合
- `hoshino/modules/bilibili/summary.py:_split_text` — 阈值 **3000 字**（`SINGLE_MSG_LIMIT`），优先在换行处断开以保护 Markdown 表格/列表，各块长度均分以避免尾部碎片

**不要使用合并转发卡片**：LLOneBot 的群合并转发同样会走 self-echo 等待，容易触发上述超时。bilibili 插件早期版本用过合并转发，现已改为纯切块发送。

---

## 6. 常见任务速查

| 任务 | 做法 |
|---|---|
| 新增插件 | 建 `hoshino/modules/<名>/`，写非下划线入口文件，加进 `MODULES_ON` |
| 拆分插件代码 | 内部模块加 `_` 前缀 |
| 加配置项 | 改 `config.json.example` + `_config.py` 的 `DEFAULT_CONFIG`（深合并会自动补全老用户配置） |
| 写测试 | 放 `test/<插件名>/`，独立脚本 + `print("[PASS]")`，用 `sim_bootstrap` 加载插件源码 |
| 调试插件 | 看 `log/error.log`；插件的 `sv.logger` 输出也在其中 |
| 群内开关服务 | `开启 <服务名>` / `关闭 <服务名>` |
| 语法检查 | `python -m py_compile <文件>`（用 Python310） |
| 查谁在用某函数 | `grep -rn "函数名" hoshino/modules/` |

---

## 7. 已知的待清理项

以下问题已确认存在，改动相关代码时可顺手处理，但**不要贸然扩大范围**：

- `hoshino/modules/bilibili/summary.py` 的发送路径当前**没有异常处理**：若 LLOneBot 抛 `waitForSelfEcho timeout`（消息其实已送达），异常会一路上抛并由框架记入日志，群内表现正常但日志有 error。若要消除噪音，需在此识别该错误并视为成功（历史版本曾把它当硬失败降级重试，已造成重复发送，故已移除）
- `tmp/` 目录已于 2026-09 清空删除，勿再使用；临时验证脚本直接放 `test/<插件名>/`，或跑完即删

### 已确认不处理（勿自行"清理"）

- **`hoshino/modules/autopcr/` 的 gitignore 规则**：有意隔离。autopcr 是**独立的嵌套 git 仓库**（有自己的上游），隔离便于整体覆盖更新
- **`test/` 不进版本控制**：有意保持本地，理由见第 4 节
- **`test/autopcr/` 下的两个 autopcr 修复脚本**：`autopcr_ocr_fix.py`（修识图漏格）与 `autopcr_add_icons.py`（补全模板库），手动按需运行的工具，说明见 `test/autopcr/README.md`；同样因 `/test/` 规则不受版本控制
