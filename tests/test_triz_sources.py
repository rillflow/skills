import importlib.util
import json
import socket
import ssl
import tempfile
import unittest
import unittest.mock
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlparse


class _FakeResponse:
    """urlopenのcontext manager契約だけを満たす応答。"""

    def __init__(self, body: bytes, headers: dict):
        self._body = body
        self.headers = headers

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def read(self) -> bytes:
        return self._body


SCRIPT = Path(__file__).parents[1] / "skills" / "ideation" / "scripts" / "triz_sources.py"
SPEC = importlib.util.spec_from_file_location("triz_sources", SCRIPT)
triz_sources = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(triz_sources)


class TrizSourcesTest(unittest.TestCase):
    def test_html_extraction_keeps_links_images_and_tables_but_not_script_or_style(self):
        text = triz_sources.html_to_text(
            "<h2>Heading</h2><p>Read <a href='/source'>source</a>.</p>"
            "<img alt='diagram' src='images/a.png'><table><tr><th>A</th><td>B</td></tr></table>"
            "<script>ignore_me()</script><style>.hidden { display: none }</style>",
            "https://example.test/base/page/",
        )
        self.assertIn("## Heading", text)
        self.assertIn("[source](https://example.test/source)", text)
        self.assertIn("![diagram](https://example.test/base/page/images/a.png)", text)
        self.assertIn("| A | B |", text)
        self.assertNotIn("ignore_me", text)
        self.assertNotIn("display: none", text)

    def test_catalog_section_patterns_keep_all_expected_ids_and_nested_511_variants(self):
        catalog = triz_sources.load_catalog()
        principles_text = "\n".join(
            f"#### Principle {identifier}. Fixture {identifier}\nCondition {identifier}"
            for identifier in catalog["principles"]
        )
        standards_text = "\n".join(
            f"#### {identifier}. Fixture {identifier}\nCondition {identifier}"
            for identifier in catalog["standards"]
        )
        standards_text = standards_text.replace(
            "#### 5.1.2.",
            "##### 5.1.1.1. Nested condition\nNested body\n#### 5.1.2.",
        )
        principles = triz_sources.extract_sections(principles_text, triz_sources.PRINCIPLE_HEADING)
        standards = triz_sources.extract_sections(standards_text, triz_sources.STANDARD_HEADING)
        triz_sources.assert_catalog_sections(catalog, principles, "principles")
        triz_sources.assert_catalog_sections(catalog, standards, "standards")
        self.assertIn("Nested condition", standards["5.1.1"]["content"])
        self.assertNotIn("5.1.1.1", standards)

    def test_fetch_with_complete_fixture_and_incomplete_manifest_rejection(self):
        catalog = triz_sources.load_catalog()
        source_index = triz_sources.load_source_index()
        documents = []
        for indexed in source_index["documents"]:
            identifier = str(indexed["id"])
            content = "<h2>Fixture document</h2><p>ordinary body</p>"
            if identifier == "6023":
                content = "".join(
                    f"<h4>Principle {number}. Fixture {number}</h4><p>Condition {number}</p>"
                    for number in catalog["principles"]
                )
            if identifier == "8094":
                content = "".join(
                    f"<h4>{number}. Fixture {number}</h4><p>Condition {number}</p>"
                    for number in catalog["standards"]
                ).replace(
                    "<h4>5.1.2.",
                    "<h5>5.1.1.1. Nested condition</h5><p>Nested body</p><h4>5.1.2.",
                )
            documents.append({
                "id": int(identifier), "link": indexed["url"], "parent": int(indexed["parent"] or 0),
                "modified": indexed["modified"], "title": {"rendered": indexed["title"]},
                "content": {"rendered": content},
            })

        calls = []

        def fixture_fetch(url):
            calls.append(url)
            parsed = urlparse(url)
            if "wp-json" in url:
                page = int(parse_qs(parsed.query)["page"][0])
                selected = documents[:100] if page == 1 else documents[100:]
                return json.dumps(selected).encode(), {"content-type": "application/json; charset=utf-8", "x-wp-totalpages": "2"}
            part = int(parsed.path.rsplit("-", 1)[1].removesuffix(".asp"))
            html = (
                "prefix site UI<!--begin container_2-->"
                f"<h3>ЧАСТЬ {part}. Fixture</h3><p>ARIZ body {part}</p>"
                "<table><tr><td>field</td><td>resource</td></tr></table>"
                "<a href='/related'>related</a><img alt='diagram' src='/diagram.png'>"
                "<script>site_ui()</script><!--end container_2-->footer site UI"
            )
            return html.encode("cp1251"), {"content-type": "text/html; charset=windows-1251"}

        with tempfile.TemporaryDirectory() as temporary:
            cache = Path(temporary) / "cache"
            manifest = triz_sources.fetch_cache(cache, refresh=True, fetcher=fixture_fetch)
            self.assertTrue(manifest["complete"])
            self.assertEqual(103, manifest["document_count"])
            self.assertEqual(40, manifest["principle_count"])
            self.assertEqual(76, manifest["standard_count"])
            self.assertEqual(9, manifest["ariz_part_count"])
            self.assertEqual(40, len(triz_sources.read_group(cache, "principles", None)))
            self.assertEqual(76, len(triz_sources.read_group(cache, "standards", None)))
            self.assertEqual(9, len(triz_sources.read_group(cache, "ariz", None)))
            document = triz_sources.read_group(cache, "documents", "6023")
            self.assertIn("content", document)
            self.assertNotIn("content_html", document)
            standard = triz_sources.read_group(cache, "standards", "5.1.1")
            self.assertIn("Nested condition", standard["content"])
            ariz = triz_sources.read_group(cache, "ariz", "1")
            self.assertIn("![diagram](https://www.altshuller.ru/diagram.png)", ariz["content"])
            self.assertIn("| field | resource |", ariz["content"])
            self.assertNotIn("site_ui", ariz["content"])
            call_count = len(calls)
            triz_sources.fetch_cache(cache, refresh=False, fetcher=lambda _: self.fail("complete cache must not refetch"))
            self.assertEqual(call_count, len(calls))
            results = triz_sources.search_cache(cache, "Nested body")
            self.assertEqual("documents", results[0]["group"])

            # 既知のversion 1派生fileは、明示的なrefreshでだけ取り除く。
            triz_sources.write_json(cache / "raw" / "ariz85v-1.html.json", {"legacy": True})
            triz_sources.fetch_cache(cache, refresh=True, fetcher=fixture_fetch)
            self.assertFalse((cache / "raw" / "ariz85v-1.html.json").exists())

            # 欠損entryではreadもfetch再利用も拒否する。
            (cache / "principles" / "40.json").unlink()
            with self.assertRaises(triz_sources.CacheStateError):
                triz_sources.read_group(cache, "principles", None)
            with self.assertRaises(triz_sources.CacheStateError):
                triz_sources.fetch_cache(cache, refresh=False, fetcher=fixture_fetch)
            triz_sources.fetch_cache(cache, refresh=True, fetcher=fixture_fetch)

            # JSONとして読めても内容変更は以前のartifact hashで通さない。
            changed = cache / "standards" / "1.1.1.json"
            changed.write_bytes(changed.read_bytes() + b" ")
            with self.assertRaises(triz_sources.CacheStateError):
                triz_sources.read_group(cache, "standards", "1.1.1")
            with self.assertRaises(triz_sources.CacheStateError):
                triz_sources.fetch_cache(cache, refresh=False, fetcher=fixture_fetch)
            triz_sources.fetch_cache(cache, refresh=True, fetcher=fixture_fetch)

            # 改変したmanifestのcount、ID、hashは再利用しない。
            manifest = triz_sources.read_json(cache / "manifest.json")
            manifest["document_count"] = 102
            triz_sources.write_json(cache / "manifest.json", manifest)
            with self.assertRaises(triz_sources.CacheStateError):
                triz_sources.read_group(cache, "principles", "1")
            with self.assertRaises(triz_sources.CacheStateError):
                triz_sources.fetch_cache(cache, refresh=False, fetcher=fixture_fetch)
            triz_sources.fetch_cache(cache, refresh=True, fetcher=fixture_fetch)

            manifest = triz_sources.read_json(cache / "manifest.json")
            manifest["sources"][0]["id"] = "matriz-docs-tampered"
            triz_sources.write_json(cache / "manifest.json", manifest)
            with self.assertRaises(triz_sources.CacheStateError):
                triz_sources.read_group(cache, "documents", "6023")
            triz_sources.fetch_cache(cache, refresh=True, fetcher=fixture_fetch)

            manifest = triz_sources.read_json(cache / "manifest.json")
            manifest["artifact_sha256"]["documents/6023.json"] = "0" * 64
            triz_sources.write_json(cache / "manifest.json", manifest)
            with self.assertRaises(triz_sources.CacheStateError):
                triz_sources.search_cache(cache, "Fixture")
            with self.assertRaises(triz_sources.CacheStateError):
                triz_sources.fetch_cache(cache, refresh=False, fetcher=fixture_fetch)

            # refresh失敗後は以前のartifactも再利用しない。
            def failed_fetch(_):
                raise OSError("controlled fetch failure")

            with self.assertRaises(OSError):
                triz_sources.fetch_cache(cache, refresh=True, fetcher=failed_fetch)
            with self.assertRaises(triz_sources.CacheStateError):
                triz_sources.read_group(cache, "principles", "1")


