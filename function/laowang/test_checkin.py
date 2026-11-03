"""离线验证：不连接论坛，不使用真实 Cookie。"""

import contextlib
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from checkin import (
    CheckinError, ForumClient, SameOriginRedirect, SignPage,
    SIGN_URL, allowed_url, checkin, cookie_jar, main,
)


def page(button, uid="123"):
    return SignPage(
        f"<html><script>var discuz_uid = '{uid}';</script>"
        f'<div class="qdleft">{button}</div>'
        '<div>今日签到人数：12345</div></html>'
    )


UNSIGNED = page(
    '<a id="JD_sign" class="btn J_chkitot" href="javascript:;" '
    'onclick="ajaxget(\'plugin.php?id=k_misign:sign&amp;operation=qiandao'
    '&amp;formhash=12ab34cd&amp;format=empty\', \'JD_sign\');">签到</a>'
)
SIGNED = page('<a id="JD_sign" class="btn btnvisted">已签到</a>')
GUEST = page('<a href="member.php?mod=logging&amp;action=login">登录</a>', uid="0")


class FakeClient:
    def __init__(self, pages, error=None, response=""):
        self.pages = iter(pages)
        self.calls = []
        self.error = error
        self.response = response

    def page(self):
        result = next(self.pages)
        result.require_login()
        return result

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        if self.error:
            raise self.error
        return self.response


class PageTests(unittest.TestCase):
    def test_guest_is_not_success(self):
        with self.assertRaises(CheckinError):
            GUEST.require_login()

    def test_authenticated_uid(self):
        UNSIGNED.require_login()
        self.assertFalse(UNSIGNED.already_signed())

    def test_css_or_scripts_do_not_prove_success(self):
        source = UNSIGNED.source + '<style>.btnvisted { color:red; }</style><script>var msg="已签到";</script>'
        self.assertFalse(SignPage(source).already_signed())

    def test_signed_button(self):
        self.assertTrue(SIGNED.already_signed())
        self.assertTrue(page('<a class="btn btnvisted"></a>').already_signed())

    def test_ranklist_does_not_prove_success(self):
        source = UNSIGNED.source + '<div>其他用户今日已签到，签到成功</div>'
        self.assertFalse(SignPage(source).already_signed())

    def test_extract_real_button_and_hash(self):
        query = parse_qs(urlsplit(UNSIGNED.action_url()).query)
        self.assertEqual(query["formhash"], ["12ab34cd"])
        self.assertEqual(query["operation"], ["qiandao"])
        self.assertEqual(query["inajax"], ["1"])

    def test_href_and_percent_encoded_plugin(self):
        actual = page('<a href="/plugin.php?id=k_misign%3Asign&amp;operation=qiandao&amp;formhash=a123bc45">签到</a>')
        self.assertTrue(actual.action_url().startswith("https://laowang.vip/plugin.php?"))

    def test_missing_hash_and_unknown_interface_fail(self):
        for button in (
            '<a href="plugin.php?id=k_misign:sign&amp;operation=qiandao">签到</a>',
            '<a href="/new-unknown-api">签到</a>',
        ):
            with self.subTest(button=button), self.assertRaises(CheckinError):
                page(button).action_url()

    def test_different_origin_is_rejected(self):
        malicious = page('<a href="https://example.org/plugin.php?id=k_misign:sign&amp;operation=qiandao&amp;formhash=abcd1234">签到</a>')
        with self.assertRaises(CheckinError):
            malicious.action_url()
        for url in ("http://laowang.vip/", "https://laowang.vip.example.org/", "https://laowang.vip:444/", "https://someone@laowang.vip/"):
            self.assertFalse(allowed_url(url))

    def test_unrecognized_page_is_rejected(self):
        with self.assertRaises(CheckinError):
            SignPage('<title>Just a moment...</title>').require_login()


class ExecutionTests(unittest.TestCase):
    def test_already_signed_does_not_submit(self):
        client = FakeClient([SIGNED])
        self.assertIn("跳过", checkin(client))
        self.assertEqual(client.calls, [])

    def test_check_only_does_not_submit(self):
        client = FakeClient([UNSIGNED])
        self.assertIn("未提交", checkin(client, check_only=True))
        self.assertEqual(client.calls, [])

    def test_success_requires_page_confirmation(self):
        client = FakeClient([UNSIGNED, SIGNED], response="unknown response")
        self.assertIn("签到成功", checkin(client))
        self.assertEqual(len(client.calls), 1)
        self.assertEqual(client.calls[0][1]["attempts"], 1)

    @patch("checkin.time.sleep")
    def test_http_200_or_success_text_alone_is_not_success(self, _):
        client = FakeClient([UNSIGNED, UNSIGNED, UNSIGNED, UNSIGNED], response="签到成功")
        with self.assertRaises(CheckinError):
            checkin(client)
        self.assertEqual(len(client.calls), 1)

    def test_submission_timeout_then_confirmed(self):
        client = FakeClient([UNSIGNED, SIGNED], error=CheckinError("访问网站失败或超时。"))
        self.assertIn("签到成功", checkin(client))
        self.assertEqual(len(client.calls), 1)

    def test_login_expired_after_submission(self):
        client = FakeClient([UNSIGNED, GUEST])
        with self.assertRaises(CheckinError):
            checkin(client)

    @patch("checkin.time.sleep")
    def test_original_submission_error_not_hidden(self, _):
        client = FakeClient([UNSIGNED, UNSIGNED, UNSIGNED, UNSIGNED], error=CheckinError("网站拒绝访问"))
        with self.assertRaisesRegex(CheckinError, "网站拒绝访问"):
            checkin(client)


class CredentialTests(unittest.TestCase):
    def test_missing_or_non_login_cookie_fails(self):
        for raw in ("", "foo=bar", "prefix_auth=", "prefix_auth=fake\r\nInjected: true"):
            with self.subTest(raw=raw), self.assertRaises(CheckinError):
                cookie_jar(raw)

    def test_cookie_header_prefix_and_domain_scope(self):
        jar = cookie_jar("Cookie: X9wU_2132_auth=fake%2Ftoken; X9wU_2132_saltkey=fake")
        self.assertEqual(len(list(jar)), 2)
        for cookie in jar:
            self.assertTrue(cookie.secure)
            self.assertEqual(cookie.domain, "laowang.vip")

    def test_redirect_stops_before_forwarding_cookie(self):
        from urllib.request import Request
        redirect = SameOriginRedirect()
        with self.assertRaises(CheckinError):
            redirect.redirect_request(Request(SIGN_URL), None, 302, "", {}, "https://example.org/")

    def test_missing_secret_is_failed_exit_and_summary(self):
        with tempfile.TemporaryDirectory() as directory:
            summary = Path(directory) / "summary.md"
            with patch.dict("os.environ", {"LAOWANG_COOKIE": "", "GITHUB_STEP_SUMMARY": str(summary)}), contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(main([]), 1)
            self.assertIn("缺少 LAOWANG_COOKIE", summary.read_text(encoding="utf-8"))

    @patch("checkin.ForumClient")
    def test_unexpected_error_cannot_expose_secret_in_logs(self, mocked):
        mocked.side_effect = ValueError("DO_NOT_LOG_THIS_COOKIE")
        output = io.StringIO()
        with patch.dict("os.environ", {"GITHUB_STEP_SUMMARY": ""}), contextlib.redirect_stdout(output):
            self.assertEqual(main([]), 1)
        self.assertNotIn("DO_NOT_LOG_THIS_COOKIE", output.getvalue())


if __name__ == "__main__":
    unittest.main()
