#!/usr/bin/env python3
"""老王论坛 k_misign 签到；仅使用 Python 标准库，不输出 Cookie 或响应正文。"""

from __future__ import annotations

import argparse
import gzip
import html
from html.parser import HTMLParser
from http.cookiejar import Cookie, CookieJar
from http.cookies import SimpleCookie
import os
from pathlib import Path
import re
import socket
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlencode, urljoin, urlsplit, urlunsplit
from urllib.request import HTTPCookieProcessor, HTTPRedirectHandler, Request, build_opener


ORIGIN = "https://laowang.vip"
SIGN_URL = ORIGIN + "/plugin.php?id=k_misign:sign"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
)
MAX_BYTES = 2 * 1024 * 1024
VOID_TAGS = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}


class CheckinError(Exception):
    """只包含可公开的固定错误信息。"""


def allowed_url(url: str) -> bool:
    parsed = urlsplit(url)
    return (
        parsed.scheme == "https"
        and parsed.netloc.lower() == "laowang.vip"
        and parsed.username is None
        and parsed.password is None
    )


class SameOriginRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not allowed_url(newurl):
            raise CheckinError("网站跳转到了其他域名或非 HTTPS 地址；已停止，未转发 Cookie。")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def cookie_jar(raw: str) -> CookieJar:
    raw = raw.strip()
    if raw.lower().startswith("cookie:"):
        raw = raw[7:].strip()
    if not raw:
        raise CheckinError("缺少 LAOWANG_COOKIE。请在仓库 Actions Secrets 中配置。")
    if any(ord(char) < 32 or ord(char) > 126 for char in raw):
        raise CheckinError("LAOWANG_COOKIE 必须是浏览器请求头中单行的 Cookie 值。")
    values = SimpleCookie()
    try:
        values.load(raw)
    except Exception:
        raise CheckinError("Cookie 格式不正确，请复制浏览器请求头中的完整 Cookie 值。") from None
    if not any(name.endswith("_auth") and morsel.value for name, morsel in values.items()):
        raise CheckinError("Cookie 中没有 Discuz 登录凭据（名称以 _auth 结尾）；请登录后重新复制。")
    jar = CookieJar()
    for name, morsel in values.items():
        jar.set_cookie(Cookie(
            version=0, name=name, value=morsel.value, port=None, port_specified=False,
            domain="laowang.vip", domain_specified=False, domain_initial_dot=False,
            path="/", path_specified=True, secure=True, expires=None, discard=True,
            comment=None, comment_url=None, rest={}, rfc2109=False,
        ))
    return jar


class PageParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack: list[tuple[str, bool]] = []
        self.elements: list[dict[str, str]] = []
        self.sign_elements: list[dict[str, str]] = []
        self.text: list[str] = []
        self.sign_text: list[str] = []
        self.skip = 0

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        self.elements.append(attrs)
        classes = attrs.get("class", "").split()
        inside = bool(self.stack and self.stack[-1][1])
        sign = inside or attrs.get("id") in {"JD_sign", "addsign"} or bool(
            {"qdleft", "J_chkitot", "btnvisted"}.intersection(classes)
        )
        if sign:
            self.sign_elements.append(attrs)
        if tag not in VOID_TAGS:
            self.stack.append((tag, sign))
        if tag in {"script", "style"}:
            self.skip += 1

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in VOID_TAGS:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        if tag in {"script", "style"}:
            self.skip = max(0, self.skip - 1)
        for index in range(len(self.stack) - 1, -1, -1):
            if self.stack[index][0] == tag:
                del self.stack[index:]
                break

    def handle_data(self, value):
        if self.skip:
            return
        self.text.append(value)
        if self.stack and self.stack[-1][1]:
            self.sign_text.append(value)


class SignPage:
    def __init__(self, source: str):
        self.source = source
        self.parser = PageParser()
        self.parser.feed(source)
        self.visible_text = " ".join(self.parser.text)
        self.sign_text = " ".join(self.parser.sign_text)

    def require_no_verification(self):
        if re.search(r"点击进行验证|请点击下面的按钮验证|请完成(?:人机验证|验证码)", self.visible_text):
            raise CheckinError("签到接口要求人工验证，请在浏览器完成验证；仅提供 Cookie 无法完成当前签到，脚本已停止。")

    def require_login(self):
        self.require_no_verification()
        uid = re.search(r"\bdiscuz_uid\s*=\s*['\"]?(\d+)", self.source)
        if uid and int(uid.group(1)) > 0:
            return
        logged_out_link = any(
            "member.php" in element.get("href", "")
            and "action=logout" in element.get("href", "")
            for element in self.parser.elements
        )
        if not uid and logged_out_link:
            return
        if uid or re.search(r"请.{0,8}(?:登录|登陆)|登录后|登陆后", self.visible_text):
            raise CheckinError("论坛未识别为登录状态：Cookie 已过期、不完整或不适用于云端 IP，请更新 LAOWANG_COOKIE。")
        raise CheckinError("无法确认论坛登录状态，可能遇到访问验证或页面已改版；已停止。")

    def already_signed(self) -> bool:
        if any("btnvisted" in element.get("class", "").split() for element in self.parser.sign_elements):
            return True
        return bool(re.search(r"(?:已签到|今日已签|今天已签|签到成功|明日再来)", self.sign_text))

    def action_url(self) -> str:
        # 只接受页面中实际出现的 k_misign 签到链接，不猜测新的接口。
        for element in self.parser.elements:
            for attribute in ("href", "onclick"):
                value = html.unescape(element.get(attribute, ""))
                candidates = [value] if attribute == "href" else re.findall(r"['\"]([^'\"]+)['\"]", value)
                for candidate in candidates:
                    url = urljoin(SIGN_URL, candidate)
                    parsed = urlsplit(url)
                    query = parse_qs(parsed.query)
                    if parsed.path != "/plugin.php" or query.get("id") != ["k_misign:sign"] or query.get("operation") != ["qiandao"]:
                        continue
                    if not allowed_url(url):
                        raise CheckinError("页面中的签到链接不是本站 HTTPS 地址；已停止。")
                    formhash = (query.get("formhash") or [""])[0]
                    if not re.fullmatch(r"[a-zA-Z0-9]{6,64}", formhash):
                        raise CheckinError("签到链接缺少有效 formhash，可能页面已改版；已停止。")
                    query.update(inajax=["1"], ajaxtarget=["JD_sign"])
                    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, urlencode(query, doseq=True), ""))
        raise CheckinError("已登录，但未找到页面中的签到链接；可能已改版、需要验证或账号暂不能签到。")


