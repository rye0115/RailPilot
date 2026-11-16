# 08 · 数据安全审计（参考实现）

> 审计日期：2026-11-14 · 方式：**静态审计，不执行未知代码/二进制**。
> 审计对象为公开仓库的当前 commit；行号会随上游变化，引用时以 commit 为准。

## 1. 结论

**没有找到一个“可以直接拿来存我私人数据”的现成项目。** 它们各有优点，但都至少有一项不适合“私人数据安全”的默认行为：

| 项目 | 安全亮点 | 关键风险 | 结论 |
|---|---|---|---|
| `c66-m/12306-ticket-monitor` | 乘车人证件/手机号 DPAPI 加密；SMTP 授权码 DPAPI；日志脱敏；`.gitignore` 完整 | **会话 Cookie 明文 JSON**；`--disable-blink-features=AutomationControlled`；第三方 IP 查询/FOFA CDN 扫描 | 参考架构/加密思路，**不复制 Cookie/反检测代码** |
| `lyt26/12306-helper` | 扫码登录、不收密码；依赖极少；无自动支付 | **Cookie 明文 JSON**；支付浏览器 profile 持久化不清理 | 参考扫码/锁座/支付页流程，**不复制存储层** |
| `Tuan-Space/12306FairTicket` | 默认不持久化会话；配置不保存 Cookie/Token/证件号；日志脱敏；关窗清理内存 Cookie | **无顶层 LICENSE**；默认 0.6s/0.25s 高频；开启持久化后 Cookie 明文 | 参考隐私测试/日志设计，**不复制代码** |
| `shyrel666/RailWatch-12306` | `SECURITY.md`/`PRIVACY.md` 完整；通知密钥 DPAPI/Keychain；无分析上报 | **`anti_detect.py` 清 `navigator.webdriver`/`cdc_`**；Chrome profile 明文；自动下载 ChromeDriver/自动更新 | 参考隐私文档/进程隔离，**不复制反检测** |
| `pjialin/py12306` | 功能完整、Apache-2.0 | **账号密码明文写在 `env.py`**；多账号；打码/代理 | 只参考通知/配置模式 |

**因此，路线定为：参考算法与产品流程，自研一个“数据不出本机、默认不落盘、可审计”的最小客户端。** 不 fork、不复制上述代码。

## 2. 审计范围与方法

| 项目 | Commit | 审计内容 |
|---|---|---|
| c66m | `cd1e6996b9f83d19216335ac1d9c0929c93cab73` | Python 主程序、浏览器下单、配置、日志、数据目录 |
| helper | `439d06e22eee279a73488a7d9142ea149d060939` | 扫码登录、Cookie、支付页、数据目录 |
| fair | `5c5a50be3ca80b188072c7344a13f44fdcae5e4e` | 配置、Cookie、日志、测试 |
| railwatch | `f01a0ec0742992dfe60f26d0a173532a45f2d6e9` | Electron/Python、隐私文档、反检测、更新/驱动 |
| py12306 | 公开 README/`env.py.example` | 账号/密码/多账号/代理设计 |

方法：
- 关键词扫描：`password`、`cookie`、`RAIL_DEVICEID`、`verify=False`、`http://`、`ip-api`、`fofa`、`anti_detect`、`navigator.webdriver`、`id_no`、`phone`、`sentry` 等。
- 人工阅读关键文件：会话保存、配置 schema、日志脱敏、支付页、隐私/安全文档。
- **没有运行**任何仓库的脚本或 EXE；避免把未知代码放进本机环境。

## 3. 数据地图：它们把什么写到哪里

