# Schedulr 课程表 · AstrBot 插件（双课表）

在 QQ 里查两份课表：两个班级、两个教务账号，各自独立刷新。

- 支持：`今天 / 明天 / 周几 / 本周 / 下周 / 第 N 周 / 下一节`
- 两个档案分别保存自己的 WebVPN Cookie，后台每 10 分钟各自刷新
- 查询时如果某个档案的缓存超过 30 分钟，会先尝试刷新再回答
- 只保存课程、时间、地点、教师；不保存学号、姓名等身份字段
- **不保存账号密码**，只保存可失效的 WebVPN 会话 Cookie

## 安装

把整个 `astrbot_plugin_schedulr` 文件夹放到 AstrBot 的插件目录：

```text
AstrBot/data/plugins/astrbot_plugin_schedulr/
├── main.py
├── metadata.yaml
├── _conf_schema.json
├── schedulr_core/
└── ...
```

AstrBot 跑在 Docker 里时，放到宿主机映射出来的 `data/plugins`，然后重启 AstrBot，或在 WebUI 插件页重载。

## 配置两个课表

WebUI → 插件 → **Schedulr 课程表** → 配置：

| 配置 | 说明 |
| --- | --- |
| `课表 1 名称` | 默认「一班」，用于命令里区分 |
| `课表 1 · Cookie` | 第一个账号的 WebVPN Cookie |
| `课表 1 · 学年/学期/开学日期` | 留空自动推断；开学日期可填实际开学那天 |
| `课表 2 名称` | 默认「二班」 |
| `课表 2 · Cookie` | 第二个账号的 WebVPN Cookie |
| `课表 2 · 学年/学期/开学日期` | 留空自动推断 |

开学日期填实际开学那天即可（例如 `2026-09-03`），插件会按那一周的周一作为教学第 1 周。

两个账号需要**分别登录**，各拿一份 Cookie。

### 怎么拿 Cookie

对每个账号重复一次：

1. 浏览器打开 <https://vpn.bitc.edu.cn/>，用该账号完成学校 IAM 登录。
2. 打开课表页：
   `https://jwxt.vpn.bitc.edu.cn/kbcx/xskbcx_cxXskbcxIndex.html?gnmkdm=N2151&layout=default`
3. 按 `F12` → `Network`（网络）→ 刷新页面。
4. 点任意一个发往 `jwxt.vpn.bitc.edu.cn` 的请求。
5. 在 `Request Headers` 里复制 `Cookie:` 的整行值（不要带 `Cookie:` 前缀）。
6. 填到对应档案的 Cookie 配置里，保存。

建议先在服务器上验证其中一份：

```bash
cd AstrBot/data/plugins/astrbot_plugin_schedulr
python tools/check.py --cookie-file /tmp/bitc_cookie_1.txt --term 1
```

`tools/check.py` 只打印课表摘要，不会回显 Cookie。

> Cookie 等同于这段时间的学校登录态。不要发群、不要提交 Git。
> 配置里的 Cookie 字段是 `secret`，WebUI 会隐藏显示。

## 使用

```text
/课表                  两个档案的今天
/课表 明天             两个档案的明天
/课表 一班             一班的今天
/课表 二班 明天        二班的明天
/课表 1 本周           一班本周
/课表 2 第10周         二班第 10 周
/课表 一班 下一节      一班下一节课
/课表 状态             两个档案的缓存和登录状态
/课表 刷新             两个档案立即刷新
/课表 二班 刷新        只刷新二班
```

档案选择器支持：配置里的名称、`1`/`2`、`一班`/`二班`、`一`/`二`。

别名：`/课程表`、`/timetable`、`/schedule`。

## 自动刷新与“最新”

- 插件每 60 秒检查一次，每个档案距离上次成功超过 `refresh_minutes`（默认 10 分钟）就各自重新拉取。
- 查询时如果该档案缓存超过 `query_refresh_minutes`（默认 30 分钟），会先刷新再回答；失败则退回缓存，并在 `状态` 里显示原因。
- `/课表 刷新` 会立即拉取（30 秒内不重复请求学校）。

“最新”的上限取决于学校会话：

- Cookie 有效时，插件确实会自己去教务取最新数据。
- 每次刷新会先访问 `vpn.bitc.edu.cn`，学校会下发新的 `_webvpn_key`（24 小时滑动续期），插件会自动把新 Cookie 写回配置。只要机器人每 24 小时内至少刷新一次，通常不需要手动换 Cookie。
- 如果机器人停摆超过 24 小时、或学校强制注销了会话，才需要重新登录该账号并把新 Cookie 填回对应档案。
- 两个档案互不影响：一个过期，另一个仍可正常刷新。

## 常见问题

**提示“登录态已失效”**

对应档案的 Cookie 过期了，重新登录该账号、复制新 Cookie。

**提示“学校 VPN 网关提示权限表不同步”**

学校网关会话/权限表异常。先在浏览器退出 WebVPN 再重新登录，复制新 Cookie；如果浏览器里也报同样的错，等几分钟再试。

**提示“无法连接学校 VPN”**

AstrBot 服务器访问不了 `vpn.bitc.edu.cn`：

```bash
curl -I https://vpn.bitc.edu.cn/
```

**Cookie 在电脑上能用，填到插件里却失效**

少数 WebVPN 会把会话绑定到登录时的客户端 IP。如果确认是这种情况，Cookie 方案在云服务器上不可用，需要改成“App 推送课表快照”或“插件用账号密码登录”的方案。

**两版课表周次不一样**

给两个档案分别设置 `开学日期`，或检查 `学年/学期`。插件优先用教务校历锚点，识别不到才用配置。

## 开发与测试

```bash
cd qqbot/astrbot_plugin_schedulr
python -m unittest discover -s tests -v
```

目录：

```text
schedulr_core/
├── bitc_client.py   # WebVPN Cookie → 课表 JSON
├── models.py        # 课程/节次/学期模型
├── parser.py        # 周次、节次、校历、课程解析
├── query.py         # 参数 → 回复文本
└── store.py         # 原子写入缓存
```
