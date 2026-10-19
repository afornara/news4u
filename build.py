"""
Rassegna giornaliera: legge i feed RSS, fa riassumere a Gemma 4 31B (Gemini API,
livello gratuito) le notizie nuove e rigenera docs/index.html.
Se GEMINI_API_KEY manca usa gli estratti dei feed.
"""
import hashlib
import html
import json
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import feedparser
import requests

ROOT = Path(__file__).parent
CONFIG = json.loads((ROOT / "feeds.json").read_text(encoding="utf-8"))
NEWS_FILE = ROOT / "data" / "news.json"
SEEN_FILE = ROOT / "data" / "seen.json"
TEMPLATE = ROOT / "template.html"
OUTPUT = ROOT / "docs" / "index.html"

# modelli provati in ordine: se uno è sovraccarico o esaurito si passa al successivo
DEFAULT_MODELS = ["gemma-4-31b-it", "gemma-4-26b-a4b-it", "gemini-3.1-flash-lite"]
MODELS = [m.strip() for m in (os.environ.get("RASSEGNA_MODEL") or ",".join(CONFIG.get("modelli", DEFAULT_MODELS))).split(",") if m.strip()]
DEAD_MODELS = set()
API_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
MAX_AGE_HOURS = 48          # considera solo articoli recenti
MAX_CANDIDATES = 30         # quanti articoli per settore mandare al modello
UA = "Mozilla/5.0 (RassegnaBot; uso personale)"
REDDIT_UA = "python:news4u-rassegna:v1.0 (uso personale, non commerciale)"
NOW = datetime.now(timezone.utc)
GIORNI = ["lunedì", "martedì", "mercoledì", "giovedì", "venerdì", "sabato", "domenica"]
MESI = ["gennaio", "febbraio", "marzo", "aprile", "maggio", "giugno", "luglio",
        "agosto", "settembre", "ottobre", "novembre", "dicembre"]


def log(*a):
    print(*a, file=sys.stderr, flush=True)


def load(path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def clean(text, limit=600):
    text = re.sub(r"<[^>]+>", " ", text or "")
    text = html.unescape(re.sub(r"\s+", " ", text)).strip()
    return text[:limit]


def uid(url):
    return hashlib.sha1(url.encode()).hexdigest()[:16]


def entry_image(e):
    for key in ("media_content", "media_thumbnail"):
        for m in e.get(key, []) or []:
            if m.get("url"):
                return m["url"]
    for l in e.get("links", []) or []:
        if l.get("rel") == "enclosure" and str(l.get("type", "")).startswith("image"):
            return l.get("href")
    raw = (e.get("summary") or "") + "".join(c.get("value", "") for c in e.get("content", []) or [])
    m = re.search(r'<img[^>]+src="([^"]+)"', raw)
    return m.group(1) if m else None


def og_image(url):
    if any(d in url for d in ("news.google.com", "x.com/", "reddit.com/")):
        return None
    try:
        r = requests.get(url, headers={"User-Agent": UA}, timeout=8)
        m = re.search(r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)', r.text[:200000]) \
            or re.search(r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:image', r.text[:200000])
        return html.unescape(m.group(1)) if m else None
    except Exception:
        return None


