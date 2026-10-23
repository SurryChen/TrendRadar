# TrendRadar 登录管理后台

基于 [sansan0/TrendRadar](https://github.com/sansan0/TrendRadar)，以独立的 `trendradar/web/` 模块补充本地后台。采集、筛选、报告仍调用上游引擎，原有 `python -m trendradar` 不变。

## 本地运行（macOS / Linux，Python 3.12+）

在仓库根目录执行：

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install -e '.[web]'
.venv/bin/python -m trendradar.web init --username admin
.venv/bin/python -m trendradar.web serve
```

打开 <http://127.0.0.1:18765>。初始化时在终端输入至少 12 字符的密码，也可使用 `init --generate-password` 生成临时密码。密码不会写入源码；登录后可在「账号设置」更改。

自定义数据目录：全局参数必须放在子命令前，两次使用同一路径：

```bash
.venv/bin/python -m trendradar.web --data-dir /path/to/private-data init
.venv/bin/python -m trendradar.web --data-dir /path/to/private-data serve --port 18765
```

不要使用原版 `http.server` / GitHub Pages 等静态服务暴露这份工作目录，否则会绕过登录。

## 当前功能

- 一个管理员账号；不开放注册。所有登录设备共享配置、任务和报告，尚未提供多用户隔离、OAuth、邮箱找回。
- 登录、退出、修改密码。PBKDF2-SHA256 加盐哈希；随机会话保存在 SQLite；12 小时后过期，退出撤销当前会话，改密撤销全部会话；登录连续失败限流。
- 登录后直接进入上游 `docs/index.html` 原版可视化配置编辑器，保留源码与表单双栏、关键词、时间线、拖拽导入、复制等功能。
- 在原编辑器下方补充一条工具栏：保存到服务器、手动采集、报告与日志、AI 关注方向、账号、退出。报告与账号使用弹窗，不另做工作台。
- 配置从后端读取，表单与源码仍按上游逻辑联动；点击「保存到服务器」保存所有已改文件，Cmd/Ctrl+S 同样保存。关闭未保存的页面会提示。多文件逐份保存，后续文件校验失败不会回滚此前已保存的文件，提示后可继续修复。
- 服务模式将上游浏览器草稿存储替换为内存，不读取旧 localStorage 草稿、不把含密钥的配置写入浏览器持久存储；刷新会重读服务器。单独打开上游静态编辑器的行为不变。
- 「报告与日志」显示采集状态、最近 30 份报告与最近 16KB 日志。
- YAML 格式与上游加载检查、当前选用时间线校验；保存原文，不重新序列化。保存时比较文件版本，防止多页互相覆盖；备份旧文件。
- 手动运行一个采集子进程；运行期间禁止再次采集或保存配置。最长 15 分钟；服务停止时结束子进程。服务重启会保留上次状态并标记中断任务。
- 报告、API、配置均需登录；报告在浏览器沙箱中运行；路径穿越与符号链接逃逸检查；同源请求与 CSRF 校验。无公开文件目录。

初始化会复制官方配置到独立工作目录，并关闭通知、AI 分析、翻译、调度，使用关键词筛选和本地存储。之后更改可在页面保存，下一次采集生效。启用 AI 会使用所配模型服务；启用通知后，采集可能发送到所配渠道。没有新增自动采集定时器，时间线仅决定单次运行的策略。

`采集完成` 表示上游进程正常退出，部分来源仍可能失败，需结合报告和日志查看；不会把它当作所有来源都成功。

## 数据与备份

默认 `.web-data/`：

```text
.web-data/
├── auth.sqlite3       # 密码哈希、会话、登录限流
├── config/            # 真正被 Web 采集使用的配置
├── backups/           # 保存前的配置备份，可能包含密钥
├── output/            # 生成的数据库与 HTML 报告
├── job.json           # 最近任务状态
├── collection.log     # 最近任务日志
└── server.lock        # 单进程锁
```

此目录被 Git 忽略。文件权限为当前用户私有；备份同样可能含 API Key / Webhook，请作为私密数据保存。Web 配置不会改写仓库的 `config/` 模板。新版本新增的模板文件会复制到运行目录，已有文件不会覆盖；配置结构升级应按上游说明人工合并。

日志与备份目前没有自动清理策略；按需归档。更换机器时停止服务后备份整个工作目录。忘记密码需停机后由本机管理员处理凭据库，不提供公开重置入口。

## 服务器部署

默认仅监听 `127.0.0.1`。如需其他设备访问，应在 HTTPS 反向代理后运行，显式设置浏览器实际使用的域名：

```bash
.venv/bin/python -m trendradar.web serve \
  --host 127.0.0.1 --port 18765 \
  --origin https://radar.example.com --secure-cookie
```

代理转发时保留 `Host: radar.example.com`，代理本地端口，不公开 `.web-data/` 文件夹，也不要单独静态托管报告。此后台使用单 worker / 单机文件锁，不能以多个 worker 运行。已在 macOS 验证；Windows 的文件锁适配尚未实现。

## 合并官方更新

保留 `master` 跟踪官方版本，在 `codex/web-admin` 上维护改动。以下使用本机代理 Git 的 `github` 命令（其他环境可换成 `git`）：

```bash
github remote -v
# 如尚无 upstream：
github remote add upstream https://github.com/sansan0/TrendRadar.git

github fetch upstream
github switch master
github merge --ff-only upstream/master
github switch codex/web-admin
github merge master
.venv/bin/python -m pip install -e '.[web]'
LITELLM_LOCAL_MODEL_COST_MAP=True .venv/bin/python -m unittest discover -s tests -p test_web_admin.py -v
github push origin codex/web-admin
```

升级前停止服务，备份 `.web-data/`；解决冲突、运行测试并核对配置升级说明后再启动。核心改动集中在新增模块，原版 `docs/` 文件没有改动。服务端读取原 HTML 并注入独立适配脚本；上游若调整 DOM ID 或编辑器函数，需同步验证适配层。现有文件只修改依赖、入口与此 README 提示，减少上游冲突。官方采集引擎变动仍需验证，不能保证每次自动合并无冲突。

## 测试

```bash
LITELLM_LOCAL_MODEL_COST_MAP=True .venv/bin/python -m unittest discover -s tests -p test_web_admin.py -v
```

测试不发送通知、不调用模型、不请求真实资讯来源。覆盖认证边界、会话撤销与过期、限流、CSRF、配置冲突与持久化、报告沙箱与路径逃逸、并发任务限制。真实来源可用性通过手动采集另行验证。

原版编辑器使用 Tailwind、Font Awesome、js-yaml、Sortable CDN；首次打开需能访问对应 CDN。编辑器响应仅放行这些固定脚本来源与上游所需内联事件，报告仍使用隔离沙箱。
