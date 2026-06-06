"""
Markdown 图文笔记生成器
将 ASR 摘要 + 关键帧截图组合为图文并茂的课程笔记
"""
import logging
import os
import re
from pathlib import Path
from typing import List, Optional

logger = logging.getLogger(__name__)


class MarkdownGenerator:
    """Markdown 笔记生成器"""

    def __init__(
        self,
        output_dir: str = "./lecture_notes",
        embed_images: bool = True,
        max_image_width: int = 800,
    ):
        self.output_dir = Path(output_dir)
        self.embed_images = embed_images
        self.max_image_width = max_image_width
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def generate_lecture_note(
        self,
        course_name: str,
        chapter_name: str,
        summary_md: str,
        keyframes: Optional[List[dict]] = None,
        asr_data: Optional[dict] = None,
    ) -> str:
        """
        生成单节课的图文笔记

        Args:
            course_name: 课程名称（如"计算机网络"）
            chapter_name: 章节名称（如"第3周_传输层"）
            summary_md: LLM 生成的摘要内容 (Markdown)
            keyframes: 关键帧列表 [{"time": 10.0, "path": "slide.jpg"}, ...]
            asr_data: ASR 完整数据（用于添加时间标记）

        Returns:
            生成的 .md 文件路径
        """
        safe_chapter = self._safe_name(chapter_name)
        md_path = self.output_dir / course_name / f"{safe_chapter}.md"
        md_path.parent.mkdir(parents=True, exist_ok=True)

        # 计算图片相对路径
        images_rel_dir = f"images/{safe_chapter}"
        images_abs_dir = md_path.parent / images_rel_dir

        content = self._build_markdown(
            course_name=course_name,
            chapter_name=chapter_name,
            summary_md=summary_md,
            keyframes=keyframes,
            images_abs_dir=images_abs_dir,
            images_rel_dir=images_rel_dir,
            asr_data=asr_data,
        )

        md_path.write_text(content, encoding="utf-8")
        logger.info(f"笔记已生成: {md_path}")
        return str(md_path)

    def _build_markdown(
        self,
        course_name: str,
        chapter_name: str,
        summary_md: str,
        keyframes: Optional[List[dict]],
        images_abs_dir: Path,
        images_rel_dir: str,
        asr_data: Optional[dict],
    ) -> str:
        """构建完整的 Markdown 内容"""
        lines = []
        lines.append(f"# {course_name} — {chapter_name}")
        lines.append("")

        # --- 元信息 ---
        lines.append("## 📋 课程信息")
        lines.append("")
        if asr_data:
            duration = asr_data.get("segments", [{}])[-1].get("end", 0) if asr_data.get("segments") else 0
            lang = asr_data.get("language", "")
            lines.append(f"- **视频时长**: {duration//60:.0f}分{duration%60:.0f}秒")
            if lang:
                lines.append(f"- **识别语言**: {lang}")
        lines.append(f"- **笔记生成时间**: {self._now()}")
        lines.append("")

        # --- 时间轴导航 ---
        if keyframes:
            lines.append("## ⏱️ 目录（时间轴）")
            lines.append("")
            for kf in keyframes:
                t = kf["time"]
                minutes = int(t // 60)
                seconds = int(t % 60)
                lines.append(f"- [{minutes:02d}:{seconds:02d}]({images_rel_dir}/{Path(kf['path']).name})")
            lines.append("")

        # --- 重点摘要（来自 LLM） ---
        lines.append("## 📝 课程重点笔记")
        lines.append("")
        lines.append(summary_md)
        lines.append("")

        # --- 关键帧画廊 ---
        if keyframes:
            lines.append("---")
            lines.append("## 🖼️ 课件截图")
            lines.append("")
            for kf in keyframes:
                t = kf["time"]
                minutes = int(t // 60)
                seconds = int(t % 60)
                img_rel = f"{images_rel_dir}/{Path(kf['path']).name}"
                lines.append(f"### ⏱ {minutes:02d}:{seconds:02d}")
                lines.append("")
                # 嵌入图片
                if self.embed_images:
                    lines.append(
                        f'<img src="{img_rel}" '
                        f'alt="幻灯片 @ {minutes:02d}:{seconds:02d}" '
                        f'width="{self.max_image_width}">'
                    )
                else:
                    lines.append(f"![]({img_rel})")
                lines.append("")
                lines.append(f"*幻灯片播放到 {minutes:02d}:{seconds:02d}*")
                lines.append("")

        # --- 完整逐字稿（折叠） ---
        if asr_data and asr_data.get("segments"):
            lines.append("---")
            lines.append("## 📜 完整逐字稿")
            lines.append("")
            lines.append("<details>")
            lines.append("<summary>点击展开完整逐字稿</summary>")
            lines.append("")
            for seg in asr_data["segments"]:
                t = seg["start"]
                minutes = int(t // 60)
                seconds = int(t % 60)
                lines.append(f"**[{minutes:02d}:{seconds:02d}]** {seg['text']}")
                lines.append("")
            lines.append("</details>")

        return "\n".join(lines)

    def generate_course_index(self, course_name: str, chapter_notes: List[str]) -> str:
        """
        生成课程总索引（所有章节笔记的目录）

        Args:
            course_name: 课程名称
            chapter_notes: 各章节笔记路径列表

        Returns:
            索引文件路径
        """
        idx_path = self.output_dir / course_name / "README.md"
        lines = [f"# 📚 {course_name} — 课程笔记索引", ""]

        for note_path in chapter_notes:
            note = Path(note_path)
            chapter_name = note.stem
            rel = note.relative_to(self.output_dir / course_name)
            lines.append(f"- [{chapter_name}]({rel})")

        lines.append("")
        lines.append(f"*笔记总数: {len(chapter_notes)}*")
        lines.append(f"*生成时间: {self._now()}*")

        idx_path.write_text("\n".join(lines), encoding="utf-8")
        logger.info(f"课程索引已生成: {idx_path}")
        return str(idx_path)

    @staticmethod
    def _safe_name(name: str) -> str:
        """生成安全的文件名"""
        name = re.sub(r'[\\/:*?"<>|]', "_", name)
        return name.strip()

    @staticmethod
    def _now() -> str:
        from datetime import datetime
        return datetime.now().strftime("%Y-%m-%d %H:%M")