def collect(section, seen):
    items = {}
    max_age = timedelta(hours=section.get("max_age_hours", MAX_AGE_HOURS))
    for url in section["feeds"]:
        try:
            if url.startswith("file://"):
                feed = feedparser.parse(url)
            else:
                # scarico con requests (usa i certificati di certifi: evita gli errori SSL del Mac)
                is_reddit = "reddit.com" in url
                for attempt in range(3):
                    if is_reddit:
                        time.sleep(3)   # Reddit limita le richieste ravvicinate
                    r = requests.get(url, headers={"User-Agent": REDDIT_UA if is_reddit else UA}, timeout=20)
                    if r.status_code == 429 and attempt < 2:
                        log(f"  … {url.split('/')[2]} chiede di rallentare, riprovo")
                        time.sleep(15 * (attempt + 1))
                        continue
                    break
                r.raise_for_status()
                feed = feedparser.parse(r.content)
        except Exception as ex:
            log(f"  ! feed non leggibile {url}: {type(ex).__name__}: {str(ex)[:150]}")
            continue
        if not feed.entries:
            reason = f" ({feed.get('bozo_exception')})" if feed.bozo else ""
            log(f"  ! feed senza articoli: {url}{reason}")
            continue
        feed_title = clean(feed.feed.get("title", ""), 80)
        for e in feed.entries:
            link = e.get("link")
            if not link or uid(link) in seen:
                continue
            t = e.get("published_parsed") or e.get("updated_parsed")
            pub = datetime.fromtimestamp(time.mktime(t), timezone.utc) if t else NOW
            if NOW - pub > max_age:
                continue
            title = clean(e.get("title"), 300)
            source = clean((e.get("source") or {}).get("title", ""), 80) or feed_title
            m_sub = re.search(r"reddit\.com/r/([^/]+)/", link)
            if m_sub:
                source = f"r/{m_sub.group(1)}"
            if "news.google.com" in url and " - " in title:   # Google News: "Titolo - Fonte"
                title, _, src2 = title.rpartition(" - ")
                source = source or src2
            items[uid(link)] = {
                "id": uid(link), "url": link, "title": title, "source": source or "Fonte",
                "published": pub.isoformat(), "snippet": clean(e.get("summary")),
                "image": entry_image(e),
            }
    out = sorted(items.values(), key=lambda x: x["published"], reverse=True)
    return out[:MAX_CANDIDATES]


# ---------- In tendenza: Hacker News (gratis) + X (opzionale, a pagamento) ----------

def fmt_n(n):
    return f"{int(n):,}".replace(",", ".")


def collect_hn(seen, cfg):
    out = []
    if not cfg.get("hacker_news", True):
        return out
    try:
        r = requests.get("https://hn.algolia.com/api/v1/search",
                         params={"tags": "front_page", "hitsPerPage": 60}, timeout=20)
        r.raise_for_status()
        for h in r.json().get("hits", []):
            pts = h.get("points") or 0
            if pts < cfg.get("hn_min_points", 150):
                continue
            url = h.get("url") or f"https://news.ycombinator.com/item?id={h['objectID']}"
            if uid(url) in seen:
                continue
            ts = h.get("created_at_i") or NOW.timestamp()
            out.append({
                "id": uid(url), "url": url, "title": clean(h.get("title"), 300),
                "source": "Hacker News",
                "published": datetime.fromtimestamp(ts, timezone.utc).isoformat(),
                "snippet": clean(h.get("story_text") or h.get("title")), "image": None,
                "metric": f"{fmt_n(pts)} punti, {fmt_n(h.get('num_comments') or 0)} commenti",
                "score": pts,
            })
    except Exception as ex:
        log(f"  ! Hacker News non disponibile: {type(ex).__name__}: {str(ex)[:150]}")
    return sorted(out, key=lambda x: x["score"], reverse=True)


def collect_x(seen, cfg):
    token = os.environ.get("X_BEARER_TOKEN")
    if not token or not cfg.get("x_query"):
        return []
    out = []
    try:
        r = requests.get(
            "https://api.x.com/2/tweets/search/recent",
            headers={"Authorization": f"Bearer {token}"},
            params={
                "query": cfg["x_query"],
                "max_results": max(10, min(100, cfg.get("x_max", 25))),  # ogni post letto si paga
                "sort_order": "relevancy",
                "tweet.fields": "created_at,public_metrics",
                "expansions": "author_id",
                "user.fields": "username,name",
            },
            timeout=30,
        )
        if not r.ok:
            raise RuntimeError(f"HTTP {r.status_code}: {r.text[:200]}")
        js = r.json()
        users = {u["id"]: u for u in js.get("includes", {}).get("users", [])}
        for t in js.get("data", []):
            m = t.get("public_metrics", {})
            likes, reposts = m.get("like_count", 0), m.get("retweet_count", 0)
            if likes < cfg.get("x_min_likes", 100):
                continue
            handle = users.get(t.get("author_id"), {}).get("username", "")
            url = f"https://x.com/{handle or 'i'}/status/{t['id']}"
            if uid(url) in seen:
                continue
            text = clean(re.sub(r"https?://\S+", "", t.get("text", "")), 600)
            out.append({
                "id": uid(url), "url": url, "title": text[:140], "source": f"@{handle} su X",
                "published": t.get("created_at") or NOW.isoformat(), "snippet": text, "image": None,
                "metric": f"{fmt_n(likes)} like, {fmt_n(reposts)} repost",
                "score": likes + 2 * reposts,
            })
    except Exception as ex:
        log(f"  ! X non disponibile: {type(ex).__name__}: {str(ex)[:150]}")
    return sorted(out, key=lambda x: x["score"], reverse=True)


