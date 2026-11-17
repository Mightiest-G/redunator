import requests, json, time
S = requests.Session(); S.headers.update({"User-Agent":"redunator-phase2d-speed/1.0"})
def get(did, params, label):
    t0=time.time()
    try:
        r = S.get(f"https://data.cityofchicago.org/resource/{did}.json", params=params, timeout=120)
        print(f"[{label}] HTTP {r.status_code} {time.time()-t0:.1f}s :: {r.text[:400]}")
        return r
    except Exception as e:
        print(f"[{label}] ERR {time.time()-t0:.1f}s :: {e!r}")

# 1. metadata columns for one taxi + one tnp dataset
for did in ["ajtu-isnz","6dvr-xwnh"]:
    r = get(did, {}, f"meta-{did}")
    # resource root returns rows, not meta; use api/views instead
    r2 = S.get(f"https://data.cityofchicago.org/api/views/{did}", timeout=60)
    try:
        j=r2.json(); cols=[(c["fieldName"],c.get("dataTypeName")) for c in j["columns"]]
        print(did, [c for c in cols if "trip_" in c[0] or "pickup" in c[0]])
    except Exception as e: print(did,"meta err",e, r2.text[:200])

# 2. dow check: 2026-08-03 is a Monday; 2026-08-01 Saturday
get("ajtu-isnz", {"$select":"date_extract_dow(trip_start_timestamp) as dow, count(*) as n",
  "$where":"trip_start_timestamp >= '2026-08-01T00:00:00' AND trip_start_timestamp < '2026-08-04T00:00:00'",
  "$group":"dow","$limit":"10"}, "dow-2026-08-01..03")
# 3. arithmetic where + sums, 1 week
get("ajtu-isnz", {"$select":"date_extract_dow(trip_start_timestamp) as dow, date_extract_hh(trip_start_timestamp) as hour, pickup_community_area as area, count(*) as n, sum(trip_miles) as sum_miles, sum(trip_seconds) as sum_seconds",
  "$where":"trip_start_timestamp >= '2026-08-03T00:00:00' AND trip_start_timestamp < '2026-08-10T00:00:00' AND trip_seconds > 0 AND trip_miles > 0 AND trip_miles * 3600 / trip_seconds <= 80",
  "$group":"dow, hour, area","$limit":"5"}, "valid-speed-week")
# 4. zero-second count, 1 week
get("ajtu-isnz", {"$select":"count(*) as n",
  "$where":"trip_start_timestamp >= '2026-08-03T00:00:00' AND trip_start_timestamp < '2026-08-10T00:00:00' AND trip_seconds = 0"},
  "zero-sec-week")
# 5. missing count, 1 week
get("ajtu-isnz", {"$select":"count(*) as n",
  "$where":"trip_start_timestamp >= '2026-08-03T00:00:00' AND trip_start_timestamp < '2026-08-10T00:00:00' AND (trip_seconds IS NULL OR trip_miles IS NULL)"},
  "missing-week")
# 6. absurd count, 1 week
get("ajtu-isnz", {"$select":"count(*) as n",
  "$where":"trip_start_timestamp >= '2026-08-03T00:00:00' AND trip_start_timestamp < '2026-08-10T00:00:00' AND trip_seconds > 0 AND trip_miles IS NOT NULL AND (trip_miles <= 0 OR trip_miles * 3600 / trip_seconds > 80)"},
  "absurd-week")
# 7. total count, 1 week (reconciliation anchor)
get("ajtu-isnz", {"$select":"count(*) as n",
  "$where":"trip_start_timestamp >= '2026-08-03T00:00:00' AND trip_start_timestamp < '2026-08-10T00:00:00'"},
  "total-week")
