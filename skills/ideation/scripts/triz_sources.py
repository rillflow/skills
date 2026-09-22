#!/usr/bin/env python3
"""公開された古典TRIZ資料を、呼び出し側が指定したcacheへ取得して読む。

repositoryにはmetadataとこのprogramだけを置く。source本文は指定された
cacheにだけ取得し、repositoryへ自動保存しない。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import socket
import ssl
import sys
import tempfile
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Callable, Iterable
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parent.parent
CATALOG_PATH = ROOT / "references" / "triz-catalog.json"
SOURCE_INDEX_PATH = ROOT / "references" / "source-index.json"
MATRIZ_REST_URL = "https://wiki.matriz.org/wp-json/wp/v2/docs?per_page=100&page={}"
ARIZ_URL = "https://www.altshuller.ru/triz/ariz85v-{}.asp"
MANIFEST_NAME = "manifest.json"
USER_AGENT = "ideation-triz-source-cache/1.0 (+https://wiki.matriz.org/)"
CACHE_ENVIRONMENT_NAME = "IDEATION_TRIZ_CACHE"


def default_cache(environment: dict[str, str] | None = None) -> Path:
    """cacheの既定位置。再起動で消える場所を使わず、環境変数で上書きできる。"""
    source = os.environ if environment is None else environment
    configured = source.get(CACHE_ENVIRONMENT_NAME)
    if configured:
        return Path(configured).expanduser()
    return Path.home() / ".cache" / "ideation" / "triz-sources"


DEFAULT_CACHE = default_cache()


class CacheStateError(RuntimeError):
    """要求されたcacheがない、未完了、または改変されている。"""


class SourceFetchError(RuntimeError):
    """原典の取得に失敗した。categoryで原因を分類し、hintで次の手を示す。"""

    def __init__(self, category: str, url: str, detail: str, hint: str) -> None:
        super().__init__(f"[{category}] {url}: {detail}\n  次の手: {hint}")
        self.category = category
        self.url = url
        self.detail = detail
        self.hint = hint


CURL_HINT = "curlで同じURLを直接読む (例: curl -sS -A 'ideation' <url>)。読めた範囲と読めなかった範囲を記録する"
TLS_CERT_HINT = (
    "Pythonの証明書storeが無い。次のいずれかで再実行する: "
    "(1) SSL_CERT_FILE=$(python3 -m certifi) を付ける (pip install certifi)、"
    "(2) macOSのpython.org版なら /Applications/Python 3.x/Install Certificates.command を実行、"
    "(3) OSの証明書を使うpython3 (homebrew等) に切り替える。それでも駄目なら " + CURL_HINT
)


def classify_fetch_error(url: str, error: Exception) -> SourceFetchError:
    """urlopenの例外を、証明書・TLS・HTTP・網の4分類へ落とす。"""
    reason = getattr(error, "reason", error)
    text = f"{type(error).__name__}: {error}"
    if isinstance(error, HTTPError):
        return SourceFetchError("http", url, f"HTTP {error.code}", "URLと公開状態を確認し、変わっていればsource-index.jsonを更新する")
    if isinstance(reason, (socket.timeout, TimeoutError)) or isinstance(error, (socket.timeout, TimeoutError)) or "timed out" in text:
        return SourceFetchError("timeout", url, text, "一時的なことが多い (altshuller.ruのTLS handshakeで実測)。同じcommandを再実行する。続くなら " + CURL_HINT)
    if isinstance(reason, ssl.SSLCertVerificationError) or "CERTIFICATE_VERIFY_FAILED" in text:
        return SourceFetchError("tls-certificate", url, text, TLS_CERT_HINT)
    if isinstance(reason, ssl.SSLError) or "SSL" in text or "TLS" in text:
        return SourceFetchError("tls-handshake", url, text, "proxyや古いTLS設定が原因のことが多い。" + CURL_HINT)
    if isinstance(reason, (socket.gaierror, ConnectionError)) or isinstance(error, ConnectionError):
        return SourceFetchError("network", url, text, "DNS・proxy・sandboxの外向き通信を確認する。復旧しなければ " + CURL_HINT)
    return SourceFetchError("unknown", url, text, CURL_HINT)


class SourceFormatError(RuntimeError):
    """公開sourceに想定した文書境界がない。"""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def sha256(data: bytes | str) -> str:
    if isinstance(data, str):
        data = data.encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def read_json(path: Path) -> Any:
    with path.open(encoding="utf-8") as source:
        return json.load(source)


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=path.parent, delete=False
    ) as temporary:
        json.dump(value, temporary, ensure_ascii=False, indent=2, sort_keys=True)
        temporary.write("\n")
        temporary_path = Path(temporary.name)
    temporary_path.replace(path)


def write_bytes(path: Path, value: bytes) -> None:
    """取得responseを変換せず、hashを再計算できる形で保存する。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("wb", dir=path.parent, delete=False) as temporary:
        temporary.write(value)
        temporary_path = Path(temporary.name)
    temporary_path.replace(path)


