import json
import re
import requests
from bs4 import BeautifulSoup, Comment

url = 'https://www.basketball-reference.com/players/a/adamsst01/on-off/2025'
headers = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/126.0 Safari/537.36',
    'Accept-Language': 'en-US,en;q=0.9',
}
r = requests.get(url, headers=headers, timeout=30)
out = {'url': url, 'status': r.status_code, 'length': len(r.text), 'title': None, 'rows': []}
if r.status_code == 200:
    soup = BeautifulSoup(r.text, 'lxml')
    if soup.title:
        out['title'] = soup.title.get_text(' ', strip=True)
    # BRef sometimes wraps tables in HTML comments.
    for c in soup.find_all(string=lambda t: isinstance(t, Comment)):
        if '<table' in c:
            c.replace_with(BeautifulSoup(c, 'lxml'))
    for tr in soup.find_all('tr'):
        text = ' | '.join(x.get_text(' ', strip=True) for x in tr.find_all(['th','td']))
        if 'On Court' in text or 'Off Court' in text or 'On − Off' in text or 'On - Off' in text:
            out['rows'].append(text)
with open('test_output.json', 'w', encoding='utf-8') as f:
    json.dump(out, f, ensure_ascii=False, indent=2)
print(json.dumps(out, ensure_ascii=False, indent=2))
