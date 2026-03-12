"""
競艇データスクレイパー v2
boatrace.jp の正確なHTML構造に基づいた高精度データ取得
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
    r = requests.get(url, headers=HDR, timeout=15)
    r.encoding = "utf-8"
    return BeautifulSoup(r.text, "html.parser")

def scrape_racelist(jcd, hd, rno):
    """出走表: 選手名・勝率・級別を取得"""
    soup = fetch(f"{BASE}/racelist?rno={rno}&jcd={jcd}&hd={hd}")
    boats = []
    
    for tbody in soup.find_all("tbody", class_="is-fs12"):
        boat = {}
        
        # 枠番: is-boatColorN クラスのtd
        color_td = tbody.find("td", class_=re.compile(r"is-boatColor\d"))
        if color_td:
            boat["num"] = len(boats) + 1
        
        # 選手名: div.is-fs18 > a
        name_div = tbody.find("div", class_="is-fs18")
        if name_div:
            a = name_div.find("a")
            if a:
                boat["name"] = a.get_text(strip=True)
                m = re.search(r"toban=(\d+)", a.get("href", ""))
                if m:
                    boat["toban"] = m.group(1)
        
        # 級別: span.is-fColor1 等
        grade_span = tbody.find("span", class_=re.compile(r"is-fColor"))
        if grade_span:
            boat["grade"] = grade_span.get_text(strip=True)
        
        # 全国勝率・当地勝率: td.is-lineH2 の中身
        line_tds = tbody.find_all("td", class_="is-lineH2")
        rates = []
        for td in line_tds:
            text = td.get_text(separator="\n", strip=True)
            lines = [l.strip() for l in text.split("\n") if l.strip()]
            if lines:
                try:
                    val = float(lines[0])
                    rates.append(val)
                except ValueError:
                    pass
        
        # rates[0]=F数関連, rates[1]=全国勝率, rates[2]=当地勝率
        # ただしF数のtdもis-lineH2なので注意
        # 実際のパターン: F0/L0/0.13 → 全国勝率6.23/43.56/58.42 → 当地勝率
        win_rates = []
        for td in line_tds:
            text = td.get_text(separator="\n", strip=True)
            lines = [l.strip() for l in text.split("\n") if l.strip()]
            if lines and re.match(r"^\d+\.\d{2}$", lines[0]):
                val = float(lines[0])
                if 1.0 <= val <= 12.0:  # 勝率の範囲
                    win_rates.append(val)
        
        if len(win_rates) >= 1:
            boat["winRate"] = win_rates[0]  # 全国勝率
        if len(win_rates) >= 2:
            boat["localWinRate"] = win_rates[1]  # 当地勝率
        
        if "name" in boat:
            if "num" not in boat:
                boat["num"] = len(boats) + 1
            boats.append(boat)
    
    return boats if len(boats) == 6 else None

def scrape_odds(jcd, hd, rno):
    """単勝オッズを取得"""
    soup = fetch(f"{BASE}/oddstf?rno={rno}&jcd={jcd}&hd={hd}")
    odds = {}
    for table in soup.find_all("table"):
        for row in table.find_all("tr"):
            tds = row.find_all("td")
            if len(tds) >= 2:
                num = tds[0].get_text(strip=True)
                odds_text = tds[-1].get_text(strip=True)
                if num in "123456" and len(num) == 1:
                    m = re.match(r"([\d.]+)", odds_text)
                    if m:
                        val = float(m.group(1))
                        if 1.0 <= val <= 999:
                            odds[int(num)] = val
    return odds if odds else None

def scrape_result(jcd, hd, rno):
    """レース結果を取得"""
    soup = fetch(f"{BASE}/raceresult?rno={rno}&jcd={jcd}&hd={hd}")
    result = {"order": []}
    
    # 全角→半角変換
    zen2han = {"１":"1","２":"2","３":"3","４":"4","５":"5","６":"6"}
    
    # 着順テーブル: table.is-w495 内の tbody
    result_table = soup.find("table", class_="is-w495")
    if result_table:
        for tbody in result_table.find_all("tbody"):
            tds = tbody.find_all("td", class_="is-fs14")
            if len(tds) >= 2:
                rank_text = tds[0].get_text(strip=True)
                boat_text = tds[1].get_text(strip=True)
                # 全角を半角に変換
                rank_text = zen2han.get(rank_text, rank_text)
                if rank_text in "123456" and boat_text in "123456":
                    result["order"].append({"rank": int(rank_text), "boat": int(boat_text)})
    
    # 払戻金: 単勝のtd > span.is-payout1
    for tbody in soup.find_all("tbody"):
        tds = tbody.find_all("td")
        for i, td in enumerate(tds):
            text = td.get_text(strip=True)
            if text == "単勝":
                # 同じtbody内の is-payout1 span を探す
                payout_span = tbody.find("span", class_="is-payout1")
                if payout_span:
                    payout_text = payout_span.get_text(strip=True).replace(",", "").replace("¥", "")
                    m = re.search(r"(\d+)", payout_text)
                    if m:
                        result["tansho_payout"] = int(m.group(1))
                break
    
    return result if result["order"] else None

def scrape_race(jcd, hd, rno):
    """1レース全データ取得"""
    race = {"venue": jcd, "venueName": VENUES.get(jcd,""), "raceNo": rno, "date": hd}
    
    try:
        boats = scrape_racelist(jcd, hd, rno)
        if not boats:
            return None
        race["boats"] = boats
    except Exception as e:
        return None
    
    time.sleep(0.3)
    
    try:
        odds = scrape_odds(jcd, hd, rno)
        if odds:
            race["odds"] = odds
    except:
        pass
    
    time.sleep(0.3)
    
    try:
        result = scrape_result(jcd, hd, rno)
        if result:
            race["result"] = result
    except:
        pass
    
    return race

def fetch_day(hd, jcd_list=None):
    """1日分取得"""
    print(f"\n{'='*50}")
    print(f"📅 {hd[:4]}/{hd[4:6]}/{hd[6:]} データ取得中...")
    
    if not jcd_list:
        # 全会場の1Rをチェックして開催会場を特定
        jcd_list = []
        for jcd in VENUES:
            try:
                boats = scrape_racelist(jcd, hd, 1)
                if boats:
                    jcd_list.append(jcd)
                    print(f"  ✅ {VENUES[jcd]}")
                time.sleep(0.2)
            except:
                pass
    
    if not jcd_list:
        print("  ❌ 開催なし")
        return None
    
    races = []
    for jcd in jcd_list:
        name = VENUES.get(jcd, jcd)
        count = 0
        for rno in range(1, 13):
            race = scrape_race(jcd, hd, rno)
            if race:
                races.append(race)
                count += 1
                # 進捗表示
                b_info = "/".join([b.get("name","?")[:2] for b in race.get("boats",[])])
                winner = ""
                if race.get("result", {}).get("order"):
                    w = next((o["boat"] for o in race["result"]["order"] if o["rank"]==1), None)
                    if w: winner = f" 1着:{w}号艇"
                odds_ok = "✅" if race.get("odds") else "❌"
                wr = race["boats"][0].get("winRate", "?")
                sys.stdout.write(f"\r  🏟️ {name} {rno}R: {b_info} (勝率{wr}/オッズ{odds_ok}{winner})   ")
                sys.stdout.flush()
        if count > 0:
            print(f"\r  🏟️ {name}: {count}レース完了                                              ")
    
    if races:
        day_data = {"date": hd, "races": races}
        path = os.path.join(DATA_DIR, f"races_{hd}.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(day_data, f, ensure_ascii=False, indent=2)
        
        # 統計
        with_wr = sum(1 for r in races if r["boats"][0].get("winRate"))
        with_odds = sum(1 for r in races if r.get("odds"))
        with_res = sum(1 for r in races if r.get("result"))
        print(f"  💾 {len(races)}レース保存 (勝率{with_wr}/オッズ{with_odds}/結果{with_res})")
        return day_data
    return None

def build_master():
    """マスターDB構築"""
    all_races = []
    for fn in sorted(os.listdir(DATA_DIR)):
        if fn.startswith("races_") and fn.endswith(".json"):
            with open(os.path.join(DATA_DIR, fn), "r", encoding="utf-8") as f:
                d = json.load(f)
                all_races.extend(d.get("races", []))
    
    wr = sum(1 for r in all_races if r.get("boats") and r["boats"][0].get("winRate"))
    od = sum(1 for r in all_races if r.get("odds"))
    rs = sum(1 for r in all_races if r.get("result"))
    
    master = {
        "totalRaces": len(all_races),
        "withWinRate": wr,
        "withOdds": od,
        "withResults": rs,
        "lastUpdated": datetime.now().isoformat(),
        "races": all_races
    }
    
    path = os.path.join(DIR, "race_db.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(master, f, ensure_ascii=False)
    
    print(f"\n🏆 マスターDB: {len(all_races)}レース (勝率{wr}/オッズ{od}/結果{rs})")
    print(f"   → {path}")
    return path

if __name__ == "__main__":
    print("🚤 競艇データ収集 v2")
    print("=" * 50)
    
    cmd = sys.argv[1] if len(sys.argv) > 1 else "default"
    
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
        jcd = sys.argv[2] if len(sys.argv) > 2 else "24"
        days = int(sys.argv[3]) if len(sys.argv) > 3 else 3
        print(f"📍 {VENUES.get(jcd, jcd)} 過去{days}日分")
        for d in range(1, days+1):
            hd = (datetime.now() - timedelta(days=d)).strftime("%Y%m%d")
            fetch_day(hd, [jcd])
            time.sleep(1)
        build_master()
    
    elif cmd == "test":
        # テスト: 1会場1日だけ取得
        jcd = sys.argv[2] if len(sys.argv) > 2 else "24"
        hd = sys.argv[3] if len(sys.argv) > 3 else (datetime.now() - timedelta(days=1)).strftime("%Y%m%d")
        print(f"🧪 テスト: {VENUES.get(jcd, jcd)} {hd}")
        fetch_day(hd, [jcd])
        build_master()
        
        # 結果を表示
        path = os.path.join(DIR, "race_db.json")
        with open(path, "r", encoding="utf-8") as f:
            db = json.load(f)
        print(f"\nサンプルデータ:")
        for r in db["races"][:2]:
            print(json.dumps(r, ensure_ascii=False, indent=2))
    
    else:
        # デフォルト: 過去3日
        for d in range(1, 4):
            hd = (datetime.now() - timedelta(days=d)).strftime("%Y%m%d")
            if os.path.exists(os.path.join(DATA_DIR, f"races_{hd}.json")):
                print(f"  ⏭️ {hd} 取得済み")
                continue
            fetch_day(hd)
            time.sleep(1)
        build_master()
