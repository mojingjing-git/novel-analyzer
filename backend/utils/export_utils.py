"""
导出工具
将分析结果导出为各种格式
"""

import json
from pathlib import Path
from typing import Optional, List

from ..models.analysis_result import AnalysisResult


def _format_chapter_list(chapter_nums: List[int]) -> str:
    """
    Format a list of chapter numbers for human-readable display.

    Deduplicates and sorts. Returns "第3章, 第5章, 第7章" format.
    Empty input returns empty string.

    Args:
        chapter_nums: List of chapter numbers (may contain duplicates, may be unsorted).

    Returns:
        Comma-separated string like "第3章, 第5章, 第7章"; empty string for empty input.
    """
    if not chapter_nums:
        return ""
    return ', '.join(f"第{n}章" for n in sorted(set(chapter_nums)))


def export_chapter_to_markdown(result: AnalysisResult, output_dir: Path) -> tuple[Path, Path]:
    """
    将单章分析结果导出为Markdown和JSON文件

    Args:
        result: 分析结果
        output_dir: 输出目录

    Returns:
        (markdown文件路径, json文件路径)
    """
    output_dir.mkdir(parents=True, exist_ok=True)

    # 生成Markdown文件
    md_filename = f"chapter_{result.chapter_number}_report.md"
    md_filepath = output_dir / md_filename

    content = f"""# 第{result.chapter_number}章 分析报告

## 核心事件

"""

    # 核心事件
    if result.core_events:
        for event in result.core_events:
            content += f"- **[{event.id}] {event.event}**\n"
            content += f"  - 角色: {event.characters}\n"
            content += f"  - 作用: {event.function}\n\n"
    else:
        content += "无明显核心事件\n\n"

    # 人物弧光
    content += "## 人物弧光\n\n"
    if result.character_arcs:
        for arc in result.character_arcs:
            content += f"### {arc.name}\n"
            content += f"- 表面行为: {arc.surface_action}\n"
            content += f"- 深层动机: {arc.inner_motivation}\n"
            content += f"- 变化量: {arc.change_delta}\n"
            content += f"- 触发事件: {arc.driver}\n\n"
    else:
        content += "无明显人物弧光\n\n"

    # 伏笔
    content += "## 伏笔\n\n"
    if result.foreshadowing:
        for foreshadow in result.foreshadowing:
            content += f"- **[{foreshadow.confidence}] {foreshadow.clue}**\n"
            content += f"  - 类型: {foreshadow.type}\n"
            content += f"  - 暗示: {foreshadow.implication}\n\n"
    else:
        content += "无明显伏笔\n\n"

    # 逻辑漏洞
    content += "## 逻辑漏洞\n\n"
    if result.plot_holes and result.plot_holes != ["无明显逻辑漏洞"]:
        for hole in result.plot_holes:
            content += f"- {hole}\n"
    else:
        content += "无明显逻辑漏洞\n"
    content += "\n"

    # 承上启下
    cb = result.cross_block
    content += f"""## 承上启下

- **摘要**: {cb.summary}
- **遗留问题**: {', '.join(cb.unresolved_questions) if cb.unresolved_questions else '无'}
- **新线索**: {', '.join(cb.new_leads) if cb.new_leads else '无'}
- **呼应前文**: {cb.contextual_link}

"""

    # 长上下文洞察
    lci = result.long_context_insights
    content += "## 长上下文洞察\n\n"

    if lci.pattern:
        content += f"### 叙事模式\n{lci.pattern}\n\n"

    if result.character_arcs:
        content += "### 角色变化\n"
        for arc in result.character_arcs:
            content += f"- **{arc.name}**: {arc.change_delta}\n"
        content += "\n"

    if lci.pacing:
        content += f"### 节奏分析\n{lci.pacing}\n\n"

    if lci.foreshadowing_network:
        content += f"### 伏笔网络\n{lci.foreshadowing_network}\n\n"

    if result.updated_knowledge.world_building:
        content += "### 世界观元素\n"
        for element in result.updated_knowledge.world_building:
            content += f"- {element}\n"
        content += "\n"

    if lci.thematic_elements:
        content += "### 主题元素\n"
        for t in lci.thematic_elements:
            content += f"- {t}\n"
        content += "\n"

    # 更新时间线
    content += f"""## 时间线更新

{result.updated_knowledge.timeline}

"""

    # 原始JSON（折叠）
    content += """<details>
<summary>查看原始JSON</summary>

```json
"""
    content += json.dumps(result.to_dict(), ensure_ascii=False, indent=2)
    content += """
```

</details>
"""

    with open(md_filepath, 'w', encoding='utf-8') as f:
        f.write(content)

    # data.json 已废弃，result.json 由 MemoryState 统一管理
    result_filepath = output_dir / f"chapter_{result.chapter_number}_result.json"
    return md_filepath, result_filepath


