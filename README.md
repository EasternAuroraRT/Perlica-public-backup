# Perlica 配置指南

## 如果你不是佩厨……

去 [identity](Script/modules/settings/identity) 把提示词改成你喜欢的角色就行了!

## 开始之前……

请参考 [config_example.json](Script/config_example.json) 写一份 config.json 放在同一目录下.

> 自己先跑 [requirements.txt](requirements.txt) (∠・ω< )⌒★

## NapCat

请自行安装配置 NapCat 并登录.

> 本项目默认使用 [NapCat WebSocket 服务端](https://napneko.github.io/config/basic), 但不强制.
> 想改的话或许要折腾一下？

## Model

自行配置模型并向 config.json 填入配置项.

> 参见 [config_example.json](Script/config_example.json).

## Tool Call

[tool_manager.py](Script/modules/tool_manager.py) 辅助 管理/添加 工具;

[tools_data.json](Script/modules/tools_data.json) 更清晰的工具配置.

### Web Search

不保证可用性. 如果有条件请自行更换方式.

> 参见 [tools.py->web_search](Script/modules/tools.py)

### Weather

采用和风天气 API.
参见 [和风天气开发者服务](https://dev.qweather.com/)
自行配置后向 config.json 填入配置项.

### RAG

运行前先跑一遍 [infolib_init.py](Script/modules/infolib_init.py).

请参考代码自行配置 Embedding 模型.

> 不配置不跑问题也不大~~, 就是佩丽卡查不了知识库会显得很无能~~.

### Logging

自己去 [logger.py](Script/modules/logger.py) 改配置.

## (^_^)

config.json 内所有配置支持热更新, 无需重启程序即可修改配置.

## 如果你是开发者……

会写 python 就行！

### 项目架构设计

[main.py](Script/main.py) 是程序的入口. 主循环监听napcat事件并构造提示词, 随后调用chat模块生成响应.
为了确保连续收到多条消息时不造成阻塞, chat的启动是并发的, 但采用了锁和打断设计确保同时只能做一件事情（就像你玩手机那样）而上下文得以保留.

这个框架高度依赖 tool call (因为设计上就是佩丽卡在~~玩手机~~使用终端), 因此一定要配置支持的模型.
于是核心功能代码在 [tools.py](Script/modules/tools.py), 拓展功能主要就是往这里面写东西.
[tool_manager.py](Script/modules/tool_manager.py) 和 [tools_data.json](Script/modules/tools_data.json) 是为了管理 [工具 json](Script/modules/tools.json) 做的脚本和简化版资源, 毕竟 api 要的 tool 格式还是太繁琐了.
其他模块则主要是对 tools 的具体实现。采用模块化设计是为了去耦合, 降低 tools.py 被直接改坏的风险,~~以及方便 vibe coding 隔离环境避免 ai 瞎改~~. 通过看 tools.py 你应当能大致了解每个模块是干什么的.

但其中 [infolib.py](Script/modules/infolib_init.py) 是为了管理角色知识库的代码, 每次更新知识库后应当跑一遍来生成搜索索引. 这个代码可以独立于项目运行; 知识库也可以运行时热更新 (代价是每次查都重新读取索引).

[tools.py](Script/modules/tools.py) 提供了长耗时任务的解决方法, 这是为了避免工具调用直接阻塞对话.
[logger.py](Script/modules/logger.py) 定义了日志相关内容, 你可以改格式改输出方式改你想改的任何东西, 反正整个项目都用的那个.
部分模块可能未完成. 项目仍处于持续开发阶段, 已有的内容也可能会有较大变更.

部分模块是ai写的, 但我明确要求了接口和模块封闭性并通过了高强度实际使用和多次调试迭代.
项目框架是自己设计的, 框架上的代码绝大多数是手写的. 我会尽力做到即便不写注释代码也能自解释.
但不论怎么说, 你一定能看出来哪些是ai哪些是非遗纯手工~~因为只有ai喜欢疯狂写注释~~.

如果你还有任何问题, 请不要轰炸我 QAQ.

### 如果你想要做出贡献

那就 fork 吧!

### 其他说明

你能看到的应当是 [Perlica-public-backup](https://github.com/EasternAuroraRT/Perlica-public-backup) 仓库. 这是为了信息安全(涉及apikey等配置信息), 避免开发过程中不小心将不应该出现的信息上传到云端 commit, 设置的专门用于公开的同步仓库. 我会在检查文件并确定可以公开后, 及时将私有仓库同步过来. 因此不必担心时效性.

## 后续开发计划

- 个人水平有限, 为了确保人设质量, 提示词很多很杂(也很史). 如果可能, 希望有大佬帮忙改改提示词, 也希望有同好帮忙整理剧情.

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