| 数据 | c66m | helper | fair | railwatch |
|---|---|---|---|---|
| 12306 会话 Cookie | `.browser_state.json` / `session_cookies.json`，**明文 JSON**，`chmod 600` | `otn_cookies.json`，**明文 JSON** | 默认不落盘；开启 `persist_session` 后 `session.cookies` 明文 | `chrome_profile_12306/`（Chrome 管理，含 Cookie） |
| 乘车人姓名 | `passengers.json`（DPAPI/Fernet） | 内存/请求体 | 配置只存姓名；证件号不落盘 | `orders.sqlite3`/草稿可含姓名 |
| 身份证号 | `passengers.json`（DPAPI） | 内存/请求体 | 测试保证不写入配置 | 可能出现在订单/日志（需脱敏） |
| 手机号 | `passengers.json`（DPAPI） | 内存/请求体 | 测试保证不写入配置 | 可能出现在订单/日志 |
| SMTP/通知密钥 | DPAPI | 无 | 无 | DPAPI/Keychain |
| 订单/车次 | `state.json`/`order_history.json`（可能含姓名） | 内存/UI | 配置/日志（脱敏） | `orders.sqlite3` |
| 日志 | `logs/`（有脱敏工具） | 本机日志 | `logging_utils` 正则脱敏 | `railwatch.log`（文档要求脱敏） |

## 4. 发现清单

### F-01（高）c66m：会话 Cookie 明文落盘

**证据**：
- `browser_order.py:49`：`STATE_PATH = appcommon.data_path(".browser_state.json")`
- `browser_order.py:586-601`：`json.dump({"cookies": cookies}, ...)`，随后 `os.chmod(tmp, 0o600)`、`os.replace`。
- `order.py:95-121` / `124-141`：`session_cookies.json` 的读写，`atomic_write_json(..., mode=0o600)`。

**影响**：`tk`/`uamtk` 等 Cookie 是登录凭证；拿到文件即可复用会话（在有效期内）。Windows 上 `chmod 600` 不等价于 ACL，实际访问控制依赖父目录 ACL；便携版放在共享目录/备份/同步盘时风险更高。

**建议**：自研实现默认**不持久化**会话；必须持久化时，用 Windows DPAPI（当前用户绑定）加密整个 Cookie blob，存 `E:\RailPilot\data` 并设置 ACL；退出/过期即删。

### F-02（中）c66m：反自动化特征隐藏

**证据**：
- `browser_order.py:525`：`"--disable-blink-features=AutomationControlled"`。
- 同文件注释提到“自动化通用开关/易被识别”等。

**影响**：这属于“隐藏自动化特征”，与“不伪造设备指纹/不绕过风控”的红线冲突；也可能被平台认定为异常行为。

**建议**：自研不使用该 flag，不注入 `navigator.webdriver`/`cdc_` 补丁；用真实浏览器 + 用户手动登录，接受平台检测。

### F-03（中）c66m：第三方 IP 查询（含明文 HTTP）

**证据**：
- `grabber_utils.py:243`：`requests.get("http://ip-api.com/json/", params={"lang": "zh-CN"}, ...)`
- `grabber_utils.py:234`：`https://whois.pconline.com.cn/ipJson.jsp`

**影响**：把本机公网 IP 发给第三方；`http://` 还可能被中间人篡改。若用于“CDN/代理诊断”，属于不必要的数据外发。

**建议**：自研**禁止**任何第三方 IP/WHOIS 查询；需要公网 IP 时只显示“未知/不需要”，或让用户自己查。

### F-04（中）c66m：FOFA/CDN 资产扫描

**证据**：`fofa_client.py:6`：`API = "https://fofa.info/api/v1/search/all"`。

**影响**：用于搜 12306 CDN/资产，属于“绕过/加速”链路；与合规边界冲突，也会引入外部 API key。

**建议**：自研不实现 CDN IP 扫描；只用官方域名。

### F-05（高）helper：Cookie 明文落盘

**证据**：
- `otn_protocol/paths.py:33-35`：`cookie_path() = private_dir() / "otn_cookies.json"`
- `otn_protocol/session.py:220-227`：`self.cookie_path.write_text(json.dumps(payload, ...), encoding="utf-8")`