def reddit_rss(subs, seen):
    """Una sola richiesta RSS per tutti i subreddit (r/A+B+C), già ordinata per voti del giorno.
    Le API JSON di Reddit bloccano le richieste senza chiave, l'RSS no."""
    url = f"https://www.reddit.com/r/{'+'.join(subs)}/top/.rss"
    for attempt in range(3):
        r = requests.get(url, params={"t": "day", "limit": 40},
                         headers={"User-Agent": REDDIT_UA}, timeout=20)
        if r.status_code == 429:
            wait = int(r.headers.get("Retry-After", "0") or 0) or 15 * (attempt + 1)
            log(f"  … Reddit chiede di rallentare, riprovo tra {wait}s")
            time.sleep(min(wait, 60))
            continue
        r.raise_for_status()
        break
    else:
        raise RuntimeError("Reddit continua a rispondere 429 (troppe richieste)")
    feed = feedparser.parse(r.content)
    per_sub = {}
    for i, e in enumerate(feed.entries):
        url = e.get("link")
        if not url or uid(url) in seen:
            continue
        m = re.search(r"/r/([^/]+)/", url)
        sub = m.group(1) if m else "reddit"
        t = e.get("updated_parsed") or e.get("published_parsed")
        pub = datetime.fromtimestamp(time.mktime(t), timezone.utc) if t else NOW
        raw = (e.get("content") or [{}])[0].get("value", "") or e.get("summary", "")
        per_sub.setdefault(sub, []).append({
            "id": uid(url), "url": url, "title": clean(e.get("title"), 300), "source": f"r/{sub}",
            "published": pub.isoformat(), "snippet": clean(raw) or clean(e.get("title")),
            "image": entry_image(e), "metric": None, "score": 1000 - i,
        })
    return per_sub


def collect_reddit(seen, cfg):
    """Un elenco per subreddit, ognuno in ordine di popolarità del giorno."""
    subs = cfg.get("reddit_subs", [])
    if not subs:
        return []
    try:
        per_sub = reddit_rss(subs, seen)
    except Exception as ex:
        log(f"  ! Reddit non disponibile: {type(ex).__name__}: {str(ex)[:150]}")
        return []
    for sub, items in per_sub.items():
        log(f"  r/{sub}: {len(items)} post")
    return list(per_sub.values())


def collect_trends(seen, cfg):
    sources = [collect_x(seen, cfg), *collect_reddit(seen, cfg), collect_hn(seen, cfg)]
    sources = [l for l in sources if l]
    mixed, ids = [], set()
    for i in range(max((len(l) for l in sources), default=0)):   # un po' da ogni fonte, a turno
        for l in sources:
            if i < len(l) and l[i]["id"] not in ids:
                ids.add(l[i]["id"]); mixed.append(l[i])
    return mixed[:MAX_CANDIDATES]


SYSTEM = (
    "Sei il redattore di una rassegna stampa quotidiana scritta interamente in italiano. "
    "Ricevi un elenco numerato di articoli candidati per un settore, in qualsiasi lingua. "
    "Scegli le notizie davvero rilevanti per il settore indicato, scarta quelle fuori tema, "
    "promozionali o duplicate (se più articoli parlano dello stesso fatto tienine uno solo). "
    "Per ogni notizia scelta scrivi un titolo in italiano chiaro e informativo (massimo 110 caratteri) "
    "e un riassunto in italiano di 2-3 frasi con parole tue, fattuale, senza citazioni letterali "
    "e senza aggiungere fatti che non sono nel testo fornito. Traduci sempre in italiano, "
    "anche se il candidato è in inglese o in un'altra lingua. "
    "Se un candidato è un post di Reddit, di X o una discussione, il titolo deve dire di cosa parla, non citarlo. "
    "Rispondi SOLO in questo formato, un blocco per notizia, dal più importante, senza nessun altro testo:\n"
    "N: <numero del candidato>\n"
    "TITOLO: <titolo in italiano>\n"
    "RIASSUNTO: <riassunto in italiano>\n"
    "(lascia una riga vuota tra un blocco e l'altro)"
)


