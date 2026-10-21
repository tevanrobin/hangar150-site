"""Builds N325CL/flights.json: completed flights from adsb.lol's open ADS-B history (ODbL, credited on the page).

Runs once a day (GitHub Actions). adsb.lol publishes each UTC day's full history as a GitHub release
(github.com/adsblol/globe_history_YYYY, split tar parts). For every day not yet logged, this streams that
day's archive and keeps only N325CL's trace, splits it into flights, names the airports at each end, and
merges the completed flights into the log. (Since 2026-10-18 the live trace files on adsb.lol sit behind
a bot challenge, so the published archive is the sanctioned source.)
Flights still in the air are left out on purpose: the page shows where the plane has been, never where it is.
usage: python3 tools/flights.py              catch up through yesterday (UTC)
       python3 tools/flights.py trace.json   build from a saved trace file (testing)"""
import gzip, json, math, os, subprocess, sys, time, urllib.error, urllib.request
from datetime import date, datetime, timedelta, timezone

HEX = 'a38120'                      # N325CL's Mode S address
SINCE = '2026-10-11'                # the log starts the day the sale page went up
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, 'N325CL', 'flights.json')
MEMBER = f'*traces/{HEX[-2:]}/trace_full_{HEX}.json'
FIRST_DAY = '2026-10-14'            # first day the old live-feed fetch missed; earlier days are already logged

GAP = 15 * 60          # silence longer than this ends a flight (landed, or out of coverage)
STOP = 5 * 60          # this long on the ground between airborne points is a stop, not a touch-and-go
MIN_AIR = 4 * 60       # shorter airborne runs are noise, not flights
SETTLE = 45 * 60       # a flight counts as finished once its last point is this old
NEAR_NM = 6            # an airport this close to the first/last point is where the flight began/ended


def parse_trace(raw):
    if not raw: return []
    if raw[:2] == b'\x1f\x8b': raw = gzip.decompress(raw)
    j = json.loads(raw)
    t0 = j['timestamp']
    return [(t0 + p[0], p[1], p[2], p[3]) for p in j.get('trace', [])]


def github(path):
    req = urllib.request.Request('https://api.github.com/' + path, headers={'Accept': 'application/vnd.github+json'})
    if os.environ.get('GITHUB_TOKEN'): req.add_header('Authorization', 'Bearer ' + os.environ['GITHUB_TOKEN'])
    try:
        return json.load(urllib.request.urlopen(req, timeout=30))
    except urllib.error.HTTPError as e:
        if e.code == 404: return None
        raise


def day_parts(day):
    """download URLs of the day's archive parts, or None if adsb.lol has not published that day yet.
    Prod is normally complete; if staging is clearly bigger (prod was down), use staging."""
    best = None
    for kind in ('prod', 'staging'):
        rel = github(f'repos/adsblol/globe_history_{day.year}/releases/tags/v{day:%Y.%m.%d}-planes-readsb-{kind}-0')
        if not rel or not rel['assets']: continue
        parts = sorted(rel['assets'], key=lambda a: a['name'])
        size = sum(a['size'] for a in parts)
        if best is None or size > best[0] * 1.2: best = (size, [a['browser_download_url'] for a in parts])
    return best and best[1]


def day_trace(day):
    """N325CL's track points for one UTC day ([] if it did not fly), or None if the day is not published yet.
    The parts are one tar split into pieces: stream them in order through tar and keep the one member."""
    urls = day_parts(day)
    if urls is None: return None
    curl = subprocess.Popen(['curl', '-sfL', '--retry', '3', *urls], stdout=subprocess.PIPE)
    tar = subprocess.run(['tar', '-xOf', '-', '--wildcards', '--occurrence=1', MEMBER],
                         stdin=curl.stdout, capture_output=True)
    curl.stdout.close(); curl.wait()
    if tar.returncode and not tar.stdout:
        if b'Not found in archive' not in tar.stderr or curl.returncode not in (0, 23):
            raise RuntimeError(f'{day}: curl {curl.returncode}, tar {tar.returncode}: {tar.stderr.decode()[-300:]}')
    print(f'{day}: {len(urls)} archive part(s), trace {len(tar.stdout)} bytes')
    return parse_trace(tar.stdout)


def nm(a, b):
    la1, lo1, la2, lo2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    return 3440.065 * 2 * math.asin(math.sqrt(h))


