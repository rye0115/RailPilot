RailPilot v0.1（Windows x64，免安装 Python）

重要：
- 本版本只做“查票 + 候补计划 + 安全自检 + 登录页”，真实下单未启用。
- 只支持 12306 App 扫码登录；不会保存密码。
- 数据默认不落盘；必须落盘时用 Windows DPAPI 加密。
- 只访问 *.12306.cn；会拦截第三方请求。
- 需要本机安装 Microsoft Edge（查询/登录用）。

用法（在本目录打开 PowerShell）：
  .\RailPilot.exe doctor
  .\RailPilot.exe clock
  .\RailPilot.exe waitlist --cutoff "2027-02-11T18:00:00+08:00"
  .\RailPilot.exe query --date 2026-11-25 --from 北京南 --to 上海虹桥 --headless
  .\RailPilot.exe login
  .\RailPilot.exe run --dry-run

注意：
- `login` 会打开浏览器，用 12306 App 扫码；只登录，不支付、不下单。
- 查询日期必须在 15 天预售期内。
- 数据目录：E:\RailPilot\data（可用 RAILPILOT_DATA_DIR 覆盖）
- 不要把这个目录发给别人；里面的配置可能包含你的行程。

无 APK：Android 端需要单独的原生/WebView 工程，当前未构建。
