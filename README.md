# Perlica 配置指南

> 自己先跑 `requirements.txt` (∠・ω< )⌒★

## NapCat

请自行安装配置 NapCat 并登录.

## Weather

采用和风天气 API.
参见 [和风天气开发者服务](https://dev.qweather.com/)
自行配置后向 `config.json` 填入配置项.

## Model

自行配置模型并向 `config.json` 填入配置项.

> 参见 `config_example.json`.

## Tool Call

`tool_manager.py` 辅助 管理/添加 工具;

`tools_data.json` 更清晰的工具配置.

## Web Search

不保证可用性。如果有条件请自行更换方式.

> 参见 `tools.py->web_search`

## RAG

运行前先跑一遍 `infolib_init.py`.

> 请参考代码自行配置 Embedding 模型.

## Logging

自己去 `logger.py` 改配置.

## ^_^

`config.json` 内所有配置支持热更新，无需重启程序即可修改配置.
