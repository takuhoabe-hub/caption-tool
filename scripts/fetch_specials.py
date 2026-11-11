"""阿部写真舘の各サイトから、今のキャンペーン・特典の見出しを集めて specials.json に書く。

GitHub Actions で毎朝動かす。標準ライブラリだけで動く。
- 各サイトのトップ（と決まったページ）から、キャンペーン・特典らしいリンクを探す
- そのページの <title> と説明文（meta description）を拾う
- 題名に「11/30まで」「5月末まで」などの期限があり、もう過ぎていれば外す
"""
import datetime
import html
import json
import re
import sys
import urllib.parse
import urllib.request

JST = datetime.timezone(datetime.timedelta(hours=9))
UA = "Mozilla/5.0 (compatible; caption-tool-specials/1.0)"

# store はページの店舗選択のキー。genre を書いたものは、そのジャンルを選んだ時だけ出す
SOURCES = [
    {"store": "大阪", "area": "大阪", "site": "https://savetheworld.jp/"},
    {"store": "徳島ウェディング", "area": "徳島", "site": "https://rulesphotogallery.com/"},
    {"store": "徳島", "area": "徳島", "site": "https://753photo.com/", "genre": "七五三"},
    {"store": "徳島", "area": "徳島", "site": "https://20photo.jp/", "genre": "成人式"},
    {"store": "茨城", "area": "茨城", "site": "https://abba-wedding.com/", "genre": "フォトウェディング"},
    {"store": "茨城", "area": "茨城", "site": "https://ep-a-style.jp/", "genre": "!フォトウェディング"},
]
LINK_RE = re.compile(r"campaign|tokuten|fullset|otoshidama|_off|off_|\d+off", re.I)
SKIP_RE = re.compile(r"tokushima|\.(jpg|jpeg|png|gif|webp|pdf)$", re.I)


def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=30) as r:
        if r.status != 200:
            return None
        return r.read().decode("utf-8", errors="ignore")


def clean_title(t):
    t = html.unescape(re.sub(r"\s+", " ", t)).strip()
    # うしろに付いたサイト名（阿部写真館／写真舘を含む部分）だけを落とす
    parts = re.split(r"\s+-\s+|｜|\s\|\s", t)
    while len(parts) > 1 and re.search(r"阿部写真[館舘]|写真館|写真舘", parts[-1]):
        parts.pop()
    return "｜".join(p.strip() for p in parts if p.strip())


def deadline_passed(text, today):
    """題名などに書かれた期限が過ぎていれば True。期限が読めなければ False。"""
    dates = []
    for m in re.finditer(r"(\d{1,2})\s*/\s*(\d{1,2})\s*(?:\([^)]*\))?\s*(?:まで|迄|申込|〆)", text):
        dates.append((int(m.group(1)), int(m.group(2))))
    for m in re.finditer(r"(\d{1,2})月(\d{1,2})日\s*(?:\([^)]*\))?\s*(?:まで|迄)", text):
        dates.append((int(m.group(1)), int(m.group(2))))
    for m in re.finditer(r"(\d{1,2})月末\s*(?:まで|迄)", text):
        mo = int(m.group(1))
        nxt = datetime.date(today.year + (mo == 12), mo % 12 + 1, 1)
        d = nxt - datetime.timedelta(days=1)
        dates.append((mo, d.day))
    if not dates:
        return False
    for mo, day in dates:
        try:
            d = datetime.date(today.year, mo, day)
        except ValueError:
            continue
        # 年が書かれていないので、半年以上前の日付は「来年」とみなす
        if (today - d).days > 183:
            d = d.replace(year=today.year + 1)
        if d >= today:
            return False
    return True


def find_links(site, page):
    base = urllib.parse.urlparse(site).netloc
    out = []
    for href in re.findall(r'href="([^"#]+)"', page):
        url = urllib.parse.urljoin(site, html.unescape(href))
        u = urllib.parse.urlparse(url)
        if u.netloc != base or SKIP_RE.search(u.path) or not LINK_RE.search(u.path):
            continue
        url = u._replace(query="", fragment="").geturl().rstrip("/") + ("/" if u.path.endswith("/") else "")
        if url not in out:
            out.append(url)
    return out


def main():
    today = datetime.datetime.now(JST).date()
    out = [a for a in sys.argv[1:] if not a.startswith("--")]
    out = out[0] if out else "specials.json"
    if "--force" not in sys.argv:
        try:
            last = json.load(open(out, encoding="utf-8"))["checked"][:10]
            if (today - datetime.date.fromisoformat(last)).days < 20:
                print(f"skip: last checked {last}")
                return
        except Exception:
            pass
    items, errors = [], []
    for src in SOURCES:
        try:
            top = fetch(src["site"]) or ""
        except Exception as e:  # サイトが落ちていても他は続ける
            errors.append(f"{src['site']}: {e}")
            continue
        urls = find_links(src["site"], top)  # トップからリンクされているページだけ（古いページを拾わないため）
        # リンクのないバナー画像だけの特典（画像の説明文に「特典」「キャンペーン」とあるもの）
        linked_text = " ".join(urls)
        # 「〇大特典」のバナーだけを拾う（ただのキャンペーンバナーや、他地域の名前が入った説明文は拾わない）
        for alt in sorted(set(re.findall(r'alt="([^"]*大特典[^"]*)"', top))):
            alt = html.unescape(alt).strip()
            if any(a in alt for a in ["大阪", "徳島", "茨城"] if a not in src["area"]):
                continue
            if "dai_tokuten" in linked_text:
                continue  # 同じ特典のページが取れている
            if any(i["store"] == src["store"] and i["title"] == alt for i in items):
                continue
            items.append({"store": src["store"], "genre": src.get("genre", ""), "title": alt,
                          "desc": "", "url": src["site"], "imageOnly": True})
        for url in urls:
            try:
                page = fetch(url)
            except Exception:
                continue  # 無いページ（404）など
            if not page:
                continue
            m = re.search(r"<title[^>]*>(.*?)</title>", page, re.S | re.I)
            if not m:
                continue
            title = clean_title(m.group(1))
            d = re.search(r'<meta\s+name="description"\s+content="([^"]*)"', page, re.I)
            desc = html.unescape(d.group(1)).strip() if d else ""
            if not title or deadline_passed(title + " " + desc, today):
                continue
            if any(i["url"] == url or (i["store"] == src["store"] and i["title"] == title) for i in items):
                continue
            items.append({
                "store": src["store"],
                "genre": src.get("genre", ""),
                "title": title,
                "desc": desc[:200],
                "url": url,
            })
    data = {"checked": datetime.datetime.now(JST).strftime("%Y-%m-%d %H:%M"), "items": items, "errors": errors}
    with open(out, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    print(f"{len(items)} items, {len(errors)} errors")


if __name__ == "__main__":
    main()
