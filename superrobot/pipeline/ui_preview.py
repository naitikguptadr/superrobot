"""Standalone browser preview for generated dr-ui components.

A terminal cannot render React, so we emit a self-contained preview.html:
React UMD + Babel standalone compile the generated TSX in the browser, with
lightweight shims standing in for the private @dr-ui component library.
"""

from __future__ import annotations

import re
from pathlib import Path

_IMPORT_REACT = re.compile(
    r"^import\s+(?:React\s*,?\s*)?(?:\{(?P<named>[^}]*)\})?\s*from\s*['\"]react['\"];?\s*$",
    re.MULTILINE,
)
_IMPORT_DRUI = re.compile(
    r"^import\s+(?P<name>\w+)\s*(?:,\s*\{(?P<named>[^}]*)\})?\s*"
    r"from\s*['\"]@dr-ui/(?P<pkg>[\w./-]+)['\"];?\s*$",
    re.MULTILINE,
)
_IMPORT_DRUI_NAMED = re.compile(
    r"^import\s*\{(?P<named>[^}]*)\}\s*from\s*['\"]@dr-ui/(?P<pkg>[\w./-]+)['\"];?\s*$",
    re.MULTILINE,
)
_IMPORT_OTHER = re.compile(r"^import\s+[^;]+from\s*['\"][^'\"]+['\"];?\s*$", re.MULTILINE)
_EXPORT_DEFAULT = re.compile(r"^export\s+default\s+(?P<name>\w+);?\s*$", re.MULTILINE)
_EXPORT_DEFAULT_DECL = re.compile(r"^export\s+default\s+", re.MULTILINE)


def rewrite_tsx_for_preview(tsx: str) -> str:
    """Strip module syntax so the TSX runs in a browser <script> tag."""
    out = tsx

    def react_repl(match: re.Match[str]) -> str:
        named = match.group("named")
        return f"const {{ {named.strip()} }} = React;" if named and named.strip() else ""

    out = _IMPORT_REACT.sub(react_repl, out)

    def drui_repl(match: re.Match[str]) -> str:
        lines = [f"const {match.group('name')} = DrUi.resolve('{match.group('name')}');"]
        named = match.group("named")
        if named:
            lines.extend(
                f"const {part.strip()} = DrUi.resolve('{part.strip()}');"
                for part in named.split(",")
                if part.strip()
            )
        return "\n".join(lines)

    out = _IMPORT_DRUI.sub(drui_repl, out)

    def drui_named_repl(match: re.Match[str]) -> str:
        return "\n".join(
            f"const {part.strip()} = DrUi.resolve('{part.strip()}');"
            for part in match.group("named").split(",")
            if part.strip()
        )

    out = _IMPORT_DRUI_NAMED.sub(drui_named_repl, out)
    out = _IMPORT_OTHER.sub("", out)

    default_name = _EXPORT_DEFAULT.search(out)
    if default_name:
        out = _EXPORT_DEFAULT.sub(f"window.__PreviewComponent = {default_name.group('name')};", out)
    else:
        # export default function App() {…} / export default () => …
        out = _EXPORT_DEFAULT_DECL.sub("window.__PreviewComponent = ", out)
    out = re.sub(r"^export\s+", "", out, flags=re.MULTILINE)
    return out


_HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>SuperRobot — dr-ui component preview</title>
<script crossorigin src="https://unpkg.com/react@18/umd/react.production.min.js"></script>
<script crossorigin src="https://unpkg.com/react-dom@18/umd/react-dom.production.min.js"></script>
<script crossorigin src="https://unpkg.com/@babel/standalone/babel.min.js"></script>
<style>
  :root {{ color-scheme: dark; }}
  body {{ margin: 0; background: #15181e; color: #e6e8ee;
         font-family: -apple-system, "Segoe UI", Roboto, sans-serif; }}
  header {{ background: #1f6feb; padding: 10px 16px; font-weight: 700; }}
  header span {{ opacity: .75; font-weight: 400; font-size: .85em; margin-left: 8px; }}
  #stage {{ max-width: 880px; margin: 32px auto; padding: 0 16px; }}
  #error {{ color: #ff7b72; white-space: pre-wrap; font-family: monospace; padding: 16px; }}
  .drui {{ box-sizing: border-box; }}
  .drui-card {{ background: #1c2128; border: 1px solid #30363d; border-radius: 10px;
               padding: 16px; margin: 12px 0; }}
  .drui-button {{ background: #1f6feb; color: white; border: 0; border-radius: 8px;
                 padding: 8px 16px; font-size: 14px; cursor: pointer; }}
  .drui-button:hover {{ background: #388bfd; }}
  .drui-input {{ background: #0d1117; color: #e6e8ee; border: 1px solid #30363d;
                border-radius: 8px; padding: 8px 12px; font-size: 14px; width: 100%;
                box-sizing: border-box; margin: 6px 0; }}
  .drui-table {{ width: 100%; border-collapse: collapse; margin: 12px 0; }}
  .drui-table th, .drui-table td {{ border-bottom: 1px solid #30363d; text-align: left;
                                   padding: 8px 10px; font-size: 14px; }}
  .drui-generic {{ border: 1px dashed #30363d; border-radius: 8px; padding: 10px;
                  margin: 8px 0; }}
  .drui-label {{ font-size: 11px; text-transform: uppercase; letter-spacing: .08em;
                color: #8b949e; display: block; margin-bottom: 4px; }}
</style>
</head>
<body>
<header>SuperRobot dr-ui preview<span>@dr-ui components rendered as local shims — layout
and wiring are real, exact styling comes from the private library</span></header>
<div id="stage"><div id="root"></div><div id="error"></div></div>
<script>
const e = React.createElement;
function shim(tag, className) {{
  return function Shim(props) {{
    const {{ children, label, placeholder, value, onChange, onClick, ...rest }} = props || {{}};
    if (tag === 'input') {{
      // @dr-ui contract: onChange receives the new string value, not the event
      const handle = onChange
        ? (ev) => {{
            try {{ onChange(ev.target.value); }}
            catch (err) {{ onChange(ev); }}
          }}
        : undefined;
      return e('div', {{className: 'drui'}},
        label ? e('span', {{className: 'drui-label'}}, label) : null,
        e('input', {{className, placeholder, value, onChange: handle}}));
    }}
    if (tag === 'button') {{
      return e('button', {{className, onClick}}, children || label || 'Button');
    }}
    return e('div', {{className: 'drui ' + className}},
      label ? e('span', {{className: 'drui-label'}}, label) : null, children);
  }};
}}
function tableShim(props) {{
  const rows = props.data || props.rows || [];
  const cols = props.columns || (rows[0] ? Object.keys(rows[0]) : []);
  const colKey = (c) => typeof c === 'string' ? c : (c.key || c.field || c.name || '');
  const colLabel = (c) => typeof c === 'string' ? c : (c.label || c.header || colKey(c));
  return e('table', {{className: 'drui-table'}},
    e('thead', null, e('tr', null, cols.map((c, i) => e('th', {{key: i}}, colLabel(c))))),
    e('tbody', null, rows.map((r, i) => e('tr', {{key: i}},
      cols.map((c, j) => e('td', {{key: j}}, String(r[colKey(c)] ?? '')))))),
    props.children || null);
}}
const known = {{
  Card: shim('div', 'drui-card'),
  Button: shim('button', 'drui-button'),
  TextInput: shim('input', 'drui-input'),
  Input: shim('input', 'drui-input'),
  TextArea: shim('input', 'drui-input'),
  DataTable: tableShim,
  Table: tableShim,
  Badge: function Badge(props) {{
    return e('span', {{style: {{background: '#238636', borderRadius: '999px',
      padding: '2px 10px', fontSize: '12px', fontWeight: 600}}}},
      (props && props.children) || '');
  }},
}};
const DrUi = {{
  resolve(name) {{
    if (known[name]) return known[name];
    return function Generic(props) {{
      return e('div', {{className: 'drui drui-generic'}},
        e('span', {{className: 'drui-label'}}, name), (props && props.children) || null);
    }};
  }}
}};
window.DrUi = DrUi;
window.addEventListener('error', (ev) => {{
  document.getElementById('error').textContent = String(ev.message || ev.error);
}});
</script>
<script type="text/plain" id="component-source">
{component_source}
</script>
<script>
// Explicit transform: Babel's text/babel auto-processing mis-parses TSX generics,
// so compile with full options and eval the result ourselves.
try {{
  const source = document.getElementById('component-source').textContent;
  const compiled = Babel.transform(source, {{
    presets: ['typescript', 'react'],
    filename: 'component.tsx',
  }}).code;
  new Function(compiled)();
  if (window.__PreviewComponent) {{
    ReactDOM.createRoot(document.getElementById('root'))
      .render(React.createElement(window.__PreviewComponent));
  }} else {{
    document.getElementById('error').textContent =
      'No default export found in the generated component.';
  }}
}} catch (err) {{
  document.getElementById('error').textContent = String(err);
}}
</script>
</body>
</html>
"""


def build_preview_html(tsx: str) -> str:
    """Render the generated TSX into a self-contained preview HTML document."""
    return _HTML_TEMPLATE.format(component_source=rewrite_tsx_for_preview(tsx))


def write_preview(tsx: str, output_dir: str | Path) -> Path:
    """Write ui/preview.html next to the generated component. Returns its path."""
    out = Path(output_dir) / "ui"
    out.mkdir(parents=True, exist_ok=True)
    path = out / "preview.html"
    path.write_text(build_preview_html(tsx))
    return path
