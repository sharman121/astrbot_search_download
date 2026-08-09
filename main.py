"""通过soutubot 实现反向搜图和通过 nhentai 下载漫画的工具"""

from __future__ import annotations

import asyncio
import os
import shutil
import tempfile
from pathlib import Path

import astrbot.api.message_components as Comp
from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, register

from .download_comic_impl import DEFAULT_PDF_PASSWORD, DownloadError, download_pdf
from .plugin_utils import resolve_first_image, schedule_cleanup
from .soutubot_search_impl import SearchError, format_summary, search_image


def _positive_float(value: str | None, default: float) -> float:
    try:
        parsed = float(value or default)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


def _non_negative_int(value: str | None, default: int) -> int:
    try:
        parsed = int(value or default)
    except (TypeError, ValueError):
        return default
    return parsed if parsed >= 0 else default


@register(
    "astrbot_soutu_download",
    "local-user",
    "提供 soutubot 反向搜图和 nhentai 漫画 PDF 下载工具。",
    "1.0.0",
)
class AstrbotSoutuDownloadPlugin(Star):
    def __init__(self, context: Context):
        super().__init__(context)
        self.timeout = _positive_float(os.getenv("ASTRBOT_SOUTU_TIMEOUT"), 60.0)
        self.result_limit = _non_negative_int(
            os.getenv("ASTRBOT_SOUTU_RESULT_LIMIT"),
            10,
        )
        self.download_root = Path(
            os.getenv(
                "ASTRBOT_NHENTAI_DOWNLOAD_DIR",
                "/AstrBot/data/plugin_data/astrbot_soutu_download/downloads",
            )
        )
        self.pdf_password = (
            os.getenv("NHENTAI_PDF_PASSWORD", DEFAULT_PDF_PASSWORD)
            or DEFAULT_PDF_PASSWORD
        )

    @filter.llm_tool(name="soutubot_search_tool")
    async def soutubot_search_tool(
        self,
        event: AstrMessageEvent,
        strict: bool = False,
        result_limit: int = -1,
    ):
        """使用 soutubot 对当前消息或引用消息中的图片进行反向搜图。

        只有当用户明确要求搜索、识图或反向搜索当前图片时才调用此工具。
        图片可以直接附在当前消息中，也可以位于用户引用的消息中。

        Args:
            strict(boolean): 是否使用更严格的相似度搜索模式。
            result_limit(number): 最多展示多少条结果，默认使用插件配置值。
        """
        image_path, temporary = await resolve_first_image(event)
        if image_path is None:
            yield event.plain_result("没有在当前消息或引用消息中找到可用图片。请先发送或引用一张图片。")
            return

        if not isinstance(result_limit, (int, float)) or result_limit < 0:
            limit = self.result_limit
        else:
            limit = max(0, int(result_limit))

        try:
            payload = await asyncio.to_thread(
                search_image,
                image_path,
                bool(strict),
                self.timeout,
            )
            yield event.plain_result(format_summary(payload, limit))
        except (OSError, SearchError, ValueError) as exc:
            logger.warning("soutubot 搜图失败: %s", exc)
            yield event.plain_result(f"搜图失败：{exc}")
        finally:
            if temporary:
                schedule_cleanup((image_path,))

    @filter.llm_tool(name="nhentai_download_tool")
    async def nhentai_download_tool(
        self,
        event: AstrMessageEvent,
        gallery_id: str = "",
    ):
        """从 nhentai 下载指定编号的漫画并生成 PDF 文件发送给用户。

        用户提出下载某个 nhentai 漫画编号（也可以提供完整的 nhentai 主页面 URL）
        时调用此工具。不要把普通聊天中的数字误当成漫画编号。

        Args:
            gallery_id(string): nhentai 漫画编号，例如 123456，或 https://nhentai.net/g/123456/。
        """
        if not str(gallery_id).strip():
            yield event.plain_result("请提供要下载的 nhentai 漫画编号或主页面 URL。")
            return

        try:
            self.download_root.mkdir(parents=True, exist_ok=True)
            self.download_root.chmod(0o755)
            output_dir = Path(
                tempfile.mkdtemp(
                    prefix="astrbot-nhentai-",
                    dir=str(self.download_root),
                )
            )
            # NapCat runs in another container and must be able to traverse this
            # directory through the shared /AstrBot/data bind mount.
            output_dir.chmod(0o755)
        except OSError as exc:
            logger.warning("无法创建 nhentai 共享下载目录: %s", exc)
            yield event.plain_result(f"无法创建 nhentai 下载目录：{exc}")
            return

        try:
            pdf_path = await asyncio.to_thread(
                download_pdf,
                str(gallery_id),
                output_dir,
                self.timeout,
                pdf_password=self.pdf_password,
            )
            pdf_path.chmod(0o644)
            chain = [
                Comp.Plain(
                    f" {gallery_id} 下载完成。"
                    f"密码：{self.pdf_password}\n"
                ),
                Comp.File(name=pdf_path.name, file=str(pdf_path)),
            ]
            yield event.chain_result(chain)
            schedule_cleanup((output_dir,), delay=3600)
        except (DownloadError, OSError, ValueError) as exc:
            logger.warning("nhentai 下载失败: %s", exc)
            shutil.rmtree(output_dir, ignore_errors=True)
            yield event.plain_result(f"nhentai 下载失败：{exc}")

    async def terminate(self):
        """插件卸载时无需额外清理。"""
