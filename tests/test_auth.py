from http.cookiejar import CookieJar
from types import SimpleNamespace

import requests

from instagram_extractor.downloader import DownloadOptions, make_loader


class _FakeLoader:
    def __init__(self, **_kwargs):
        self.context = SimpleNamespace(
            _session=SimpleNamespace(cookies=requests.cookies.RequestsCookieJar()),
            username=None,
        )

    def test_login(self):
        return "authenticated_user"

    def load_session(self, username, session_data):
        self.context.username = username
        self.session_data = session_data


def test_browser_cookies_establish_an_instaloader_login(monkeypatch, tmp_path):
    cookies = CookieJar()
    cookies.set_cookie(
        requests.cookies.create_cookie(name="sessionid", value="secret", domain=".instagram.com")
    )
    monkeypatch.setattr("instagram_extractor.downloader.instaloader.Instaloader", _FakeLoader)
    monkeypatch.setattr("instagram_extractor.downloader._browser_cookies", lambda _browser: cookies)

    options = DownloadOptions(output_dir=tmp_path, cookies_from_browser="chrome")
    loader = make_loader(options)

    assert loader.context.username == "authenticated_user"
    assert loader.session_data["sessionid"] == "secret"
    assert options.instagram_user == "authenticated_user"
