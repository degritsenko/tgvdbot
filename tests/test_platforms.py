import importlib
import os
import sys
import types
import unittest


class PlatformParsingTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ.setdefault("TELEGRAM_BOT_TOKEN", "test-token")
        os.environ.setdefault("DOWNLOAD_DIR", "/tmp/tgvdbot-tests")
        cls._install_dependency_stubs()
        cls.bot = importlib.import_module("bot")

    @staticmethod
    def _install_dependency_stubs():
        yt_dlp = types.ModuleType("yt_dlp")
        yt_dlp.YoutubeDL = object
        sys.modules.setdefault("yt_dlp", yt_dlp)

        telegram = types.ModuleType("telegram")
        telegram.Update = object
        sys.modules.setdefault("telegram", telegram)

        telegram_ext = types.ModuleType("telegram.ext")
        telegram_ext.ApplicationBuilder = object
        telegram_ext.CommandHandler = object
        telegram_ext.ContextTypes = types.SimpleNamespace(DEFAULT_TYPE=object)
        telegram_ext.MessageHandler = object
        telegram_ext.filters = types.SimpleNamespace(TEXT=object(), COMMAND=object())
        sys.modules.setdefault("telegram.ext", telegram_ext)

    def test_parses_threads_share_urls(self):
        self.assertEqual(
            self.bot.parse_platform("https://www.threads.com/share/BBZwkPWZ5c/"),
            "threads",
        )

    def test_extracts_plain_url_from_markdown_link(self):
        text = (
            "[https://www.threads.com/share/BBZwkPWZ5c/]"
            "(https://www.threads.com/share/BBZwkPWZ5c/)"
        )

        self.assertEqual(
            self.bot.extract_url(text),
            "https://www.threads.com/share/BBZwkPWZ5c/",
        )

    def test_extracts_instagram_url_from_markdown_link(self):
        text = (
            "[https://www.instagram.com/reel/DUtBRu1jUiA/]"
            "(https://www.instagram.com/reel/DUtBRu1jUiA/)"
        )

        url = self.bot.extract_url(text)

        self.assertEqual(url, "https://www.instagram.com/reel/DUtBRu1jUiA/")
        self.assertEqual(self.bot.parse_platform(url), "instagram")

    def test_parses_existing_supported_platforms(self):
        cases = {
            "https://x.com/rainmaker1973/status/2021539093793153320": "x",
            "https://www.instagram.com/reel/DUtBRu1jUiA/": "instagram",
        }

        for url, platform in cases.items():
            with self.subTest(url=url):
                self.assertEqual(self.bot.parse_platform(url), platform)


if __name__ == "__main__":
    unittest.main()