class FetchErrorClassificationTest(unittest.TestCase):
    def classify(self, error):
        return triz_sources.classify_fetch_error("https://example.test/doc", error)

    def test_certificate_tls_http_timeout_and_connection_failures_get_separate_categories(self):
        certificate_error = ssl.SSLCertVerificationError(1, "certificate verify failed")
        self.assertEqual("tls-certificate", self.classify(URLError(certificate_error)).category)
        self.assertEqual("tls-handshake", self.classify(URLError(ssl.SSLError("record layer failure"))).category)
        self.assertEqual("timeout", self.classify(URLError(socket.timeout("handshake operation timed out"))).category)
        self.assertEqual("network", self.classify(URLError(ConnectionRefusedError(61, "Connection refused"))).category)
        self.assertEqual("network", self.classify(URLError(socket.gaierror(8, "nodename nor servname provided"))).category)
        http_error = HTTPError("https://example.test/doc", 404, "Not Found", {}, None)
        self.assertEqual("http", self.classify(http_error).category)

    def test_failure_records_category_and_next_step_in_manifest(self):
        def failing_fetch(url):
            raise triz_sources.classify_fetch_error(url, URLError(ssl.SSLCertVerificationError(1, "certificate verify failed")))

        with tempfile.TemporaryDirectory() as temporary:
            cache = Path(temporary) / "cache"
            with self.assertRaises(triz_sources.SourceFetchError):
                triz_sources.fetch_cache(cache, refresh=True, fetcher=failing_fetch)
            manifest = triz_sources.read_json(cache / "manifest.json")
            self.assertFalse(manifest["complete"])
            self.assertEqual("tls-certificate", manifest["error_category"])
            self.assertIn("SSL_CERT_FILE", manifest["error_hint"])

    def test_timeout_retries_once_and_other_failures_do_not_retry(self):
        attempts = []

        def timeout_then_success(request, timeout):
            attempts.append(request.full_url)
            if len(attempts) == 1:
                raise URLError(socket.timeout("handshake operation timed out"))
            return _FakeResponse(b"body", {"Content-Type": "text/html"})

        with unittest.mock.patch.object(triz_sources, "urlopen", timeout_then_success):
            body, headers = triz_sources.http_get("https://example.test/doc")
        self.assertEqual(b"body", body)
        self.assertEqual("text/html", headers["content-type"])
        self.assertEqual(2, len(attempts))

        refused = []

        def always_refused(request, timeout):
            refused.append(request.full_url)
            raise URLError(ConnectionRefusedError(61, "Connection refused"))

        with unittest.mock.patch.object(triz_sources, "urlopen", always_refused):
            with self.assertRaises(triz_sources.SourceFetchError):
                triz_sources.http_get("https://example.test/doc")
        self.assertEqual(1, len(refused))


class DefaultCacheTest(unittest.TestCase):
    def test_cache_argument_is_optional_and_environment_overrides_the_default(self):
        self.assertEqual(
            Path.home() / ".cache" / "ideation" / "triz-sources",
            triz_sources.default_cache({}),
        )
        self.assertEqual(
            Path("/var/tmp/ideation"),
            triz_sources.default_cache({"IDEATION_TRIZ_CACHE": "/var/tmp/ideation"}),
        )
        parser = triz_sources.build_parser()
        self.assertEqual(triz_sources.DEFAULT_CACHE, parser.parse_args(["fetch"]).cache)
        self.assertEqual(
            Path("/var/tmp/explicit"),
            parser.parse_args(["read", "--group", "principles", "--cache", "/var/tmp/explicit"]).cache,
        )


if __name__ == "__main__":
    unittest.main()
