#!/usr/bin/env python3
"""Up-and-Coming top-10 forecast (standard library only, deterministic).

One static forecast is scored against every hourly read of the next 72 hours, so for each place we want the share of
that window a game spends there. The model, per game on the chart as it stood when the history ends:

  * an "effective rank": the current rank, pulled up (or down) by its recent momentum, i.e. how far it climbed or
    fell between the last read and the reads about 8 and 24 hours earlier (a game that was not on the chart then
    counts as a strong riser);
  * the chance it is in the top 10 during the window decays with that effective rank (a stretched exponential plus
    a 1/rank tail), and is cut for games whose universe id is old next to the newest id on the chart (the chart
    only lists recent games, and young games grow fastest);
  * given it is in the top 10, its place is a Gaussian around the effective rank (clamped to RMAX), wider for lower
    ranks, mixed with a flat share over the ten places;
  * each place's named mass is capped at SMAX so a little is left for games that are not on the chart yet.

The constants were fitted by walk-forward folds on the kit history (backtest: static forecast from each earlier
snapshot against the following 72 hours of reads).
"""
import json
import math
import os

INPUT_DIR = os.environ.get("MAKEFUN_INPUT_DIR", "/input")
OUTPUT_DIR = os.environ.get("MAKEFUN_OUTPUT_DIR", "/output")

# top-10 membership: exp(-((re-1)/S)**Q) + C/re
S, Q, C = 6.875, 1.3, 1.25
# place spread: sigma = SIG0 + SIG1*sqrt(re-1); centre clamp; flat mix
SIG0, SIG1, RMAX, EPS = 1.062, 0.487, 9.963, 0.501
SMAX = 0.98
# momentum: lags (hours) and weights on log rank improvement; credit for a game new to the chart
D1, B1, D2, B2, NEWB = 8.0, 0.2, 24.0, 0.325, 2.375
# id-age penalty: exp(-AGEPEN * max(0, gap - AGE0)), gap = (newest id - id) / 1e6
AGEPEN, AGE0 = 0.4, 1.837


def parse_ts(s):
    from datetime import datetime, timezone

    return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(timezone.utc).timestamp()


def load_history(path):
    snaps = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                s = json.loads(line)
            except ValueError:
                continue
            rows = s.get("up_and_coming") or []
            if s.get("ok") is False or not rows:
                continue
            rows = sorted(rows, key=lambda r: r["rank"])
            snaps.append((parse_ts(s["at"]), [int(r["universe_id"]) for r in rows]))
    snaps.sort(key=lambda x: x[0])
    return snaps


def nearest_before(snaps, hours):
    """Index of the earlier snapshot whose age (against the last one) is closest to `hours`."""
    if len(snaps) < 2:
        return None
    tl = snaps[-1][0]
    return min(range(len(snaps) - 1), key=lambda j: (abs((tl - snaps[j][0]) / 3600.0 - hours), j))


def forecast(snaps, places):
    chart = snaps[-1][1]
    refs = []
    for hours in (D1, D2):
        j = nearest_before(snaps, hours)
        refs.append(None if j is None else {g: r for r, g in enumerate(snaps[j][1], 1)})
    big = [g for g in chart if g > 10 ** 10]
    maxid = max(big) if big else 0
    fc = {k: {} for k in range(1, places + 1)}
    for r, g in enumerate(chart, 1):
        lr = math.log(r)
        for ref, b in zip(refs, (B1, B2)):
            if ref is None:
                continue
            rr = ref.get(g)
            lr -= b * (math.log(rr / r) if rr else NEWB)
        re = max(1.0, math.exp(lr))
        gap = (maxid - g) / 1e6 if g > 10 ** 10 else 3.0
        pen = math.exp(-AGEPEN * max(0.0, gap - AGE0))
        pin = min(0.995, (math.exp(-(((re - 1.0) / S) ** Q)) + C / re) * pen)
        rc = min(re, RMAX)
        sig = SIG0 + SIG1 * math.sqrt(re - 1.0)
        w = [math.exp(-0.5 * ((k - rc) / sig) ** 2) for k in range(1, places + 1)]
        z = sum(w)
        for k in range(1, places + 1):
            fc[k][g] = pin * ((1.0 - EPS) * w[k - 1] / z + EPS / places)
    for k in fc:
        t = sum(fc[k].values())
        if t > SMAX:
            for g in fc[k]:
                fc[k][g] *= SMAX / t
    return chart, fc


def greedy_top(fc, chart, places):
    """Best-guess order: for each place, the unused game most likely to hold it."""
    used, top = set(), []
    for k in range(1, places + 1):
        cands = [(-fc[k].get(g, 0.0), r, g) for r, g in enumerate(chart) if g not in used]
        g = min(cands)[2]
        used.add(g)
        top.append(g)
    return top


def main():
    with open(os.path.join(INPUT_DIR, "task.json"), encoding="utf-8") as f:
        task = json.load(f)
    places = int(task.get("places", 10))
    snaps = load_history(os.path.join(INPUT_DIR, "history.ndjson"))
    chart, fc = forecast(snaps, places)
    probs = []
    for k in range(1, places + 1):
        items = sorted(fc[k].items(), key=lambda kv: (-kv[1], kv[0]))[:100]
        for g, p in items:
            p = math.floor(p * 1e6) / 1e6
            if p > 0:
                probs.append({"universe_id": g, "place": k, "p": p})
    out = {"probabilities": probs, "top10": greedy_top(fc, chart, places)}
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    with open(os.path.join(OUTPUT_DIR, task.get("output", "forecast.json")), "w", encoding="utf-8") as f:
        f.write(json.dumps(out, separators=(",", ":")) + "\n")


if __name__ == "__main__":
    main()
