import requests
from pathlib import Path

out = Path('probe_output')
out.mkdir(exist_ok=True)
urls = {
    'pau_2013': 'https://web.archive.org/web/20130412104327id_/http://www.basketball-reference.com/players/g/gasolpa01/on-off/',
    'chandler_2013': 'https://web.archive.org/web/20130426052613id_/http://www.basketball-reference.com/players/c/chandty01/on-off/',
    'brand_2015': 'https://web.archive.org/web/20150905224450id_/http://www.basketball-reference.com/players/b/brandel01/on-off/',
    'millsap_2013': 'https://web.archive.org/web/20130514233455id_/http://www.basketball-reference.com/players/m/millspa01/on-off/',
}
for name, url in urls.items():
    try:
        r = requests.get(url, timeout=120, headers={'User-Agent': 'Mozilla/5.0'})
        print(name, r.status_code, len(r.text), r.url, flush=True)
        (out / f'{name}.txt').write_text(r.text, encoding='utf-8')
    except Exception as exc:
        print(name, type(exc).__name__, exc, flush=True)
        (out / f'{name}.txt').write_text(repr(exc), encoding='utf-8')