def fallback(cands, limit):
    return [dict(c, title_it=c["title"], summary=c["snippet"][:320]) for c in cands[:limit]]


def api_error(r):
    try:
        return r.json()["error"]["message"][:160]
    except Exception:
        return r.text[:160]


def ask_model(prompt, key):
    """Chiama la Gemini API provando i modelli in ordine; salta quelli che non rispondono."""
    body = {
        # Gemma non accetta istruzioni di sistema separate: vanno nel messaggio
        "contents": [{"role": "user", "parts": [{"text": SYSTEM + "\n\n" + prompt}]}],
        "generationConfig": {"temperature": 0.3, "maxOutputTokens": 8192},
    }
    last = "nessun modello disponibile"
    for model in MODELS:
        if model in DEAD_MODELS:
            continue
        for attempt in range(2):
            try:
                r = requests.post(API_URL.format(model=model), json=body, timeout=120,
                                  headers={"x-goog-api-key": key})
            except requests.RequestException as ex:
                last = f"{model}: {type(ex).__name__}"
                log(f"  … {model} non raggiungibile, riprovo")
                time.sleep(10)
                continue
            if r.status_code in (429, 500, 503):
                last = f"{model}: HTTP {r.status_code} {api_error(r)}"
                if attempt == 0:
                    log(f"  … {model} risponde {r.status_code}, riprovo tra 15s")
                    time.sleep(15)
                continue
            if not r.ok:
                last = f"{model}: HTTP {r.status_code} {api_error(r)}"
                break
            try:
                parts = r.json()["candidates"][0]["content"]["parts"]
            except (KeyError, IndexError, ValueError):
                last = f"{model}: risposta vuota"
                continue
            if model != MODELS[0]:
                log(f"  (usato {model})")
            # se il modello restituisce anche il ragionamento, teniamo solo la risposta
            return "".join(p.get("text", "") for p in parts if not p.get("thought"))
        DEAD_MODELS.add(model)
        log(f"  ! {last} → per oggi salto {model}")
    raise RuntimeError(last)


BLOCK_RE = re.compile(
    r"N\s*[:=.]\s*(\d+)\s*\n+\s*TITOLO\s*:\s*(.+?)\s*\n+\s*RIASSUNTO\s*:\s*(.+?)"
    r"(?=\n\s*\n|\n\s*N\s*[:=.]\s*\d|\Z)", re.S | re.I)


def parse_items(text):
    """Legge i blocchi N/TITOLO/RIASSUNTO; se il modello ha risposto in JSON, prova anche quello."""
    text = text.replace("**", "").replace("\r", "")
    items = [{"n": int(m.group(1)), "titolo": clean(m.group(2), 200), "riassunto": clean(m.group(3), 800)}
             for m in BLOCK_RE.finditer(text)]
    if items:
        return items
    try:
        data = json.loads(text[text.find("{"): text.rfind("}") + 1])
        return data.get("items", [])
    except Exception:
        return []


def summarize(section, cands, limit):
    key = os.environ.get("GEMINI_API_KEY")
    if not key or not cands:
        return fallback(cands, limit)
    lines = [f'{i+1}. [{c["source"]}] {c["title"]}\n   {c["snippet"][:400]}' for i, c in enumerate(cands)]
    prompt = (f"Settore: {section['name']} — {section['topic']}.\n"
              f"Scegli al massimo {limit} notizie.\n\nCandidati:\n" + "\n".join(lines))
    try:
        for attempt in range(2):
            text = ask_model(prompt, key)
            parsed = parse_items(text)
            if parsed:
                break
            log(f"  … risposta non leggibile ({text[:80]!r}), riprovo")
            time.sleep(4)
        else:
            raise RuntimeError("il modello non ha rispettato il formato")
        out, used = [], set()
        for it in parsed:
            try:
                n = int(it.get("n", 0)) - 1
            except (TypeError, ValueError):
                continue
            if 0 <= n < len(cands) and n not in used:
                used.add(n)
                out.append(dict(cands[n], title_it=it.get("titolo") or cands[n]["title"],
                                summary=it.get("riassunto") or cands[n]["snippet"][:320]))
            if len(out) >= limit:
                break
        time.sleep(4)   # resta largo sotto il limite di richieste al minuto
        return out
    except Exception as ex:
        log(f"  ! modello non disponibile per {section['id']} ({ex}): uso gli estratti")
        return fallback(cands, limit)


