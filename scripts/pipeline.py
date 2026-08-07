#!/usr/bin/env python3
"""桃園托育地圖 資料 pipeline：官方 CSV → 清洗 → geocode（快取）→ GeoJSON

用法：
  python3 scripts/pipeline.py parse     # 讀 data/raw 最新名冊 → data/processed/facilities.json
  python3 scripts/pipeline.py geocode   # 對 facilities.json 補座標（Nominatim，快取 data/geocode_cache.json）
  python3 scripts/pipeline.py build     # 產出 data/processed/facilities.geojson ＋ 驗證報告
  python3 scripts/pipeline.py all

資料源（桃園市開放資料，政府資料開放授權條款 v1）：
  dataset 168379 114年桃園市公私立托嬰中心名冊（cp950）
零金鑰：geocode 用 Nominatim 公用 API（1 req/1.1s、自報 UA）；結果進版控快取，重跑不再打 API。
"""
import csv, io, json, re, sys, time, urllib.parse, urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RAW, PROC = ROOT / "data/raw", ROOT / "data/processed"
CACHE = ROOT / "data/geocode_cache.json"
FACIL = PROC / "facilities.json"
UA = "taoyuan-childcare-map/0.1 (civic-tech demo; opendata pipeline)"

def norm_header(h):
    return re.sub(r"\s+", "", h or "")

def latest_raw():
    files = sorted(RAW.glob("nurseries_*.raw.csv"))
    if not files:
        sys.exit("no raw csv; run curl download first")
    return files[-1]

def parse():
    src = latest_raw()
    text = src.read_bytes().decode("cp950", errors="replace")
    rows = list(csv.reader(io.StringIO(text)))
    headers = [norm_header(h) for h in rows[0]]
    idx = {h: i for i, h in enumerate(headers)}
    def col(row, key_part):
        for h, i in idx.items():
            if key_part in h:
                return (row[i] if i < len(row) else "").strip()
        return ""
    out = []
    for row in rows[1:]:
        if not any(c.strip() for c in row):
            continue
        name = col(row, "機構名稱")
        if not name:
            continue
        fee_raw = col(row, "平均月費")
        fee_m = re.search(r"([\d,]{4,})", fee_raw)
        grade_raw = col(row, "評鑑")
        gm = re.match(r"([優甲乙丙丁])\s*[（(]?(\d{2,3})?", grade_raw)
        beds = re.sub(r"\D", "", col(row, "核定床位")) or None
        out.append({
            "id": f"ty-{col(row,'序號') or len(out)+1}",
            "name": name,
            "type": col(row, "機構類別"),
            "district": col(row, "區域").replace("桃園市", ""),
            "quasi_public": col(row, "準公共") == "是",
            "grade": gm.group(1) if gm else None,
            "grade_year": gm.group(2) if gm and gm.group(2) else None,
            "grade_raw": grade_raw,
            "fee_monthly": int(fee_m.group(1).replace(",", "")) if fee_m else None,
            "established": col(row, "設立日期"),
            "floor": col(row, "立案樓層"),
            "area_m2": col(row, "機構面積"),
            "beds_approved": int(beds) if beds else None,
            "address": col(row, "地址"),
            "phone": col(row, "聯絡電話"),
            # 空位欄位：MVP 全 null，schema 先長對（政策層再接資料）
            "vacancy": None, "available_from": None,
            "vacancy_source": "none", "vacancy_updated_at": None,
        })
    PROC.mkdir(parents=True, exist_ok=True)
    FACIL.write_text(json.dumps(out, ensure_ascii=False, indent=1), "utf-8")
    print(f"parse: {len(out)} facilities ← {src.name}")

def geo_query(params):
    url = "https://nominatim.openstreetmap.org/search?" + urllib.parse.urlencode(
        {**params, "format": "jsonv2", "limit": 1, "countrycodes": "tw"})
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=20) as r:
        hits = json.load(r)
    if not hits:
        return None
    return {"lat": float(hits[0]["lat"]), "lon": float(hits[0]["lon"]),
            "display": hits[0].get("display_name", "")}

def attempts(key, district):
    """查詢嘗試序（由準到寬）。OSM 台灣有門牌資料，但只吃結構化英式順序
    （street=\"22 忠勇五街\"）——自由文字「忠勇五街22號」解析不到，實測 2026-08-06。"""
    rest = key.replace("桃園市", "").replace(district, "", 1)
    m = re.match(r"(.+?[路街巷弄村])(\d+(?:[之-]\d+)?)號", rest)
    if m:
        road, no = m.group(1), m.group(2).replace("之", "-")
        cn_road = re.sub(r"(\d+)(?=[街路巷])", lambda x: _cn(int(x.group(1))), road)
        yield {"street": f"{no} {road}", "city": district, "county": "桃園市"}, "exact"
        if cn_road != road:
            yield {"street": f"{no} {cn_road}", "city": district, "county": "桃園市"}, "exact"
        if "-" in no:
            yield {"street": f"{no.split('-')[0]} {road}", "city": district, "county": "桃園市"}, "exact"
        yield {"street": cn_road, "city": district, "county": "桃園市"}, "street"
    yield {"q": key}, ("exact" if "號" in key else "street")
    street_free = re.sub(r"\d+([-之]\d+)?號.*$", "", key)
    if street_free != key:
        yield {"q": street_free}, "street"

