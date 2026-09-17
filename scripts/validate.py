#!/usr/bin/env python3
"""スキルのfrontmatterとMarkdown内の相対リンクを検証する。"""
from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import unquote, urlsplit

import yaml
from markdown_it import MarkdownIt


ROOT = Path(__file__).resolve().parents[1]
NAME = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


class UniqueLoader(yaml.SafeLoader):
    pass


def mapping(loader, node, deep=False):
    result = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if not isinstance(key, str):
            raise yaml.YAMLError("キーは文字列にします")
        if key in result:
            raise yaml.YAMLError(f"重複するキー: {key}")
        result[key] = loader.construct_object(value_node, deep=deep)
    return result


UniqueLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, mapping)


def err(errors, path, line, message):
    errors.append(f"{path.relative_to(ROOT)}:{line}: {message}")


def read_frontmatter(path, errors):
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---\n"):
        err(errors, path, 1, "YAML frontmatterがありません")
        return None

    match = re.search(r"^---\s*$", text[4:], re.M)
    if not match:
        err(errors, path, 1, "YAML frontmatterが閉じられていません")
        return None

    try:
        data = yaml.load(text[4 : 4 + match.start()], Loader=UniqueLoader)
        if not isinstance(data, dict):
            err(errors, path, 1, "frontmatterは空でないマッピングにします")
            return None
        return data

    except yaml.YAMLError as exc:
        err(errors, path, 1, f"YAMLが不正です: {exc}")
        return None


def tokens_with_children(tokens):
    for token in tokens:
        yield token
        if token.children:
            yield from tokens_with_children(token.children)


def check_links(path, errors):
    tokens = MarkdownIt("commonmark").parse(path.read_text(encoding="utf-8"))
    for token in tokens_with_children(tokens):
        if token.type not in {"link_open", "image"}:
            continue

        href = token.attrGet("href") if token.type == "link_open" else token.attrGet("src")
        url = urlsplit(href or "")
        if url.scheme or url.netloc or not url.path:
            continue

        value = unquote(url.path)
        if not (path.parent / value).resolve().exists():
            line = token.map[0] + 1 if token.map else 1
            err(errors, path, line, f"相対リンク先がありません: {href}")


def main():
    errors = []
    names = {}
    skills = sorted(ROOT.glob("skills/*/SKILL.md"))

    if not skills:
        errors.append("skills/にスキルがありません")

    for directory in (ROOT / "skills").iterdir():
        if directory.is_dir() and not (directory / "SKILL.md").is_file():
            err(errors, directory, 1, "SKILL.mdがありません")

    for path in skills:
        data = read_frontmatter(path, errors)
        if not isinstance(data,dict):
            if data is not None:
                err(errors, path, 1, "frontmatterはマッピングにします")
            continue

        name = data.get("name")
        description = data.get("description")
        if not isinstance(name, str) or not name:
            err(errors, path, 1, "nameがありません")
        elif len(name) > 64 or not NAME.fullmatch(name):
            err(errors, path, 1, "nameは1〜64文字の英小文字、数字、ハイフンだけにします")
        else:
            if name in names:
                err(errors, path, 1, f"nameが重複しています: {name}")
            else:
                names[name] = path
            if name != path.parent.name:
                err(errors, path, 1, "nameと親ディレクトリ名が一致しません")

        if (
            not isinstance(description, str)
            or not description.strip()
            or len(description) > 1024
        ):
            err(errors, path, 1, "descriptionは空でない1〜1024文字の文字列で必要です")

    documents = [
        path
        for path in ROOT.rglob("*.md")
        if not any(part.startswith(".") for part in path.relative_to(ROOT).parts)
    ]
    for path in documents:
        check_links(path, errors)

    if errors:
        print("検証に失敗しました。\n" + "\n".join(errors))
        return 1

    print(f"検証に成功しました: {len(skills)}個のスキル、{len(documents)}個のMarkdownファイル")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
