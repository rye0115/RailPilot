# 12306 私人购票助手：调研、参考算法与安全方案

> 调研/审计截止：2026-11-14 · 仓库：`E:\DSH-Data\projects\12306` · 状态：v0.1 安全内核 + 查询/候补/护栏已实现，真实下单未启用

## 结论

**不 fork、不复制现成项目；参考它们的算法和流程，自研一个“数据不出本机”的最小客户端。** 现成项目里，星高的已停更，能跑的又有明文 Cookie、反检测、第三方 IP 查询或许可证问题。私人使用最该先解决的不是“抢得多快”，而是**账号凭证和身份证号不泄露**。

## 快速开始

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt

# 安全自检（DPAPI/密文/白名单）
.\.venv\Scripts\python.exe -m railpilot doctor

# 校时
.\.venv\Scripts\python.exe -m railpilot clock

# 生成候补计划（只生成，不提交）
.\.venv\Scripts\python.exe -m railpilot waitlist --cutoff "2027-02-11T18:00:00+08:00"

# 查指定日期余票（公开页，需 Playwright + Edge）
.\.venv\Scripts\python.exe -m railpilot query --date 2026-11-25 --from 北京南 --to 上海虹桥 --headless

# 单元测试
.\.venv\Scripts\python.exe -m pytest
```

> 真实下单未启用；`run` 只做 dry-run。现场测试步骤见 `docs/09-implementation-status.md`。

### Windows GUI 客户端

已打包，双击即可：

```text
dist\RailPilotGUI\RailPilotGUI.exe
```

便携包：`dist\RailPilotGUI-v0.1-win64-E.zip`（约 55MB）。

本地数据默认存 `E:\RailPilot\data`；可用环境变量 `RAILPILOT_DATA_DIR` 覆盖。

界面包含：总览、查票、候补、登录、安全、日志。查票使用真实浏览器，候补只生成计划，真实下单默认关闭。

### Windows CLI 版

已打包：`dist/RailPilot/RailPilot.exe`（约 135MB，含 Playwright 驱动），压缩包 `dist/RailPilot-v0.1-win64.zip`（约 52MB）。

在 `dist/RailPilot` 目录打开 PowerShell：

```powershell
.\RailPilot.exe doctor
.\RailPilot.exe query --date 2026-11-25 --from 北京南 --to 上海虹桥 --headless
.\RailPilot.exe waitlist --cutoff "2027-02-11T18:00:00+08:00"
.\RailPilot.exe login
```

**没有 APK**：Android 需要单独的原生/WebView 工程，当前未构建。

## 为什么不是直接复用

| 项目 | 值得参考 | 为什么不能直接拿来用 |
|---|---|---|
| `c66-m/12306-ticket-monitor` | 真实 Edge 提交、订单回读 + 时间戳归因、DPAPI 加密乘车人、日志脱敏 | **会话 Cookie 明文 JSON**；`--disable-blink-features=AutomationControlled`；第三方 IP 查询/FOFA CDN 扫描 |
| `lyt26/12306-helper` | 扫码登录、锁座、带登录支付页、不收密码 | **Cookie 明文 JSON**；支付浏览器 profile 持久化不清理 |
| `Tuan-Space/12306FairTicket` | 校时、预热、购物车、成功后停止、日志/配置的隐私测试 | **无顶层 LICENSE**；默认 0.6s/0.25s 高频；开启持久化后 Cookie 明文 |
| `shyrel666/RailWatch-12306` | 隐私文档、进程隔离、通知密钥 DPAPI/Keychain | **`anti_detect.py` 清 `navigator.webdriver`/`cdc_`**；Chrome profile 明文；自动下载驱动/更新 |
| `pjialin/py12306` | 通知/配置/Web 管理的信息架构 | **账号密码明文写在 `env.py`**；多账号、打码、代理池 |

> 详细证据、行号、commit：见 [`docs/08-security-audit.md`](docs/08-security-audit.md)。审计是静态的，没有运行它们的代码。

## 安全底线（先于功能）

1. **不存密码**：只支持 12306 App 扫码登录。
2. **Cookie 默认只存内存**；必须持久化时用 Windows DPAPI（当前用户）加密 + ACL + 过期删除。
3. **身份证号/手机号不落盘**：从官方账号读取，内存使用后清零；本地只存姓名/类型。
4. **网络白名单**：只允许 `*.12306.cn`；无遥测、无第三方 IP 查询、无 CDN 扫描。
5. **不做反检测**：不注入 `navigator.webdriver`/`cdc_`，不伪造指纹，不用代理/多账号/打码。
6. **人工支付**：默认只打开支付页/倒计时；自动支付必须显式授权 + 限额。

## 参考什么，不参考什么

| 参考 | 来源 | 用法 |
|---|---|---|
| 校时：多次采样、选最小 RTT、补偿本机时差 | 12306FairTicket、TopF1ag | 按算法自己实现 |
| 开售前：复查登录、再次校时、查询热身 | 12306FairTicket | 按流程自己实现 |
| 多站点购物车 + 优先级 + 成功后停止 | 12306FairTicket、Bypass | 按产品结构自己实现 |
| 真实浏览器提交 + 订单回读 + 时间戳归因 | c66-m | 按思路自己实现；**不复制 Cookie/反检测代码** |
| 扫码登录 + 锁座 + 支付页 | 12306-helper | 按流程自己实现；**不复制存储层** |
| 通知适配器/配置/日志脱敏 | py12306、RailWatch、fair | 按接口/测试思路自己实现 |
| 候补/支付/取消规则 | 12306 官方 | 直接按公开规则实现 |

**不参考**：验证码识别、滑块绕过、代理池、多账号、CDN IP 扫描、自动支付默认开、`anti_detect`、明文 Cookie/密码。

## 自研范围（最小可用）

```text
Plan（计划） → Arm（备战） → Sprint（冲刺） → Fallback（兜底） → Guard（守护）
  起售日历      扫码/预填        T0 提交         候补编排        支付倒计时
  候选排序      校时/预热        订单回读         接受新增列车    取消预算
  中转方案      预检/空跑        幂等             截止时间        通知/复盘