def load_catalog() -> dict[str, list[str]]:
    catalog = read_json(CATALOG_PATH)
    required = {"schema_version", "principles", "standards", "ariz", "tools"}
    if set(catalog) != required or catalog["schema_version"] != 1:
        raise SourceFormatError("triz-catalog.jsonのschemaに対応していません")
    if (
        len(catalog["principles"]) != 40
        or len(set(catalog["principles"])) != 40
        or catalog["principles"] != [str(number) for number in range(1, 41)]
    ):
        raise SourceFormatError("principlesはID 1から40を一意に含む必要があります")
    if len(catalog["standards"]) != 76 or len(set(catalog["standards"])) != 76:
        raise SourceFormatError("standardsは76個の一意なIDを含む必要があります")
    if catalog["ariz"] != [str(number) for number in range(1, 10)]:
        raise SourceFormatError("arizはID 1から9を含む必要があります")
    return catalog


def load_source_index() -> dict[str, Any]:
    index = read_json(SOURCE_INDEX_PATH)
    documents = index.get("documents", [])
    if index.get("schema_version") != 1 or len(documents) != 103:
        raise SourceFormatError("source-index.jsonは103文書snapshotを含む必要があります")
    ids = [str(document["id"]) for document in documents]
    if len(set(ids)) != 103:
        raise SourceFormatError("source-index.jsonに重複document IDがあります")
    return index


def http_get(url: str) -> tuple[bytes, dict[str, str]]:
    request = Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json, text/html"})
    last_error: SourceFetchError | None = None
    for attempt in range(2):  # timeoutだけ1回再試行する。証明書・HTTP・接続拒否は再試行しない。
        try:
            with urlopen(request, timeout=30) as response:
                return response.read(), {name.lower(): value for name, value in response.headers.items()}
        except (URLError, OSError, ssl.SSLError) as error:
            last_error = classify_fetch_error(url, error)
            if last_error.category != "timeout" or attempt == 1:
                raise last_error from error
    raise last_error  # pragma: no cover


def charset_from_headers(headers: dict[str, str], fallback: str) -> str:
    content_type = headers.get("content-type", "")
    matched = re.search(r"charset=([A-Za-z0-9._-]+)", content_type, flags=re.I)
    return matched.group(1) if matched else fallback


class TextExtractor(HTMLParser):
    """Converts source HTML to readable markdown without executing or retaining UI code."""

    block_tags = {
        "address", "article", "blockquote", "dd", "div", "dl", "dt", "figcaption",
        "figure", "footer", "h1", "h2", "h3", "h4", "h5", "h6", "header", "li",
        "main", "ol", "p", "pre", "section", "ul",
    }

    def __init__(self, base_url: str) -> None:
        super().__init__(convert_charrefs=True)
        self.base_url = base_url
        self.parts: list[str] = []
        self.ignored_depth = 0
        self.links: list[str] = []
        self.table_cell_count: list[int] = []

    def add(self, text: str) -> None:
        self.parts.append(text)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        attributes = dict(attrs)
        if tag in {"script", "style", "noscript", "template"}:
            self.ignored_depth += 1
            return
        if self.ignored_depth:
            return
        if tag in {"br", "hr"}:
            self.add("\n")
        elif tag in {"h1", "h2", "h3", "h4", "h5", "h6"}:
            self.add("\n" + "#" * int(tag[1]) + " ")
        elif tag == "li":
            self.add("\n- ")
        elif tag in self.block_tags:
            self.add("\n")
        elif tag == "a":
            href = attributes.get("href")
            if href:
                self.add("[")
                self.links.append(urljoin(self.base_url, href))
        elif tag == "img":
            source = attributes.get("src")
            if source:
                alt = attributes.get("alt", "")
                self.add(f"![{alt}]({urljoin(self.base_url, source)})")
        elif tag == "tr":
            self.table_cell_count.append(0)
            self.add("\n| ")
        elif tag in {"td", "th"} and self.table_cell_count:
            count = self.table_cell_count[-1]
            if count:
                self.add(" | ")
            self.table_cell_count[-1] += 1

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in {"script", "style", "noscript", "template"}:
            if self.ignored_depth:
                self.ignored_depth -= 1
            return
        if self.ignored_depth:
            return
        if tag == "a" and self.links:
            self.add(f"]({self.links.pop()})")
        elif tag == "tr" and self.table_cell_count:
            self.add(" |\n")
            self.table_cell_count.pop()
        elif tag in self.block_tags:
            self.add("\n")

    def handle_data(self, data: str) -> None:
        if not self.ignored_depth:
            self.add(data)

    def text(self) -> str:
        lines: list[str] = []
        for line in "".join(self.parts).replace("\r", "").split("\n"):
            compact = re.sub(r"[\t \u00a0]+", " ", line).strip()
            if compact or (lines and lines[-1]):
                lines.append(compact)
        return "\n".join(lines).strip() + "\n"


