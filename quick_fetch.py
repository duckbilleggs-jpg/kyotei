"""
高速データ取得スクリプト
指定会場・指定日の全レースデータ(出走表+オッズ+結果)を一括取得
"""
import requests
from bs4 import BeautifulSoup
import json, time, os, re, sys
from datetime import datetime, timedelta

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
    """URLを取得してBeautifulSoupを返す"""
    r = requests.get(url, headers=HDR, timeout=15)
    r.encoding = "utf-8"
    return BeautifulSoup(r.text, "html.parser")

def get_active_venues(hd):
    """指定日に開催中の会場一覧を返す"""
    # 結果一覧ページからチェック
    active = []
    for jcd, name in VENUES.items():
        try:
            url = f"{BASE}/racelist?rno=1&jcd={jcd}&hd={hd}"
            soup = fetch(url)
            # 出走表テーブルがあるか確認
            tables = soup.find_all("table", class_="is-w748")
            if not tables:
                tables = soup.find_all("table", summary=re.compile("レース"))
            if tables or soup.find("div", class_="table1"):
                active.append(jcd)
                print(f"  ✅ {name}")
            time.sleep(0.2)
        except:
            pass
    return active

def scrape_race(jcd, hd, rno):
    """1レース分の出走表+オッズ+結果を取得"""
    race = {"venue": jcd, "venueName": VENUES.get(jcd,""), "raceNo": rno, "date": hd}
    
    # === 出走表 ===
    try:
        soup = fetch(f"{BASE}/racelist?rno={rno}&jcd={jcd}&hd={hd}")
        boats = []
        
        # テーブルの各行(tbody)から選手データを取得
        for tbody in soup.find_all("tbody", class_="is-fs12"):
            boat = {}
            # 選手名
            a = tbody.find("a", href=re.compile(r"profile\?toban="))
            if a:
                boat["name"] = a.get_text(strip=True)
                # 登録番号
                m2 = re.search(r"toban=(\d+)", a["href"]) 
                if m2: boat["toban"] = m2.group(1)
            
            # 全テキストを取得して数値を解析  
            tds = tbody.find_all("td")
            nums = []
            for td in tds:
                t = td.get_text(strip=True)
                # 勝率パターン: X.XX
                if re.match(r"^\d\.\d{2}$", t):
                    nums.append(float(t))
            
            # 最初の勝率らしき数値を採用(全国勝率)
            if nums:
                boat["winRate"] = nums[0]
                if len(nums) >= 2:
                    boat["winRate2"] = nums[1]  # 当地勝率
            
            if "name" in boat:
                boat["num"] = len(boats) + 1
                boats.append(boat)
        
        if len(boats) != 6:
            return None
        race["boats"] = boats
    except Exception as e:
        return None
    
    time.sleep(0.3)
    
    # === 単勝オッズ ===
    try:
        soup = fetch(f"{BASE}/oddstf?rno={rno}&jcd={jcd}&hd={hd}")
        odds = {}
        
        # oddsTableの各行から取得
        for table in soup.find_all("table"):
            # テーブル全体のテキストに"単勝"が含まれるか
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
    
    # === レース結果 ===
    try:
        soup = fetch(f"{BASE}/raceresult?rno={rno}&jcd={jcd}&hd={hd}")
        result = {"order": []}
        
        # 着順テーブル - is-fs14 or table1
        for tbody in soup.find_all("tbody", class_="is-fs14"):
            tds = tbody.find_all("td")
            texts = [td.get_text(strip=True) for td in tds]
            if len(texts) >= 2:
                rank_text = texts[0]
                boat_text = texts[1]
                if rank_text in "123456" and boat_text in "123456":
                    result["order"].append({
                        "rank": int(rank_text),
                        "boat": int(boat_text)
                    })
        
        # 払戻金
        pay_table = soup.find("table", class_="is-w495")
        if not pay_table:
            for t in soup.find_all("table"):
                if "単勝" in t.get_text():
                    pay_table = t
                    break
        
        if pay_table:
            for row in pay_table.find_all("tr"):
                th = row.find("th")
                if th and "単勝" in th.get_text():
                    tds = row.find_all("td")
                    for td in tds:
                        t = td.get_text(strip=True).replace(",","").replace("円","").replace("¥","")
                        m = re.match(r"(\d+)", t)
                        if m:
                            result["tansho_payout"] = int(m.group(1))
        
        if result["order"]:
            race["result"] = result
    except:
        pass
    
    time.sleep(0.3)
    
    # === 直前情報（気象＋体重）===
    try:
        soup = fetch(f"{BASE}/beforeinfo?rno={rno}&jcd={jcd}&hd={hd}")
        weather = {}
        
        # 気象データ: .weather1 内のラベルとデータを取得
        w1 = soup.find("div", class_="weather1")
        if w1:
            labels = w1.find_all("span", class_="weather1_bodyUnitLabelTitle")
            datas = w1.find_all("span", class_="weather1_bodyUnitLabelData")
            if not datas:
                datas = w1.find_all("div", class_="weather1_bodyUnitLabelData")
            
            all_text = w1.get_text()
            
            # 気温
            m = re.search(r'気温\s*([\d.]+)', all_text)
            if m: weather["temp"] = float(m.group(1))
            
            # 水温
            m = re.search(r'水温\s*([\d.]+)', all_text)
            if m: weather["waterTemp"] = float(m.group(1))
            
            # 風速
            m = re.search(r'風速\s*(\d+)', all_text)
            if m: weather["windSpeed"] = int(m.group(1))
            
            # 波高
            m = re.search(r'波高\s*(\d+)', all_text)
            if m: weather["waveHeight"] = int(m.group(1))
            
            # 天候
            for sky in ["晴", "曇り", "曇", "雨", "雪", "霧"]:
                if sky in all_text:
                    weather["sky"] = sky
                    break
            
            # 風向き（is-windXX クラスから）
            wind_el = w1.find("p", class_=re.compile(r"is-wind\d"))
            if not wind_el:
                wind_el = w1.find("div", class_=re.compile(r"is-wind\d"))
            if wind_el:
                cls = [c for c in wind_el.get("class", []) if c.startswith("is-wind")]
                if cls:
                    m2 = re.search(r'is-wind(\d+)', cls[0])
                    if m2: weather["windDir"] = int(m2.group(1))
        
        if weather:
            race["weather"] = weather
        
        # 体重（各選手）
        weights = []
        for tbody in soup.find_all("tbody", class_="is-fs12"):
            for td in tbody.find_all("td"):
                t = td.get_text(strip=True)
                m = re.match(r'([\d.]+)kg', t)
                if m:
                    weights.append(float(m.group(1)))
                    break
        
        if weights and "boats" in race:
            for i, w in enumerate(weights):
                if i < len(race["boats"]):
                    race["boats"][i]["weight"] = w
    except:
        pass
    
    return race

