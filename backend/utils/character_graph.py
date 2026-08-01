import json, re
from pathlib import Path
from typing import Dict, Tuple, Optional

try:
    import networkx as nx
    HAS_NETWORKX = True
except ImportError:
    HAS_NETWORKX = False
try:
    from pyvis.network import Network
    HAS_PYVIS = True
except ImportError:
    HAS_PYVIS = False

def _detect_communities(G):
    communities = {}
    for i, component in enumerate(nx.connected_components(G)):
        for node in component:
            communities[node] = i
    return communities

STAR_PALETTE = [
    '#6EB5FF', '#A8C8FF', '#CAD8FF', '#FFF4F0',
    '#FFEDD5', '#FFD2A1', '#FFB56B', '#FF8C42',
    '#C792EA', '#89DDFF', '#FFCB6B', '#F78C6C',
]
C_BG = '#0A0E1A'
C_BORDER = 'rgba(45, 85, 140, 0.4)'
C_TEXT = '#C8D6E5'
C_TEXT_BRIGHT = '#E8F0FE'
C_ACCENT = '#00BFFF'
C_ACCENT_DIM = 'rgba(0, 191, 255, 0.3)'
FONT = "'JetBrains Mono', 'Consolas', 'Microsoft YaHei', monospace"

def _hex_rgb(h):
    h = h.lstrip('#')
    return tuple(int(h[i:i+2], 16) for i in (0, 2, 4))
def _rgb_hex(r, g, b):
    return '#{:02x}{:02x}{:02x}'.format(max(0,min(255,int(r))), max(0,min(255,int(g))), max(0,min(255,int(b))))
def _lighten(h, f):
    r, g, b = _hex_rgb(h)
    return _rgb_hex(r+(255-r)*f, g+(255-g)*f, b+(255-b)*f)