AIRPORTS = json.load(open(os.path.join(ROOT, 'tools', 'airports-us.json')))
def airport(lat, lon):
    best = min(AIRPORTS, key=lambda a: nm((lat, lon), (a[4], a[5])))
    d = nm((lat, lon), (best[4], best[5]))
    if d <= NEAR_NM: return {'code': best[0], 'city': best[2], 'st': best[3], 'll': [best[4], best[5]]}
    return {'code': None, 'city': best[2], 'st': best[3], 'near': True, 'll': [round(lat, 3), round(lon, 3)]}


def split(points):
    """track points -> list of flights, each a list of points from first movement to last airborne point"""
    runs, cur = [], []
    for p in points:
        if cur and p[0] - cur[-1][0] > GAP: runs.append(cur); cur = []
        cur.append(p)
    if cur: runs.append(cur)
    flights = []
    for run in runs:            # inside one unbroken run, a long ground stretch between airborne points is a stop
        seg, ground_since = [], None
        for p in run:
            on_ground = p[3] == 'ground'
            if on_ground:
                if ground_since is None: ground_since = p[0]
                if seg and any(q[3] != 'ground' for q in seg) and p[0] - ground_since > STOP:
                    flights.append(seg); seg = []
            else: ground_since = None
            seg.append(p)
        if seg: flights.append(seg)
    out = []
    for seg in flights:
        air = [p for p in seg if p[3] != 'ground']
        if len(air) < 2 or air[-1][0] - air[0][0] < MIN_AIR: continue
        out.append((seg, air))
    return out


def build(points, now, end=None):
    """completed flights in the points; `end` is where the data stops (a day's archive ends at midnight UTC),
    so a flight still airborne close to it is left for the next day, which carries this day's points along"""
    log = []
    for seg, air in split(points):
        if now - seg[-1][0] < SETTLE and seg[-1][3] != 'ground': continue     # still flying, or just landed
        if end and end - seg[-1][0] < GAP and seg[-1][3] != 'ground': continue  # crosses midnight UTC
        off, on = air[0][0], air[-1][0]
        log.append({'off': int(off), 'on': int(on), 'mins': round((on - off) / 60),
                    'from': airport(seg[0][1], seg[0][2]), 'to': airport(seg[-1][1], seg[-1][2])})
    return log


def merge(by_off, found, since):
    for f in found:
        if f['off'] < since: continue
        # the same flight seen in two runs can shift by a few seconds at the edges: match within 10 minutes
        same = next((k for k in by_off if abs(k - f['off']) < 600), None)
        if same is not None: del by_off[same]
        by_off[f['off']] = f


def main():
    now = time.time()
    old = json.load(open(OUT)) if os.path.exists(OUT) else {'flights': []}
    since = datetime.fromisoformat(SINCE).replace(tzinfo=timezone.utc).timestamp() - 12 * 3600
    by_off = {f['off']: f for f in old['flights']}
    through = old.get('through')
    if len(sys.argv) > 1:
        merge(by_off, build(parse_trace(open(sys.argv[1], 'rb').read()), now), since)
    else:
        day = date.fromisoformat(through) + timedelta(1) if through else date.fromisoformat(FIRST_DAY)
        yesterday = datetime.now(timezone.utc).date() - timedelta(1)
        prev = None                          # the day before's points, for a flight that crosses midnight UTC
        while day <= yesterday:
            pts = day_trace(day)
            if pts is None: print(f'{day}: not published yet'); break
            start = pts
            if pts and pts[0][3] != 'ground':            # the day opens in the air: a flight from yesterday
                if prev is None: prev = day_trace(day - timedelta(1)) or []
                start = prev + pts
            end = datetime(day.year, day.month, day.day, tzinfo=timezone.utc).timestamp() + 86400
            merge(by_off, build(start, now, end), since)
            prev, through = pts, day.isoformat()
            day += timedelta(1)
    flights = sorted(by_off.values(), key=lambda f: -f['off'])
    # 'updated' changes on every run so the page can say when it was last refreshed;
    # 'through' is the last UTC day read from the archive
    data = {'since': SINCE, 'source': 'adsb.lol (ODbL)', 'updated': int(now), 'through': through, 'flights': flights}
    json.dump(data, open(OUT, 'w'), indent=1)
    print(f'flights.json: {len(flights)} flights')


if __name__ == '__main__':
    main()
