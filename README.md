# Perlica 配置指南

## 如果你不是佩厨……

去 [identity](Script/modules/settings/identity) 把提示词改成你喜欢的角色就行了!

> 人设提示词拆成多份文本, 按 [settings.json](Script/modules/settings/settings.json) 里声明的顺序拼接成系统提示词: `identity`(角色设定) / `operators`(干员) / `language`(语言风格) / `abouttools`(工具使用规范). 换角色基本只要动 `identity`, 其余按需保留或删减.
>
> settings/ 里还躺着没被加载的 `infolib`(关系人背景) 和空的 `schedule`, 要用就自己加进 settings.json.

## 开始之前……

请参考 [config_example.json](Script/config_example.json) 写一份 config.json 放在同一目录下.

> 自己先跑 [requirements.txt](requirements.txt) (∠・ω< )⌒★
>
> config 有严格校验 (见 [config.py](Script/config.py)): 缺字段或填错类型会直接报错并指出具体是哪个键. 示例不一定跟得上最新字段, 缺的照 `Config` 补.

## 启动

常规就是进 `Script/` 里拿 python 直接跑:

    cd Script
    python main.py

> 图省事这样就行, 但直接跑有两个毛病: 佩丽卡是真会执行代码和系统命令的, 它拿到的就是你的权限, 一旦抽风很危险; 再就是只能前台挂着, 想常驻、崩了自动拉起还得另想办法.
>
> 所以推荐使用[进阶配置](#进阶配置), 虽然折腾一点但是确实安全可靠.

## NapCat

请自行安装配置 NapCat 并登录.

> 本项目默认使用 [NapCat WebSocket 服务端](https://napneko.github.io/config/basic), 但不强制.
> 想改的话或许要折腾一下？

## Model

自行配置模型并向 config.json 填入配置项.

> 参见 [config_example.json](Script/config_example.json).
> 一份 config.json 里可以放多套模型 (每套带 `multimodal` 标记). `using_model` 指定对话用的主模型; 多模态任务 (看图/看表情包/压缩长上下文) 会走 `using_multimodal` 指向的模型.

## Tool Call

工具相关的文件都收在 [tools/](Script/modules/tools/) 下:

- [tools_data.json](Script/modules/tools/tools_data.json) — 更清晰的工具配置;
- [tools/tools_manager.py](Script/modules/tools/tools_manager.py) — 交互式管理/添加工具, 并把 tools_data.json 导出成标准格式;
- [tools.json](Script/modules/tools/tools.json) — API 实际使用的标准 tool 格式;
- [tools/\_\_init\_\_.py](Script/modules/tools/__init__.py) — 工具定义与注册 (函数与 schema 一一对应);
- [tools/impl/](Script/modules/tools/impl/) — 各工具的具体实现 (闹钟/倒计时/日程/日记/天气/文件/农历/图片/代码执行/系统命令/生理状态等).

### Web Search

不保证可用性. 如果有条件请自行更换方式.

> 联网搜索使用 DuckDuckGo (`ddgs`), 抓网页正文使用 Jina Reader, 均为后台异步执行. 参见 [tools/\_\_init\_\_.py->web_search/read_web](Script/modules/tools/__init__.py)

### Weather

采用和风天气 API.
参见 [和风天气开发者服务](https://dev.qweather.com/)
自行配置后向 config.json 填入配置项.

> 实现见 [tools/impl/weather.py](Script/modules/tools/impl/weather.py)

### RAG

首次运行或每次更新知识源后, 跑一遍构建脚本:

    python -m modules.knowledge.infolib_init

> 参见 [modules/knowledge/infolib_init.py](Script/modules/knowledge/infolib_init.py).
> 数据管线见 [EndfieldLibrary/](Script/EndfieldLibrary/): [fz_wiki_sync/sync.py](Script/EndfieldLibrary/fz_wiki_sync/sync.py) 抓取终末地 Wiki 原文 (它是个 Next.js 站, 得直接调后端 API, 爬 HTML 没用), [prepare_rag.py](Script/EndfieldLibrary/fz_wiki_sync/prepare_rag.py) 整理成 [rag_source](Script/EndfieldLibrary/rag_source) 里的 Markdown, 构建出的索引放在 [EndfieldLibrary/storage](Script/EndfieldLibrary/storage). Embedding 使用本地 Ollama, 请按 [infolib_init.py](Script/modules/knowledge/infolib_init.py) 里的代码自行配置模型.
>
> 检索由工具 `search_knowledge` 走, 受 `config.enable_rag` 开关控制.

不配置不跑问题也不大 ~~, 就是佩丽卡查不了知识库会显得很无能~~ .

> 索引是磁盘型紧凑存储 (二进制向量 + jsonl), 检索时只读 mmap, 不会整库占内存; 知识库在运行中重建后, 检索侧会自动重载索引.
>
> [rag_pdf.py](Script/modules/knowledge/rag_pdf.py) 是多模态 PDF 检索的尝试, 未完成、未启用.

### Logging

自己去 [logger.py](Script/modules/core/logger.py) 改配置.

## (^_^)

config.json 内所有配置支持热更新, 无需重启程序即可修改配置.

## 进阶配置

为了您账户的数据安全, 强烈建议创建一个新的专属用户, 限定权限后启用程序.

以下教程**仅适用** **Linux** ~~*才不是因为我懒才没有配置 Windows !*~~ (◣ ‸ ◢)

假设专用账户的名字为 `perlica` :

1. 建用户并设密码:

       sudo useradd -m -s /bin/bash perlica
       sudo passwd perlica

2. 把整个项目交给它:

       sudo chown -R perlica:perlica /path/to/QBotPerlica

3. 启动方式二选一:

   - 前台直接跑 [run.sh](run.sh): 它会切到 `perlica`、进 `Script/`、用 `npsdk/bin/python` 起 [main.py](Script/main.py). 当前用户不是 `perlica` 时会提示你输密码; 不想每次输就自己配免密.
   - 后台常驻且免密: 照 [qbot.service.example](qbot.service.example) 整一份自己的 service (把用户名和项目路径换成自己的), 让 systemd 用 `User=perlica` 直接拉起来, 全程不用切用户:

         sudo cp qbot.service.example /etc/systemd/system/qbot.service
         # 按文件里的注释把占位改掉
         sudo systemctl daemon-reload
         sudo systemctl enable --now qbot.service

## 如果你是开发者……

会写 python 就行！

### 项目架构设计

[main.py](Script/main.py) 是程序的入口. 主循环监听 napcat 事件, 先由 [core/events.py](Script/modules/core/events.py) 把事件解析成"提示词片段"并标注紧急程度 (`Ignore` / `Normal` / `Important` / `Urgent`), 再决定立刻行动、攒起来等活跃时段再处理还是干脆忽略. 佩丽卡有自己的"作息": 活跃时间窗口与勿扰模式之外, 还有后台线程一直在跑的"生物节律"模拟 ([simulation/biosim/](Script/modules/simulation/biosim/))——会困、会饿、有精力、压力和情绪, 每来一个事件就把当前状态渲染成一行塞进提示词, 紧急事件还能把它强行吵醒.

聊天部分的启动是并发的, 但采用锁和打断设计确保同时只能做一件事情 (就像你玩手机那样), 而上下文得以保留; 这部分在 [core/act.py](Script/modules/core/act.py). 消息攒多了, 会在后台把历史用模型压成长文摘要 ([core/context_compress.py](Script/modules/core/context_compress.py)), 避免把上下文窗口撑爆.

这个框架高度依赖 tool call (因为设计上就是佩丽卡在~~玩手机~~使用终端), 因此一定要配置支持的模型. 工具的注册表在 [tools/\_\_init\_\_.py](Script/modules/tools/__init__.py), 具体实现按功能放在 [tools/impl/](Script/modules/tools/impl/) 下做隔离, 免得 [\_\_init\_\_.py](Script/modules/tools/__init__.py) 被直接改坏,~~以及方便 vibe coding 隔离环境避免 ai 瞎改~~. [tools_manager.py](Script/modules/tools/tools_manager.py) 和 [tools_data.json](Script/modules/tools/tools_data.json) 是为了管理 [tools.json](Script/modules/tools/tools.json) (api 要的标准 tool 格式) 做的脚本和简化版资源. 长耗时工具会放进后台线程跑, 先返回一个 `task_id`, 之后用 `get_tool_result` 取结果, 避免工具调用直接阻塞对话.

模块大致分为:

- [core/](Script/modules/core/) — 框架本体: [env.py](Script/modules/core/env.py) 全局状态与初始化, [act.py](Script/modules/core/act.py) 对话循环, [events.py](Script/modules/core/events.py) 事件解析, [history.py](Script/modules/core/history.py) 本地历史消息 (带索引, 重启不丢, 支持按 id 撤回/改), [qmessage.py](Script/modules/core/qmessage.py) QQ 消息段解析 (含引用/表情/图片), [chatwindow.py](Script/modules/core/chatwindow.py) "终端输入框", [logger.py](Script/modules/core/logger.py) 日志.
- [knowledge/](Script/modules/knowledge/) — 角色知识库 (RAG) 的构建与检索, 代码可以独立于项目运行.
- [settings/](Script/modules/settings/) — 人设/干员/语言/工具规范等提示词文本 (见文首).
- [simulation/](Script/modules/simulation/) — 模拟: [biosim/](Script/modules/simulation/biosim/) 生物节律 (效果只声明、引擎改状态, 可以对照说明书进行拓展, 说明书在 [biosim-effect.md](Script/modules/simulation/biosim-effect.md)), [lifesim.py](Script/modules/simulation/lifesim.py) 文本世界引擎原型 (还没做).
- 运行数据与资源: [history/](Script/history) 聊天记录, [diary/](Script/diary) 日记, [private_space/](Script/private_space) 机器人的专属文件夹 (代码执行沙箱也只能在里面写), [working_cache/](Script/working_cache) 闹钟/日程等运行状态, [Qface/](Script/Qface) QQ 表情资源 (多模态模型可以"看见"表情包), [EndfieldLibrary/](Script/EndfieldLibrary) 知识库数据.

部分模块可能未完成. 项目仍处于持续开发阶段, 已有的内容也可能会有较大变更.

部分模块是ai写的, 但我明确要求了接口和模块封闭性并通过了高强度实际使用和多次调试迭代.
项目框架是自己设计的, 框架上的代码绝大多数是手写的. 我会尽力做到即便不写注释代码也能自解释.
但不论怎么说, 你一定能看出来哪些是ai哪些是非遗纯手工~~因为只有ai喜欢疯狂写注释~~.

如果你还有任何问题, 请不要轰炸我 QAQ.

### 调试时快速重启

[logger.py](Script/modules/core/logger.py) 里的 `log_level` 设成 `DEBUG` 就是调试模式. 这时拿 `config.manager_id` 那个号私聊戳一戳佩丽卡, 它会直接抛 `UserRestart` 把整个进程重启 (抛在 [core/events.py](Script/modules/core/events.py), 接在 [main.py](Script/main.py): 用 `os.execv` 原样重跑一遍).

> 改完代码不用手动 Ctrl+C, `config.json` 也会跟着重新读. 只认私聊的戳一戳, 群里的不算, 也不是 `manager_id` 发的不管.
>
> `execv` 万一失败就非 0 退出, 交给 systemd / run.sh 重新拉进程——进程内是刷不出新代码的.
>
> `log_level` 改回 `INFO` 之后就失效了——线上总不能让谁戳一下就重启.

### 如果你想要做出贡献

那就 fork 吧!

### 其他说明

你能看到的应当是 [Perlica-public-backup](https://github.com/EasternAuroraRT/Perlica-public-backup) 仓库. 这是为了信息安全(涉及apikey等配置信息), 避免开发过程中不小心将不应该出现的信息上传到云端 commit, 设置的专门用于公开的同步仓库. 我会在检查文件并确定可以公开后, 及时将私有仓库同步过来. 因此不必担心时效性.

> 同步脚本是 [sync.sh](sync.sh): 本地历史不动, 只往公开仓库追加一个聚合提交.

## 后续开发计划

- 个人水平有限, 为了确保人设质量, 提示词很多很杂(也很史). 如果可能, 希望有大佬帮忙改改提示词, 也希望有同好帮忙整理剧情.

- [biosim](Script/modules/simulation/biosim/) 的数值和真实感还差得远, 欢迎帮忙调参或加效果.

- 目前支持的功能较少, 将逐步补全 QQ 上的互动功能.

- 目前多模态支持很朴素(?), 希望能有所改进.

- 为了让模型更贴合角色, 以后希望能做一个微调模型, 将角色设定和世界观喂进去, 就不用这么麻烦的提示词了, 也更不容易出戏.

- 目前AI模型还不能适应群友高频玩梗整活的语言风格, 希望提示词或微调能带来好转.

- 个人硬件设备有限, 暂时跑不了微调(哭).

## Thanks

感谢 [NapCat](https://napneko.github.io/guide/napcat) 提供的通信层接口

感谢 [napcat-sdk](https://github.com/faithleysath/napcat-sdk) 提供的语言层接口

感谢 [终末地 Wiki](https://www.fz.wiki) 和 [明日方舟 Wiki](https://prts.wiki) 的世界观/人设资料

感谢 [QQ Emoji 表情库](https://koishi.js.org/QFace/#/qqnt) 的表情资源

> 代码很史, 感谢各位耐心阅读

*如有侵权, 请联系删除.*