**影响**：同 F-01；且源码运行时目录是 `js_reverse_cache/private`，虽然 `.gitignore` 已排除，但备份/压缩/共享仍可能泄露。

**建议**：同 F-01；默认内存会话，必要时 DPAPI。

### F-06（中）helper：支付浏览器 profile 持久化

**证据**：
- `otn_protocol/pay_open.py:33-35`：`default_pay_profile() = ... / "otn_pay_profile"`
- `:140-145`：`--user-data-dir={profile}` 启动浏览器。

**影响**：支付页专用 profile 会保留 Cookie/缓存；用完后不清理，等于第二份会话凭证。

**建议**：自研用临时 profile（`tempfile.mkdtemp`），支付完成后删除；或复用主浏览器上下文，不落第二份。

### F-07（高，法律）fair：没有顶层 LICENSE

**证据**：仓库树只有 `docs/licenses/`（Qt/OpenSSL/Python 第三方许可），没有顶层 `LICENSE`；README 自称“开源购票助手”。

**影响**：默认“保留所有权利”，不能复制/修改/再发布；与安全无关，但会直接影响“能不能参考代码”。

**建议**：只读参考；给作者提 Issue 请求 LICENSE；在许可证明确前不复制任何代码。

### F-08（中）fair：默认高频轮询

**证据**：`ticket_app/gui/settings.py:75-80`：`query_interval_seconds=0.6`、`hot_query_interval_seconds=0.25`、`max_retries=1000`、`auto_submit=True`。

**影响**：更容易进慢速队列/触发验证码；与“低频、单账号”的设计原则冲突。

**建议**：自研默认查询 ≥5 秒，售罄后 ≥30 秒；冲刺窗口 ≤3 秒；不复制其节奏。

### F-09（中）fair：可选 Cookie 持久化是明文

**证据**：`ticket_app/client.py:69-91`：`MozillaCookieJar` 读写；`settings.py:83` 默认 `persist_session=False`。

**影响**：默认安全，但用户一旦开启，Cookie 明文落盘。

**建议**：自研不提供“明文持久化”选项；只提供“内存会话（默认）/ DPAPI 加密持久化”。

### F-10（中）railwatch：反检测脚本

**证据**：
- `anti_detect.py:1-10`：明确“clears navigator.webdriver / cdc_ keys”。
- `anti_detect.py` 还包含 UA/屏幕/seed 等 profile 逻辑（部分 legacy 未接入）。

**影响**：与“不伪造设备指纹/不绕过风控”冲突；即使只清 `navigator.webdriver`，也是反检测。

**建议**：自研不引入 `anti_detect.py`；不做任何 `navigator.webdriver`/`cdc_` 补丁。

### F-11（中）railwatch：自动下载 ChromeDriver + 自动更新

**证据**：
- `requirements.txt`：`selenium==4.44.0`；`chromedriver_manager.py` 自动下载。
- `package.json`：`electron-updater`。
- `PRIVACY.md`/`SECURITY.md` 未给出签名校验细节。

**影响**：供应链/更新通道被劫持时可下发恶意二进制；ChromeDriver 下载源需校验。

**建议**：自研优先用系统 Edge/Chrome + Playwright 自带浏览器（版本锁定）；如必须下载驱动，固定 URL + SHA256 + 签名校验；更新默认手动/签名验证。

### F-12（低/中）railwatch：通知走第三方

**证据**：`ntfy.sh`、`api.day.app`（Bark）、`sctapi.ftqq.com`（ServerChan）出现在代码/文档中。

**影响**：用户配置后，订单/车次提醒内容会经过第三方；若包含姓名/身份证/订单号，属于数据外发。

**建议**：自研通知默认只发“状态 + 截止时间”，不发姓名/证件号；第三方通道需显式开启并提示数据范围。

### F-13（高）py12306：账号密码明文配置

