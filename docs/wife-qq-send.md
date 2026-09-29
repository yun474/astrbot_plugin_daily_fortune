# 今日老婆 Markdown 发送约定

已在 wife.py 和 main.py 接入 QQ 官方群聊/私聊发送。单元测试覆盖请求体和重试，尚未连接真实 QQ 账号验收。

消息内容见 `wife-qq-markdown.md`，完整请求体见 `wife-qq-payload.json`：开头艾特触发用户，随后显示角色名、作品名和角色原图。

两个指令按钮为“今日老婆”“今日运势”，`action.type=2`、`enter=true`、`permission.type=2`。按钮 data 只放这四个汉字，不加斜杠、唤醒词或手工拼接机器人艾特。QQ 自动插入 @机器人，点击者成为新命令的发送者。

`force_verify_image_resource: true` 放在请求体顶层，与 `markdown`、`keyboard` 同级。首次发送就开启，使图片资源问题尽量在发送响应中暴露，不先发送可能缺图的消息。

已实现的重试策略：仅在平台明确返回图片拉取/转存失败时，等待 1 秒后带 `force_verify_image_resource=true` 最多重试三次（不含首次请求，可通过 image_host.retry_count 修改）；保持同一角色、作品和 URL，不重新抽取。明确失败后的重试保留原消息 ID 与发送序号。不对超时这种结果不明的请求盲目重发，不对权限、参数错误无限重试。botpy 异常只保留错误描述，因此当前按明确的图片下载/转存/校验失败描述识别；未知错误不重试。

MD 模式先将图片上传至配置的图床，再将返回的公网地址写入请求。force_verify_image_resource 用于校验，不会让不可达地址变得可达。两个功能共用图床配置与重试次数。

按当前需求直接发送自定义按钮，不以旧文档的“内邀”标注阻止发送。权限或参数错误会记录并报告失败，不盲目重试。

来源：
- [QQ 官方 Markdown 文档](https://bot.q.qq.com/wiki/develop/api-v2/server-inter/message/type/markdown.html)
- [QQ 官方按钮文档](https://bot.q.qq.com/wiki/develop/api-v2/server-inter/message/trans/msg-btn.html)
- [OlivOS 发送参数说明](https://doc.olivos.wiki/DevPlugin/API/)：包含 `force_verify_image_resource` 参数说明；本次未成功从 QQ 官方发送接口页面核对该字段，仍需真实平台验证。

图片必须按 QQ 官方示例写成 `![图片 #宽px #高px](URL)`，插件读取压缩后上传文件的真实尺寸。示例文件里的尺寸仅为占位示例。QQ 返回消息 ID 只能确认平台接收，不能当作客户端已成功显示图片的证明。
