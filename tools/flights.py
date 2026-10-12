"""Builds N325CL/flights.json: completed flights from the open adsb.lol ADS-B feed (ODbL, credited on the page).

Runs once a day (GitHub Actions). adsb.lol keeps roughly the last day of track per aircraft, so each run
splits that track into flights, names the airports at each end, and merges new completed flights into the log.
Flights still in the air are left out on purpose: the page shows where the plane has been, never where it is.
usage: python3 tools/flights.py [trace.json]   (a saved trace file is for testing)"""
import gzip, json, math, os, sys, time, urllib.request
from datetime import datetime, timezone

HEX = 'a38120'                      # N325CL's Mode S address
SINCE = '2026-10-11'                # the log starts the day the sale page went up
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, 'N325CL', 'flights.json')
URL = f'https://adsb.lol/data/traces/{HEX[-2:]}/trace_full_{HEX}.json'

GAP = 15 * 60          # silence longer than this ends a flight (landed, or out of coverage)
STOP = 5 * 60          # this long on the ground between airborne points is a stop, not a touch-and-go
MIN_AIR = 4 * 60       # shorter airborne runs are noise, not flights
SETTLE = 45 * 60       # a flight counts as finished once its last point is this old
NEAR_NM = 6            # an airport this close to the first/last point is where the flight began/ended


def load_trace(path=None):
    if path: raw = open(path, 'rb').read()
    else:
        req = urllib.request.Request(URL, headers={'User-Agent': 'hangar150.com N325CL flight log'})
        raw = urllib.request.urlopen(req, timeout=30).read()
    if raw[:2] == b'\x1f\x8b': raw = gzip.decompress(raw)
    j = json.loads(raw)
    t0 = j['timestamp']
    return [(t0 + p[0], p[1], p[2], p[3]) for p in j.get('trace', [])]


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


def build(points, now):
    log = []
    for seg, air in split(points):
        if now - seg[-1][0] < SETTLE and seg[-1][3] != 'ground': continue     # still flying, or just landed
        off, on = air[0][0], air[-1][0]
        log.append({'off': int(off), 'on': int(on), 'mins': round((on - off) / 60),
                    'from': airport(seg[0][1], seg[0][2]), 'to': airport(seg[-1][1], seg[-1][2])})
    return log


def main():
    pts = load_trace(sys.argv[1] if len(sys.argv) > 1 else None)
    now = time.time()
    old = json.load(open(OUT)) if os.path.exists(OUT) else {'flights': []}
    since = datetime.fromisoformat(SINCE).replace(tzinfo=timezone.utc).timestamp() - 12 * 3600
    by_off = {f['off']: f for f in old['flights']}
    for f in build(pts, now):
        if f['off'] < since: continue
        # the same flight seen in two runs can shift by a few seconds at the edges: match within 10 minutes
        same = next((k for k in by_off if abs(k - f['off']) < 600), None)
        if same is not None: del by_off[same]
        by_off[f['off']] = f
    flights = sorted(by_off.values(), key=lambda f: -f['off'])
    # 'updated' changes on every run so the page can say when it was last refreshed
    data = {'since': SINCE, 'source': 'adsb.lol (ODbL)', 'updated': int(now), 'flights': flights}
    json.dump(data, open(OUT, 'w'), indent=1)
    print(f'flights.json: {len(flights)} flights')


if __name__ == '__main__':
    main()
