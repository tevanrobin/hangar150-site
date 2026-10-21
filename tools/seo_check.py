"""SEO health check for hangar150.com/N325CL, run by .github/workflows/seo.yml.

Checks the committed page (title, description, H1, canonical, JSON-LD, offer
expiry) and, when --live is given, that the live page answers 200 with the same
canonical. Exits non-zero on any problem so GitHub emails the repo owner.
--ping sends the page and sitemap to IndexNow (Bing, Yandex, Seznam, Naver...).
"""
import datetime, glob, html as H, json, os, re, sys, urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
URL = "https://hangar150.com/N325CL/"
URLS = [URL, "https://hangar150.com/sitemap.xml"]
MUST = ["SR22", "G3", "Turbo", "N325CL"]


def key():
    for p in glob.glob(os.path.join(ROOT, "*.txt")):
        name = os.path.basename(p)[:-4]
        if re.fullmatch(r"[0-9a-f]{32}", name) and open(p).read().strip() == name:
            return name
    sys.exit("IndexNow key file missing at the site root")


def check(html, where):
    bad = []
    title = re.search(r"<title>(.*?)</title>", html, re.S)
    desc = re.search(r'<meta name="description" content="([^"]*)"', html)
    h1 = re.findall(r"<h1[^>]*>(.*?)</h1>", html, re.S)
    if not title or not all(w in title.group(1) for w in MUST):
        bad.append("title is missing one of " + ", ".join(MUST))
    elif len(title.group(1)) > 70:
        bad.append(f"title is {len(title.group(1))} chars (keep it under 70)")
    if not desc or not 70 <= len(H.unescape(desc.group(1))) <= 160:
        bad.append("meta description missing or not 70-160 chars (Google cuts it off past ~160)")
    if len(h1) != 1:
        bad.append(f"{len(h1)} <h1> tags (want exactly 1)")
    if f'<link rel="canonical" href="{URL}">' not in html:
        bad.append("canonical link missing or changed")
    m = re.search(r'<script type="application/ld\+json">(.*?)</script>', html, re.S)
    try:
        ld = json.loads(m.group(1))
        offer = next(x for x in ld if x.get("@type") == "Product")["offers"]
        until = datetime.date.fromisoformat(offer["priceValidUntil"])
        if until < datetime.date.today() + datetime.timedelta(days=14):
            bad.append(f"offer priceValidUntil {until} is within 2 weeks; move it out")
    except Exception as e:
        bad.append(f"JSON-LD unreadable or incomplete: {e}")
    return [f"{where}: {b}" for b in bad]


def main():
    problems = check(open(os.path.join(ROOT, "N325CL/index.html")).read(), "repo")
    k = key()
    if "--live" in sys.argv:
        req = urllib.request.Request(URL, headers={"User-Agent": "hangar150-seo-check"})
        with urllib.request.urlopen(req, timeout=30) as r:
            problems += check(r.read().decode(), "live")
        req = urllib.request.Request(f"https://hangar150.com/{k}.txt")
        with urllib.request.urlopen(req, timeout=30) as r:
            if r.read().decode().strip() != k:
                problems.append("live: IndexNow key file does not match")
    if "--ping" in sys.argv:
        body = json.dumps({"host": "hangar150.com", "key": k,
                           "keyLocation": f"https://hangar150.com/{k}.txt", "urlList": URLS}).encode()
        req = urllib.request.Request("https://api.indexnow.org/indexnow", data=body,
                                     headers={"Content-Type": "application/json; charset=utf-8"})
        with urllib.request.urlopen(req, timeout=30) as r:
            print("IndexNow:", r.status)
    for p in problems:
        print("PROBLEM:", p)
    if problems:
        sys.exit(1)
    print("SEO check passed")


main()
