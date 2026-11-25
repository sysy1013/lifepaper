# -*- coding: utf-8 -*-
"""테마 CSS가 외부 폰트·CDN을 불러오지 않는지 정적 검사 (제3자 요청 차단)."""
import os
import re

import core.theme as theme

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_URL_RE = re.compile(r"https?://", re.I)


def test_theme_css_has_no_external_urls():
    assert not _URL_RE.search(theme._CSS)
    assert "@import" not in theme._CSS


def test_app_and_theme_sources_have_no_font_or_cdn_urls():
    for rel in ("app.py", os.path.join("core", "theme.py")):
        with open(os.path.join(ROOT, rel), encoding="utf-8") as f:
            src = f.read()
        assert not _URL_RE.search(src), rel


def test_config_disables_usage_stats():
    with open(os.path.join(ROOT, ".streamlit", "config.toml"), encoding="utf-8") as f:
        cfg = f.read()
    assert re.search(r"^\[browser\]\s*\n(?:.*\n)*?gatherUsageStats\s*=\s*false", cfg, re.M)


def test_verdict_cell_style_maps_known_verdicts():
    assert "#C23B2E" in theme.verdict_cell_style("기재 불가")
    assert theme.verdict_cell_style("통과") == "" or "통과" in theme.VERDICT_CLASS
    assert theme.verdict_cell_style("확인 필요 · 교사 판단 필요") == theme.VERDICT_CELL_STYLE["교사 판단 필요"]
