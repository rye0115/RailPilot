# 09 · 实现状态（v0.1）

> 日期：2026-11-14 · 状态：安全内核 + 查询/候补/护栏可用；**真实下单未启用**。

## 1. 已实现

| 模块 | 文件 | 状态 |
|---|---|---|
| DPAPI 当前用户加解密 | `railpilot/security/dpapi.py` | ✅ |
| DPAPI 加密状态文件（拒绝明文） | `railpilot/store/state.py` | ✅ |
| 日志/通知脱敏 | `railpilot/security/redact.py` | ✅ |
| 只允许 `*.12306.cn` | `railpilot/security/allowlist.py` | ✅ |
| 私有目录/原子写/ACL/删除 | `railpilot/security/files.py` | ✅ |
| SQLite 事件日志（无 PII） | `railpilot/store/events.py` | ✅ |
| HTTP Date 校时 + 单调倒计时 | `railpilot/core/clock.py` | ✅ |
| 候选排序/指纹 | `railpilot/core/planner.py` | ✅ |
| 候补 6×3×60×19 编排 | `railpilot/core/waitlist.py` | ✅ |
| 支付/取消/幂等护栏 | `railpilot/core/guard.py` | ✅ |
| 通知通道（console/webhook） | `railpilot/notify/channels.py` | ✅ |
| 官方余票查询（Playwright） | `railpilot/rail/adapter.py` | ✅ 实查通过 |
| 扫码登录页（打开 + 轮询） | `railpilot/rail/adapter.py` | ⚠️ 未做端到端 |
| CLI（doctor/clock/waitlist/query/run） | `railpilot/cli.py` | ✅ |
| 单元测试 | `tests/` | ✅ 32 passed |
| Windows GUI | `dist/RailPilotGUI/RailPilotGUI.exe`（zip 约 55MB） | ✅ 可启动；查票/候补/安全/日志页面可用 |
| Windows CLI | `dist/RailPilot/RailPilot.exe`（目录约 135MB；zip 约 52MB） | ✅ `doctor`/`query` 实测通过 |
| APK | — | ❌ 未构建（需单独 Android 工程） |

## 2. 已验证（本机）

```text
python -m pytest
32 passed

python -m railpilot doctor
ok=true（DPAPI/状态密文/白名单/删除全部通过）

python -m railpilot clock
offset_ms≈-0.5 ~ -1.2s（HTTP Date 只有秒级 + 本机代理；目标是稳定偏移，不是毫秒真值）
min_rtt_ms=62, samples=5

python -m railpilot query --date 2026-11-25 --from 北京南 --to 上海虹桥 --headless
count=94（G547/G1/G3...，二等座/一等座/商务座）

python -m railpilot waitlist --cutoff 2027-02-11T18:00:00+08:00
1 单 / 6 组合（3 个日期约束内）

python -m railpilot login --timeout 5
官方登录页可打开、可见“扫码登录”；未扫码（预期 false）；
blocked_hosts=["g.alicdn.com"]（登录滑块/CDN 被白名单拦截）
```

**没有下过真实订单。** 查询是公开余票页，不涉及登录和支付。

## 2.1 GUI 试跑（2026-11-15）

- 启动 `RailPilotGUI`，在查票页查 2026-11-25 北京南→上海虹桥。
- 结果：**93 条席别候选 / 37 趟车**；状态栏提示 `已拦截 g.alicdn.com`。
- 第一次试跑失败：`main thread is not in main loop`。原因是在 Playwright 工作线程里调用了 `headless_var.get()`（Tk 变量跨线程）；已改为在主线程先取 `headless` 再传进线程。
- 第二次试跑成功，GUI 表格正常聚合车次。
- 试跑后数据目录只有 `events.db`（13 条事件），`%TEMP%` 无 Playwright profile 残留；`events.db` 扫描无 `tk=`/`RAIL_DEVICEID`/18 位身份证。
- 依赖审计：`pip-audit` 升级 pip/setuptools 后 0 漏洞；`bandit` 0 MEDIUM/HIGH（15 LOW）；仓库自扫 0 HIGH。

## 3. 未实现/限制

| 项 | 现状 | 下一步 |
|---|---|---|
| 真实下单 | `submit()` 返回 `manual`，不点提交 | 现场测试订单页后再开 `--live` |
| 订单回读 | 已接官方未完成订单接口；未登录/HTML 会抛错，不返回假空列表；字段映射待真实登录校验 | 用真实账号核对字段，补“空列表≠无订单”测试 |
| 扫码登录端到端 | 打开独立 Playwright 窗口并轮询 `tk/uamtk`；会 `bring_to_front` | 用真实账号在**该窗口**扫码；普通 Edge 的 Cookie 不共享；处理滑块/验证码 |
| 第三方资源 | 白名单拦了 `g.alicdn.com`（登录滑块脚本） | 评估是否允许该单一依赖；默认仍拦 |
| GUI | Tkinter GUI 已可用（总览/查票/候补/登录/安全/日志） | 继续补订单回读、真实下单开关、多任务/中转/选座 |
| 通知配置 | 只有 console/webhook 类 | 接 config 的 ntfy/ServerChan/Telegram |
| 自动支付 | 未实现，默认禁止 | 暂不做 |
| 多任务/中转/选座 | 未实现 | v0.2 |

## 4. 安全验证结果

- 数据目录默认 `E:\RailPilot\data`（可用 `RAILPILOT_DATA_DIR` 覆盖；无 E 盘时回退 `%LOCALAPPDATA%\RailPilot`）。
- `StateStore` 写出的 `state.enc` 是 `dpapi1:` 密文；`strings` 不含明文姓名。
- 读到明文 JSON 会抛 `StateSecurityError`，不会静默接受。
- `StateStore` 清理由 `secure_delete` 覆盖后删除。
- 本机 DPAPI roundtrip：通过；密文不含明文；篡改后解密失败。
- 本仓库 `security_scan.ps1`：0 HIGH。
- 官方查询时白名单拦截 `g.alicdn.com`，说明第三方请求确实被挡住。

## 5. 已知风险

1. **登录滑块可能依赖 `g.alicdn.com`**：默认拦截可能让扫码/验证失败；需要单独评估（允许单一域名 vs 保持全拦）。
2. **订单回读字段映射未用真实登录校验**：`read_orders()` 已接官方接口、未登录/HTML 会抛错，但字段名可能变；空列表/异常都不能当“没有订单”的证据。
3. **页面改版**：查询字段索引、登录页、订单页都可能变；已有 `parse_query_rows` 单测，改版时先改 fixture。
4. **同用户恶意软件**：DPAPI 防不了；仍需全盘加密/EDR/最小软件面。
5. **现场测试会占用取消次数**：只买 1 张可退票，注意一天 3 次限制。

## 6. 下一步（按顺序）

1. **扫码登录现场测试**：用真实账号，确认 `tk/uamtk` 获取、登录态保持、退出清理。
2. **订单回读**：实现“未支付订单”读取 + 指纹归因；加故障注入测试（超时/重复响应）。
3. **真实下单（显式开关）**：`--live --i-understand` 才允许；默认一次只提交 1 单；成功后立即停。
4. **GUI**：PySide6 作战面板 + 候补面板；不重写核心。
5. **现场跑通**：低峰线路、1 张可退票、人工支付；记录 T0 延迟和结果。