class CharacterGraphGenerator:
    def __init__(self, output_dir):
        self.output_dir = Path(output_dir)
        self.characters = {}
        self.edges = {}
        self.chapter_count = 0

    def load_results(self, max_chapters=None):
        files = sorted(
            self.output_dir.glob('chapter_*_result.json'),
            key=lambda f: int(re.search(r'chapter_(\d+)', f.name).group(1))
              if re.search(r'chapter_(\d+)', f.name) else 0
        )
        if max_chapters:
            files = files[:max_chapters]
        for f in files:
            try:
                data = json.loads(f.read_text(encoding='utf-8'))
            except Exception:
                continue
            ch = data.get('chapter_number', 0)
            arcs = data.get('character_arcs', [])
            if not arcs:
                continue
            self.chapter_count = max(self.chapter_count, ch)
            names_in_ch = []
            for arc in arcs:
                if not isinstance(arc, dict):
                    continue
                name = (arc.get('name') or '').strip()
                if not name or len(name) > 10:
                    continue
                action = (arc.get('surface_action') or '').strip()
                if name not in self.characters:
                    self.characters[name] = {'count': 0, 'actions': []}
                self.characters[name]['count'] += 1
                if action and len(self.characters[name]['actions']) < 3:
                    self.characters[name]['actions'].append(action)
                names_in_ch.append(name)
            unique = list(dict.fromkeys(names_in_ch))
            for i, a in enumerate(unique):
                for b in unique[i+1:]:
                    key = (a, b) if a < b else (b, a)
                    if key not in self.edges:
                        self.edges[key] = {'count': 0, 'chapters': []}
                    self.edges[key]['count'] += 1
                    if len(self.edges[key]['chapters']) < 5:
                        self.edges[key]['chapters'].append(ch)
        return len(files)

    def build_graph(self, min_edge_weight=2, min_node_count=2):
        if not HAS_NETWORKX:
            raise ImportError('需要 networkx')
        G = nx.Graph()
        for name, info in self.characters.items():
            if info['count'] >= min_node_count:
                G.add_node(name, count=info['count'], actions=info['actions'])
        for (a, b), info in self.edges.items():
            if info['count'] >= min_edge_weight and a in G and b in G:
                G.add_edge(a, b, weight=info['count'], chapters=info['chapters'])
        G.remove_nodes_from(list(nx.isolates(G)))
        return G

    def generate_html(self, output_path=None, min_edge_weight=2,
                      min_node_count=2, max_nodes=80,
                      height='100%', width='100%'):
        if not HAS_PYVIS:
            raise ImportError('需要 pyvis')
        G = self.build_graph(min_edge_weight, min_node_count)
        if len(G.nodes) > max_nodes:
            sorted_nodes = sorted(G.nodes(data=True),
                                  key=lambda x: x[1].get('count', 0), reverse=True)
            keep = set(n[0] for n in sorted_nodes[:max_nodes])
            G = G.subgraph(keep).copy()
            G.remove_nodes_from(list(nx.isolates(G)))
        if len(G.nodes) == 0:
            raise ValueError('没有符合条件的角色节点')
        communities = _detect_communities(G)
        pos = nx.spring_layout(G, k=2.5, iterations=80, weight='weight', seed=42)

        net = Network(height=height, width=width, bgcolor='rgba(0,0,0,0)',
                      font_color=C_TEXT, directed=False, notebook=False)
        net.set_options(json.dumps({
            "interaction": {"hover": True, "tooltipDelay": 100,
                            "zoomView": True, "dragView": True},
            "physics": {
                "barnesHut": {
                    "gravitationalConstant": -15000,
                    "centralGravity": 0.15,
                    "springLength": 280,
                    "springConstant": 0.01,
                    "damping": 0.5
                }
            }
        }))

        counts = [d.get('count', 1) for _, d in G.nodes(data=True)]
        c_min, c_max = min(counts), max(counts)

        def node_radius(c):
            if c_max == c_min:
                return 22
            return 14 + (c - c_min) / (c_max - c_min) * 36

        # Nodes first
        for node, data in G.nodes(data=True):
            x, y = pos[node]
            count = data.get('count', 1)
            comm = communities.get(node, 0)
            color = STAR_PALETTE[comm % len(STAR_PALETTE)]
            r = node_radius(count)
            t = (count - c_min) / max(1, (c_max - c_min))
            brightness = 0.2 + t ** 0.6 * 0.8
            glow_color = _lighten(color, brightness * 0.6)
            border_col = _lighten(color, 0.3)
            net.add_node(
                node, label=node, size=r,
                color={'background': color, 'border': border_col,
                       'highlight': {'background': _lighten(color, 0.2), 'border': C_ACCENT},
                       'hover': {'background': _lighten(color, 0.15), 'border': border_col}},
                font={'face': 'Segoe UI, Microsoft YaHei, sans-serif',
                      'size': max(10, int(r * 0.45)), 'color': '#FFFFFF',
                      'strokeWidth': 3, 'strokeColor': 'rgba(0,0,0,0.6)',
                      'vadjust': int(r) + 12},
                shape='dot', borderWidth=0,
                shadow={'enabled': True, 'color': glow_color,
                        'size': int(r * 1.5 * brightness), 'x': 0, 'y': 0},
                x=x * 600, y=y * 600,
            )

        # Edges (transparent - drawn by overlay)
        for u, v, data in G.edges(data=True):
            net.add_edge(u, v, value=data.get('weight', 1),
                         color={'color': 'rgba(0,0,0,0)',
                                'highlight': 'rgba(0,0,0,0)',
                                'hover': 'rgba(0,0,0,0)'},
                         smooth={'type': 'continuous'})

        if output_path is None:
            output_path = self.output_dir / 'character_graph.html'
        output_path = Path(output_path)
        net.save_graph(str(output_path))
        self._patch_html(output_path, G)
        return output_path

    def _patch_html(self, html_path, G):
        n_nodes = len(G.nodes)
        n_edges = len(G.edges)

        # Read the backup JS template
        js_template_path = Path(__file__).parent / '_character_graph_js.txt'
        if js_template_path.exists():
            custom_js = js_template_path.read_text(encoding='utf-8')
        else:
            # 模板缺失：用空交互脚本占位，避免调用不存在的 _default_js() 抛 AttributeError
            # 导致整个角色图谱生成必崩（该类当前未接线，但应保证不崩溃）
            custom_js = ""

        # Build edge data
        edge_data = []
        for u, v, d in G.edges(data=True):
            edge_data.append([u, v, d.get('weight', 1)])
        edge_json = json.dumps(edge_data, ensure_ascii=False)

        # Inject edge data into JS
        custom_js = re.sub(r'var EDGE_DATA = .+?;\n',
                           'var EDGE_DATA = ' + edge_json + ';\n',
                           custom_js, count=1)

        css = self._css_template()
        body = self._body_template(n_nodes, n_edges)

        html = html_path.read_text(encoding='utf-8')
        html = html.replace('<body>', '<body>\n' + css + body)
        html = html.replace('</body>', custom_js + '\n</body>')
        html_path.write_text(html, encoding='utf-8')

    def _css_template(self):
        return """
<style>
  @import url('https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@300;400;500;600&display=swap');
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body { background: """ + C_BG + """; overflow: hidden; font-family: """ + FONT + """; }
  #starfield { position: fixed; top: 0; left: 0; width: 100%; height: 100%; z-index: 0; pointer-events: none; }
  #edgeOverlay { position: fixed; top: 0; left: 0; width: 100%; height: 100%; z-index: 1; pointer-events: none; }
  #mynetwork { position: fixed !important; top: 0; left: 0; right: 0; bottom: 0; z-index: 2; background: transparent !important; }
  .grid-overlay {
    position: fixed; top: 0; left: 0; width: 100%; height: 100%;
    z-index: 3; pointer-events: none;
    background-image:
      linear-gradient(""" + C_BORDER + """ 1px, transparent 1px),
      linear-gradient(90deg, """ + C_BORDER + """ 1px, transparent 1px);
    background-size: 80px 80px; opacity: 0.12;
  }
  .scanline {
    position: fixed; top: 0; left: 0; width: 100%; height: 100%;
    z-index: 4; pointer-events: none;
    background: repeating-linear-gradient(0deg, transparent, transparent 2px, rgba(0,191,255,0.006) 2px, rgba(0,191,255,0.006) 4px);
  }
  .hud-top {
    position: fixed; top: 0; left: 0; right: 0; z-index: 100; height: 44px;
    background: linear-gradient(180deg, rgba(10,14,26,0.95), rgba(10,14,26,0.8));
    border-bottom: 1px solid """ + C_BORDER + """; display: flex; align-items: center; padding: 0 20px;
    backdrop-filter: blur(12px);
  }
  .hud-top .logo { font-size: 11px; font-weight: 600; color: """ + C_ACCENT + """; letter-spacing: 3px; text-transform: uppercase; }
  .hud-top .sep { width: 1px; height: 18px; background: """ + C_BORDER + """; margin: 0 14px; }
  .hud-top .title { font-size: 12px; color: """ + C_TEXT + """; letter-spacing: 0.5px; }
  .hud-top .stat { margin-left: auto; display: flex; gap: 18px; }
  .hud-top .stat-item { font-size: 10px; color: """ + C_TEXT + """; letter-spacing: 0.5px; }
  .hud-top .stat-item .val { color: """ + C_ACCENT + """; font-weight: 600; font-size: 12px; margin-left: 4px; }
  .hud-panel {
    position: fixed; top: 56px; left: 12px; z-index: 100; width: 200px;
    background: rgba(10, 18, 40, 0.85); border: 1px solid """ + C_BORDER + """; border-radius: 4px;
    padding: 14px 16px; backdrop-filter: blur(16px);
  }
  .hud-panel .panel-title { font-size: 9px; font-weight: 600; color: """ + C_ACCENT + """; letter-spacing: 2px; text-transform: uppercase; margin-bottom: 10px; padding-bottom: 6px; border-bottom: 1px solid """ + C_BORDER + """; }
  .hud-panel .row { display: flex; justify-content: space-between; align-items: center; padding: 3px 0; }
  .hud-panel .label { font-size: 10px; color: """ + C_TEXT + """; }
  .hud-panel .val { font-size: 12px; color: """ + C_TEXT_BRIGHT + """; font-weight: 500; }
  .hud-panel .divider { height: 1px; background: """ + C_BORDER + """; margin: 8px 0; }
  .hud-panel .legend-item { font-size: 9px; color: rgba(200,214,229,0.5); padding: 2px 0; }
  .hud-bottom {
    position: fixed; bottom: 0; left: 0; right: 0; z-index: 100; height: 28px;
    background: rgba(10,14,26,0.9); border-top: 1px solid """ + C_BORDER + """;
    display: flex; align-items: center; justify-content: center;
    gap: 24px; font-size: 9px; color: rgba(200,214,229,0.4);
    letter-spacing: 1px; text-transform: uppercase; backdrop-filter: blur(8px);
  }
  .hud-bottom .key { color: """ + C_ACCENT + """; font-weight: 500; }
  .corner { position: fixed; z-index: 99; width: 20px; height: 20px; pointer-events: none; }
  .corner-tl { top: 44px; left: 0; border-top: 1px solid """ + C_ACCENT_DIM + """; border-left: 1px solid """ + C_ACCENT_DIM + """; }
  .corner-tr { top: 44px; right: 0; border-top: 1px solid """ + C_ACCENT_DIM + """; border-right: 1px solid """ + C_ACCENT_DIM + """; }
  .corner-bl { bottom: 28px; left: 0; border-bottom: 1px solid """ + C_ACCENT_DIM + """; border-left: 1px solid """ + C_ACCENT_DIM + """; }
  .corner-br { bottom: 28px; right: 0; border-bottom: 1px solid """ + C_ACCENT_DIM + """; border-right: 1px solid """ + C_ACCENT_DIM + """; }
</style>
"""

    def _body_template(self, n_nodes, n_edges):
        return """
<canvas id="starfield"></canvas>
<canvas id="edgeOverlay"></canvas>
<div class="grid-overlay"></div>
<div class="scanline"></div>
<div class="corner corner-tl"></div>
<div class="corner corner-tr"></div>
<div class="corner corner-bl"></div>
<div class="corner corner-br"></div>
<div class="hud-top">
  <span class="logo">STAR MAP</span>
  <div class="sep"></div>
  <span class="title">Character Network</span>
  <div class="stat">
    <span class="stat-item">STARS<span class="val">""" + str(n_nodes) + """</span></span>
    <span class="stat-item">LINKS<span class="val">""" + str(n_edges) + """</span></span>
    <span class="stat-item">CHAPTERS<span class="val">""" + str(self.chapter_count) + """</span></span>
  </div>
</div>
<div class="hud-panel">
  <div class="panel-title">Navigator</div>
  <div class="row"><span class="label">Stars</span><span class="val">""" + str(n_nodes) + """</span></div>
  <div class="row"><span class="label">Links</span><span class="val">""" + str(n_edges) + """</span></div>
  <div class="row"><span class="label">Range</span><span class="val">Ch.1-""" + str(self.chapter_count) + """</span></div>
  <div class="divider"></div>
  <div class="legend-item">Star size = frequency</div>
  <div class="legend-item">Glow = importance</div>
  <div class="legend-item">Link glow = co-occurrence</div>
  <div class="legend-item">Color = community</div>
</div>
<div class="hud-bottom">
  <span><span class="key">DRAG</span> Reposition</span>
  <span><span class="key">SCROLL</span> Zoom</span>
  <span><span class="key">CLICK</span> Highlight</span>
</div>
"""

    def get_stats(self):
        return {
            'total_characters': len(self.characters),
            'total_edges': len(self.edges),
            'chapters': self.chapter_count,
            'top_characters': sorted(self.characters.items(),
                                     key=lambda x: x[1]['count'], reverse=True)[:10],
            'strongest_edges': sorted(self.edges.items(),
                                      key=lambda x: x[1]['count'], reverse=True)[:10],
        }

