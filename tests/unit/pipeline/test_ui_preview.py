"""dr-ui browser preview tests."""

from __future__ import annotations

from pathlib import Path

from superrobot.pipeline.ui_preview import (
    build_preview_html,
    rewrite_tsx_for_preview,
    write_preview,
)

SAMPLE_TSX = """import React, { useState } from 'react';
import Card from '@dr-ui/card';
import TextInput from '@dr-ui/text-input';
import Button from '@dr-ui/button';

const ResultsPanel: React.FC = () => {
  const [query, setQuery] = useState<string>('');
  return (
    <Card>
      <TextInput label="Query" value={query} onChange={(e) => setQuery(e.target.value)} />
      <Button>Run</Button>
    </Card>
  );
};

export default ResultsPanel;
"""


def test_rewrite_strips_imports_and_exposes_component() -> None:
    out = rewrite_tsx_for_preview(SAMPLE_TSX)
    assert "import " not in out
    assert "const { useState } = React;" in out
    assert "const Card = DrUi.resolve('Card');" in out
    assert "const TextInput = DrUi.resolve('TextInput');" in out
    assert "window.__PreviewComponent = ResultsPanel;" in out


def test_rewrite_handles_inline_default_export() -> None:
    tsx = "import React from 'react';\nexport default function App() { return <div/>; }\n"
    out = rewrite_tsx_for_preview(tsx)
    assert "window.__PreviewComponent = function App()" in out


def test_build_preview_html_is_self_contained() -> None:
    html = build_preview_html(SAMPLE_TSX)
    assert "react.production.min.js" in html
    assert "babel.min.js" in html
    assert "window.__PreviewComponent = ResultsPanel;" in html
    assert "DrUi" in html


def test_write_preview(tmp_path: Path) -> None:
    path = write_preview(SAMPLE_TSX, tmp_path)
    assert path == tmp_path / "ui" / "preview.html"
    assert "ResultsPanel" in path.read_text()
