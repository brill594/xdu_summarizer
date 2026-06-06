"""
LLM 摘要引擎
调用大模型从逐字稿中提取结构化课程重点
"""
import json
import logging
import os
import re
from typing import Optional

logger = logging.getLogger(__name__)


SUMMARY_SYSTEM_PROMPT = """你是一个优秀的课程笔记整理助手。你的任务是将网课逐字稿整理成结构清晰、重点突出的课程笔记。

要求：
1. 提取课程的核心知识点、关键概念、公式/定义
2. 按逻辑顺序组织（如老师讲课的章节顺序）
3. 每个知识点用简洁的要点列出
4. 如有代码/公式，用代码块或 LaTeX 格式呈现
5. 包含教师重点强调的内容、考试可能考的提示
6. 语言风格：中文，专业但易懂
7. 输出格式：Markdown

请特别注意：
- 只提取课程中实际讲授的内容，不要编造
- 如果逐字稿中有不完整的地方，说明可能的缺失
- 对于每个知识点，附上对应的视频时间戳（参考逐字稿中的时间标记）"""


SUMMARY_USER_TEMPLATE = """请根据以下课程逐字稿，整理出课程重点笔记。

课程标题：{title}
逐字稿总时长：{duration_sec:.1f}秒

逐字稿内容：
```
{transcript}
```

请输出结构化的 Markdown 笔记。"""


class SummaryEngine:
    """LLM 摘要引擎"""

    def __init__(
        self,
        provider: str = "openai",
        model: str = "gpt-4o-mini",
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
    ):
        self.provider = provider
        self.model = model

        if provider == "ollama":
            from openai import OpenAI
            self._client = OpenAI(
                base_url=base_url or "http://localhost:11434/v1",
                api_key="ollama",
            )
        else:
            from openai import OpenAI
            self._client = OpenAI(
                api_key=api_key or os.getenv("OPENAI_API_KEY") or os.getenv("LLM_API_KEY"),
                base_url=base_url or os.getenv("LLM_BASE_URL"),
            )

    def summarize(
        self,
        transcript_text: str,
        title: str = "课程视频",
        duration_sec: float = 0,
        keyframe_paths: Optional[list] = None,
    ) -> str:
        """
        生成课程摘要

        Args:
            transcript_text: 逐字稿完整文本
            title: 课程标题
            duration_sec: 视频时长（秒）
            keyframe_paths: 关键帧路径列表，用于辅助（多模态场景）

        Returns:
            Markdown 格式的课程笔记
        """
        # 截断过长的逐字稿（token 限制）
        max_chars = 60000
        if len(transcript_text) > max_chars:
            logger.warning(f"逐字稿过长 ({len(transcript_text)}字符)，截断至{max_chars}")
            transcript_text = transcript_text[:max_chars] + "\n\n... [内容截断]"

        user_msg = SUMMARY_USER_TEMPLATE.format(
            title=title,
            duration_sec=duration_sec,
            transcript=transcript_text,
        )

        logger.info(f"调用 LLM 生成摘要: model={self.model}")
        response = self._client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": SUMMARY_SYSTEM_PROMPT},
                {"role": "user", "content": user_msg},
            ],
            temperature=0.3,
            max_tokens=4096,
        )

        summary = response.choices[0].message.content
        logger.info(f"摘要生成完成 ({len(summary)}字符)")
        return summary

    def summarize_with_timestamps(
        self,
        segments: list,
        title: str = "课程视频",
    ) -> str:
        """
        对带时间戳的逐字稿分段生成摘要

        Args:
            segments: ASR segments [{"start": 0, "end": 10, "text": "..."}, ...]
            title: 课程标题

        Returns:
            Markdown 笔记
        """
        # 拼接成带时间标记的文本
        lines = []
        for seg in segments:
            start_min = int(seg["start"]) // 60
            start_sec = int(seg["start"]) % 60
            lines.append(f"[{start_min:02d}:{start_sec:02d}] {seg['text']}")

        transcript = "\n".join(lines)
        duration = segments[-1]["end"] if segments else 0

        return self.summarize(transcript, title, duration)

    def extract_keywords(self, summary_md: str) -> list:
        """从摘要中提取关键词（提供给后续搜索用）"""
        # 简单的基于 markdown 标题的关键词提取
        keywords = set()
        for line in summary_md.split("\n"):
            # 提取 ### 标题
            if line.startswith("###"):
                kw = line.strip("#").strip()
                keywords.add(kw)
            # 提取 **粗体** 内容
            for match in re.finditer(r"\*\*(.+?)\*\*", line):
                keywords.add(match.group(1))
        return list(keywords)