def html_to_text(html: str, base_url: str) -> str:
    extractor = TextExtractor(base_url)
    extractor.feed(html)
    extractor.close()
    return extractor.text()


def altshuller_content_fragment(html: str) -> str:
    start_marker = "<!--begin container_2-->"
    end_marker = "<!--end container_2-->"
    start = html.find(start_marker)
    end = html.find(end_marker, start + len(start_marker))
    if start < 0 or end < 0:
        raise SourceFormatError("ARIZ pageに文書本文containerがありません")
    return html[start + len(start_marker):end]


def extract_sections(text: str, pattern: re.Pattern[str]) -> dict[str, dict[str, str]]:
    matches = list(pattern.finditer(text))
    sections: dict[str, dict[str, str]] = {}
    for position, match in enumerate(matches):
        identifier = match.group("id")
        title = match.group("title").strip()
        end = matches[position + 1].start() if position + 1 < len(matches) else len(text)
        sections[identifier] = {"title": title, "content": text[match.start():end].strip() + "\n"}
    return sections


PRINCIPLE_HEADING = re.compile(
    r"(?m)^(?:#+\s+)?Principle\s+(?P<id>[1-9]|[1-3][0-9]|40)\.\s*(?P<title>.+)$"
)
STANDARD_HEADING = re.compile(
    r"(?m)^(?:#+\s+)?(?P<id>[1-5]\.[1-5]\.[0-9]+)\.(?![0-9])\s*(?P<title>.+)$"
)


def assert_catalog_sections(
    catalog: dict[str, list[str]], sections: dict[str, dict[str, str]], group: str
) -> None:
    expected = set(catalog[group])
    actual = set(sections)
    if actual != expected:
        missing = sorted(expected - actual)
        unexpected = sorted(actual - expected)
        raise SourceFormatError(
            f"{group}のheading境界が一致しません。missing={missing}, unexpected={unexpected}"
        )


def document_record(document: dict[str, Any], fetched_at: str) -> dict[str, Any]:
    content = document.get("content", {}).get("rendered")
    if not isinstance(content, str):
        raise SourceFormatError(f"MATRIZ document {document.get('id')}にrendered contentがありません")
    source_url = str(document.get("link", ""))
    if not source_url:
        raise SourceFormatError(f"MATRIZ document {document.get('id')}にsource URLがありません")
    title = str(document.get("title", {}).get("rendered", "")).strip()
    if not title:
        raise SourceFormatError(f"MATRIZ document {document.get('id')}にtitleがありません")
    return {
        "id": str(document["id"]),
        "title": title,
        "url": source_url,
        "parent": None if document.get("parent") is None else str(document.get("parent")),
        "modified": document.get("modified"),
        "fetched_at": fetched_at,
        # REST原文はcache/raw/に残す。readは整形済み本文だけを返すため、
        # source pageのscript/styleをTRIZ文書として読ませない。
        "content": html_to_text(content, source_url),
    }


FetchFunction = Callable[[str], tuple[bytes, dict[str, str]]]


def cache_path(cache: Path, group: str, identifier: str) -> Path:
    return cache / group / f"{identifier}.json"


def remove_legacy_artifacts(cache: Path, catalog: dict[str, list[str]]) -> None:
    """明示的なrefresh時だけ、version 1が残した既知の派生fileを除く。"""
    for identifier in catalog["ariz"]:
        legacy_path = cache / "raw" / f"ariz85v-{identifier}.html.json"
        if legacy_path.exists():
            legacy_path.unlink()


