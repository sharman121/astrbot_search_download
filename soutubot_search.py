"""通过请求 soutubot.moe 搜图并返回搜图结果"""

from __future__ import annotations

import argparse
import base64
import json
import mimetypes
import re
import sys
import time
import uuid
from decimal import Decimal
from http.cookiejar import CookieJar
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import HTTPCookieProcessor, Request, build_opener


BASE_URL = "https://soutubot.moe"
SEARCH_URL = f"{BASE_URL}/api/search"
SOURCE_HOSTS = {
    "nhentai": "nhentai.net",
    "ehentai": "e-hentai.org",
    "panda": "panda.chaika.moe",
}
SUPPORTED_TYPES = {"image/jpeg", "image/png", "image/webp", "image/gif"}
USER_AGENT = "Mozilla/5.0 soutubot-search/1.0"
M_PATTERN = re.compile(rb"\bm\s*:\s*(\d+)")


class SearchError(RuntimeError):
    """远程搜图请求无法完成时抛出的异常。"""


def _multipart_body(image_path: Path, factor: float) -> tuple[bytes, str]:
    """定义multipart格式的请求体"""
    boundary = f"----soutubot-{uuid.uuid4().hex}"
    mime_type = mimetypes.guess_type(image_path.name)[0] or "application/octet-stream"
    image_data = image_path.read_bytes()

    chunks = [
        f"--{boundary}\r\n".encode(),
        b'Content-Disposition: form-data; name="factor"\r\n\r\n',
        f"{factor}\r\n".encode(),
        f"--{boundary}\r\n".encode(),
        (
            f'Content-Disposition: form-data; name="file"; '
            f'filename="{image_path.name}"\r\n'
        ).encode("utf-8"),
        f"Content-Type: {mime_type}\r\n\r\n".encode(),
        image_data,
        b"\r\n",
        f"--{boundary}--\r\n".encode(),
    ]
    return b"".join(chunks), f"multipart/form-data; boundary={boundary}"


def _api_key(unix_time: int, user_agent: str, dynamic_value: int) -> str:
    # 使用网站规则生成api_key
    number = float(unix_time) ** 2
    number += float(len(user_agent)) ** 2
    number += float(dynamic_value)
    number_text = format(Decimal(repr(number)), "f")
    encoded = base64.b64encode(number_text.encode("ascii")).decode("ascii")
    return encoded[::-1].replace("=", "")


def search_image(
    image_path: Path,
    strict: bool = False,
    timeout: float = 60,
) -> dict[str, Any]:
    factor = 1.4 if strict else 1.2
    body, content_type = _multipart_body(image_path, factor)
    opener = build_opener(HTTPCookieProcessor(CookieJar()))

    home_request = Request(
        f"{BASE_URL}/",
        headers={"Accept": "text/html", "User-Agent": USER_AGENT},
    )
    try:
        with opener.open(home_request, timeout=timeout) as response:
            home_html = response.read()
    except HTTPError as exc:
        raise SearchError(f"读取网站首页失败，HTTP {exc.code}: {exc.reason}") from exc
    except URLError as exc:
        raise SearchError(f"无法连接 {BASE_URL}: {exc.reason}") from exc
    except TimeoutError as exc:
        raise SearchError(f"读取网站首页超过 {timeout:g} 秒未完成") from exc

    match = M_PATTERN.search(home_html)
    if not match:
        raise SearchError("无法从网站首页读取接口校验参数，网页结构可能已经变更")
    api_key = _api_key(int(time.time()), USER_AGENT, int(match.group(1)))

    request = Request(
        SEARCH_URL,
        data=body,
        method="POST",
        headers={
            "Accept": "application/json",
            "Content-Type": content_type,
            "Origin": BASE_URL,
            "Referer": f"{BASE_URL}/",
            "User-Agent": USER_AGENT,
            "X-API-KEY": api_key,
            "X-Requested-With": "XMLHttpRequest",
        },
    )

    try:
        with opener.open(request, timeout=timeout) as response:
            payload = response.read().decode("utf-8")
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace").strip()
        if exc.code == 401:
            detail = "请求被网站拒绝；请检查系统时间、网络环境，或稍后重试"
        raise SearchError(f"网站返回 HTTP {exc.code}: {detail or exc.reason}") from exc
    except URLError as exc:
        raise SearchError(f"无法连接 {SEARCH_URL}: {exc.reason}") from exc
    except TimeoutError as exc:
        raise SearchError(f"请求超过 {timeout:g} 秒未完成") from exc

    try:
        result = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise SearchError("网站返回了无法解析的内容，接口可能已经变更") from exc
    if not isinstance(result, dict):
        raise SearchError("网站返回的 JSON 格式不符合预期")
    return result