def main():
    import argparse
    parser = argparse.ArgumentParser(description='角色关系图 - 戴森球风格星图')
    parser.add_argument('output_dir', help='分析结果输出目录')
    parser.add_argument('-o', '--output', help='HTML 输出路径')
    parser.add_argument('--min-weight', type=int, default=2)
    parser.add_argument('--min-count', type=int, default=2)
    parser.add_argument('--max-nodes', type=int, default=80)
    parser.add_argument('--max-chapters', type=int, default=None)
    args = parser.parse_args()

    gen = CharacterGraphGenerator(args.output_dir)
    n = gen.load_results(max_chapters=args.max_chapters)
    print(f'已加载 {n} 个章节, {len(gen.characters)} 个角色, {len(gen.edges)} 条关系')
    stats = gen.get_stats()
    print('\n出现最多:')
    for name, info in stats['top_characters'][:5]:
        print(f'  {name}: {info["count"]} 章')
    print('\n共现最强:')
    for (a, b), info in stats['strongest_edges'][:5]:
        print(f'  {a} - {b}: {info["count"]} 章')

    out = Path(args.output) if args.output else None
    path = gen.generate_html(output_path=out, min_edge_weight=args.min_weight,
                             min_node_count=args.min_count, max_nodes=args.max_nodes)
    print(f'\n星图已生成: {path}')

if __name__ == '__main__':
    main()
