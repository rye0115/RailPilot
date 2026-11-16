# 07 · 算法/流程参考表（只读，不复制代码）

> 目标：把“站在肩膀上”限定为**参考算法和产品流程**，代码自己写。
> 理由：现成项目普遍有明文 Cookie、反检测、无 LICENSE 等问题；复制代码会把它们的风险一起继承过来。

## 1. 总表

| 需求 | 参考来源 | 参考什么 | 不参考什么 | 自研实现 |
|---|---|---|---|---|
| 校时 | 12306FairTicket、TopF1ag | 多次采样、选最小 RTT、补偿本机时差 | 它们的代码/默认高频 | `core/clock.py` |
| 开售前预热 | 12306FairTicket | 复查登录、再次校时、查询热身 | 0.25s/0.6s 高频 | `core/orchestrator.py` |
| 多站点购物车 | 12306FairTicket、Bypass | 多车站/车次/席别、拖拽优先级、成功后停止 | 复制 UI/代码 | `core/planner.py` + UI |
| T0 提交 | c66-m | 真实浏览器、提交后回读、时间戳归因 | `AutomationControlled`、明文 Cookie | `rail/adapter.py` |
| 订单幂等 | c66-m | 提交前查未支付、超时回读、指纹归因 | 明文 `session_cookies.json` | `core/guard.py` |
| 扫码登录 | 12306-helper | 扫码、本地会话、不收密码 | 明文 `otn_cookies.json`、持久支付 profile | `rail/adapter.py` + `security/` |
| 锁座/支付页 | 12306-helper | 锁座 ≠ 买到、带登录支付页、人工支付 | 复制存储层 | `core/guard.py` |
| 通知 | py12306、RailWatch | 多通道、重试、模板 | 账号密码明文、多账号、代理 | `notify/channels.py` |
| 隐私文档 | RailWatch | SECURITY/PRIVACY 的结构、边界声明 | `anti_detect.py`、自动下载驱动 | `docs/08` |
| 失败分类 | Bypass 帮助中心 | 缓存票/区间限售/未放票/排队人数 | 复制文案/图标/代码 | `core/explain.py` |
| 候补规则 | 12306 官方 | 6×3×60、预付款、截止、接受新增列车 | — | `core/waitlist.py` |
| 日志脱敏 | fair、c66m | 正则/Formatter 思路 | 复制代码 | `security/redact.py` |

## 2. Clean-room 流程

为了避免“参考”变成“复制”，按下面做：

1. **读文档/行为，不读实现细节**：先看 README/帮助/公开规则，写下功能规格。
2. **写规格**：输入/输出/边界/失败状态，放到 `docs/03` 或 issue。
3. **独立实现**：不打开原项目源码逐行翻译；需要查算法时只看公式/流程，不复制代码。
4. **记录参考**：在 commit message/文档里写“参考了 X 的 Y 思路”，不写“基于 X 代码”。
5. **审查**：提交前用 `tools/security_scan.ps1` + 人工 diff，确认没有整段相似代码。
6. **许可证**：如果确实需要复制 MIT/Apache 代码，单独开 PR，保留 LICENSE/NOTICE；默认不复制。

## 3. 逐项说明

### 3.1 校时（参考 fair/TopF1ag）

**参考点**：
- 多次采样，取 RTT 最小的样本；
- 用 `server_time + rtt/2 - local_time` 估算偏移；
- 开售前 T-10m/T-2m/T-10s 复校；
- 用单调时钟倒计时，避免系统时间被改。

**不参考**：
- 它们的默认轮询频率；
- 任何“毫秒级精确”的宣传（HTTP Date 只有秒级）。

**自研**：`core/clock.py`，5 次采样，目标偏移稳定 ≤1s（HTTP Date 秒级）。

### 3.2 T0 提交/回读（参考 c66-m）

**参考点**：
- 用真实浏览器上下文，不用纯 HTTP 逆向；
- 提交后不假设结果，回读订单列表；
- 用时间戳/指纹归因，避免“超时但成功”被重试成两单；
- 查询和下单分离，减少登录态被查询打掉。

**不参考**：
- `--disable-blink-features=AutomationControlled`；
- 明文 `.browser_state.json`/`session_cookies.json`；
- 第三方 IP 查询/FOFA CDN 扫描。

**自研**：`rail/adapter.py` + `core/guard.py`；临时上下文 + DPAPI（可选）+ 幂等键。

### 3.3 扫码/锁座/支付页（参考 12306-helper）

**参考点**：
- 只扫码，不收密码；
- Cookie 只存本机；
- 锁座后打开带登录支付页，人工支付；
- “锁座 ≠ 买到”的提示。

**不参考**：
- 明文 `otn_cookies.json`；
- 持久化 `otn_pay_profile`；
- Android 端密码登录。

**自研**：内存会话 + 同一 context 打开支付页；退出清理。

### 3.4 候补（参考官方规则 + Bypass 产品化）

**参考点**：
- 6 单 × 3 日期 × 60 组合；
- 预付款 30 分钟内支付；
- 接受新增列车；
- 截止时间可设，最晚开车前 20 分钟；
- 售罄后第一时间候补，不继续刷。

**自研**：`core/waitlist.py`；默认只生成计划/打开官方页面，用户确认提交。

### 3.5 通知/复盘（参考 py12306/Bypass）

**参考点**：
- 多通道、模板、重试；
- 失败分类：缓存票/区间限售/未放票/排队人数大于余票；
- 支付截止提醒。

**不参考**：
- 账号密码明文；
- 多账号/代理/打码；
- 复制文案/图标。

**自研**：`notify/channels.py` + `core/explain.py`；只发状态/截止，不发 PII。

## 4. 明确不参考

| 不参考 | 原因 |
|---|---|
| 验证码识别/滑块绕过 | 违规；判例关键情节；维护无底洞 |
| 多账号/账号池 | 违反实名制/平台规则；风控重点 |
| 代理池/IP 轮换 | 规避风控；监管禁止干扰安全核验 |
| CDN IP 扫描 | 数据不可信；容易被识别 |
| `anti_detect`/`navigator.webdriver` 补丁 | 反检测；与红线冲突 |
| 明文 Cookie/密码 | 泄露即盗号 |
| 自动支付默认开 | 误买/重复买/退款麻烦 |
| 买短乘长/买长乘短 | 监管点名；安全/合同风险 |
| 复制无 LICENSE 代码 | 著作权侵权 |

## 5. 参考记录模板

```text
Reference: <project> / <algorithm or flow>
Used for: <what>
Not copied: <explicitly say no code copied>
Implementation: <our file/module>
License: <MIT/Apache/none — read-only>
```

示例：

```text
Reference: Tuan-Space/12306FairTicket / 校时流程（README）
Used for: 多样本 + 最小 RTT 的 offset 估算
Not copied: 未复制代码；按公开描述独立实现
Implementation: railpilot/core/clock.py
License: 未声明 — 只读参考
```

## 6. 代码相似度审查

- 提交前跑 `tools/security_scan.ps1`（敏感模式）+ 人工 diff；
- 对可疑文件做“变量名/结构/注释”对比；
- 不把原项目的注释/中文文案整段搬过来；
- 如果确实复制了 MIT/Apache 代码，单独 commit，保留 LICENSE/NOTICE，并在 `docs/07` 记录。

## 7. 一句话

**参考的是“怎么做”，不是“抄过来”。** 算法可以学，代码要自己写；数据安全不能继承别人的坑。
