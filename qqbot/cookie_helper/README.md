# Schedulr Cookie 获取小工具

给 AstrBot 插件用的 Windows 小工具。用它登录两个学校账号，自动提取并验证 WebVPN Cookie，省得手动翻 DevTools。

## 用法

1. 双击 `启动.bat`。第一次会自动装 Selenium，等一两分钟。
2. 在「账号 1 · 一班」点「打开登录页」。
3. 在弹出的 Chrome 里完成学校登录：账号 + 验证码 + 手机/微信动态密码。
4. 看到学校 VPN 门户后，回到小工具点「提取并验证」。
5. 成功会显示「✅ 验证成功：N 节课程」，Cookie 自动复制到剪贴板。
6. 同一个窗口里关掉 Chrome，切到「账号 2 · 二班」，重复 2-5。
7. 最后把两个 Cookie 分别填进 AstrBot 插件配置：
   - `课表 1 · Cookie`
   - `课表 2 · Cookie`

## 保存位置

```text
%USERPROFILE%\SchedulrBot\
├── profile_1.cookie.txt
├── profile_2.cookie.txt
└── cookies.json
```

`cookies.json` 里是两个档案的 Cookie，方便之后一次性填配置。

## 说明

- 工具用**独立的 Chrome 临时配置**，不会碰你平时浏览器的登录状态；每次打开都是干净的。
- 验证时会真的调用一次教务课表接口，成功才说明 Cookie 可用。
- Cookie 是登录凭证，等同于这段时间的学校会话。不要发群、不要提交 Git。
- 如果提示「登录态已失效」，重新打开登录页登录一次，再提取。
- 如果提示「学校 VPN 网关提示权限表不同步」，先在浏览器退出 WebVPN 再重新登录。

## 依赖

- Windows
- Python 3.10+（`python` 在 PATH 里）
- Google Chrome
- `selenium`（`启动.bat` 会自动装到 `.venv`）