def _absolute_url(host: str | None, path: Any) -> str:
    if not host or not isinstance(path, str) or not path:
        return ""
    if path.startswith(("http://", "https://")):
        return path
    return f"https://{host}/{path.lstrip('/')}"


def normalized_results(payload: dict[str, Any]) -> list[dict[str, Any]]:
    normalized = []
    for item in payload.get("data", []):
        if not isinstance(item, dict):
            continue
        source = str(item.get("source", ""))
        host = SOURCE_HOSTS.get(source)
        normalized.append(
            {
                **item,
                "subjectUrl": _absolute_url(host, item.get("subjectPath")),
                "pageUrl": _absolute_url(host, item.get("pagePath")),
            }
        )
    return normalized


def format_summary(payload: dict[str, Any], limit: int = 10) -> str:
    """整理搜图结果为简洁整齐的格式"""
    results = normalized_results(payload)
    result_id = payload.get("id", "")
    lines = [
        "搜图完成。",
        f"共找到 {len(results)} 条结果。",
    ]
    if result_id:
        lines.append(f"完整结果页：{BASE_URL}/results/{result_id}")
    if not results:
        return "\n".join(lines)

    for index, item in enumerate(results[: max(0, limit)], start=1):
        similarity = item.get("similarity", "?")
        title = item.get("title") or "无标题"
        source = item.get("source", "?")
        language = item.get("language", "?")
        url = item.get("pageUrl") or item.get("subjectUrl") or "无链接"
        lines.append(
            f"{index}. [{similarity}%] {title}\n"
            f"   来源：{source}  语言：{language}\n"
            f"   链接：{url}"
        )
    return "\n".join(lines)


def print_summary(payload: dict[str, Any], limit: int) -> None:
    results = normalized_results(payload)
    result_id = payload.get("id", "")
    print(f"结果页: {BASE_URL}/results/{result_id}" if result_id else "结果页: 未提供")
    print(f"耗时: {payload.get('executionTime', '?')} 秒")
    print(f"搜索参数: {payload.get('searchOption', '?')}")
    print(f"共找到 {len(results)} 条结果")

    if not results:
        return
    for index, item in enumerate(results[:limit], start=1):
        similarity = item.get("similarity", "?")
        title = item.get("title") or "无标题"
        url = item.get("pageUrl") or item.get("subjectUrl") or "无链接"
        print(f"\n{index}. [{similarity}%] {title}")
        print(f"   来源: {item.get('source', '?')}  语言: {item.get('language', '?')}")
        print(f"   链接: {url}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="上传本地图片到 soutubot.moe 并返回搜图结果。",
    )
    parser.add_argument("image", type=Path, help="本地图片路径")
    parser.add_argument("--strict", action="store_true", help="使用严格搜索模式")
    parser.add_argument("--limit", type=int, default=10, help="终端最多显示多少条结果（默认 10）")
    parser.add_argument("--timeout", type=float, default=60, help="请求超时秒数（默认 60）")
    parser.add_argument("--json", action="store_true", help="在终端输出完整 JSON")
    parser.add_argument("--output", type=Path, help="将完整 JSON 保存到指定文件")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    image_path = args.image.expanduser().resolve()

    if not image_path.is_file():
        print(f"错误: 图片不存在或不是文件: {image_path}", file=sys.stderr)
        return 2
    mime_type = mimetypes.guess_type(image_path.name)[0]
    if mime_type not in SUPPORTED_TYPES:
        print("错误: 仅支持 JPG、PNG、WebP 或 GIF 图片", file=sys.stderr)
        return 2
    if args.limit < 0:
        print("错误: --limit 不能小于 0", file=sys.stderr)
        return 2
    if args.timeout <= 0:
        print("错误: --timeout 必须大于 0", file=sys.stderr)
        return 2

    try:
        payload = search_image(image_path, strict=args.strict, timeout=args.timeout)
    except (OSError, SearchError) as exc:
        print(f"搜索失败: {exc}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print_summary(payload, args.limit)

    if args.output:
        output_path = args.output.expanduser().resolve()
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        print(f"\n完整 JSON 已保存到: {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