def it_date(iso):
    d = datetime.fromisoformat(iso)
    return f"{d.day} {MESI[d.month - 1]}"


def add_items(news, section_id, items, today):
    for rank, it in enumerate(items):
        if not it.get("image"):
            it["image"] = og_image(it["url"])
        news.append({
            "id": it["id"], "section": section_id, "t": it["title_it"], "s": it["summary"],
            "src": it["source"], "u": it["url"], "img": it.get("image"), "m": it.get("metric"),
            "r": rank, "published": it["published"], "added": today,
        })


def roman(n):
    out = ""
    for v, r in ((1000, "M"), (900, "CM"), (500, "D"), (400, "CD"), (100, "C"), (90, "XC"),
                 (50, "L"), (40, "XL"), (10, "X"), (9, "IX"), (5, "V"), (4, "IV"), (1, "I")):
        while n >= v:
            out += r
            n -= v
    return out


def edition():
    """Anno e numero dell'edizione contati dalla data di fondazione in feeds.json."""
    try:
        start = datetime.fromisoformat(CONFIG.get("fondazione", "")).date()
    except ValueError:
        return ""
    days = (NOW.date() - start).days
    if days < 0:
        return ""
    return f"Anno {roman(days // 365 + 1)}, n. {days + 1}"


def main():
    news = load(NEWS_FILE, [])
    seen = load(SEEN_FILE, {})
    limit = CONFIG.get("max_per_settore", 6)
    today = NOW.date().isoformat()

    for s in CONFIG["sections"]:
        log(f"- {s['name']}")
        cands = collect(s, seen)
        log(f"  {len(cands)} candidati nuovi")
        for c in cands:                 # tutti i candidati valutati non vengono riproposti
            seen[c["id"]] = today
        add_items(news, s["id"], summarize(s, cands, limit), today)

    tcfg = CONFIG.get("tendenze", {})
    if tcfg.get("enabled", True):
        tsec = {"id": "tendenze", "name": tcfg.get("name", "In tendenza"),
                "topic": tcfg.get("topic", "nuove tecnologie e AI")}
        log(f"- {tsec['name']}")
        cands = collect_trends(seen, tcfg)
        log(f"  {len(cands)} candidati nuovi")
        for c in cands:
            seen[c["id"]] = today
        add_items(news, "tendenze", summarize(tsec, cands, tcfg.get("max", 8)), today)

    keep_from = (NOW - timedelta(days=CONFIG.get("giorni_in_pagina", 7))).date().isoformat()
    news = [n for n in news if n["added"] >= keep_from]
    seen_from = (NOW - timedelta(days=30)).date().isoformat()
    seen = {k: v for k, v in seen.items() if v >= seen_from}

    NEWS_FILE.parent.mkdir(exist_ok=True)
    NEWS_FILE.write_text(json.dumps(news, ensure_ascii=False, indent=1), encoding="utf-8")
    SEEN_FILE.write_text(json.dumps(seen, indent=0), encoding="utf-8")

    for n in news:
        n["d"] = it_date(n["published"])
    sections = [{k: s[k] for k in ("id", "name", "motif")} for s in CONFIG["sections"]]
    if CONFIG.get("tendenze", {}).get("enabled", True):
        sections.append({"id": "tendenze", "name": CONFIG["tendenze"].get("name", "In tendenza"),
                         "motif": "nodes"})
    payload = {
        "title": CONFIG.get("titolo", "News4u"),
        "updated": f"{NOW.day} {MESI[NOW.month - 1]} {NOW.year}",
        "date_long": f"{GIORNI[NOW.weekday()].capitalize()} {NOW.day} {MESI[NOW.month - 1]} {NOW.year}",
        "direttore": CONFIG.get("direttore", ""),
        "edition": edition(),
        "sections": sections,
        "news": sorted(news, key=lambda n: (n["added"], n["published"]), reverse=True),
    }
    data = json.dumps(payload, ensure_ascii=False).replace("</", "<\\/")
    page = TEMPLATE.read_text(encoding="utf-8").replace("/*__DATA__*/null", data)
    OUTPUT.parent.mkdir(exist_ok=True)
    OUTPUT.write_text(page, encoding="utf-8")
    log(f"Fatto: {len(news)} notizie in pagina")


if __name__ == "__main__":
    main()
