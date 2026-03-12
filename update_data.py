"""
データ更新スクリプト
cp932環境でも動作するようemoji不使用
"""
import requests
from bs4 import BeautifulSoup
import json, time, os, re, sys
from datetime import datetime, timedelta

# cp932対策
if sys.stdout.encoding and sys.stdout.encoding.lower() in ('cp932', 'shift_jis', 'shift-jis'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

BASE = "https://www.boatrace.jp/owpc/pc/race"
HDR = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(DIR, "data")
os.makedirs(DATA_DIR, exist_ok=True)

VENUES = {
    "01":"桐生","02":"戸田","03":"江戸川","04":"平和島","05":"多摩川",
    "06":"浜名湖","07":"蒲郡","08":"常滑","09":"津","10":"三国",
    "11":"びわこ","12":"住之江","13":"尼崎","14":"鳴門","15":"丸亀",
    "16":"児島","17":"宮島","18":"徳山","19":"下関","20":"若松",
    "21":"芦屋","22":"福岡","23":"唐津","24":"大村"
}

def fetch(url):
    r = requests.get(url, headers=HDR, timeout=15)
    r.encoding = "utf-8"
    return BeautifulSoup(r.text, "html.parser")

def scrape_race(jcd, hd, rno):
    """1レース分の出走表+オッズ+結果を取得"""
    race = {"venue": jcd, "venueName": VENUES.get(jcd,""), "raceNo": rno, "date": hd}
    
    # 出走表
    try:
        soup = fetch(f"{BASE}/racelist?rno={rno}&jcd={jcd}&hd={hd}")
        boats = []
        for tbody in soup.find_all("tbody", class_="is-fs12"):
            boat = {}
            a = tbody.find("a", href=re.compile(r"profile\?toban="))
            if a:
                boat["name"] = a.get_text(strip=True)
                m2 = re.search(r"toban=(\d+)", a["href"])
                if m2: boat["toban"] = m2.group(1)
            
            # 級別
            grade_span = tbody.find("span", class_=re.compile(r"is-fColor"))
            if grade_span:
                boat["grade"] = grade_span.get_text(strip=True)
            
            tds = tbody.find_all("td")
            nums = []
            for td in tds:
                t = td.get_text(strip=True)
                if re.match(r"^\d\.\d{2}$", t):
                    nums.append(float(t))
            
            if nums:
                boat["winRate"] = nums[0]
                if len(nums) >= 2:
                    boat["localWinRate"] = nums[1]
            
            if "name" in boat:
                boat["num"] = len(boats) + 1
                boats.append(boat)
        
        if len(boats) != 6:
            return None
        race["boats"] = boats
    except Exception as e:
        return None
    
    time.sleep(0.3)
    
    # 単勝オッズ
    try:
        soup = fetch(f"{BASE}/oddstf?rno={rno}&jcd={jcd}&hd={hd}")
        odds = {}
        for table in soup.find_all("table"):
            rows = table.find_all("tr")
            for row in rows:
                tds = row.find_all("td")
                if len(tds) >= 2:
                    num_text = tds[0].get_text(strip=True)
                    odds_text = tds[-1].get_text(strip=True)
                    if num_text in "123456" and len(num_text) == 1:
                        m = re.match(r"([\d.]+)", odds_text)
                        if m:
                            val = float(m.group(1))
                            if 1.0 <= val <= 999:
                                odds[int(num_text)] = val
        if odds:
            race["odds"] = odds
    except:
        pass
    
    time.sleep(0.3)
    
    # レース結果
    try:
        soup = fetch(f"{BASE}/raceresult?rno={rno}&jcd={jcd}&hd={hd}")
        result = {"order": []}
        for tbody in soup.find_all("tbody", class_="is-fs14"):
            tds = tbody.find_all("td")
            texts = [td.get_text(strip=True) for td in tds]
            if len(texts) >= 2:
                rank_text = texts[0]
                boat_text = texts[1]
                if rank_text in "123456" and boat_text in "123456":
                    result["order"].append({"rank": int(rank_text), "boat": int(boat_text)})
        
        # 単勝払戻金
        for table in soup.find_all("table"):
            text = table.get_text()
            if "単勝" in text:
                for row in table.find_all("tr"):
                    th = row.find("th")
                    if th and "単勝" in th.get_text():
                        for td in row.find_all("td"):
                            t = td.get_text(strip=True).replace(",","").replace("円","").replace("¥","")
                            m = re.match(r"(\d+)", t)
                            if m:
                                result["tansho_payout"] = int(m.group(1))
                break
        
        if result["order"]:
            race["result"] = result
    except:
        pass
    
    return race

def get_active_venues(hd):
    """指定日に開催中の会場を確認"""
    active = []
    for jcd, name in VENUES.items():
        try:
            url = f"{BASE}/racelist?rno=1&jcd={jcd}&hd={hd}"
            soup = fetch(url)
            tables = soup.find_all("table", class_="is-w748")
            if not tables:
                tables = soup.find_all("table", summary=re.compile("レース"))
            if tables or soup.find("div", class_="table1"):
                # 出走表の選手名があるか確認
                tbodies = soup.find_all("tbody", class_="is-fs12")
                if tbodies:
                    a = tbodies[0].find("a", href=re.compile(r"profile\?toban="))
                    if a:
                        active.append(jcd)
                        print(f"  [OK] {name}")
            time.sleep(0.15)
        except:
            pass
    return active

def fetch_day(hd, jcd_list=None):
    """1日分の全レースを取得"""
    print(f"\n== {hd[:4]}/{hd[4:6]}/{hd[6:]} データ取得中... ==")
    
    if not jcd_list:
        print("  開催会場を確認中...")
        jcd_list = get_active_venues(hd)
    
    if not jcd_list:
        print("  開催なし")
        return None
    
    print(f"  開催: {len(jcd_list)}会場")
    
    races = []
    for jcd in jcd_list:
        name = VENUES.get(jcd, jcd)
        race_count = 0
        for rno in range(1, 13):
            race = scrape_race(jcd, hd, rno)
            if race:
                races.append(race)
                race_count += 1
                winner = ""
                if race.get("result") and race["result"].get("order"):
                    w = next((o["boat"] for o in race["result"]["order"] if o["rank"]==1), None)
                    if w: winner = f" 1着:{w}号艇"
                odds_ok = "OK" if race.get("odds") else "NG"
                sys.stdout.write(f"\r  {name}: {rno}R (odds:{odds_ok}{winner})   ")
                sys.stdout.flush()
        if race_count > 0:
            print(f"\r  {name}: {race_count}R取得完了                    ")
    
    if races:
        day_data = {"date": hd, "races": races}
        path = os.path.join(DATA_DIR, f"races_{hd}.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(day_data, f, ensure_ascii=False, indent=2)
        print(f"  => {len(races)}レース保存 -> {path}")
        return day_data
    return None

def build_master():
    """全日のデータを統合してrace_db.jsonに"""
    all_races = []
    for fn in sorted(os.listdir(DATA_DIR)):
        if fn.startswith("races_") and fn.endswith(".json"):
            with open(os.path.join(DATA_DIR, fn), "r", encoding="utf-8") as f:
                d = json.load(f)
                all_races.extend(d.get("races", []))
    
    with_results = sum(1 for r in all_races if r.get("result"))
    with_odds = sum(1 for r in all_races if r.get("odds"))
    with_wr = sum(1 for r in all_races if r.get("boats") and r["boats"][0].get("winRate"))
    
    master = {
        "totalRaces": len(all_races),
        "withWinRate": with_wr,
        "withResults": with_results,
        "withOdds": with_odds,
        "lastUpdated": datetime.now().isoformat(),
        "races": all_races
    }
    
    path = os.path.join(DIR, "race_db.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(master, f, ensure_ascii=False)
    
    print(f"\n== マスターDB: {len(all_races)}レース (勝率{with_wr}/odds{with_odds}/結果{with_results}) ==")
    return path

if __name__ == "__main__":
    print("=== 競艇データ更新 ===")
    
    # 過去3日分を取得（取得済みは上書き）
    for d in range(0, 4):
        hd = (datetime.now() - timedelta(days=d)).strftime("%Y%m%d")
        if d > 0 and os.path.exists(os.path.join(DATA_DIR, f"races_{hd}.json")):
            # 既存ファイルのレース数を確認
            with open(os.path.join(DATA_DIR, f"races_{hd}.json"), "r", encoding="utf-8") as f:
                existing = json.load(f)
            count = len(existing.get("races", []))
            if count >= 100:  # 十分なデータがある場合はスキップ
                print(f"\n  >> {hd} 取得済み ({count}レース)")
                continue
            else:
                print(f"\n  >> {hd} データ不足 ({count}レース), 再取得...")
        fetch_day(hd)
        time.sleep(1)
    
    build_master()
    print("\n=== 完了 ===")
