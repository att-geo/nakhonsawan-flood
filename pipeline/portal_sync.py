# -*- coding: utf-8 -*-
"""อัปเดตชั้น "สถานการณ์ล่าสุด" บน ArcGIS Enterprise Portal จากผลรอบรายชั่วโมง (stdlib อย่างเดียว)

    python pipeline/portal_sync.py --site .          # ใช้ data/live/*.json ที่ run_update.py เพิ่งเขียน

ตั้งค่าผ่าน env (GitHub → Settings → Secrets and variables → Actions):
  PORTAL_USERNAME, PORTAL_PASSWORD   บัญชี built-in ที่เป็นเจ้าของ layer หรือ admin
  (หรือ PORTAL_TOKEN ถ้ามี token อยู่แล้ว)
URL ของ portal / service อ่านจาก data/static/portal_items.json (สร้างตอน publish)
อัปเดตเฉพาะ attribute (geometry ไม่เปลี่ยน) ด้วย applyEdits ทีละ 2,000 แถว
"""
import argparse, json, os, sys, time, urllib.parse, urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from portal_layers import hex_status_rows, district_status_rows, load, fetch_live  # noqa: E402

REFERER = os.environ.get("PORTAL_REFERER", "https://att-geo.github.io/nakhonsawan-flood/")


def post(url, data, timeout=180, tries=3):
    body = urllib.parse.urlencode(data).encode()
    for k in range(tries):
        try:
            req = urllib.request.Request(url, body, {"Referer": REFERER, "Content-Type": "application/x-www-form-urlencoded"})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                j = json.loads(r.read().decode("utf-8"))
            if "error" in j: raise RuntimeError(f"{url}: {j['error']}")
            return j
        except Exception as e:
            if k == tries - 1: raise
            print("retry", k + 1, e); time.sleep(5 * (k + 1))


def get_token(portal):
    if os.environ.get("PORTAL_TOKEN"): return os.environ["PORTAL_TOKEN"]
    u, p = os.environ.get("PORTAL_USERNAME"), os.environ.get("PORTAL_PASSWORD")
    if not (u and p): raise SystemExit("ไม่ได้ตั้ง PORTAL_USERNAME / PORTAL_PASSWORD — ข้าม sync")
    j = post(portal.rstrip("/") + "/sharing/rest/generateToken",
             {"username": u, "password": p, "client": "referer", "referer": REFERER, "expiration": 60, "f": "json"})
    return j["token"]


def oid_map(layer_url, key, token):
    oid = post(layer_url, {"f": "json", "token": token}).get("objectIdField", "objectid")
    j = post(layer_url + "/query", {"where": "1=1", "outFields": f"{oid},{key}", "returnGeometry": "false",
                                     "resultRecordCount": 20000, "f": "json", "token": token})
    return {f["attributes"][key]: f["attributes"][oid] for f in j["features"]}, oid


def push(layer_url, rows, key, token, batch=2000):
    m, oid = oid_map(layer_url, key, token); ups = []
    for r in rows:
        if r[key] in m:
            a = {k: v for k, v in r.items() if k.lower() != oid.lower()}; a[oid] = m[r[key]]; ups.append({"attributes": a})
    ok = bad = 0
    for i in range(0, len(ups), batch):
        j = post(layer_url + "/applyEdits", {"updates": json.dumps(ups[i:i + batch], ensure_ascii=False),
                                             "rollbackOnFailure": "false", "f": "json", "token": token})
        for x in j.get("updateResults", []):
            if x.get("success"): ok += 1
            else: bad += 1
    return ok, bad


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--site", default=".")
    ap.add_argument("--live-url", default="", help="ดึง data/live จาก GitHub Pages แทนไฟล์ในเครื่อง")
    a = ap.parse_args()
    cfg = load(os.path.join(a.site, "data", "static", "portal_items.json"))
    live = cfg["live"]; D = fetch_live(a.site, a.live_url)
    params = load(os.path.join(a.site, "data", "static", "params.json"))
    status, meta, districts = D["status"], D["meta"], D["districts"]
    token = get_token(cfg["portal"]); t0 = time.time()
    ok, bad = push(f"{live['url']}/{live['layers']['hex_status']}", hex_status_rows(status, params, meta), "hid", token)
    print(f"hex_status: {ok} updated, {bad} failed")
    ok2, bad2 = push(f"{live['url']}/{live['layers']['district_status']}",
                     list(district_status_rows(districts, meta).values()), "amphoe", token)
    print(f"district_status: {ok2} updated, {bad2} failed ; data at {meta['generated']} ; {time.time() - t0:.0f}s")
    if bad or bad2: sys.exit(1)


if __name__ == "__main__":
    main()