def expected_artifact_paths(catalog: dict[str, list[str]], source_index: dict[str, Any]) -> set[str]:
    """complete cacheに必ず存在する、manifest以外の全fileを返す。"""
    paths = {"raw/matriz-docs-1.json", "raw/matriz-docs-2.json"}
    paths.update(f"documents/{document['id']}.json" for document in source_index["documents"])
    paths.update(f"principles/{identifier}.json" for identifier in catalog["principles"])
    paths.update(f"standards/{identifier}.json" for identifier in catalog["standards"])
    paths.update(f"raw/ariz85v-{identifier}.html" for identifier in catalog["ariz"])
    paths.update(f"ariz/{identifier}.json" for identifier in catalog["ariz"])
    return paths


def artifact_hashes(cache: Path, expected_paths: set[str]) -> dict[str, str]:
    """保存済みfileのexact bytesをhashする。normalized textではない。"""
    actual_paths = {
        path.relative_to(cache).as_posix()
        for path in cache.rglob("*")
        if path.is_file() and path.name != MANIFEST_NAME
    }
    if actual_paths != expected_paths:
        missing = sorted(expected_paths - actual_paths)
        unexpected = sorted(actual_paths - expected_paths)
        raise CacheStateError(
            f"cache file集合が一致しません。missing={missing}, unexpected={unexpected}"
        )
    return {
        relative_path: sha256((cache / relative_path).read_bytes())
        for relative_path in sorted(expected_paths)
    }


def verify_complete_cache(
    cache: Path, manifest: dict[str, Any], catalog: dict[str, list[str]], source_index: dict[str, Any]
) -> None:
    """manifest、bundle fingerprint、保存file集合、各exact-byte hashを相互検証する。"""
    if manifest.get("schema_version") != 2 or manifest.get("complete") is not True:
        raise CacheStateError("cacheは完全なschema version 2のmanifestではありません")
    expected_counts = {
        "document_count": 103,
        "principle_count": 40,
        "standard_count": 76,
        "ariz_part_count": 9,
    }
    if any(manifest.get(key) != value for key, value in expected_counts.items()):
        raise CacheStateError("manifestの件数がcatalog/source indexと一致しません")
    if manifest.get("catalog_sha256") != sha256(CATALOG_PATH.read_bytes()):
        raise CacheStateError("triz-catalog.jsonのfingerprintがcacheと一致しません")
    if manifest.get("source_index_sha256") != sha256(SOURCE_INDEX_PATH.read_bytes()):
        raise CacheStateError("source-index.jsonのfingerprintがcacheと一致しません")

    expected_paths = expected_artifact_paths(catalog, source_index)
    actual_hashes = artifact_hashes(cache, expected_paths)
    recorded_hashes = manifest.get("artifact_sha256")
    if not isinstance(recorded_hashes, dict) or recorded_hashes != actual_hashes:
        raise CacheStateError("cache artifactのSHA-256がmanifestと一致しません")

    sources = manifest.get("sources")
    if not isinstance(sources, list) or len(sources) != 11:
        raise CacheStateError("manifestのsource record数が一致しません")
    expected_source_urls = {
        **{f"matriz-docs-{page}": MATRIZ_REST_URL.format(page) for page in (1, 2)},
        **{f"ariz85v-{identifier}": ARIZ_URL.format(identifier) for identifier in catalog["ariz"]},
    }
    if {source.get("id") for source in sources} != set(expected_source_urls):
        raise CacheStateError("manifestのsource ID集合が一致しません")
    for source in sources:
        source_id = source.get("id")
        raw_path = source.get("raw_path")
        if source_id not in expected_source_urls or source.get("url") != expected_source_urls[source_id]:
            raise CacheStateError("manifestのsource IDまたはURLが一致しません")
        if not isinstance(raw_path, str) or raw_path not in expected_paths:
            raise CacheStateError("manifestのraw_pathがcache file集合と一致しません")
        if source.get("sha256") != actual_hashes[raw_path]:
            raise CacheStateError("保存したresponse bytesのSHA-256がmanifestと一致しません")
        if source_id.startswith("matriz-docs-"):
            expected_count = 100 if source_id.endswith("-1") else 3
            if source.get("document_count") != expected_count:
                raise CacheStateError("manifestのMATRIZ page document countが一致しません")