def clean_addr(addr):
    a = re.sub(r"[（(].*?[)）]", "", addr)
    for _ in range(2):  # 去里/村（僅緊接在區之後；跑兩次處理「長庚里長庚村」連寫）
        a = re.sub(r"(?<=區)[一-鿿]{1,4}[里村]", "", a)
    a = re.sub(r"\d+鄰", "", a)                              # 去鄰
    # 去樓層：含「2樓」「1-3樓」「1、2樓」「2樓及68-8號2樓」「地下1樓」等寫法
    a = re.sub(r"\d+([-、及至~]\d+)*樓.*$|地下.*$|[之-]\d+樓?$", "", a)
    return a.strip()

def _cn(n):
    d = "零一二三四五六七八九"
    if n > 99: return str(n)   # 巷弄大號不轉（街名數字不會這麼大）
    if n < 10: return d[n]
    if n < 20: return "十" + (d[n % 10] if n % 10 else "")
    return d[n // 10] + "十" + (d[n % 10] if n % 10 else "")

def addr_variants(key):
    """由嚴到寬：多門牌取第一 → 數字街名轉中文 → 支號轉「之」→ 支號捨去 → 街道層"""
    v = [key]
    first = re.sub(r"(\d+(?:-\d+)?)[、及.．](?:\d+(?:-\d+)?[、及.．])*\d+(?:-\d+)?號", r"\1號", key)
    if first != key: v.append(first)
    cn_st = re.sub(r"(\d+)(?=[街路巷])", lambda m: _cn(int(m.group(1))), v[-1])
    if cn_st != v[-1]: v.append(cn_st)
    base = v[-1]
    zhi = re.sub(r"(\d+)-(\d+)號", r"\1之\2號", base)
    if zhi != base: v.append(zhi)
    plain = re.sub(r"(\d+)-\d+號", r"\1號", base)
    if plain != base: v.append(plain)
    street = re.sub(r"\d+([-之]\d+)?號.*$", "", base)
    if street and street != base: v.append(street)
    return list(dict.fromkeys(v))  # 去重保序

def geocode():
    cache = json.loads(CACHE.read_text("utf-8")) if CACHE.exists() else {}
    facil = json.loads(FACIL.read_text("utf-8"))
    todo = [f for f in facil
            if cache.get(clean_addr(f["address"]), {}).get("precision", "retry") in ("none", "retry")]
    print(f"geocode: {len(todo)} to query ({len(cache)} cached)")
    for n, f in enumerate(todo, 1):
        key = clean_addr(f["address"])
        # 多門牌（22、24號／1357.1359號）先取第一個再解析
        key1 = re.sub(r"(\d+(?:-\d+)?)[、及.．](?:\d+(?:-\d+)?[、及.．])*\d+(?:-\d+)?號", r"\1號", key)
        result, precision = None, "none"
        for params, prec in attempts(key1, f["district"]):
            try:
                result = geo_query(params)
            except Exception as e:
                print(f"  ! {params}: {e}")
                result = None
            if result and f["district"] not in result["display"]:
                result = None  # 落在錯的行政區＝視為未命中（防同名路跨區錯配）
            if result:
                precision = prec
                break
            time.sleep(1.1)
        cache[key] = ({"lat": result["lat"], "lon": result["lon"], "precision": precision,
                       "display": result["display"]} if result else {"precision": "none"})
        if n % 10 == 0 or n == len(todo):
            CACHE.write_text(json.dumps(cache, ensure_ascii=False, indent=0), "utf-8")
            print(f"  {n}/{len(todo)}")
        time.sleep(1.1)  # Nominatim usage policy
    CACHE.write_text(json.dumps(cache, ensure_ascii=False, indent=0), "utf-8")

def build():
    cache = json.loads(CACHE.read_text("utf-8")) if CACHE.exists() else {}
    # 人工校正優先：data/manual_overrides.json {clean_addr: {lat, lon}}（社群 PR 友善通道）
    ovr_path = ROOT / "data/manual_overrides.json"
    if ovr_path.exists():
        for k, v in json.loads(ovr_path.read_text("utf-8")).items():
            if v.get("lat") is not None and v.get("lon") is not None:
                cache[k] = {**v, "precision": "manual"}
    facil = json.loads(FACIL.read_text("utf-8"))
    feats, miss = [], []
    for f in facil:
        hit = cache.get(clean_addr(f["address"]), {})
        if hit.get("precision") in ("exact", "street", "manual"):
            props = dict(f); props["geo_precision"] = hit["precision"]
            feats.append({"type": "Feature",
                          "geometry": {"type": "Point", "coordinates": [hit["lon"], hit["lat"]]},
                          "properties": props})
        else:
            miss.append(f["name"] + " | " + f["address"])
    gj = {"type": "FeatureCollection",
          "metadata": {"source": "桃園市政府開放資料 dataset 168379（政府資料開放授權條款 v1）",
                       "fetched": latest_raw().stem.split("_")[1].replace(".raw", ""),
                       "count": len(feats), "missing": len(miss)},
          "features": feats}
    (PROC / "facilities.geojson").write_text(json.dumps(gj, ensure_ascii=False), "utf-8")
    hit_rate = len(feats) / len(facil) * 100 if facil else 0
    print(f"build: {len(feats)}/{len(facil)} features ({hit_rate:.1f}%), missing {len(miss)}")
    for m in miss[:20]:
        print("  miss:", m)

if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "all"
    {"parse": parse, "geocode": geocode, "build": build,
     "all": lambda: (parse(), geocode(), build())}[cmd]()
