"""SR-09 T-prewarm（S26/A7）：prewarm 對 fin_cale 的引用必須走公開 register()。

prewarm.py 的模組/函式 docstring 是 plugin 作者的範板來源——範例若引用
私有 `_ensure_calendar_registered`，各倉 plugin 會照抄形成跨包私有耦合
（A7，ADR-039 D3 收口目標）。本測試防再犯：prewarm.py 與 PREWARM 指南
全文不得再出現 `_ensure_calendar_registered`。
"""
from __future__ import annotations

from pathlib import Path

import funlab.core.prewarm as prewarm_mod

REPO_ROOT = Path(__file__).resolve().parents[1]
FORBIDDEN = '_ensure_calendar_registered'


def test_prewarm_module_source_has_no_private_calendar_api():
    src = Path(prewarm_mod.__file__).read_text(encoding='utf-8')
    assert FORBIDDEN not in src, (
        'prewarm.py 範例引用了 fin_cale 私有 API；改用 fin_cale.register()'
        '（ADR-039 D3）')


def test_prewarm_guide_doc_has_no_private_calendar_api():
    doc = REPO_ROOT / 'docs' / 'PREWARM開發使用指南.md'
    if not doc.exists():
        import pytest
        pytest.skip('PREWARM 指南不在本 checkout')
    assert FORBIDDEN not in doc.read_text(encoding='utf-8')
