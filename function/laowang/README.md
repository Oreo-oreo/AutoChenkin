# 老王论坛 GitHub Actions 每日签到

在 GitHub 托管的 Ubuntu 运行器执行，无需自己的电脑保持开机。Python 3.12 标准库实现，无需 pip 安装依赖。

签到页面：<https://laowang.vip/plugin.php?id=k_misign:sign>。

## 一次配置

1. 打开仓库的 **Settings → Secrets and variables → Actions → New repository secret**。
2. 新建 **`LAOWANG_COOKIE`**，填写你登录老王论坛后的完整请求 Cookie，只有值，不需要 `Cookie:` 前缀。
3. 可选新建 **`LAOWANG_USER_AGENT`**，填写复制 Cookie 时浏览器请求头的 `User-Agent`。不填使用脚本默认值。
4. 打开 **Actions → 老王论坛每日签到 → Run workflow**，选择 `master` 分支。第一次可勾选“仅检查登录状态”，检查通过后取消勾选，再执行一次真正签到。
5. 查看 **执行签到** 作业和运行摘要。只有显示“签到成功，已重新读取页面确认”或“今日已经签到”才算签到通过。代码检查通过不代表已经完成真实签到。

Cookie 只放在 GitHub Secrets。它相当于登录凭据，不要放进代码、Issue、聊天、截图或提交历史。日志不会打印 Cookie、页面正文或完整异常。

## 如何复制 Cookie

在 Chrome 中登录论坛，打开上方签到页面：

1. 按 `F12` 打开开发者工具，选择 **Network / 网络**。
2. 刷新签到页面，在请求列表中选择 `plugin.php?id=k_misign:sign`。
3. 在 **Headers / 标头 → Request Headers / 请求标头** 中找到 **Cookie**，复制完整值。
4. 该值应包含名称以 `_auth` 结尾的 Cookie（本次查看的论坛前缀为 `X9wU_2132_`，实际以浏览器为准）。
5. 将值直接填入仓库的 `LAOWANG_COOKIE` Secret。不要复制 `Set-Cookie` 响应头，也不要使用 `document.cookie`，后者可能缺少 HttpOnly 登录凭据。

## 运行时间

- 每天北京时间 **09:17** 首次签到。
- 每天北京时间 **17:17** 再检查，上午已签到就跳过；上午临时失败可在下午补签。
- 工作流采用 UTC，`17 1,9 * * *` 对应上述北京时间。定时任务可能延迟，并非准点保证；错开整点是为了减少排队。
- 定时工作流文件必须位于默认分支。目前该仓库默认分支是 `master`。
- 本仓库为公开仓库，GitHub 会在连续 60 天没有仓库活动时禁用定时工作流。需定期查看状态并重新启用；本脚本不会为了保活而修改仓库。

不要启用本仓库原有的 **同步源仓库** 工作流来同步本功能：它同步的是上游项目，可能覆盖此次增加的文件。此次部署没有修改原有工作流。

## 脚本怎样判断成功

1. 从 Secret 读取 Cookie，访问本站 HTTPS 签到页。
2. 确认登录状态；如果未登录或页面无法识别，退出失败。
3. 已签到则跳过；否则从页面实际的 `href` / `onclick` 中提取签到链接和最新 `formhash`。
4. 只提交一次签到请求，然后重新读取签到页面，确认签到按钮变为已签到。
5. 不把 HTTP 200 或接口出现“成功”二字直接当成成功；无法核实则退出失败。

只向 `https://laowang.vip` 请求，阻止跨域或 HTTP 重定向，不向新域名自动发送 Cookie。脚本不自动登录、不处理验证码、不绕过站点验证。

## 故障处理与实际验证范围

- **缺少 LAOWANG_COOKIE**：检查 Secret 名称拼写及配置的仓库。
- **未识别为登录状态**：浏览器重新登录后更新 Cookie；Cookie 可能已过期，也可能绑定了 IP/设备，不能在 GitHub 运行器使用。
- **HTTP 401/403/429、验证码或访问验证**：GitHub 运行器 IP 可能受限，或需要人工验证；仅更新 Cookie 不保证解决。
- **找不到签到链接 / 页面未显示今日已签到**：可能站点模板或接口变化，需要根据登录后的页面调整。脚本会停止而不猜接口。
- **定时没有执行**：确认默认分支、Actions 是否启用、工作流是否被禁用；GitHub 调度也可能延迟。
- Cookie 有效期由论坛决定。本脚本无法保证 Cookie 永久有效；失效后需手动更新 Secret。

交付前验证包括离线回归、Python 语法和未登录页面识别。真正的账号签到、Cookie 在 GitHub IP 上是否有效，需要配置 Secret 后手动运行才能验证；不能仅凭离线测试宣称已签到。

## 本地运行（可选）

将 `LAOWANG_COOKIE` 放入当前进程的环境变量后：

```console
python function/laowang/checkin.py --check-only
python function/laowang/checkin.py
python -m unittest discover -s function/laowang -p "test_*.py" -v
```

不要将真实 Cookie 直接写进命令行，以免进入终端历史。

## 文件与官方文档

- `.github/workflows/laowang.yml`：定时、手动执行以及提交后的离线检查。
- `function/laowang/checkin.py`：签到逻辑。
- `function/laowang/test_checkin.py`：无需登录或联网的回归检查。
- [GitHub 定时任务规则](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule)
- [GitHub Actions Secrets 配置](https://docs.github.com/en/actions/how-tos/write-workflows/choose-what-workflows-do/use-secrets)