def fetch_day(hd, jcd_list=None):
    """1日分の全レースを取得"""
    print(f"\n📅 {hd[:4]}/{hd[4:6]}/{hd[6:]} データ取得中...")
    
    if not jcd_list:
        print("  📍 開催会場を確認中...")
        jcd_list = get_active_venues(hd)
    
    if not jcd_list:
        print("  ❌ 開催なし")
        return None
    
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
                odds_ok = "✅" if race.get("odds") else "❌"
                sys.stdout.write(f"\r  🏟️ {name}: {rno}R (オッズ{odds_ok}{winner})   ")
                sys.stdout.flush()
        if race_count > 0:
            print(f"\r  🏟️ {name}: {race_count}R取得完了                    ")
    
    if races:
        day_data = {"date": hd, "races": races}
        path = os.path.join(DATA_DIR, f"races_{hd}.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(day_data, f, ensure_ascii=False, indent=2)
        print(f"  💾 {len(races)}レース保存 → {path}")
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
    
    master = {
        "totalRaces": len(all_races),
        "withResults": with_results,
        "withOdds": with_odds,
        "lastUpdated": datetime.now().isoformat(),
        "races": all_races
    }
    
    path = os.path.join(DIR, "race_db.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(master, f, ensure_ascii=False)
    
    print(f"\n🏆 マスターDB: {len(all_races)}レース (結果{with_results}件/オッズ{with_odds}件)")
    return path

if __name__ == "__main__":
    print("🚤 競艇データ収集")
    print("=" * 40)
    
    if len(sys.argv) > 1:
        cmd = sys.argv[1]
        if cmd == "today":
            hd = datetime.now().strftime("%Y%m%d")
            fetch_day(hd)
            build_master()
        elif cmd == "past":
            days = int(sys.argv[2]) if len(sys.argv) > 2 else 7
            for d in range(1, days+1):
                hd = (datetime.now() - timedelta(days=d)).strftime("%Y%m%d")
                if os.path.exists(os.path.join(DATA_DIR, f"races_{hd}.json")):
                    print(f"  ⏭️ {hd} 取得済み")
                    continue
                fetch_day(hd)
                time.sleep(1)
            build_master()
        elif cmd == "venue":
            # 特定会場の過去N日
            jcd = sys.argv[2] if len(sys.argv) > 2 else "24"
            days = int(sys.argv[3]) if len(sys.argv) > 3 else 7
            print(f"📍 {VENUES.get(jcd, jcd)} 過去{days}日分")
            for d in range(1, days+1):
                hd = (datetime.now() - timedelta(days=d)).strftime("%Y%m%d")
                fetch_day(hd, [jcd])
                time.sleep(1)
            build_master()
        else:
            # 日付として扱う
            fetch_day(cmd)
            build_master()
    else:
        # デフォルト: 過去3日分を取得
        for d in range(1, 4):
            hd = (datetime.now() - timedelta(days=d)).strftime("%Y%m%d")
            if os.path.exists(os.path.join(DATA_DIR, f"races_{hd}.json")):
                print(f"  ⏭️ {hd} 取得済み")
                continue
            fetch_day(hd)
            time.sleep(1)
        build_master()
