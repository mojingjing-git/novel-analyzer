"""
角色卡生成器
基于聚合数据生成详细的角色卡片
"""

import json
import re
import html
from pathlib import Path
from typing import Dict, List, Any, Optional
from datetime import datetime


def _esc(value: Any) -> str:
    """HTML 转义，防 XSS / 格式破坏（角色名、事件文本等不可信输入）"""
    return html.escape(str(value), quote=True)


def _safe_filename(name: str) -> str:
    """将角色名转换为安全的文件名（去除路径分隔符/非法字符，限长）"""
    cleaned = re.sub(r'[\\/:*?"<>|]', '_', str(name)).strip()
    cleaned = cleaned.replace('\n', '_').replace('\r', '_')
    return cleaned[:50] if cleaned else "unnamed"


class CharacterCardGenerator:
    """角色卡生成器"""
    
    def __init__(self, aggregated_dir: Path):
        """
        初始化角色卡生成器
        
        Args:
            aggregated_dir: 聚合数据目录
        """
        self.aggregated_dir = Path(aggregated_dir)
        self.character_data = None
        self.character_arcs = None
        self.core_events = None
        self.long_context_insights = None
        
    def _find_aggregated(self, name: str) -> Path | None:
        """聚合文件可能直接位于 aggregated_dir，也可能在其 ``aggregated`` 子目录中
        （路由只把 character_tracking 聚合到前者，其余文件常驻后者）。两处都查。"""
        candidates = [
            self.aggregated_dir / name,
            self.aggregated_dir / "aggregated" / name,
        ]
        for p in candidates:
            if p.exists():
                return p
        return None

    def load_aggregated_data(self):
        """加载所有聚合数据"""
        try:
            # 加载角色追踪数据
            tracking_path = self._find_aggregated("character_tracking_aggregated.json")
            if tracking_path is not None:
                with open(tracking_path, 'r', encoding='utf-8') as f:
                    self.character_data = json.load(f)
            
            # 加载人物弧光数据
            arcs_path = self._find_aggregated("character_arcs_aggregated.json")
            if arcs_path is not None:
                with open(arcs_path, 'r', encoding='utf-8') as f:
                    self.character_arcs = json.load(f)
            
            # 加载核心事件数据
            events_path = self._find_aggregated("core_events_aggregated.json")
            if events_path is not None:
                with open(events_path, 'r', encoding='utf-8') as f:
                    self.core_events = json.load(f)
            
            # 加载长上下文洞察
            insights_path = self._find_aggregated("long_context_insights_aggregated.json")
            if insights_path is not None:
                with open(insights_path, 'r', encoding='utf-8') as f:
                    self.long_context_insights = json.load(f)
            
            return True
            
        except Exception as e:
            print(f"加载聚合数据失败: {e}")
            return False
    
    def get_all_characters(self) -> List[str]:
        """
        获取所有角色列表
        
        Returns:
            角色名称列表，按出场次数排序
        """
        if not self.character_data:
            if not self.load_aggregated_data():
                return []
        # load_aggregated_data 在 tracking 文件缺失时仍可能返回 True，二次兜底（同 get_character_summaries）
        if not self.character_data:
            return []

        # 按事件总数排序
        characters = [
            (name, data.get('total_events', 0))
            for name, data in self.character_data.items()
        ]
        characters.sort(key=lambda x: x[1], reverse=True)

        return [name for name, _ in characters]

    def get_character_summaries(self) -> List[Dict]:
        """角色卡索引：返回 [{name, first_appearance, total_events, chapters_count}]，按事件数降序。

        供 API `/api/books/{id}/characters` 使用（前端 CharactersPage 的列表）。
        """
        if not self.character_data:
            if not self.load_aggregated_data():
                return []
        # load_aggregated_data 在 tracking 文件缺失时仍可能返回 True，
        # 此时 character_data 仍为 None，需二次兜底避免 .items() 崩溃
        if not self.character_data:
            return []
        summaries = []
        for name, data in self.character_data.items():
            summaries.append({
                "name": name,
                "first_appearance": data.get("first_appearance"),
                "total_events": data.get("total_events", 0),
                "chapters_count": len(data.get("chapters", [])),
            })
        summaries.sort(key=lambda x: x.get("total_events", 0), reverse=True)
        return summaries

    def get_card_data(self, character_name: str) -> Dict:
        """单个角色卡的结构化数据，供 API `/api/books/{id}/characters/{name}` 使用。

        返回 dict（而非纯文本），由前端以玻璃卡片形式渲染；缺失数据则抛 KeyError（→ 404）。
        """
        if not self.character_data:
            if not self.load_aggregated_data():
                raise KeyError(f"角色 '{character_name}' 数据未加载")
        # 同上：load 后仍需确认 character_data 已填充，否则对 None 做 `in` 会 TypeError
        if not self.character_data:
            raise KeyError(f"角色 '{character_name}' 数据未加载")
        if character_name not in self.character_data:
            raise KeyError(f"角色 '{character_name}' 不存在")
        char_info = self.character_data[character_name]
        return {
            "name": character_name,
            "first_appearance": char_info.get("first_appearance"),
            "chapters": char_info.get("chapters", []),
            "total_events": char_info.get("total_events", 0),
            "arcs": self._get_character_arcs(character_name),
            "events": self._get_character_events(character_name),
            "states": self._get_character_states(character_name),
            "relationships": self._analyze_relationships(character_name),
        }

    def generate_character_card(self, character_name: str, format: str = "text") -> str:
        """
        生成角色卡
        
        Args:
            character_name: 角色名称
            format: 输出格式 (text, html, markdown)
            
        Returns:
            角色卡字符串
        """
        if not self.character_data:
            if not self.load_aggregated_data():
                return f"错误：无法加载数据"
        
        if character_name not in self.character_data:
            return f"错误：角色 '{character_name}' 不存在"
        
        # 收集角色信息
        char_info = self.character_data[character_name]
        char_arcs = self._get_character_arcs(character_name)
        char_events = self._get_character_events(character_name)
        char_states = self._get_character_states(character_name)
        char_relationships = self._analyze_relationships(character_name)
        
        # 根据格式生成
        if format.lower() == "html":
            return self._generate_html_card(character_name, char_info, char_arcs, char_events, char_states, char_relationships)
        elif format.lower() == "markdown":
            return self._generate_markdown_card(character_name, char_info, char_arcs, char_events, char_states, char_relationships)
        else:
            return self._generate_text_card(character_name, char_info, char_arcs, char_events, char_states, char_relationships)
    
    def _get_character_arcs(self, character_name: str) -> List[Dict]:
        """获取角色的弧光数据"""
        arcs = []
        if not self.character_arcs:
            return arcs
        
        for chapter_key, chapter_arcs in self.character_arcs.items():
            for arc in chapter_arcs:
                if arc['name'] == character_name:
                    chapter_num = int(chapter_key.replace('chapter_', ''))
                    arcs.append({
                        'chapter': chapter_num,
                        'surface_action': arc['surface_action'],
                        'inner_motivation': arc['inner_motivation'],
                        'change_delta': arc['change_delta'],
                        'driver': arc['driver']
                    })
        
        arcs.sort(key=lambda x: x['chapter'])
        return arcs
    
    def _get_character_events(self, character_name: str) -> List[Dict]:
        """获取角色参与的事件"""
        events = []
        if not self.character_data or character_name not in self.character_data:
            return events
        
        char_info = self.character_data[character_name]
        return char_info.get('events', [])
    
    def _get_character_states(self, character_name: str) -> Dict[int, str]:
        """获取角色状态演变"""
        states = {}
        if not self.character_data or character_name not in self.character_data:
            return states
        
        char_info = self.character_data[character_name]
        state_history = char_info.get('state_history', {})
        
        for chapter_str, state in state_history.items():
            try:
                chapter_num = int(chapter_str)
                states[chapter_num] = state
            except ValueError:
                continue
        
        return states
    
    def _analyze_relationships(self, character_name: str) -> Dict[str, Dict]:
        """分析角色关系"""
        relationships = {}
        
        if not self.core_events:
            return relationships
        
        # 从事件中提取角色关系
        for chapter_key, events in self.core_events.items():
            chapter_num = int(chapter_key.replace('chapter_', ''))
            
            for event in events:
                raw_chars = event.get('characters', '')
                if isinstance(raw_chars, list):
                    raw_chars = ", ".join(str(c) for c in raw_chars)
                else:
                    raw_chars = str(raw_chars)
                event_characters = [c.strip() for c in raw_chars.replace("，", ",").split(',') if c.strip()]
                
                if character_name in event_characters:
                    # 这个事件涉及的其他角色都是相关角色
                    for other_char in event_characters:
                        if other_char != character_name:
                            if other_char not in relationships:
                                relationships[other_char] = {
                                    'first_encounter': chapter_num,
                                    'joint_events': [],
                                    'event_count': 0
                                }
                            
                            relationships[other_char]['joint_events'].append({
                                'chapter': chapter_num,
                                'event': event.get('event', ''),
                                'function': event.get('function', '')
                            })
                            relationships[other_char]['event_count'] += 1
        
        return relationships
    
    def _generate_text_card(self, character_name: str, char_info: Dict, 
                            char_arcs: List[Dict], char_events: List[Dict], 
                            char_states: Dict, relationships: Dict) -> str:
        """生成文本格式角色卡"""
        card = []
        card.append("=" * 80)
        card.append(f"角色卡: {character_name}")
        card.append("=" * 80)
        card.append("")
        
        # 基本信息
        card.append("【基本信息】")
        card.append(f"首次出现章节: 第{char_info.get('first_appearance', '未知')}章")
        card.append(f"出场章节总数: {len(char_info.get('chapters', []))}")
        card.append(f"参与事件总数: {char_info.get('total_events', 0)}")
        card.append("")
        
        # 出场章节列表
        chapters = char_info.get('chapters', [])
        if chapters:
            card.append("【出场章节】")
            chapter_ranges = self._format_chapter_ranges(chapters)
            card.append(f"  {chapter_ranges}")
            card.append("")
        
        # 人物弧光
        if char_arcs:
            card.append(f"【人物弧光】 (共{len(char_arcs)}个)")
            for arc in char_arcs:
                card.append(f"  第{arc['chapter']}章:")
                card.append(f"    表面行为: {arc['surface_action']}")
                card.append(f"    内在动机: {arc['inner_motivation']}")
                card.append(f"    变化幅度: {arc['change_delta']}")
                card.append(f"    驱动因素: {arc['driver']}")
                card.append("")
        
        # 角色状态演变
        if char_states:
            card.append(f"【状态演变】 (共{len(char_states)}个状态)")
            for chapter_num, state in sorted(char_states.items()):
                card.append(f"  第{chapter_num}章: {state}")
            card.append("")
        
        # 主要事件
        if char_events:
            card.append(f"【主要事件】 (共{len(char_events)}个)")
            for event in char_events:
                card.append(f"  第{event['chapter']}章 - {event['event']}")
                card.append(f"    功能: {event['function']}")
            card.append("")
        
        # 人际关系
        if relationships:
            card.append(f"【人际关系】 (共{len(relationships)}个关系)")
            # 按共同事件数量排序
            sorted_relations = sorted(relationships.items(), key=lambda x: x[1]['event_count'], reverse=True)
            
            for other_char, rel_data in sorted_relations[:5]:  # 只显示前5个关系
                card.append(f"  {other_char}:")
                card.append(f"    首次相遇: 第{rel_data['first_encounter']}章")
                card.append(f"    共同事件: {rel_data['event_count']}个")
                if rel_data['joint_events']:
                    latest_event = rel_data['joint_events'][-1]
                    card.append(f"    最近互动: 第{latest_event['chapter']}章 - {latest_event['event'][:50]}...")
            card.append("")
        
        card.append("=" * 80)
        card.append(f"生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        card.append("=" * 80)
        
        return "\n".join(card)
    
    def _generate_markdown_card(self, character_name: str, char_info: Dict, 
                                char_arcs: List[Dict], char_events: List[Dict], 
                                char_states: Dict, relationships: Dict) -> str:
        """生成Markdown格式角色卡"""
        card = []
        card.append(f"# 角色卡: {character_name}")
        card.append("")
        
        # 基本信息
        card.append("## 基本信息")
        card.append(f"- **首次出现章节**: 第{char_info.get('first_appearance', '未知')}章")
        card.append(f"- **出场章节总数**: {len(char_info.get('chapters', []))}")
        card.append(f"- **参与事件总数**: {char_info.get('total_events', 0)}")
        card.append("")
        
        # 出场章节
        chapters = char_info.get('chapters', [])
        if chapters:
            card.append("## 出场章节")
            card.append(f"```\n{self._format_chapter_ranges(chapters)}\n```")
            card.append("")
        
        # 人物弧光
        if char_arcs:
            card.append(f"## 人物弧光 ({len(char_arcs)}个)")
            for arc in char_arcs:
                card.append(f"### 第{arc['chapter']}章")
                card.append(f"- **表面行为**: {arc['surface_action']}")
                card.append(f"- **内在动机**: {arc['inner_motivation']}")
                card.append(f"- **变化幅度**: {arc['change_delta']}")
                card.append(f"- **驱动因素**: {arc['driver']}")
                card.append("")
        
        # 角色状态演变
        if char_states:
            card.append(f"## 状态演变 ({len(char_states)}个状态)")
            card.append("| 章节 | 状态 |")
            card.append("|------|------|")
            for chapter_num, state in sorted(char_states.items()):
                card.append(f"| 第{chapter_num}章 | {state} |")
            card.append("")
        
        # 主要事件
        if char_events:
            card.append(f"## 主要事件 ({len(char_events)}个)")
            for event in char_events:
                card.append(f"### 第{event['chapter']}章")
                card.append(f"**事件**: {event['event']}")
                card.append(f"**功能**: {event['function']}")
                card.append("")
        
        # 人际关系
        if relationships:
            card.append(f"## 人际关系 ({len(relationships)}个关系)")
            sorted_relations = sorted(relationships.items(), key=lambda x: x[1]['event_count'], reverse=True)
            
            for other_char, rel_data in sorted_relations:
                card.append(f"### {other_char}")
                card.append(f"- **首次相遇**: 第{rel_data['first_encounter']}章")
                card.append(f"- **共同事件数**: {rel_data['event_count']}个")
                if rel_data['joint_events']:
                    latest_event = rel_data['joint_events'][-1]
                    card.append(f"- **最近互动**: 第{latest_event['chapter']}章 - {latest_event['event'][:100]}...")
                card.append("")
        
        card.append("---")
        card.append(f"*生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}*")
        
        return "\n".join(card)
    
    def _generate_html_card(self, character_name: str, char_info: Dict, 
                           char_arcs: List[Dict], char_events: List[Dict], 
                           char_states: Dict, relationships: Dict) -> str:
        """生成HTML格式角色卡"""
        card = []
        card.append('<!DOCTYPE html>')
        card.append('<html lang="zh-CN">')
        card.append('<head>')
        card.append('  <meta charset="UTF-8">')
        card.append('  <meta name="viewport" content="width=device-width, initial-scale=1.0">')
        card.append(f'  <title>角色卡 - {_esc(character_name)}</title>')
        card.append('  <style>')
        card.append('    body { font-family: "Microsoft YaHei", Arial, sans-serif; max-width: 800px; margin: 0 auto; padding: 20px; background-color: #f5f5f5; }')
        card.append('    .card { background: white; border-radius: 10px; padding: 30px; box-shadow: 0 2px 10px rgba(0,0,0,0.1); }')
        card.append('    h1 { color: #333; border-bottom: 3px solid #4CAF50; padding-bottom: 10px; }')
        card.append('    h2 { color: #666; margin-top: 30px; }')
        card.append('    h3 { color: #888; margin-top: 20px; }')
        card.append('    .info-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 15px; margin: 20px 0; }')
        card.append('    .info-item { background: #f9f9f9; padding: 15px; border-radius: 5px; border-left: 4px solid #4CAF50; }')
        card.append('    .label { font-weight: bold; color: #666; }')
        card.append('    .value { color: #333; margin-top: 5px; }')
        card.append('    .section { margin: 25px 0; }')
        card.append('    .arc-item { background: #f0f8ff; padding: 15px; margin: 10px 0; border-radius: 5px; border-left: 4px solid #2196F3; }')
        card.append('    .event-item { background: #fff3e0; padding: 10px; margin: 8px 0; border-radius: 5px; border-left: 4px solid #FF9800; }')
        card.append('    .relationship-item { background: #f3e5f5; padding: 15px; margin: 10px 0; border-radius: 5px; border-left: 4px solid #9C27B0; }')
        card.append('    .timestamp { text-align: right; color: #999; font-size: 12px; margin-top: 30px; }')
        card.append('  </style>')
        card.append('</head>')
        card.append('<body>')
        card.append('  <div class="card">')
        card.append(f'    <h1>👤 角色卡: {_esc(character_name)}</h1>')
        
        # 基本信息
        card.append('    <div class="section">')
        card.append('      <h2>📊 基本信息</h2>')
        card.append('      <div class="info-grid">')
        card.append(f'        <div class="info-item"><div class="label">首次出现</div><div class="value">第{char_info.get("first_appearance", "未知")}章</div></div>')
        card.append(f'        <div class="info-item"><div class="label">出场章节数</div><div class="value">{len(char_info.get("chapters", []))}章</div></div>')
        card.append(f'        <div class="info-item"><div class="label">参与事件数</div><div class="value">{char_info.get("total_events", 0)}个</div></div>')
        card.append('      </div>')
        card.append('    </div>')
        
        # 出场章节
        chapters = char_info.get('chapters', [])
        if chapters:
            card.append('    <div class="section">')
            card.append('      <h2>📚 出场章节</h2>')
            card.append(f'      <p>{self._format_chapter_ranges(chapters)}</p>')
            card.append('    </div>')
        
        # 人物弧光
        if char_arcs:
            card.append(f'    <div class="section">')
            card.append(f'      <h2>🌟 人物弧光 ({len(char_arcs)}个)</h2>')
            for arc in char_arcs:
                card.append('      <div class="arc-item">')
                card.append(f'        <h3>第{arc["chapter"]}章</h3>')
                # LLM 输出转义，防止 XSS（surface_action/inner_motivation 等裸插 HTML）
                card.append(f'        <p><strong>表面行为:</strong> {_esc(arc["surface_action"])}</p>')
                card.append(f'        <p><strong>内在动机:</strong> {_esc(arc["inner_motivation"])}</p>')
                card.append(f'        <p><strong>变化幅度:</strong> {_esc(arc["change_delta"])}</p>')
                card.append(f'        <p><strong>驱动因素:</strong> {_esc(arc["driver"])}</p>')
                card.append('      </div>')
            card.append('    </div>')
        
        # 角色状态演变
        if char_states:
            card.append(f'    <div class="section">')
            card.append(f'      <h2>🔄 状态演变 ({len(char_states)}个状态)</h2>')
            card.append('      <table style="width: 100%; border-collapse: collapse;">')
            card.append('        <tr style="background: #f5f5f5;"><th style="padding: 10px; text-align: left;">章节</th><th style="padding: 10px; text-align: left;">状态</th></tr>')
            for chapter_num, state in sorted(char_states.items()):
                card.append(f'        <tr><td style="padding: 10px; border-bottom: 1px solid #eee;">第{chapter_num}章</td><td style="padding: 10px; border-bottom: 1px solid #eee;">{_esc(state)}</td></tr>')
            card.append('      </table>')
            card.append('    </div>')
        
        # 主要事件
        if char_events:
            card.append(f'    <div class="section">')
            card.append(f'      <h2>⚡ 主要事件 ({len(char_events)}个)</h2>')
            for event in char_events:
                card.append('      <div class="event-item">')
                card.append(f'        <h3>第{event["chapter"]}章</h3>')
                card.append(f'        <p><strong>事件:</strong> {_esc(event["event"])}</p>')
                card.append(f'        <p><strong>功能:</strong> {_esc(event["function"])}</p>')
                card.append('      </div>')
            card.append('    </div>')
        
        # 人际关系
        if relationships:
            card.append(f'    <div class="section">')
            card.append(f'      <h2>🤝 人际关系 ({len(relationships)}个关系)</h2>')
            sorted_relations = sorted(relationships.items(), key=lambda x: x[1]['event_count'], reverse=True)
            
            for other_char, rel_data in sorted_relations:
                card.append('      <div class="relationship-item">')
                card.append(f'        <h3>{_esc(other_char)}</h3>')
                card.append(f'        <p><strong>首次相遇:</strong> 第{rel_data["first_encounter"]}章</p>')
                card.append(f'        <p><strong>共同事件数:</strong> {rel_data["event_count"]}个</p>')
                if rel_data['joint_events']:
                    latest_event = rel_data['joint_events'][-1]
                    card.append(f'        <p><strong>最近互动:</strong> 第{latest_event["chapter"]}章 - {_esc(latest_event["event"][:100])}...</p>')
                card.append('      </div>')
            card.append('    </div>')
        
        card.append(f'    <div class="timestamp">生成时间: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}</div>')
        card.append('  </div>')
        card.append('</body>')
        card.append('</html>')
        
        return "\n".join(card)
    
    def _format_chapter_ranges(self, chapters: List[int]) -> str:
        """格式化章节范围为可读格式"""
        if not chapters:
            return "无"
        
        if len(chapters) <= 10:
            return ", ".join([f"第{c}章" for c in chapters])
        
        # 简化显示
        ranges = []
        current_range = [chapters[0]]
        
        for i in range(1, len(chapters)):
            if chapters[i] == chapters[i-1] + 1:
                current_range.append(chapters[i])
            else:
                ranges.append(current_range)
                current_range = [chapters[i]]
        
        ranges.append(current_range)
        
        formatted = []
        for r in ranges:
            if len(r) == 1:
                formatted.append(f"第{r[0]}章")
            elif len(r) == 2:
                formatted.append(f"第{r[0]}章、{r[1]}章")
            else:
                formatted.append(f"第{r[0]}-{r[-1]}章")
        
        return "、".join(formatted)
    
    def generate_all_character_cards(self, output_dir: Path = None, format: str = "text"):
        """
        为所有角色生成角色卡
        
        Args:
            output_dir: 输出目录
            format: 输出格式 (text, html, markdown)
            
        Returns:
            生成的文件路径字典
        """
        if not self.character_data:
            if not self.load_aggregated_data():
                return {}
        
        if output_dir is None:
            output_dir = self.aggregated_dir / "character_cards"
        
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        
        generated_files = {}
        characters = self.get_all_characters()
        
        print(f"为 {len(characters)} 个角色生成角色卡...")
        
        for character in characters:
            card_content = self.generate_character_card(character, format)
            
            # 根据格式决定文件扩展名（文件名安全化，防路径注入）
            safe_char = _safe_filename(character)
            if format.lower() == "html":
                file_name = f"{safe_char}.html"
            elif format.lower() == "markdown":
                file_name = f"{safe_char}.md"
            else:
                # text 分支同样需要文件名消毒，否则角色名含 ../ 时可写到目录外
                file_name = f"{_safe_filename(character)}.txt"
            
            file_path = output_dir / file_name
            
            try:
                with open(file_path, 'w', encoding='utf-8') as f:
                    f.write(card_content)
                
                generated_files[character] = file_path
                print(f"  ✓ {character}: {file_name}")
                
            except Exception as e:
                print(f"  ✗ {character}: 生成失败 - {e}")
        
        print(f"\n角色卡生成完成！共生成 {len(generated_files)} 个文件")
        return generated_files
    
    def generate_character_index(self, output_dir: Path = None, format: str = "html"):
        """
        生成角色索引页面
        
        Args:
            output_dir: 输出目录
            format: 输出格式 (html, markdown)
            
        Returns:
            生成的索引文件路径
        """
        if not self.character_data:
            if not self.load_aggregated_data():
                return None
        
        if output_dir is None:
            output_dir = self.aggregated_dir / "character_cards"
        
        output_dir = Path(output_dir)
        characters = self.get_all_characters()
        
        if format.lower() == "html":
            return self._generate_html_index(characters, output_dir)
        else:
            return self._generate_markdown_index(characters, output_dir)
    
    def _generate_html_index(self, characters: List[str], output_dir: Path) -> Path:
        """生成HTML索引"""
        index_path = output_dir / "index.html"
        
        html_content = []
        html_content.append('<!DOCTYPE html>')
        html_content.append('<html lang="zh-CN">')
        html_content.append('<head>')
        html_content.append('  <meta charset="UTF-8">')
        html_content.append('  <meta name="viewport" content="width=device-width, initial-scale=1.0">')
        html_content.append('  <title>角色索引</title>')
        html_content.append('  <style>')
        html_content.append('    body { font-family: "Microsoft YaHei", Arial, sans-serif; max-width: 1000px; margin: 0 auto; padding: 20px; background-color: #f5f5f5; }')
        html_content.append('    .header { background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); color: white; padding: 30px; border-radius: 10px; margin-bottom: 30px; }')
        html_content.append('    .header h1 { margin: 0; }')
        html_content.append('    .grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(250px, 1fr)); gap: 20px; }')
        html_content.append('    .card { background: white; border-radius: 10px; padding: 20px; box-shadow: 0 2px 10px rgba(0,0,0,0.1); transition: transform 0.2s; cursor: pointer; }')
        html_content.append('    .card:hover { transform: translateY(-5px); }')
        html_content.append('    .card-name { font-size: 1.3em; font-weight: bold; color: #333; margin-bottom: 10px; }')
        html_content.append('    .card-stats { color: #666; font-size: 0.9em; }')
        html_content.append('    .stat { margin: 5px 0; }')
        html_content.append('  </style>')
        html_content.append('</head>')
        html_content.append('<body>')
        html_content.append('  <div class="header">')
        html_content.append('    <h1>👥 角色索引</h1>')
        html_content.append(f'    <p>共 {len(characters)} 个角色</p>')
        html_content.append('  </div>')
        html_content.append('  <div class="grid">')
        
        for character in characters:
            char_info = self.character_data.get(character, {})
            safe_char = _safe_filename(character)
            html_content.append('    <div class="card" onclick="location.href=\'{}.html\'">'.format(safe_char))
            html_content.append('      <div class="card-name">{}</div>'.format(_esc(character)))
            html_content.append('      <div class="card-stats">')
            html_content.append('        <div class="stat">📖 首次出现: 第{}章</div>'.format(char_info.get('first_appearance', '未知')))
            html_content.append('        <div class="stat">📚 出场章节: {}章</div>'.format(len(char_info.get('chapters', []))))
            html_content.append('        <div class="stat">⚡ 参与事件: {}个</div>'.format(char_info.get('total_events', 0)))
            html_content.append('      </div>')
            html_content.append('    </div>')
        
        html_content.append('  </div>')
        html_content.append('</body>')
        html_content.append('</html>')
        
        with open(index_path, 'w', encoding='utf-8') as f:
            f.write('\n'.join(html_content))
        
        print(f"✓ 生成索引页面: {index_path}")
        return index_path
    
    def _generate_markdown_index(self, characters: List[str], output_dir: Path) -> Path:
        """生成Markdown索引"""
        index_path = output_dir / "index.md"
        
        md_content = []
        md_content.append("# 👥 角色索引")
        md_content.append("")
        md_content.append(f"共 {len(characters)} 个角色")
        md_content.append("")
        md_content.append("---")
        md_content.append("")
        
        for character in characters:
            char_info = self.character_data.get(character, {})
            md_content.append(f"## [{character}]({character}.md)")
            md_content.append("")
            md_content.append(f"- 📖 **首次出现**: 第{char_info.get('first_appearance', '未知')}章")
            md_content.append(f"- 📚 **出场章节**: {len(char_info.get('chapters', []))}章")
            md_content.append(f"- ⚡ **参与事件**: {char_info.get('total_events', 0)}个")
            md_content.append("")
        
        with open(index_path, 'w', encoding='utf-8') as f:
            f.write('\n'.join(md_content))
        
        print(f"✓ 生成索引页面: {index_path}")
        return index_path