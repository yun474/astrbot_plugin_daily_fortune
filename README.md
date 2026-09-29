# 今日运势与今日老婆

一个 AstrBot 每日抽签插件，给群友看看运势，再抽一位二次元老婆。使用免费图源，无需申请 API Key。

- **今日运势**：用户头像搭配二次元插画、吉凶、宜忌和一言，文字使用半透明面板，不显示昵称。
- **今日老婆**：抽取角色并展示角色名、所属作品和图片。
- **可选 MD 模式**：两项功能均可在 QQ 官方群聊、私聊中发送 Markdown，附带「今日老婆」「今日运势」按钮；需要配置图床。
- **每天固定**：按北京时间每日更新，同一用户在同一平台实例内当天结果不变，重启后仍可保留。

没有签到、经验、等级或货币系统。

## 效果预览

![今日运势预览](docs/preview.png)

运势卡会使用发送者的头像，图中为演示头像。

开启今日老婆 MD 模式后，QQ 官方群聊、私聊中的消息内容如下，图片与按钮的外观由 QQ 客户端呈现：

> @用户
>
> 您的今日老婆是：**芙宁娜**
>
> 作品：原神
>
> （角色图片）
>
> 【今日老婆】　【今日运势】

非 Markdown 场景发送合成图：完整角色插画下方配有半透明信息栏，展示用户头像、角色名和作品名。

![今日老婆合成图预览](docs/wife-preview.png)

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
| 获取自己的运势背景原图 | `/运势原图` | — |
| 获取自己的老婆原图 | `/老婆原图` | — |

上表以 `/` 为命令前缀，实际以 AstrBot 配置为准。QQ 官方群聊中先 @机器人，再发送命令。

两项功能默认发送合成图。QQ 官方群聊和私聊可分别开启 `qq_fortune_markdown`（今日运势）和 `qq_wife_markdown`（今日老婆）：消息开头 @发送者，并提供两个快捷按钮。OneBot 和频道场景仍发送合成图。

两项功能均不查询昵称。运势卡和今日老婆合成图只获取用户头像；老婆 MD 展示角色原图，运势 MD 展示完整运势卡。

### 获取原图

先抽取今日运势或今日老婆，再发送对应的原图指令。机器人只发送图片，不附带提示文字，也不叠加头像和信息栏。

在指令后通过 QQ 的 @功能选择用户，可以获取对方当天的原图，例如：

```text
@机器人 /运势原图 @用户
@机器人 /老婆原图 @用户
```

不加 @用户时获取自己的原图；添加多个用户时取第一个。查询范围是当前平台实例内的当天结果，不会为对方重新抽取。没有当天记录或读取失败时不发送消息，可在 AstrBot 日志中检查读取错误。

运势背景按原始分辨率保存为 PNG，随图片缓存清理；安装此功能前生成的旧运势卡没有保存背景原图，需等下一次生成新卡。老婆原图使用已抽中角色的图片地址。

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
| `qq_wife_markdown` | 今日老婆 MD 模式，需要配置图床 | 关闭 |
| `qq_fortune_markdown` | 今日运势 MD 模式，需要配置图床 | 关闭 |
| `show_image_host` | 展开／收起图床配置，不影响运行 | 收起 |
| `cache_retention_days` | 缓存保留天数，包含今天，范围 1～365 | `7` |
| `cache_cleanup_interval_hours` | 自动清理间隔，范围 1～168 小时 | `6` |
| `wife_catalog_cache_hours` | 角色列表缓存时间，范围 1～168 小时 | `24` |
| `request_timeout_seconds` | 图片、角色列表及一言下载超时，范围 5～120 秒 | `15` |
| `render_concurrency` | 运势卡与老婆合成图共用的生成并发数，范围 1～4 | `2` |

保存配置后重载插件生效。调整缓存和并发设置不会重新抽取当天运势背景。

### 开启 MD 模式与配置图床

1. 打开「展开图床配置」，填写上传接口和鉴权信息。
2. 按图床接口文档填写文件字段与返回地址路径，下表默认格式兼容兰空 V2。
3. 分别开启「今日运势：MD 模式」或「今日老婆：MD 模式」，保存并重载插件。
4. 配置完成后可关闭「展开图床配置」收起详细设置，上传功能仍正常工作。