def fetch_cache(cache: Path, refresh: bool, fetcher: FetchFunction = http_get) -> dict[str, Any]:
    catalog = load_catalog()
    source_index = load_source_index()
    manifest_path = cache / MANIFEST_NAME
    if manifest_path.exists() and not refresh:
        manifest = read_json(manifest_path)
        verify_complete_cache(cache, manifest, catalog, source_index)
        return manifest

    cache.mkdir(parents=True, exist_ok=True)
    if refresh:
        remove_legacy_artifacts(cache, catalog)
    manifest: dict[str, Any] = {
        "schema_version": 2,
        "complete": False,
        "started_at": utc_now(),
        "catalog_sha256": sha256(CATALOG_PATH.read_bytes()),
        "source_index_sha256": sha256(SOURCE_INDEX_PATH.read_bytes()),
        "sources": [],
        "missing": [],
    }
    write_json(manifest_path, manifest)

    try:
        fetched_at = utc_now()
        remote_documents: list[dict[str, Any]] = []
        for page in (1, 2):
            url = MATRIZ_REST_URL.format(page)
            body, headers = fetcher(url)
            payload = json.loads(body.decode(charset_from_headers(headers, "utf-8")))
            if not isinstance(payload, list):
                raise SourceFormatError(f"MATRIZ REST page {page} is not a JSON list")
            raw_path = f"raw/matriz-docs-{page}.json"
            write_bytes(cache / raw_path, body)
            remote_documents.extend(payload)
            manifest["sources"].append({
                "id": f"matriz-docs-{page}", "url": url, "fetched_at": fetched_at,
                "raw_path": raw_path, "sha256": sha256(body), "document_count": len(payload),
                "total_pages_header": headers.get("x-wp-totalpages"),
            })

        expected_ids = {str(document["id"]) for document in source_index["documents"]}
        remote_ids = [str(document.get("id")) for document in remote_documents]
        if len(remote_documents) != 103 or len(set(remote_ids)) != 103 or set(remote_ids) != expected_ids:
            raise SourceFormatError("MATRIZ REST結果が103文書source indexと一致しません")

        records = {str(document["id"]): document_record(document, fetched_at) for document in remote_documents}
        for identifier, record in records.items():
            write_json(cache_path(cache, "documents", identifier), record)

        principle_sections = extract_sections(records["6023"]["content"], PRINCIPLE_HEADING)
        standard_sections = extract_sections(records["8094"]["content"], STANDARD_HEADING)
        assert_catalog_sections(catalog, principle_sections, "principles")
        assert_catalog_sections(catalog, standard_sections, "standards")
        for group, sections, source_id in (
            ("principles", principle_sections, "6023"),
            ("standards", standard_sections, "8094"),
        ):
            for identifier, section in sections.items():
                write_json(cache_path(cache, group, identifier), {
                    "id": identifier, "title": section["title"], "url": records[source_id]["url"],
                    "source_document_id": source_id, "fetched_at": fetched_at,
                    "content": section["content"],
                })

        for identifier in catalog["ariz"]:
            url = ARIZ_URL.format(identifier)
            body, headers = fetcher(url)
            encoding = charset_from_headers(headers, "windows-1251")
            html = body.decode(encoding, errors="replace")
            fragment = altshuller_content_fragment(html)
            text = html_to_text(fragment, url)
            if not re.search(rf"(?im)^#+\s+ЧАСТЬ\s+{identifier}\.\s+", text):
                raise SourceFormatError(f"ARIZ part {identifier}のheadingがありません")
            raw_path = f"raw/ariz85v-{identifier}.html"
            write_bytes(cache / raw_path, body)
            write_json(cache_path(cache, "ariz", identifier), {
                "id": identifier, "title": f"ARIZ-85V Part {identifier}", "url": url,
                "fetched_at": fetched_at, "content": text,
            })
            manifest["sources"].append({
                "id": f"ariz85v-{identifier}", "url": url, "fetched_at": fetched_at,
                "raw_path": raw_path, "sha256": sha256(body), "content_container": "container_2",
            })

        manifest["artifact_sha256"] = artifact_hashes(
            cache, expected_artifact_paths(catalog, source_index)
        )
        manifest.update({
            "complete": True,
            "completed_at": utc_now(),
            "document_count": len(records),
            "principle_count": len(principle_sections),
            "standard_count": len(standard_sections),
            "ariz_part_count": len(catalog["ariz"]),
            "missing": [],
        })
        write_json(manifest_path, manifest)
        return manifest
    except Exception as error:
        manifest["complete"] = False
        manifest["failed_at"] = utc_now()
        manifest["error"] = f"{type(error).__name__}: {error}"
        if isinstance(error, SourceFetchError):
            manifest["error_category"] = error.category
            manifest["error_url"] = error.url
            manifest["error_hint"] = error.hint
        manifest["missing"] = ["documents", "principles", "standards", "ariz"]
        write_json(manifest_path, manifest)
        raise


