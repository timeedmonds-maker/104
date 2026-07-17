from pathlib import Path

path = Path('tools/build_additional_22.py')
text = path.read_text(encoding='utf-8')
text = text.replace(
    'cells = [html.unescape(x.strip()) for x in line.strip().strip("|").split("|")]\n        try:\n            idx = next(i for i, c in enumerate(cells) if c in {"On Court", "Off Court"})',
    'cells = [html.unescape(x.strip()) for x in line.strip().strip("|").split("|")]\n        cells = [re.sub(r"[*_`]", "", c).strip() for c in cells]\n        try:\n            idx = next(i for i, c in enumerate(cells) if c in {"On Court", "Off Court"})'
)
text = text.replace(
    'team = re.sub(r"[^A-Z0-9]", "", cells[1])\n        if not re.fullmatch(r"[A-Z]{2,3}", team):',
    'team_cell = cells[1]\n        link_match = re.search(r"\\[([A-Z]{2,3})\\]", team_cell)\n        team = link_match.group(1) if link_match else re.sub(r"[^A-Z0-9]", "", team_cell)\n        if not re.fullmatch(r"[A-Z]{2,3}", team):'
)
text = text.replace(
    'def parse_onoff(text: str) -> list[dict]:\n    rows = parse_markdown_rows(text) or parse_html_rows(text)',
    'def parse_onoff(text: str) -> list[dict]:\n    regular = re.search(r"## [^\\n]*Regular Season\\n(.*?)(?=\\n## [^\\n]*Playoffs|\\n## More )", text, flags=re.S)\n    if regular:\n        text = regular.group(1)\n    rows = parse_markdown_rows(text) or parse_html_rows(text)'
)
path.write_text(text, encoding='utf-8')
print('patched markdown parser and restricted parsing to regular season')
