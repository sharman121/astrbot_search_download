"""为反向搜图工具提供 AstrBot 消息与媒体处理辅助函数"""

from __future__ import annotations

import asyncio
import mimetypes
import os
import shutil
import tempfile
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import Any

import astrbot.api.message_components as Comp


def iter_image_components(chain: Iterable[Any] | None) -> Iterator[Comp.Image]:
    """遍历消息链中的图片，包括 Reply.chain 内的图片"""
    for component in chain or ():
        if isinstance(component, Comp.Image):
            yield component
            continue

        nested = getattr(component, "chain", None)
        if nested:
            yield from iter_image_components(nested)


def _image_suffix(path: Path) -> str | None:
    mime_type = mimetypes.guess_type(path.name)[0]
    if mime_type in {
        "image/jpeg",
        "image/png",
        "image/webp",
        "image/gif",
    }:
        return path.suffix.lower()

    try:
        from PIL import Image

        with Image.open(path) as image:
            return {
                "JPEG": ".jpg",
                "PNG": ".png",
                "WEBP": ".webp",
                "GIF": ".gif",
            }.get(str(image.format).upper())
    except (ImportError, OSError, ValueError):
        return None


async def resolve_first_image(event: Any) -> tuple[Path | None, bool]:
    """将当前消息或引用消息中的第一张图片解析为本地路径。

    返回 ``(path, temporary)``。当为了适配 soutubot 的 multipart 上传而
    创建了带受支持图片后缀的副本时，``temporary`` 为真。
    """
    get_messages = getattr(event, "get_messages", None)
    chain = get_messages() if callable(get_messages) else []
    for image in iter_image_components(chain):
        #消费图片生成器，直到找到第一张图片
        try:
            raw_path = await image.convert_to_file_path()
        except (AttributeError, OSError, ValueError, RuntimeError):
            raw_path = getattr(image, "path", None) or getattr(image, "file", None)

        if not raw_path:
            continue
        candidate = Path(str(raw_path)).expanduser()
        if not candidate.is_file():
            continue

        suffix = _image_suffix(candidate)
        if not suffix:
            continue
        if candidate.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp", ".gif"}:
            return candidate.resolve(), False

        fd, copied_name = tempfile.mkstemp(prefix="astrbot-soutu-", suffix=suffix)
        os.close(fd)
        copied = Path(copied_name)
        shutil.copyfile(candidate, copied)
        return copied.resolve(), True
        #创建并返回临时副本

    return None, False


def schedule_cleanup(paths: Iterable[Path], delay: float = 3600) -> None:
    """在消息留出足够发送时间后，安排尽力而为的延迟清理。"""
    paths = tuple(Path(path) for path in paths)

    async def _cleanup() -> None:
        await asyncio.sleep(max(1.0, delay))
        for path in paths:
            try:
                if path.is_dir():
                    shutil.rmtree(path, ignore_errors=True)
                else:
                    path.unlink(missing_ok=True)
            except OSError:
                pass

    asyncio.create_task(_cleanup())