def load_complete_manifest(cache: Path) -> dict[str, Any]:
    manifest_path = cache / MANIFEST_NAME
    if not manifest_path.exists():
        raise CacheStateError(f"cacheがありません。実行してください: {fetch_command_hint(cache)}")
    manifest = read_json(manifest_path)
    try:
        verify_complete_cache(cache, manifest, load_catalog(), load_source_index())
    except CacheStateError as error:
        raise CacheStateError(
            f"cacheは未完了または改変されています。再取得してください: "
            f"{fetch_command_hint(cache)} --refresh ({error})"
        ) from error
    return manifest


def fetch_command_hint(cache: Path) -> str:
    return f"python3 {Path(__file__).name} fetch --cache {cache}"


def add_cache_argument(subparser: argparse.ArgumentParser) -> None:
    subparser.add_argument(
        "--cache", type=Path, default=DEFAULT_CACHE,
        help=f"cacheの保存先。既定は環境変数{CACHE_ENVIRONMENT_NAME}、無ければ {DEFAULT_CACHE}",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    fetch_parser = commands.add_parser("fetch", help="指定したcacheにsourceを取得する")
    add_cache_argument(fetch_parser)
    fetch_parser.add_argument("--refresh", action="store_true")
    read_parser = commands.add_parser("read", help="cacheしたsource全体または1件を読む")
    add_cache_argument(read_parser)
    read_parser.add_argument("--group", required=True, choices=("principles", "standards", "ariz", "documents"))
    read_parser.add_argument("--id")
    search_parser = commands.add_parser("search", help="cacheしたsourceのtitleと本文を検索する")
    add_cache_argument(search_parser)
    search_parser.add_argument("query")
    return parser


def natural_id(identifier: str) -> tuple[int, ...]:
    return tuple(int(part) for part in identifier.split("."))


def read_group(cache: Path, group: str, identifier: str | None) -> Any:
    load_complete_manifest(cache)
    if group not in {"principles", "standards", "ariz", "documents"}:
        raise CacheStateError(f"不明なgroupです: {group}")
    directory = cache / group
    if identifier is not None:
        path = cache_path(cache, group, identifier)
        if not path.exists():
            raise CacheStateError(f"cacheに{group}のid={identifier}がありません")
        return read_json(path)
    paths = sorted(directory.glob("*.json"), key=lambda path: natural_id(path.stem))
    return [read_json(path) for path in paths]


def iter_search_records(cache: Path) -> Iterable[tuple[str, str, dict[str, Any]]]:
    for group in ("documents", "ariz"):
        for path in (cache / group).glob("*.json"):
            record = read_json(path)
            yield group, path.stem, record


def search_cache(cache: Path, query: str) -> list[dict[str, str]]:
    load_complete_manifest(cache)
    needle = query.casefold()
    matches: list[dict[str, str]] = []
    for group, identifier, record in iter_search_records(cache):
        title = str(record.get("title", ""))
        body = str(record.get("content", ""))
        haystack = f"{title}\n{body}"
        position = haystack.casefold().find(needle)
        if position < 0:
            continue
        start = max(0, position - 160)
        end = min(len(haystack), position + len(query) + 240)
        snippet = re.sub(r"\s+", " ", haystack[start:end]).strip()
        matches.append({
            "group": group, "id": identifier, "title": title,
            "url": str(record.get("url", "")), "snippet": snippet,
        })
    return sorted(matches, key=lambda item: (item["group"], natural_id(item["id"])))


def main(argv: list[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    try:
        if arguments.command == "fetch":
            result = fetch_cache(arguments.cache, arguments.refresh)
        elif arguments.command == "read":
            result = read_group(arguments.cache, arguments.group, arguments.id)
        else:
            result = search_cache(arguments.cache, arguments.query)
        json.dump(result, sys.stdout, ensure_ascii=False, indent=2, sort_keys=True)
        sys.stdout.write("\n")
        return 0
    except SourceFetchError as error:  # 取得失敗はincomplete manifestを残した上で分類を表示する。
        print(f"取得エラー {error}", file=sys.stderr)
        return 3
    except (CacheStateError, SourceFormatError, OSError, ValueError, json.JSONDecodeError) as error:
        print(f"エラー: {error}", file=sys.stderr)
        return 2
    except Exception as error:  # network失敗でもincomplete manifestを残す。
        print(f"エラー: {type(error).__name__}: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