| 图床配置项 | 填写方式 / 默认值 |
| --- | --- |
| `upload_url` | 完整 POST 上传接口，例如 `https://你的图床/api/v1/upload` |
| `authorization` | Authorization 请求头，例如 `Bearer 你的Token`；匿名接口可留空 |
| `file_field` | multipart 文件字段名，默认 `file` |
| `url_path` | 响应 JSON 中图片直链路径，默认 `data.links.url`；也支持 `data.0.url` 等数组路径 |
| `retry_count` | 失败后最多重试次数，默认 `3`，范围 0～10；不含首次请求 |
| `timeout_seconds` | 每次图床上传超时，默认 `30` 秒，范围 5～120 |

图床需支持 `multipart/form-data` 上传并返回 JSON，图片直链必须允许 QQ 服务器公开访问。默认格式参考 [兰空 V2 上传配置](https://github.com/lsky-org/lsky-pro/discussions/357)。仅支持这一类 HTTP 上传接口，不直接支持 S3 签名上传或网页表单登录。

运势 MD 上传完整运势卡，老婆 MD 下载选中的角色图片再上传。相同图片在当天复用已上传地址；本地上传记录随图片缓存清理。图床上的远程图片需通过图床自身的保留策略清理。

图床网络错误、429 和 5xx 响应，以及 QQ 明确的图片下载／校验失败，默认最多重试 3 次（共 4 次尝试）。鉴权错误、响应字段错误不重试；QQ 发送超时因结果不明也不自动重发。每次 QQ MD 请求都开启 `force_verify_image_resource=true`。

旧版已开启的老婆 MD 配置会保留，请补填图床或关闭该开关。未配置图床时不会使用源站链接发送 MD。图床设置的收起功能依赖 AstrBot 配置页对条件显示的支持，旧版面板可能始终展示这些字段。

### 更换运势背景

`background_url` 应直接返回图片，不要填写返回 JSON 的接口地址。本地相对路径以插件目录为基准，例如 `assets/default_background.jpg`；使用本地图片后，不再请求在线背景图源。

更换图片时，请同时修改 `background_credit` 中的来源标注。

### 更换角色图库

`wife_list_url` 指向纯文本列表，每行一张图片的相对路径，例如：

```text
img2/原神!芙宁娜.jpg
```

插件从文件名中读取作品名和角色名，并将相对路径拼接到 `wife_image_base` 后获取图片。使用自建图床或镜像时，目录结构需要与列表保持一致，图片地址也必须能被 QQ 服务器公开访问。

列表默认缓存 24 小时，过期后在下次抽取时刷新。更换图库不会改变用户当天已经抽到的角色。

### 缓存清理

插件启动时立即清理一次，之后默认每 6 小时自动清理，即使没有新命令也会执行。默认保留包含今天在内的 7 个北京时间日期，设 `cache_retention_days=1` 可只保留当天。

- `cards/`：按日期保存运势合成图、背景原图及老婆合成图，过期目录统一删除。
- `wife/`：删除过期用户抽取记录，角色列表只保留一份并在需要时刷新。
- 异常退出留下的临时图片和临时记录，超过 24 小时后删除。

清理仅针对插件数据目录，不删除配置中的本地图片、仓库预览图或当天结果。图片下载和浏览器页面使用内存，不另存头像缓存。运行数据位于 `data/plugin_data/astrbot_plugin_daily_fortune/`，无需手动定期清空。

## 常见问题

### 今日运势只返回文字，没有图片

通常是浏览器没有安装、运行依赖缺失，或 `browser_executable_path` 填写错误。请确认安装命令使用的是 AstrBot 的 Python 环境，并查看 AstrBot 日志中的「今日运势图片生成失败」记录。

### 图片中的中文显示为方框

在 AstrBot 所在系统或容器中安装中文字体，例如 `fonts-noto-cjk`，然后重启 AstrBot。当天已缓存的图片需要清理后重新生成。

### 今日老婆图片发送失败

MD 模式下请确认图床上传成功，返回的是公开图片直链而非预览网页，并检查 QQ 服务器是否能访问它。若机器人本身无法下载 GitHub 角色原图，可以将 `wife_image_base` 改为同路径镜像。

插件已启用图片资源校验，默认失败后最多重试 3 次，可在图床配置中修改；失败后会保留当天角色，不会重新抽取。具体错误请查看 AstrBot 日志中的「今日老婆 Markdown 发送失败」记录。

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
