# 今日老婆 Markdown 发送约定

已在 wife.py 和 main.py 接入 QQ 官方群聊/私聊发送。单元测试覆盖请求体和重试，尚未连接真实 QQ 账号验收。

消息内容见 `wife-qq-markdown.md`，完整请求体见 `wife-qq-payload.json`：开头艾特触发用户，随后显示角色名、作品名和角色原图。

两个指令按钮为“今日老婆”“今日运势”，`action.type=2`、`enter=true`、`permission.type=2`。按钮 data 只放这四个汉字，不加斜杠、唤醒词或手工拼接机器人艾特。QQ 自动插入 @机器人，点击者成为新命令的发送者。

`force_verify_image_resource: true` 放在请求体顶层，与 `markdown`、`keyboard` 同级。首次发送就开启，使图片资源问题尽量在发送响应中暴露，不先发送可能缺图的消息。

已实现的重试策略：仅在平台明确返回图片拉取/转存失败时，等待 1 秒后带 `force_verify_image_resource=true` 重试一次；保持同一角色、作品和 URL，不重新抽取。明确失败后的重试保留原消息 ID 与发送序号。不对超时这种结果不明的请求盲目重发，不对权限、参数错误无限重试。botpy 异常只保留错误描述，因此当前按明确的图片下载/转存/校验失败描述识别；未知错误不重试。

此字段用于校验，不会让不可达的 GitHub URL 自动变得可达。重试仍失败时应报告图片源失败；稳定部署可改用自有公网图床。

按当前需求直接发送自定义按钮，不以旧文档的“内邀”标注阻止发送。权限或参数错误会记录并报告失败，不盲目重试。

来源：
- [QQ 官方 Markdown 文档](https://bot.q.qq.com/wiki/develop/api-v2/server-inter/message/type/markdown.html)
- [QQ 官方按钮文档](https://bot.q.qq.com/wiki/develop/api-v2/server-inter/message/trans/msg-btn.html)
- [OlivOS 发送参数说明](https://doc.olivos.wiki/DevPlugin/API/)：包含 `force_verify_image_resource` 参数说明；本次未成功从 QQ 官方发送接口页面核对该字段，仍需真实平台验证。
