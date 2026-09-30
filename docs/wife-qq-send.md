# 今日老婆 Markdown 发送约定

已在 wife.py 和 main.py 接入 QQ 官方群聊/私聊发送。测试使用本地 HTTP 服务检查实际发出的 JSON、鉴权头、平台错误码与重试，覆盖今日老婆和今日运势；尚未连接真实 QQ 账号验收。

消息内容见 `wife-qq-markdown.md`，完整请求体见 `wife-qq-payload.json`：开头艾特触发用户，随后显示角色名、作品名和角色原图。

两个指令按钮为“今日老婆”“今日运势”，`action.type=2`、`enter=true`、`permission.type=2`。按钮 data 只放这四个汉字，不加斜杠、唤醒词或手工拼接机器人艾特。QQ 自动插入 @机器人，点击者成为新命令的发送者。

`force_verify_image_resource: true` 放在 `markdown` 对象内，与 `content` 同级。QQ 官方群聊、单聊发送接口都将它定义为 `MessageMarkdown` 的字段；放在请求体顶层不符合该结构。首次发送就开启，图片转存失败时由平台返回错误并阻止发送。

已实现的重试策略：仅在平台返回图片转存错误码 `304010`／`40034004`／`40034141` 时，等待 1 秒后默认最多重试三次（不含首次请求，可通过 image_host.retry_count 修改）；每次请求均保持 `markdown.force_verify_image_resource=true`，保持同一角色、作品和 URL，不重新抽取。明确失败后的重试保留原消息 ID 与发送序号。不对超时这种结果不明的请求盲目重发，不对权限、参数错误重试。发送复用 botpy 管理的鉴权与 HTTP 会话，直接解析响应以保留 `err_code`（兼容 `code`）、HTTP 状态和 trace_id，避免 botpy 异常丢失错误码；不再按错误文案猜测是否重试。

MD 模式先将图片上传至配置的图床，再将返回的公网地址写入请求。force_verify_image_resource 用于校验，不会让不可达地址变得可达。两个功能共用图床配置与重试次数。

按当前需求直接发送自定义按钮，不以旧文档的“内邀”标注阻止发送。权限或参数错误会记录并报告失败，不盲目重试。

来源：
- [QQ 官方 Markdown 文档](https://bot.q.qq.com/wiki/develop/api-v2/server-inter/message/type/markdown.html)
- [QQ 官方按钮文档](https://bot.q.qq.com/wiki/develop/api-v2/server-inter/message/trans/msg-btn.html)
- [QQ 官方群聊发送接口](https://bot.q.qq.com/wiki/develop/api-v2/autogen/api/v2_groups_group_openid_messages.post.html)：`MessageMarkdown.force_verify_image_resource` 与 `40034004`。
- [QQ 官方单聊发送接口](https://bot.q.qq.com/wiki/develop/api-v2/autogen/api/v2_users_user_openid_messages.post.html)：同样在 `MessageMarkdown` 内定义图片转存校验。
- [QQ 官方 API 调用指南](https://bot.q.qq.com/wiki/develop/api-v2/dev-prepare/api-call-guide.html)：按错误码判断失败，`304010` 为图片转存错误；trace_id 用于问题排查。

图片必须按 QQ 官方示例写成 `![图片 #宽px #高px](URL)`，插件读取压缩后上传文件的真实尺寸。示例文件里的尺寸仅为占位示例。QQ 返回消息 ID 只能确认平台接收，不能当作客户端已成功显示图片的证明。
