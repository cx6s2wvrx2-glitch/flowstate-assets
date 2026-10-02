# -*- coding: utf-8 -*-
"""Flowstate IG publisher — 1 πρωί + 1 απόγευμα/μέρα, ανθεκτικό στις καθυστερήσεις cron."""
import os, json, time, datetime, subprocess
import requests
from zoneinfo import ZoneInfo

GRAPH = "https://graph.facebook.com/v21.0"
IG_USER_ID = "17841465653746505"
TOKEN = os.environ["IG_ACCESS_TOKEN"]
QUEUE = "posts/queue.json"
TZ = ZoneInfo("Europe/Athens")
AM_WINDOW = range(6, 13)    # πρωί: 06:00–12:59 Ελλάδας
PM_WINDOW = range(13, 23)   # απόγευμα/βράδυ: 13:00–22:59 Ελλάδας

def now(): return datetime.datetime.now(datetime.timezone.utc)
def load():
    with open(QUEUE, encoding="utf-8") as f: return json.load(f)
def save(q):
    with open(QUEUE, "w", encoding="utf-8") as f: json.dump(q, f, ensure_ascii=False, indent=2)

def slots_published_today(q, a):
    slots = set()
    for p in q:
        pa = p.get("published_at")
        if not pa: continue
        try:
            d = datetime.datetime.fromisoformat(pa)
            if d.tzinfo is None: d = d.replace(tzinfo=datetime.timezone.utc)
            la = d.astimezone(TZ)
            if la.date() == a.date():
                slots.add("am" if la.hour < 13 else "pm")
        except Exception: pass
    return slots

def resolve_token():
    global TOKEN, IG_USER_ID
    try:
        r = requests.get(f"{GRAPH}/me/accounts", params={
            "fields": "name,access_token,instagram_business_account{id,username}",
            "access_token": TOKEN, "limit": 100}, timeout=60)
        data = r.json().get("data", []) if r.ok else []
    except Exception as e:
        print("resolve_token warn:", e); data = []
    for pg in data:
        iba = pg.get("instagram_business_account") or {}
        if str(iba.get("id")) == str(IG_USER_ID) or iba.get("username") == "flow_state_swim_lab":
            if iba.get("id"): IG_USER_ID = str(iba["id"])
            if pg.get("access_token"): TOKEN = pg["access_token"]
            print("Resolved PAGE token for IG", IG_USER_ID); return
    print("No matching page — using provided token.")

def _post(path, data):
    r = requests.post(f"{GRAPH}/{path}", data={**data, "access_token": TOKEN}, timeout=60)
    if not r.ok: raise RuntimeError(f"{r.status_code} {r.text[:300]}")
    return r.json()
def _get(path, params):
    r = requests.get(f"{GRAPH}/{path}", params={**params, "access_token": TOKEN}, timeout=60)
    if not r.ok: raise RuntimeError(f"{r.status_code} {r.text[:300]}")
    return r.json()

def wait_ready(cid, timeout=420):
    t0 = time.time()
    while time.time() - t0 < timeout:
        sc = _get(cid, {"fields": "status_code"}).get("status_code")
        if sc == "FINISHED": return
        if sc == "ERROR": raise RuntimeError("container ERROR")
        time.sleep(6)
    raise TimeoutError("container not ready")

def publish(p):
    fmt, cap, media = p["format"], p.get("caption", ""), p["media"]
    if fmt == "carousel":
        ch = [_post(f"{IG_USER_ID}/media", {"image_url": u, "is_carousel_item": "true"})["id"] for u in media]
        cid = _post(f"{IG_USER_ID}/media", {"media_type": "CAROUSEL", "children": ",".join(ch), "caption": cap})["id"]
        try: wait_ready(cid, 150)
        except Exception: pass
    elif fmt == "image":
        cid = _post(f"{IG_USER_ID}/media", {"image_url": media[0], "caption": cap})["id"]
    elif fmt == "reel":
        cid = _post(f"{IG_USER_ID}/media", {"media_type": "REELS", "video_url": media[0], "caption": cap})["id"]
        wait_ready(cid, 420)
    else:
        raise ValueError(f"unknown format: {fmt}")
    return _post(f"{IG_USER_ID}/media_publish", {"creation_id": cid})["id"]

def git(*a): subprocess.run(["git", *a], check=True)
def commit(msg):
    git("config", "user.name", "flowstate-bot"); git("config", "user.email", "bot@flowstate.local")
    git("add", QUEUE); git("commit", "-m", msg); git("push")

def main():
    a = datetime.datetime.now(TZ)
    event = os.environ.get("GITHUB_EVENT_NAME", "")
    resolve_token()
    q = load(); n = now()

    # Χειροκίνητο = πάντα· προγραμματισμένο = 1 πρωί + 1 απόγευμα, ανά παράθυρο
    if event == "schedule":
        if a.hour in AM_WINDOW: slot = "am"
        elif a.hour in PM_WINDOW: slot = "pm"
        else:
            print(f"Εκτός παραθύρου (Athens hour {a.hour})."); return
        if slot in slots_published_today(q, a):
            print(f"Ήδη ανέβηκε στο slot {slot} σήμερα. Skip."); return

    due = [p for p in q if p.get("status") == "pending"
           and datetime.datetime.fromisoformat(p["scheduled_at"].replace("Z", "+00:00")) <= n]
    if not due:
        print("Nothing due."); return
    due.sort(key=lambda p: p["scheduled_at"])
    p = due[0]
    print(f"Due: {p['id']} ({p['format']})")
    p["status"] = "publishing"; save(q)
    try: commit(f"lock {p['id']}")
    except Exception as e: print("Lock failed:", e); return
    try:
        mid = publish(p)
        p["status"] = "published"; p["ig_post_id"] = mid; p["published_at"] = n.isoformat()
        print("PUBLISHED:", mid)
    except Exception as e:
        p["status"] = "failed"; p["error"] = str(e)[:400]; print("FAILED:", e)
    save(q); commit(f"{p['status']} {p['id']}")

if __name__ == "__main__":
    main()
