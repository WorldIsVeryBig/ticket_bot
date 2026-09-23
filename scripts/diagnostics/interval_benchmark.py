#!/usr/bin/env python3
"""
間隔 vs 403 機率 benchmark — 測試不同 watch interval 的 Cloudflare 被擋率

Usage:
    python scripts/diagnostics/interval_benchmark.py [--rounds 15] [--url AREA_URL]

結果輸出到 data/interval_benchmark.json，可供 RL bandit 參考。
"""

import argparse
import json
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

from curl_cffi import requests


AREA_URL = "https://tixcraft.com/ticket/area/26_ive/1"
INTERVALS = [2, 3, 4, 5, 6, 7, 8, 10]
COOKIE_FILE = Path("tixcraft_cookies.json")
OUTPUT_DIR = Path("data")


def load_cookies():
    if not COOKIE_FILE.exists():
        raise FileNotFoundError(f"找不到 {COOKIE_FILE}")
    data = json.loads(COOKIE_FILE.read_text())
    return {c["name"]: c["value"] for c in data if "tixcraft" in c.get("domain", "")}


def test_interval(session, url: str, interval: float, rounds: int) -> dict:
    ok = 0
    blocked = 0
    errors = 0
    latencies = []

    for i in range(rounds):
        try:
            start = time.perf_counter()
            r = session.get(url, timeout=15)
            latency = (time.perf_counter() - start) * 1000
            latencies.append(latency)

            if r.status_code in (200, 301, 302):
                ok += 1
            elif r.status_code in (401, 403):
                blocked += 1
            else:
                errors += 1

            status_char = "✓" if r.status_code in (200, 301, 302) else "✗"
            print(f"  [{interval}s] #{i+1:2d} {status_char} {r.status_code} ({latency:.0f}ms)")
        except Exception as e:
            errors += 1
            print(f"  [{interval}s] #{i+1:2d} ERR {e}")

        if i < rounds - 1:
            time.sleep(interval)

    total = ok + blocked + errors
    success_rate = ok / total if total > 0 else 0
    avg_latency = sum(latencies) / len(latencies) if latencies else 0

    return {
        "interval": interval,
        "rounds": rounds,
        "ok": ok,
        "blocked": blocked,
        "errors": errors,
        "success_rate": round(success_rate, 3),
        "avg_latency_ms": round(avg_latency, 1),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--rounds", type=int, default=15, help="每個間隔測幾輪")
    parser.add_argument("--url", default=AREA_URL, help="測試 URL")
    parser.add_argument("--intervals", default=None, help="自訂間隔，逗號分隔（如 3,5,7）")
    args = parser.parse_args()

    intervals = INTERVALS
    if args.intervals:
        intervals = [float(x) for x in args.intervals.split(",")]

    jar = load_cookies()

    print(f"間隔 vs 403 Benchmark")
    print(f"URL: {args.url}")
    print(f"間隔: {intervals}")
    print(f"每組: {args.rounds} 輪")
    print(f"預估時間: {sum(i * args.rounds for i in intervals) / 60:.1f} 分鐘")
    print("=" * 50)

    results = []
    for interval in intervals:
        # 每組用新 session 避免累積效應
        session = requests.Session(impersonate="chrome124")
        for k, v in jar.items():
            session.cookies.set(k, v)

        # 冷卻 10 秒再開始下一組
        if results:
            print(f"\n  冷卻 10 秒...")
            time.sleep(10)

        print(f"\n--- interval = {interval}s ---")
        result = test_interval(session, args.url, interval, args.rounds)
        results.append(result)
        print(f"  => 成功率: {result['success_rate']*100:.1f}% | 平均延遲: {result['avg_latency_ms']:.0f}ms")

    # 輸出摘要
    print("\n" + "=" * 50)
    print(f"{'間隔':>6} {'成功率':>8} {'OK':>4} {'403':>4} {'延遲':>8}")
    print("-" * 50)
    for r in results:
        bar = "█" * int(r["success_rate"] * 20)
        print(f"{r['interval']:>5}s {r['success_rate']*100:>6.1f}% {r['ok']:>4} {r['blocked']:>4} {r['avg_latency_ms']:>6.0f}ms {bar}")

    # 儲存結果
    OUTPUT_DIR.mkdir(exist_ok=True)
    tz = timezone(timedelta(hours=8))
    output = {
        "timestamp": datetime.now(tz).isoformat(),
        "url": args.url,
        "rounds_per_interval": args.rounds,
        "environment": "local_direct",
        "results": results,
    }
    out_path = OUTPUT_DIR / "interval_benchmark.json"
    # 追加模式：讀取舊資料合併
    history = []
    if out_path.exists():
        try:
            history = json.loads(out_path.read_text())
            if not isinstance(history, list):
                history = [history]
        except Exception:
            history = []
    history.append(output)
    out_path.write_text(json.dumps(history, ensure_ascii=False, indent=2))
    print(f"\n結果已儲存: {out_path}")


if __name__ == "__main__":
    main()