def export_summary_report(all_results: List[AnalysisResult], output_dir: Path) -> Path:
    """
    生成汇总报告

    Args:
        all_results: 所有章节的分析结果列表
        output_dir: 输出目录

    Returns:
        生成的文件路径
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    filepath = output_dir / "analysis_summary.md"

    content = "# 小说分析汇总报告\n\n"
    content += f"**总章节数**: {len(all_results)}\n\n"

    # 章节目录
    content += "## 章节目录\n\n"
    content += "| 章节 | 核心事件数 | 人物弧光数 | 伏笔数 | 逻辑漏洞 |\n"
    content += "|------|-----------|-----------|--------|----------|\n"

    for result in all_results:
        event_count = len(result.core_events)
        arc_count = len(result.character_arcs)
        foreshadow_count = len(result.foreshadowing)
        hole_count = len([h for h in result.plot_holes if str(h) != "无明显逻辑漏洞"])

        content += f"| 第{result.chapter_number}章 | {event_count} | {arc_count} | {foreshadow_count} | {hole_count} |\n"

    content += "\n"

    # 角色统计
    content += "## 角色出现统计\n\n"
    character_appearances = {}
    for result in all_results:
        for event in result.core_events:
            raw_chars = event.characters
            if isinstance(raw_chars, list):
                raw_chars = ", ".join(str(c) for c in raw_chars)
            chars = [c.strip() for c in str(raw_chars).replace("，", ",").split(',') if c.strip()]
            for char in chars:
                if char not in character_appearances:
                    character_appearances[char] = []
                character_appearances[char].append(result.chapter_number)

    for char, chapters in sorted(character_appearances.items()):
        chapter_text = _format_chapter_list(chapters)
        content += f"- **{char}**: 出现在{chapter_text}\n"

    content += "\n"

    # 伏笔汇总
    content += "## 伏笔汇总\n\n"
    all_foreshadows = []
    for result in all_results:
        for foreshadow in result.foreshadowing:
            all_foreshadows.append((result.chapter_number, foreshadow))

    if all_foreshadows:
        for chapter, foreshadow in all_foreshadows:
            content += f"- **[第{chapter}章]** [{foreshadow.confidence}] {foreshadow.clue}\n"
            content += f"  - 暗示: {foreshadow.implication}\n\n"
    else:
        content += "无明显伏笔\n\n"

    # 世界观元素
    content += "## 世界观元素\n\n"
    world_elements = set()
    for result in all_results:
        for element in result.updated_knowledge.world_building:
            world_elements.add(element)

    if world_elements:
        for element in sorted(world_elements):
            content += f"- {element}\n"
    else:
        content += "无明显世界观元素\n"

    content += "\n"

    # 主题元素
    content += "## 主题元素\n\n"
    themes = set()
    for result in all_results:
        for t in result.long_context_insights.thematic_elements:
            themes.add(t)
    if themes:
        for t in sorted(themes):
            content += f"- {t}\n"
    else:
        content += "无\n"
    content += "\n"

    with open(filepath, 'w', encoding='utf-8') as f:
        f.write(content)

    return filepath