**证据**：README/`env.py.example` 的 `USER_ACCOUNTS` 含 `user_name`/`password` 明文；支持多账号、代理池、共享打码。

**影响**：账号密码落盘明文；多账号/代理/打码与合规边界冲突。

**建议**：不采用其账号模型；自研只支持扫码登录、单账号。

### F-14（中）共性：浏览器 profile 是“第二份凭证”

**证据**：c66m `.browser_profile/`、helper `otn_pay_profile/`、railwatch `chrome_profile_12306/` 都是持久化 Chromium profile。

**影响**：即使应用层加密了 Cookie，浏览器 profile 仍可能含 Cookie/缓存；备份/共享/恶意软件可读取。

**建议**：自研默认用临时 profile，退出即删；必须持久化时，放在 DPAPI 加密的容器/受 ACL 保护的目录，并在退出时清理。

### F-15（中）共性：日志/截图可能含 PII

**证据**：c66m 有 `masking.py`/`probe_login.py` 脱敏；fair 有 `logging_utils.py`；railwatch 文档要求脱敏；但截图/请求体仍需逐项检查。

**影响**：订单页截图可能含姓名/证件号/订单号；日志可能含 Cookie/Token。

**建议**：自研日志只在 formatter 层输出脱敏文本；禁止记录请求/响应体；截图只截订单区域并打码；默认不保存截图。

## 4.1 静态扫描汇总（启发式，需人工复核）

用 `tools/security_scan.ps1` 扫描（只输出文件/行号，不输出内容）：

| 项目 | 文件数 | HIGH | MEDIUM | 说明 |
|---|---:|---:|---:|---|
| c66m | 109 | 111 | 120 | 多数是 `session_cookies`/`storage_state` 的配置/注释命中；确认的高风险见 F-01/F-02/F-03/F-04 |
| helper | 30 | 1 | 19 | 确认 F-05（`paths.py:35`）；其余为 subprocess/PII 模式 |
| fair | 90 | 0 | 37 | 无 HIGH；F-07 是许可证问题，扫描器不覆盖；F-08 是频率，非代码漏洞 |
| railwatch | 319 | 73 | 85 | 多数是 `anti_detect.py` 的重复命中；确认 F-10/F-11 |

> 扫描器是启发式：它会把“文档里提到 cookie”也标成 HIGH，也会漏掉逻辑漏洞。**不要把 0 HIGH 当成安全证明。**

## 5. 自研安全需求（硬门槛）

| ID | 要求 | 验收方式 |
|---|---|---|
| S-01 | 不存 12306 密码；只支持 App 扫码登录 | 代码/配置无 password 字段；登录流程审计 |
| S-02 | 会话 Cookie 默认**只存内存**；不写 `storage_state` | 退出后 `E:\RailPilot\data` 无 cookie 文件；Process Monitor 验证 |
| S-03 | 如必须持久化：DPAPI（当前用户）加密 + ACL + 过期删除 | 另一个 Windows 用户无法解密；`strings` 无 `tk=`/`RAIL_DEVICEID` |
| S-04 | 不落盘身份证号/手机号；只从官方账号读取，内存使用后清零 | `strings`/SQLite 扫描无 18 位身份证/手机号 |
| S-05 | 日志/截图默认脱敏；不记录请求/响应体 | 合成 PII 测试；日志正则检查 |
| S-06 | 网络白名单：只允许 `*.12306.cn`；无遥测/第三方 IP/CDN 扫描 | mitmproxy/Wireshark 只看到 12306 域名 |
| S-07 | 不做反检测/指纹伪造/验证码绕过/代理/多账号 | 代码审计；无 `navigator.webdriver`/`AutomationControlled`/proxy 配置 |
| S-08 | 依赖锁定 + 哈希 + 漏洞扫描；不自动下载二进制 | `pip-audit`、`pip-compile --generate-hashes`、`bandit` |
| S-09 | 更新默认手动；如自动更新，签名验证 | 更新包签名/哈希校验 |
| S-10 | 本地 DB 不存敏感字段；必须存时用 DPAPI/SQLCipher | DB schema + `strings` 检查 |
| S-11 | 一键停止/自动锁定/退出清理临时文件 | kill switch <1s；退出后临时目录为空 |
| S-12 | 不把数据发到通知渠道（默认只发状态/截止） | 通知模板审计；第三方通道显式提示 |

