# 今日运势与今日老婆

一个 AstrBot 每日抽签插件，给群友看看运势，再抽一位二次元老婆。使用免费图源，无需申请 API Key。

- **今日运势**：用户头像搭配二次元插画、吉凶、宜忌和一言，文字使用半透明面板，不显示昵称。
- **今日老婆**：抽取角色并展示角色名、所属作品和图片；QQ 官方群聊、私聊使用 Markdown，附带「今日老婆」「今日运势」按钮。
- **每天固定**：按北京时间每日更新，同一用户在同一平台实例内当天结果不变，重启后仍可保留。

没有签到、经验、等级或货币系统。

## 效果预览

![今日运势预览](docs/preview.png)

运势卡会使用发送者的头像，图中为演示头像。

今日老婆在 QQ 官方群聊、私聊中的消息内容如下，图片与按钮的外观由 QQ 客户端呈现：

> @用户
>
> 您的今日老婆是：**芙宁娜**
>
> 作品：原神
>
> （角色图片）
>
> 【今日老婆】　【今日运势】

## 安装

需要 **AstrBot 3.5.3 或以上版本**。支持 QQ 官方 WebSocket、QQ 官方 Webhook 和 OneBot（aiocqhttp）。

### 1. 安装插件

在 AstrBot 插件管理中选择从链接安装，填入仓库地址：

```text
https://github.com/yun474/astrbot_plugin_daily_fortune
```

也可以将仓库放入 `AstrBot/data/plugins/astrbot_plugin_daily_fortune/`。

### 2. 准备图片渲染环境

今日运势需要 Chromium 渲染图片。在 **AstrBot 使用的 Python 环境**中，进入插件目录后执行：

```bash
python -m pip install -r requirements.txt
python -m playwright install chromium
```

Linux / Docker 环境还需要浏览器运行库和中文字体。Debian / Ubuntu 可执行以下命令，其中系统依赖安装需要 root 权限：

```bash
python -m playwright install-deps chromium
apt-get update
apt-get install -y fonts-noto-cjk
```

Docker 部署请在运行 AstrBot 的容器内安装，并将相关安装步骤写入自定义镜像，避免重建容器后丢失。浏览器应安装在 AstrBot 运行用户可访问的位置。

如果已经安装 Chrome / Chromium，可以在插件配置中填写 `browser_executable_path`，使用现有浏览器。

### 3. 启用插件

在插件管理中启用或重载插件，发送下方命令即可使用。

## 使用方法

| 功能 | 命令 | 别名 |
| --- | --- | --- |
| 抽取今日运势 | `/今日运势` | `/jrys`、`/运势` |
| 抽取今日老婆 | `/今日老婆` | `/jrlp`、`/抽老婆` |

上表以 `/` 为命令前缀，实际以 AstrBot 配置为准。QQ 官方群聊中先 @机器人，再发送命令。

QQ 官方群聊和私聊的今日老婆消息会在开头 @发送者，并提供两个快捷按钮，点击即可触发对应命令。OneBot 和频道场景使用普通文字与角色原图。

今日老婆不查询昵称，也不合成头像；今日运势只获取头像，不查询昵称。

## 配置说明

在 AstrBot 插件管理中打开本插件的配置页面，修改并保存。

| 配置项 | 用途 | 默认设置 |
| --- | --- | --- |
| `background_url` | 运势卡背景，可填图片直链或本地路径 | 妖狐二次元图库 |
| `background_credit` | 运势卡上的背景来源标注 | 妖狐图库 |
| `hitokoto_api` | 一言接口，留空使用内置句子 | 一言动画、漫画分类 |
| `browser_executable_path` | Chrome / Chromium 可执行文件路径 | 留空使用 Playwright Chromium |
| `wife_list_url` | 今日老婆角色列表地址 | `https://animewife.dpdns.org/list.txt` |
| `wife_image_base` | 今日老婆图片的公网基础地址 | `https://raw.githubusercontent.com/monbed/wife/main/` |

### 更换运势背景

`background_url` 应直接返回图片，不要填写返回 JSON 的接口地址。本地相对路径以插件目录为基准，例如 `assets/default_background.jpg`；使用本地图片后，不再请求在线背景图源。

更换图片时，请同时修改 `background_credit` 中的来源标注。

### 更换角色图库

`wife_list_url` 指向纯文本列表，每行一张图片的相对路径，例如：

```text
img2/原神!芙宁娜.jpg
```

插件从文件名中读取作品名和角色名，并将相对路径拼接到 `wife_image_base` 后获取图片。使用自建图床或镜像时，目录结构需要与列表保持一致，图片地址也必须能被 QQ 服务器公开访问。

列表每天缓存一次。更换图库不会改变用户当天已经抽到的角色。

## 常见问题

### 今日运势只返回文字，没有图片

通常是浏览器没有安装、运行依赖缺失，或 `browser_executable_path` 填写错误。请确认安装命令使用的是 AstrBot 的 Python 环境，并查看 AstrBot 日志中的「今日运势图片生成失败」记录。

### 图片中的中文显示为方框

在 AstrBot 所在系统或容器中安装中文字体，例如 `fonts-noto-cjk`，然后重启 AstrBot。当天已缓存的图片需要清理后重新生成。

### 今日老婆图片发送失败

默认图片来自 GitHub 原图地址，QQ 服务器可能无法稳定拉取。可以将 `wife_image_base` 改为可公开访问的同路径图床或镜像。

插件已启用图片资源校验，明确的图片拉取失败会自动重试一次；失败后会保留当天角色，不会重新抽取。其他发送错误请查看 AstrBot 日志中的「今日老婆 Markdown 发送失败」记录。

### 为什么重复发送命令，结果没有变化？

这是每日抽签的正常行为。运势与角色按北京时间每天更新，当天重复请求会复用结果。

数据保存在 `data/plugin_data/astrbot_plugin_daily_fortune/` 下。其中 `cards/` 是运势图片缓存；如需重新生成图片，可删除对应日期目录。清除成图后，吉凶宜忌不变，插画和一言可能变化。

## 来源与许可

作者：[yun474](https://github.com/yun474)。代码采用 [MIT 许可证](LICENSE)。

- [astrbot_plugin_jrys](https://github.com/fiatlux2333/astrbot_plugin_jrys)：每日抽签与 HTML 截图思路参考。
- [一言](https://developer.hitokoto.cn/sentence/)：运势卡句子来源。
- [妖狐图库](https://acg.yaohud.cn/)：默认运势背景来源，仅限非商业用途。
- [monbed/wife](https://github.com/monbed/wife)：今日老婆角色图库。

第三方插画版权归原作者，不随代码以 MIT 许可重新授权；图片与预览素材说明见 [图片来源](assets/NOTICE.md)。
