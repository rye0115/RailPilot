# 模块接口草案（自研客户端）

> 目的：把“参考算法、自己实现”落到接口，先定契约，再写代码。
> 安全不变量见 `docs/08-security-audit.md` 的 S-01 ~ S-12。

## 1. 分层

```text
app/UI  →  core/orchestrator  →  rail/adapter  →  Playwright → 12306
                         ├─ core/clock
                         ├─ core/planner
                         ├─ core/waitlist
                         ├─ core/guard
                         ├─ notify/channels
                         └─ store/state + store/events
```

**规则**：
- `rail/adapter` 是唯一碰官方页面的模块；
- `security/` 是唯一碰 DPAPI/脱敏/白名单的模块；
- `store/state` 是唯一碰敏感状态的模块；
- UI 不直接调用 Playwright/网络。

## 2. 数据类

```python
@dataclass(frozen=True)
class Candidate:
    id: str
    date: str
    train_no: str
    from_station: str
    to_station: str
    seat: str
    priority: int

@dataclass(frozen=True)
class SubmitResult:
    status: Literal["success", "queued", "sold_out", "unknown", "risk", "login_expired"]
    order_no: str | None = None
    message: str = ""
    latency_ms: int = 0

@dataclass(frozen=True)
class Order:
    masked_no: str
    status: Literal["unpaid", "paid", "cancelled", "unknown"]
    amount: float | None = None
    pay_deadline: str | None = None
    train_no: str | None = None
    date: str | None = None
```

> `Order.masked_no` 只用于本地展示/通知；完整订单号只在内存中保留到支付完成。

## 3. RailAdapter

```python
class RailAdapter(Protocol):
    def open(self) -> None: ...
    def close(self) -> None: ...
    def is_logged_in(self) -> bool: ...
    def query(self, date: str, from_station: str, to_station: str) -> list[Candidate]: ...
    def check_prefill(self, task: Task) -> PrefillReport: ...
    def submit(self, candidate: Candidate) -> SubmitResult: ...
    def read_orders(self, status: str | None = None) -> list[Order]: ...
    def open_payment(self, order: Order) -> None: ...
    def open_waitlist(self, plan: WaitlistPlan) -> None: ...
```

**实现约束**：
- `open()` 用 `browser.new_context()`，不设 `user_data_dir`；
- 所有请求经过 `security/allowlist`；
- 不在 adapter 里写日志；事件由 orchestrator 记录；
- 不实现验证码/指纹/代理。

## 4. Clock

```python
@dataclass(frozen=True)
class ClockState:
    offset_ms: int
    measured_at: str
    samples: int
    min_rtt_ms: float

class Clock(Protocol):
    def calibrate(self) -> ClockState: ...
    def now_ms(self) -> int: ...          # monotonic + offset
    def sleep_until(self, target_ms: int) -> None: ...
```

**约束**：
- 只请求 `https://www.12306.cn`/`https://kyfw.12306.cn`；
- 5 次采样，取最小 RTT；
- HTTP Date 秒级；目标误差 ≤100ms；
- T-10m/T-2m/T-10s 复校。

## 5. Planner

```python
class Planner(Protocol):
    def sale_time(self, station: str, date: str) -> str: ...
    def rank(self, task: Task, candidates: list[Candidate]) -> list[Candidate]: ...
    def transfer_options(self, task: Task) -> list[TransferPlan]: ...
```

**约束**：
- 起售时间缓存 24h；
- 候选排序 = 用户优先级 + 可行性（到达时间/换乘缓冲/席别）；
- 不生成买短乘长/买长乘短默认方案。

## 6. Waitlist

```python
@dataclass(frozen=True)
class WaitlistCombo:
    date: str
    train_no: str
    seat: str
    priority: int

@dataclass(frozen=True)
class WaitlistOrder:
    index: int
    dates: list[str]
    combos: list[WaitlistCombo]
    passengers: list[str]

@dataclass(frozen=True)
class WaitlistPlan:
    orders: list[WaitlistOrder]
    total_combos: int
    cutoff_at: str
    accept_new_trains: bool
```

**约束**：
- `len(orders) <= 6`；
- 每单 `len(dates) <= 3`，`len(combos) <= 60`，`len(passengers) <= 19`；
- 组合必须通过可行性过滤；
- 默认 `auto_submit=False`：只生成/打开官方页面。

## 7. Guard

```python
class Guard(Protocol):
    def before_submit(self, task: Task) -> Decision: ...
    def after_result(self, task: Task, result: SubmitResult) -> None: ...
    def check_payment_deadlines(self) -> list[PaymentReminder]: ...
    def record_manual_cancel(self) -> None: ...
```

**约束**：
- 一天最多 2 次手动取消；到 2 次硬停；
- 同时最多 1 个未支付订单；
- 成功后任务级停止；
- 未知响应先回读，不盲重试。

## 8. Notifier

```python
class Notifier(Protocol):
    def send(self, event: NotifyEvent) -> bool: ...
```

`NotifyEvent` 只含：`kind`、`masked_order_no`、`deadline`、`status`、`next_action`。
**不含**：姓名、身份证号、手机号、完整订单号、Cookie。

## 9. Store

```python
class StateStore(Protocol):
    def load(self) -> State: ...       # DPAPI 解密
    def save(self, state: State) -> None: ...  # DPAPI 加密 + 原子写
    def clear(self) -> None: ...

class EventStore(Protocol):
    def append(self, event: Event) -> None: ...
    def timeline(self, task_id: str) -> list[Event]: ...
```

**约束**：
- `State` 可含姓名/任务/可选 `storage_state`；整个 JSON 用 DPAPI 加密；
- `Event` 不含 PII；只存 code/latency/status；
- 文件默认在 `E:\RailPilot\data`（可用 `RAILPILOT_DATA_DIR` 覆盖）；`icacls` 仅当前用户。

## 10. 事件码

```python
EVENT_CODES = {
    "QUERY_EMPTY", "SOLD_OUT", "QUEUE",
    "SUBMIT_OK", "SUBMIT_UNKNOWN",
    "RISK_SLOW_QUEUE", "RISK_CAPTCHA",
    "LOGIN_EXPIRED", "NETWORK",
    "BUDGET_BLOCK", "DUPLICATE_BLOCK",
    "BLOCKED_HOST", "PREFILL_MISSING",
    "PAY_REMINDER", "PAID", "EXPIRED",
}
```

## 11. 安全不变量（代码评审必查）

1. 代码/配置中没有 `password` 字段；
2. 没有 `storage_state`/`user_data_dir` 明文路径；
3. 没有 `verify=False`/`http://`；
4. 没有 `navigator.webdriver`/`AutomationControlled`/`anti_detect`；
5. 没有第三方 IP/WHOIS/CDN/遥测；
6. 日志 formatter 强制脱敏；
7. 退出时关闭 context、删除临时文件；
8. DPAPI 解密失败不回退明文。

## 12. 测试契约

- 每个 Protocol 有 fake 实现；
- `tests/test_security.py`：DPAPI/脱敏/白名单/无明文；
- `tests/test_waitlist.py`：6/3/60/19 边界；
- `tests/test_guard.py`：预算/未支付/幂等；
- `tests/test_clock.py`：offset/单调；
- `tests/test_rail_mock.py`：本地 HTML 克隆页。

## 13. 一句话

**接口先定死，实现再替换；唯一允许“碰敏感数据”的模块是 `security/` 和 `store/state`。**