## 6. 威胁模型

| 威胁 | 现实影响 | 控制 |
|---|---|---|
| 同机恶意软件/木马 | 可读内存/DPAPI 当前用户密钥 | 全盘加密、EDR、最小权限、不装未知软件 |
| 共用电脑/家人 | 可打开应用/读用户目录 | Windows 账户隔离、应用锁、退出清理 |
| 备份/同步盘 | Cookie/日志被同步到云 | 数据目录排除同步；默认不落盘 |
| 截图/录屏 | 身份证/订单泄露 | 截图打码/裁剪；默认不保存 |
| 依赖供应链 | 恶意包/驱动/更新 | 锁版本+哈希、`pip-audit`、不自动下载 |
| 通知渠道 | 订单信息经过第三方 | 只发状态/截止；显式开启 |
| 平台风控 | 锁号/慢速队列 | 单账号、低频、停止条件、官方候补兜底 |
| 法律 | 代抢/多账号/打码 | 红线：不收费、不批量、不绕过 |

> DPAPI 只能防“另一个用户/离线拷贝”，**防不了同用户恶意软件**。私人数据安全还需要全盘加密、系统补丁、最小软件面。

## 7. 安全验收清单（MVP 前必须跑）

- [x] DPAPI 当前用户 roundtrip + 防篡改：`tools/dpapi_roundtrip.ps1` 已在本机通过（2026-11-14）。
- [ ] `strings` 扫描数据目录：无 `tk=`、`uamtk`、`RAIL_DEVICEID`、`password`、18 位身份证、11 位手机号。
- [ ] 退出应用后：`E:\RailPilot\data` 无 Cookie/临时 profile；`temp` 无残留。
- [ ] 用另一个 Windows 用户尝试解密 DPAPI blob：失败。
- [ ] mitmproxy/Wireshark：空闲/查询/提交全程只访问 `*.12306.cn`（通知通道显式开启除外）。
- [ ] 合成 PII 写入日志：输出已脱敏。
- [ ] `pip-audit`：无 high/critical；`bandit`：无 high。
- [ ] kill switch：1 秒内停止所有任务/定时器。
- [ ] 断网/超时/验证码：不重试到高频，进入冷却。
- [ ] 订单回读：不产生重复订单；未支付订单只允许 1 个。
- [ ] 取消预算：一天最多 2 次手动取消，第 2 次硬停。

## 8. 复现方式

```powershell
# 只读克隆（用于审计，不运行）
git clone --depth 1 https://github.com/c66-m/12306-ticket-monitor.git
git clone --depth 1 https://github.com/lyt26/12306-helper.git
git clone --depth 1 https://github.com/Tuan-Space/12306FairTicket.git
git clone --depth 1 https://github.com/shyrel666/RailWatch-12306.git

# 查看关键文件（示例）
# c66m: browser_order.py / order.py / passengers.py / grabber_utils.py / fofa_client.py
# helper: otn_protocol/session.py / paths.py / pay_open.py
# fair: ticket_app/client.py / gui/settings.py / logging_utils.py
# railwatch: anti_detect.py / SECURITY.md / PRIVACY.md
```

> 审计是静态的，不等于“运行时安全”。自研代码落地前，必须做一次动态审计（Process Monitor + mitmproxy + 内存/磁盘检查）。

## 9. 一句话

**参考他们的算法，不继承他们的数据习惯。** 自研的第一行代码之前，先把 S-01 ~ S-12 写成测试；过不了安全门槛，就不进入功能开发。