```

1. **校时 + 起售时间**：HTTP Date 多样本，单调时钟倒计时。
2. **预填检查 + T0 单次提交**：真实浏览器，只点一次；3 秒内没结果就停。
3. **订单回读 + 幂等**：提交前查未支付订单；超时先回读，绝不盲重试。
4. **候补编排**：6 单 × 3 日期 × ≤60 组合，可行性过滤，接受新增列车。
5. **支付/取消护栏**：未支付订单倒计时；一天最多 2 次手动取消；一键停止。
6. **通知 + 复盘**：锁座/兑现/截止提醒；失败分类 + 下一步。

## 三条路线

| 路线 | 做法 | 成本 | 风险 | 适合谁 |
|---|---|---|---|---|
| A. 官方 only | 12306 App：预填、起售提醒、候补、预约购票 | 0 | 最低 | 90% 的人 |
| B. 直接用现成 | Bypass / 12306FairTicket / 12306-helper | 0 | 中（风控、停更、明文 Cookie、许可证） | 不想写代码、能接受风险 |
| C. 参考算法 + 自研（推荐） | 自写最小客户端；只参考流程；安全门槛先过 | 2–4 周 | 中 | 想长期用、重视隐私、愿意维护 |

**推荐 C，但把“抢”改成“编排官方能力”**：不碰验证码、不碰多账号、不碰代理池，只做单账号、低频、可解释的自动化。真正提高成功率的不是“刷得更快”，而是：起售时间算准、预填提前做好、T0 只提交一次、售罄立刻转候补、候补组合拉满、支付不超时。

## 文档导航

| 文件 | 内容 |
|---|---|
| [`docs/00-summary.md`](docs/00-summary.md) | 一页纸结论：参考矩阵、路线、关键数字 |
| [`docs/01-competitive-research.md`](docs/01-competitive-research.md) | 20 个竞品、许可证、功能矩阵、可参考/不可参考 |
| [`docs/02-12306-rules.md`](docs/02-12306-rules.md) | 预售、起售、预填、候补、预约、风控、支付/取消规则 |
| [`docs/03-features.md`](docs/03-features.md) | 自研功能清单、验收标准、反功能、交互流程 |
| [`docs/04-architecture.md`](docs/04-architecture.md) | 自研安全架构、模块、状态机、校时/幂等、数据模型 |
| [`docs/05-compliance.md`](docs/05-compliance.md) | 监管口径、判例、许可证/协议风险、红线 |
| [`docs/06-roadmap.md`](docs/06-roadmap.md) | 2 天安全 spike + 4 周 MVP、验收门槛 |
| [`docs/07-reuse-map.md`](docs/07-reuse-map.md) | 算法/流程参考表（只读，不复制代码） |
| [`docs/08-security-audit.md`](docs/08-security-audit.md) | **第三方项目数据安全审计（证据/行号/commit）** |
| [`research/repos.csv`](research/repos.csv) | 竞品结构化数据（含 license/security/verdict） |
| [`research/sources.md`](research/sources.md) | 来源、日期、关键结论、置信度 |
| [`design/config.example.yaml`](design/config.example.yaml) | 自研客户端配置（单账号/默认不落盘/人工支付） |
| [`design/module-contracts.md`](design/module-contracts.md) | 模块接口草案（页面适配/校时/候补/护栏/通知） |
| [`docs/09-implementation-status.md`](docs/09-implementation-status.md) | v0.1 实现状态、已验证项、未实现/限制 |
| [`tools/security_scan.ps1`](tools/security_scan.ps1) | 本地静态安全扫描脚本（不打印敏感值） |
| [`tools/dpapi_roundtrip.ps1`](tools/dpapi_roundtrip.ps1) | Windows DPAPI 当前用户加解密/防篡改自检 |

## 怎么读

- 只想决定“用哪个/做不做”：读本页 + `docs/00-summary.md`。
- 想动手：先读 `docs/08-security-audit.md` 的 S-01~S-12，再读 `docs/04` → `design/module-contracts.md` → `docs/06`。
- 担心风险：先读 `docs/05-compliance.md`。

## 边界与免责声明

- 本项目是**个人出行辅助**的调研与实现方案，不构成法律意见；不提供代抢、倒卖、多账号、验证码绕过、代理池等能力。
- 12306 是中国铁路唯一官方网络售票渠道，从未与第三方合作；任何“加速包/优先通道”的说法都不可信。
- 自动化访问仍可能违反 12306 服务协议并触发风控；即使按本方案实现，也不保证购票成功，账号风险由使用者自担。
- 参考第三方代码/算法前必须核对 LICENSE；本项目只参考公开算法/流程，不复制代码；没有许可证的项目只读。
- 票价、规则、接口、页面都会变；本文所有规则以 2026-11-14 的公开资料为准，落地前必须重新核对官方页面。

## 下一步

先做 **2 天安全 spike**，不写 UI：
1. DPAPI 加密/解密一个假 Cookie blob，确认另一个 Windows 用户解不开；
2. 用 Playwright 打开官方页面，确认退出后临时 profile 被删除、无 Cookie 落盘；
3. mitmproxy 确认只访问 `*.12306.cn`；
4. 合成身份证/手机号写入日志，确认脱敏。
四项全过，再进入 4 周 MVP；门槛见 `docs/06-roadmap.md`。