class ForumClient:
    def __init__(self, raw_cookie: str, user_agent: str = USER_AGENT):
        if not user_agent or any(ord(c) < 32 or ord(c) > 126 for c in user_agent):
            raise CheckinError("LAOWANG_USER_AGENT 格式不正确，必须是单行 ASCII 请求头。")
        self.opener = build_opener(HTTPCookieProcessor(cookie_jar(raw_cookie)), SameOriginRedirect())
        self.user_agent = user_agent

    def get(self, url: str, *, ajax: bool = False, attempts: int = 3) -> str:
        if not allowed_url(url):
            raise CheckinError("请求不是本站 HTTPS 地址；已停止。")
        headers = {
            "User-Agent": self.user_agent,
            "Referer": SIGN_URL,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9",
            "Cache-Control": "no-cache",
        }
        if ajax:
            headers["X-Requested-With"] = "XMLHttpRequest"
        for attempt in range(attempts):
            try:
                with self.opener.open(Request(url, headers=headers), timeout=30) as response:
                    if not allowed_url(response.url):
                        raise CheckinError("响应地址不是本站 HTTPS 地址；已停止。")
                    raw = response.read(MAX_BYTES + 1)
                    if len(raw) > MAX_BYTES:
                        raise CheckinError("服务器响应过大，无法确认签到状态。")
                    if response.headers.get("Content-Encoding", "").lower() == "gzip":
                        raw = gzip.decompress(raw)
                    if len(raw) > MAX_BYTES:
                        raise CheckinError("服务器响应过大，无法确认签到状态。")
                    charset = response.headers.get_content_charset() or "utf-8"
                    try:
                        return raw.decode(charset, errors="replace")
                    except LookupError:
                        return raw.decode("utf-8", errors="replace")
            except HTTPError as error:
                if error.code in {401, 403, 429}:
                    raise CheckinError("网站拒绝访问或要求验证（HTTP 401/403/429）；请检查 Cookie 和云端 IP，脚本不处理验证码。") from None
                if error.code not in {500, 502, 503, 504}:
                    raise CheckinError(f"网站返回 HTTP {error.code}，未确认签到成功。") from None
            except (URLError, TimeoutError, socket.timeout, ConnectionError, OSError):
                pass
            if attempt + 1 < attempts:
                time.sleep(2 ** attempt)
        raise CheckinError("访问网站失败或超时；请稍后重试。若刚提交过签到，应先检查页面状态。")

    def page(self) -> SignPage:
        page = SignPage(self.get(SIGN_URL))
        page.require_login()
        return page


def checkin(client: ForumClient, *, check_only: bool = False) -> str:
    before = client.page()
    if before.already_signed():
        return "今日已经签到，已跳过。"
    action = before.action_url()
    if check_only:
        return "登录有效，已找到签到链接；本次仅检查，未提交签到。"
    # 有副作用的请求只提交一次；即使响应超时，也通过重新读取页面判断。
    action_error = None
    try:
        response = client.get(action, ajax=True, attempts=1)
        SignPage(response).require_no_verification()
    except CheckinError as error:
        action_error = error
    for attempt in range(3):
        after = client.page()
        if after.already_signed():
            return "签到成功，已重新读取页面确认今日签到状态。"
        if attempt < 2:
            time.sleep(1)
    if action_error:
        raise action_error
    raise CheckinError("已提交签到，但页面未显示今日已签到；可能需要验证码或接口已改版，请手动检查。")


def report(success: bool, message: str):
    status = "成功" if success else "失败"
    print(f"[{status}] {message}")
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with Path(summary).open("a", encoding="utf-8") as file:
            file.write(f"### 老王论坛签到：{status}\n\n{message}\n")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check-only", action="store_true", help="仅检查登录和签到链接，不提交签到")
    args = parser.parse_args(argv)
    try:
        client = ForumClient(os.environ.get("LAOWANG_COOKIE", ""), os.environ.get("LAOWANG_USER_AGENT") or USER_AGENT)
        report(True, checkin(client, check_only=args.check_only))
        return 0
    except CheckinError as error:
        report(False, str(error))
        return 1
    except Exception:
        # 不打印异常正文、响应、URL 或堆栈，以免将登录凭据写入公开日志。
        report(False, "出现未预期的错误，未确认签到成功；请检查页面是否改版。")
        return 1


if __name__ == "__main__":
    sys.exit(main())
