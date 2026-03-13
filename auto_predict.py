"""
全レース自動予測スクリプト v8 - 全要素統合版

予測の考え方:
1. なぜこの人が1位になったのか? → 選手実力 × コース × 対戦相手との力量差
2. 強い人でも負ける相手はいるか? → 力量差テーブル(実データ)
3. 特異な場所はあるか? → 会場別の特性（荒れやすさ）
4. オッズは何をもとに決まるか? → 公衆の評価。モデルとの乖離がEV
5. 天候/風/水温 → コース有利度に影響（向かい風→1コース不利等）

全ファクター:
- 選手実力: レーサーDB（直近3期加重平均勝率）
- 対戦マッチアップ: コース×勝率帯テーブル + 力量差テーブル（実データ分析結果）
- モーター: motor2ren（モーター2連対率）
- ボート: boat2ren（ボート2連対率）
- ST: avgST（コース別平均ST / 当日ST）
- 天候: 風速・風向き → 1コース有利度に影響
- 水面: 水温・波高 → 荒れ度に影響
- 体重: 軽量有利（冬場は重い選手が不利）
- オッズ: 期待値(EV)計算に使用
"""
import json, os, math
from datetime import datetime
from collections import defaultdict

DIR = os.path.dirname(os.path.abspath(__file__))

# ============================================
# 実データ分析結果のテーブル（976レースから算出）
# ============================================
COURSE_WR_RATE = {
    (1, 'S'): 0.776, (1, 'A'): 0.652, (1, 'B'): 0.481, (1, 'C'): 0.366, (1, 'D'): 0.319,
    (2, 'S'): 0.185, (2, 'A'): 0.221, (2, 'B'): 0.134, (2, 'C'): 0.112, (2, 'D'): 0.052,
    (3, 'S'): 0.177, (3, 'A'): 0.214, (3, 'B'): 0.176, (3, 'C'): 0.092, (3, 'D'): 0.072,
    (4, 'S'): 0.102, (4, 'A'): 0.135, (4, 'B'): 0.106, (4, 'C'): 0.081, (4, 'D'): 0.039,
    (5, 'S'): 0.085, (5, 'A'): 0.110, (5, 'B'): 0.075, (5, 'C'): 0.030, (5, 'D'): 0.006,
    (6, 'S'): 0.048, (6, 'A'): 0.079, (6, 'B'): 0.029, (6, 'C'): 0.024, (6, 'D'): 0.015,
}

GAP_TO_BOAT1_WIN = {
    -3: 0.244, -2: 0.346, -1: 0.482,
     0: 0.629, 1: 0.804, 2: 0.850, 3: 0.950,
}

# 風向き: 追い風(1コース有利) vs 向かい風(1コース不利)
# windDir: 角度(0-360?)  追い風≒0-90,270-360、向かい風≒90-270（仮定）
# 実際はboatrace.jpの数値体系に依存するので、まず分析で確認する


def wr_band(wr):
    if wr >= 7.0: return 'S'
    if wr >= 6.0: return 'A'
    if wr >= 5.0: return 'B'
    if wr >= 4.0: return 'C'
    return 'D'


def load_racer_db():
    db_path = os.path.join(DIR, 'racer_db.json')
    if not os.path.exists(db_path):
        return {}
    with open(db_path, 'r', encoding='utf-8') as f:
        db = json.load(f)
    print(f"📊 レーサーDB: {db.get('totalRacers',0):,}人, {db.get('totalRecords',0):,}件")
    return db.get('racers', {})


def get_racer_wr(racer_db, toban):
    """選手の直近3期加重平均勝率とSTを取得"""
    racer = racer_db.get(toban)
    if not racer or not racer.get('periods'):
        return 0, '', 0
    
    periods = racer['periods']
    recent = periods[-3:]
    w_total = 0
    w_wr = 0
    w_st = 0
    for i, p in enumerate(recent):
        w = 1.0 + i * 1.0
        wr = p.get('winRate', 0)
        st = p.get('avgST', 0)
        if wr > 0:
            w_wr += wr * w
            w_total += w
        if st > 0:
            w_st += st * w
    
    avg_wr = w_wr / w_total if w_total > 0 else 0
    avg_st = w_st / w_total if w_total > 0 else 0
    grade = periods[-1].get('grade', 'B1')
    return avg_wr, grade, avg_st


def interpolate_gap(gap):
    """力量差の線形補間"""
    gap_clamped = max(-3.0, min(3.0, gap))
    low = int(math.floor(gap_clamped))
    high = low + 1
    if high > 3: high = 3
    if low < -3: low = -3
    
    low_val = GAP_TO_BOAT1_WIN.get(low, 0.55)
    high_val = GAP_TO_BOAT1_WIN.get(high, 0.55)
    
    if low == high:
        return low_val
    frac = gap_clamped - low
    return low_val + (high_val - low_val) * frac


# デフォルトのファクター重み
DEFAULT_WEIGHTS = {
    'motor': 0.10,       # モーター影響度
    'st': 2.0,           # ST影響度
    'wind_head': 0.08,   # 向かい風の1コース不利度
    'wind_tail': 0.03,   # 追い風の1コース有利度
    'wave': 0.10,        # 波高の荒れ度影響
    'weight': 0.005,     # 体重影響度
    'adj_bonus': 0.04,   # 隣接コース格下ボーナス
    'gap_strength': 1.0, # 力量差テーブルの適用強度
    'venue_affinity': 0.08, # 会場相性ファクター重み
    'recent_form': 0.06,    # 直近フォームファクター重み
}


def build_performance_index(races):
    """
    race_db.jsonの過去レース結果から、選手ごとの:
    1. 会場別勝率 (venue_win[toban][venue] = 勝率)
    2. 直近10レースの勝率 (recent_wins[toban] = 勝率)
    を集計して辞書として返す。
    新たなスクレイピングなし。既存データのみ使用。
    """
    from collections import defaultdict
    
    # 各选手のレース履歴: {toban: [(date, venue, course_no, rank)]}
    racer_history = defaultdict(list)
    
    for race in races:
        result = race.get('result', {})
        order = result.get('order', [])
        if not order:
            continue
        
        date = race.get('date', '')
        venue = race.get('venue', '')
        boats = race.get('boats', [])
        
        # 着順を辞書化: {boat_num: rank}
        rank_by_boat = {o['boat']: o['rank'] for o in order}
        
        for i, boat in enumerate(boats):
            toban = boat.get('toban', '')
            if not toban:
                continue
            boat_num = i + 1  # 1-indexed
            rank = rank_by_boat.get(boat_num, 99)  # 99=不明
            racer_history[toban].append({
                'date': date,
                'venue': venue,
                'boat_num': boat_num,
                'rank': rank
            })
    
    # 会場別勝率の集計
    venue_win = defaultdict(lambda: defaultdict(lambda: {'wins': 0, 'total': 0}))
    for toban, history in racer_history.items():
        for h in history:
            v = h['venue']
            venue_win[toban][v]['total'] += 1
            if h['rank'] == 1:
                venue_win[toban][v]['wins'] += 1
    
    venue_wr = {}  # {toban: {venue: win_rate}}
    for toban, venues in venue_win.items():
        venue_wr[toban] = {}
        for v, stat in venues.items():
            if stat['total'] >= 3:  # 3レース以上あれば信頼性あり
                venue_wr[toban][v] = stat['wins'] / stat['total']
    
    # 直近10レースの勝率
    recent_form = {}  # {toban: recent_win_rate}
    for toban, history in racer_history.items():
        sorted_h = sorted(history, key=lambda x: x['date'], reverse=True)
        recent = sorted_h[:10]  # 最新10レース
        if len(recent) >= 3:
            wins = sum(1 for h in recent if h['rank'] == 1)
            recent_form[toban] = wins / len(recent)
    
    print(f"📊 パフォーマンスインデックス構築: {len(venue_wr)}選手, {len(recent_form)}選手の直近フォーム")
    return {'venue_wr': venue_wr, 'recent_form': recent_form}


def predict_race(boats, venue, racer_db, weather=None, odds=None, fw=None, perf_index=None):
    """
    v8: 全要素統合予測（ファクター重み付き）
    fw: factor_weights - 自己修正ループで更新される重み辞書
    perf_index: build_performance_index()の結果（会場相性・直近フォーム）
    """
    if len(boats) < 6:
        return None
    if fw is None:
        fw = DEFAULT_WEIGHTS.copy()
    
    # === 全選手のデータ取得 ===
    wrs = []     # 勝率
    sts = []     # 平均ST
    motors = []  # モーター2連対率
    boat2s = []  # ボート2連対率
    weights = [] # 体重
    
    for i, boat in enumerate(boats):
        toban = boat.get('toban', '')
        wr, grade, st = get_racer_wr(racer_db, toban)
        if wr == 0:
            wr = boat.get('winRate', 0) or 3.5
        if st == 0:
            st = boat.get('avgST', 0) or 0
        wrs.append(wr)
        sts.append(st)
        motors.append(boat.get('motor2ren', 0) or 0)
        boat2s.append(boat.get('boat2ren', 0) or 0)
        weights.append(boat.get('weight', 0) or 52)
    
    # === ファクター1: ベーススコア（コース×勝率帯テーブル）===
    scores = []
    for i in range(6):
        course = i + 1
        band = wr_band(wrs[i])
        base_rate = COURSE_WR_RATE.get((course, band), 0.05)
        
        # バンド内の微調整（±10%）
        band_centers = {'S': 7.5, 'A': 6.5, 'B': 5.5, 'C': 4.5, 'D': 3.0}
        center = band_centers[band]
        fine_adj = 1.0 + (wrs[i] - center) * 0.05
        score = base_rate * max(0.85, min(1.15, fine_adj))
        scores.append(score)
    
    # === ファクター2: 力量差による1号艇補正 ===
    boat1_wr = wrs[0]
    max_outer_wr = max(wrs[1:])
    gap = boat1_wr - max_outer_wr
    expected_boat1 = interpolate_gap(gap)
    
    # 現在のスコアでの1号艇シェアとの差分で補正
    current_total = sum(scores)
    if current_total > 0:
        current_boat1_share = scores[0] / current_total
        if current_boat1_share > 0.01:
            correction = expected_boat1 / current_boat1_share
            # 補正は穏やかに適用（0.7〜1.5倍に制限）
            correction = max(0.7, min(1.5, correction))
            scores[0] *= correction
    
    # === ファクター3: モーター補正 ===
    avg_motor = sum(m for m in motors if m > 0) / max(1, sum(1 for m in motors if m > 0))
    for i in range(6):
        if motors[i] > 0 and avg_motor > 0:
            motor_ratio = motors[i] / avg_motor
            motor_adj = 1.0 + (motor_ratio - 1.0) * fw['motor']
            motor_adj = max(0.90, min(1.10, motor_adj))
            scores[i] *= motor_adj
    
    # === ファクター4: ST補正 ===
    for i in range(6):
        if sts[i] > 0:
            optimal = 0.13
            diff = sts[i] - optimal
            if diff <= 0:
                st_adj = 1.0 + diff * 1.5
            else:
                st_adj = 1.0 - diff * fw['st']
            st_adj = max(0.85, min(1.10, st_adj))
            scores[i] *= st_adj
    
    # === ファクター5: 天候・風・水面 ===
    if weather:
        wind_speed = weather.get('windSpeed', 0) or 0
        wind_dir = weather.get('windDir', 0) or 0
        wave = weather.get('waveHeight', 0) or 0
        water_temp = weather.get('waterTemp', 0) or 0
        
        # 風の影響:
        # 追い風(1コース有利): windDir 概ね0-4,14-17（北方向）
        # 向かい風(1コース不利): windDir 概ね7-11（南方向）
        # ※ boatrace.jpのwindDirは角度ではなく方位コード
        is_headwind = 7 <= wind_dir <= 11  # 向かい風
        is_tailwind = wind_dir <= 4 or wind_dir >= 14  # 追い風
        
        if wind_speed >= 3:
            if is_headwind:
                scores[0] *= (1.0 - fw['wind_head'])
                for i in range(1, 6):
                    scores[i] *= (1.0 + fw['wind_head'] * 0.25)
            elif is_tailwind:
                scores[0] *= (1.0 + fw['wind_tail'])
            
            if wind_speed >= 5:
                for i in range(2, 6):
                    scores[i] *= (1.0 + fw['wind_tail'])
        
        # 波高の影響: 波高→荒れ→番狂わせ増
        if wave >= 3:
            # 高波 → 実力差が出にくい、番狂わせ
            for i in range(6):
                scores[i] *= 1.0 + (0.5 - abs(scores[i] / max(sum(scores), 0.01) - 1/6)) * 0.10
        
        # 水温の影響: 水温低い→モーター出力UP→外からの旋回有利
        if water_temp > 0 and water_temp < 12:
            # 低水温 → 外コースの旋回がやや有利
            for i in range(2, 6):
                scores[i] *= 1.01
    
    # === ファクター6: 体重差 ===
    avg_weight = sum(w for w in weights if w > 0) / max(1, sum(1 for w in weights if w > 0))
    for i in range(6):
        if weights[i] > 0 and avg_weight > 0:
            # 平均より軽い→有利(最大5%)、重い→不利(最大5%)
            weight_diff = avg_weight - weights[i]
            weight_adj = 1.0 + weight_diff * 0.005
            weight_adj = max(0.95, min(1.05, weight_adj))
            scores[i] *= weight_adj
    
    # === ファクター7: 隣接コースの力量関係 ===
    for i in range(1, 6):
        if wrs[i] > wrs[i-1] + 1.5:
            scores[i] *= (1.0 + fw['adj_bonus'])
        if i < 5 and wrs[i] > wrs[i+1] + 1.5:
            scores[i] *= (1.0 + fw['adj_bonus'] * 0.5)
    
    # === ファクター8: 会場相性（既存race_dbから集計済み）===
    if perf_index and perf_index.get('venue_wr'):
        venue_wr = perf_index['venue_wr']
        # 全選手の当会場での平均勝率（基準値として使用）
        venue_rates = []
        tobans_list = [boats[i].get('toban', '') for i in range(6)]
        for i in range(6):
            toban = tobans_list[i]
            if toban and toban in venue_wr and venue in venue_wr[toban]:
                venue_rates.append(venue_wr[toban][venue])
            else:
                venue_rates.append(None)
        
        # データがある選手のみ適用（1/6=0.167 が「普通」）
        valid_rates = [r for r in venue_rates if r is not None]
        if valid_rates:
            avg_venue_rate = sum(valid_rates) / len(valid_rates)
            for i in range(6):
                if venue_rates[i] is not None:
                    # 平均を基準に上下補正（最大±10%）
                    ratio = venue_rates[i] / max(avg_venue_rate, 0.001)
                    adj = 1.0 + (ratio - 1.0) * fw.get('venue_affinity', 0.08)
                    adj = max(0.90, min(1.10, adj))
                    scores[i] *= adj
    
    # === ファクター9: 直近フォーム（既存race_dbから集計済み）===
    if perf_index and perf_index.get('recent_form'):
        recent_form = perf_index['recent_form']
        # 平均期待勝率 1/6=0.167
        base_win_rate = 1.0 / 6.0
        for i in range(6):
            toban = boats[i].get('toban', '')
            if toban and toban in recent_form:
                rf = recent_form[toban]
                # フォームが良い（高勝率）→ボーナス、悪い→ペナルティ（最大±8%）
                ratio = rf / max(base_win_rate, 0.001)
                adj = 1.0 + (ratio - 1.0) * fw.get('recent_form', 0.06)
                adj = max(0.92, min(1.08, adj))
                scores[i] *= adj
    
    # === 確率に変換 ===
    total = sum(scores)
    if total == 0:
        return None
    probs = [s / total for s in scores]
    
    # === EV計算とファクター記録 ===
    results = []
    for i in range(len(boats)):
        boat_odds = 0
        if odds and str(i+1) in odds:
            boat_odds = odds[str(i+1)] or 0
        elif boats[i].get('odds'):
            boat_odds = boats[i]['odds']
        ev = round(probs[i] * boat_odds, 4) if boat_odds > 0 else None
        
        # 各ファクターの恩恵を記録 (1.0基準)
        factor = {
            'base': round(scores[i] / max(0.001, total), 4), # ベース確率貢献度
            'motor': round(1.0 + (motors[i] / max(1, avg_motor) - 1.0) * fw['motor'], 3) if motors[i] > 0 else 1.0,
        }
        
        if sts[i] > 0:
            diff = sts[i] - 0.13
            factor['st'] = round(1.0 + diff * 1.5 if diff <= 0 else 1.0 - diff * fw['st'], 3)
        else:
            factor['st'] = 1.0
            
        results.append({
            'boat': i+1, 'prob': round(probs[i], 4),
            'ev': ev, 'odds': boat_odds,
            'factors': factor
        })
    
    top = max(results, key=lambda x: x['prob'])
    # EV最大の艇も記録（オッズとの乖離=妙味ある艇）
    ev_top = max((r for r in results if r['ev'] is not None and r['ev'] > 0),
                 key=lambda x: x['ev'], default=None)
    
    return {
        'topPick': top['boat'], 'topProb': top['prob'],
        'topEV': top['ev'], 'allProbs': results,
        'evPick': ev_top['boat'] if ev_top else None,
        'evValue': ev_top['ev'] if ev_top else None,
    }


def update_weights(fw, race, boats, pred, result_order, wrs, motors, sts, weather, weights_list):
    """
    予測結果と実際の結果を比較し、ファクター重みを逐次更新
    学習率α=0.01で緩やかに補正
    """
    if not result_order:
        return
    
    sorted_order = sorted(result_order, key=lambda x: x.get('rank', 99))
    winner = sorted_order[0].get('boat', 0) if sorted_order else 0
    if winner == 0 or winner > 6:
        return
    
    winner_idx = winner - 1
    alpha = 0.01  # 学習率
    
    # --- モーター補正の学習 ---
    # 実際の勝者のモーター2連対率が平均より上→モーター重みを増やす、下→減らす
    avg_motor = sum(m for m in motors if m > 0) / max(1, sum(1 for m in motors if m > 0))
    if motors[winner_idx] > 0 and avg_motor > 0:
        if motors[winner_idx] > avg_motor * 1.1:
            fw['motor'] += alpha  # モーター上位が勝った→モーター重要
        elif motors[winner_idx] < avg_motor * 0.9:
            fw['motor'] -= alpha * 0.5  # モーター下位が勝った→モーター過大評価
        fw['motor'] = max(0.01, min(0.30, fw['motor']))
    
    # --- ST補正の学習 ---
    if sts[winner_idx] > 0:
        if sts[winner_idx] < 0.15:
            fw['st'] += alpha  # 速いSTの選手が勝った→ST重要
        elif sts[winner_idx] > 0.18:
            fw['st'] -= alpha  # 遅いSTの選手が勝った→ST過大評価
        fw['st'] = max(0.5, min(4.0, fw['st']))
    
    # --- 風補正の学習 ---
    if weather:
        wind_speed = weather.get('windSpeed', 0) or 0
        wind_dir = weather.get('windDir', 0) or 0
        is_headwind = 7 <= wind_dir <= 11
        
        if wind_speed >= 3 and is_headwind:
            if winner != 1:
                fw['wind_head'] += alpha  # 向かい風で1コース敗北→向かい風影響大
            else:
                fw['wind_head'] -= alpha * 0.5
            fw['wind_head'] = max(0.01, min(0.20, fw['wind_head']))
    
    # --- 体重補正の学習 ---
    avg_w = sum(w for w in weights_list if w > 0) / max(1, sum(1 for w in weights_list if w > 0))
    if weights_list[winner_idx] > 0 and avg_w > 0:
        if weights_list[winner_idx] < avg_w - 2:
            fw['weight'] += alpha * 0.5  # 軽量選手が勝った→体重重要
        fw['weight'] = max(0.001, min(0.02, fw['weight']))
    
    # --- 隣接コース補正の学習 ---
    if winner_idx > 0 and wrs[winner_idx] > wrs[winner_idx-1] + 1.5:
        fw['adj_bonus'] += alpha  # 格下隣接を抜いた→ボーナス効果確認
    elif winner == 1:
        fw['adj_bonus'] -= alpha * 0.3  # 1コースが勝った→外コース補正過大
    fw['adj_bonus'] = max(0.01, min(0.10, fw['adj_bonus']))


def process_all_races():
    db_path = os.path.join(DIR, 'race_db.json')
    if not os.path.exists(db_path):
        print("race_db.json not found")
        return
    
    with open(db_path, 'r', encoding='utf-8') as f:
        db = json.load(f)
    
    racer_db = load_racer_db()
    
    races = db.get('races', [])
    print(f"Total {len(races)} races")
    
    by_date = defaultdict(list)
    for race in races:
        d = race.get('date', '')
        if d:
            by_date[d].append(race)
    
    dates = sorted(by_date.keys())  # 古い順!
    if not dates:
        return
    print(f"{len(dates)} days: {dates[0]} - {dates[-1]}")
    
    # 既存race_dbから選手のパフォーマンスインデックスを構築（新たなスクレイピング不要）
    perf_index = build_performance_index(races)
    
    # 自己修正ファクター重み（古い日から順に更新される）
    fw = DEFAULT_WEIGHTS.copy()
    
    predictions = []
    stats = {'total': 0, 'withResult': 0, 'hit': 0, 'miss': 0, 'ev_hit': 0, 'ev_miss': 0}
    daily_stats = {}
    pick_dist = defaultdict(lambda: {'total': 0, 'hit': 0})
    ev_pick_dist = defaultdict(lambda: {'total': 0, 'hit': 0})
    
    for date in dates:
        day_races = by_date[date]
        day_hit = 0
        day_miss = 0
        
        for race in day_races:
            boats = race.get('boats', [])
            if not boats or len(boats) < 6:
                continue
            
            # レーサーDBでデータ補完
            for b in boats:
                toban = b.get('toban', '')
                if toban and racer_db:
                    wr, grade, st = get_racer_wr(racer_db, toban)
                    if wr > 0 and not b.get('winRate'):
                        b['winRate'] = wr
                    if grade and not b.get('grade'):
                        b['grade'] = grade
                    if st > 0 and not b.get('avgST'):
                        b['avgST'] = st
            
            if not any(b.get('winRate', 0) for b in boats):
                continue
            
            weather = race.get('weather', None)
            odds = race.get('odds', None)
            venue = race.get('venue', '')
            
            # 現在のfwで予測
            pred = predict_race(boats, venue, racer_db, weather, odds, fw, perf_index=perf_index)
            if not pred:
                continue
            
            stats['total'] += 1
            pick_dist[pred['topPick']]['total'] += 1
            if pred.get('evPick'):
                ev_pick_dist[pred['evPick']]['total'] += 1
            
            entry = {
                'date': date, 'venue': venue,
                'venueName': race.get('venueName', ''),
                'raceNo': race.get('raceNo', 0),
                'topPick': pred['topPick'],
                'topProb': pred['topProb'],
                'topEV': pred['topEV'],
                'evPick': pred.get('evPick'),
                'evValue': pred.get('evValue'),
                'allProbs': pred.get('allProbs', []),  # 各艇の確率・EV・factors
            }
            
            result = race.get('result', {})
            order = result.get('order', [])
            if order:
                stats['withResult'] += 1
                sorted_order = sorted(order, key=lambda x: x.get('rank', 99))
                first = sorted_order[0] if sorted_order else None
                
                entry['result'] = {
                    'order': sorted_order[:3],
                    'winner': first['boat'] if first else None,
                    'tansho_payout': result.get('tansho_payout', 0)
                }
                
                if first and first.get('boat') == pred['topPick']:
                    entry['hit'] = True
                    stats['hit'] += 1
                    day_hit += 1
                    pick_dist[pred['topPick']]['hit'] += 1
                else:
                    entry['hit'] = False
                    stats['miss'] += 1
                    day_miss += 1
                
                if pred.get('evPick') and first:
                    if first['boat'] == pred['evPick']:
                        stats['ev_hit'] += 1
                        ev_pick_dist[pred['evPick']]['hit'] += 1
                    else:
                        stats['ev_miss'] += 1
                
                # === 自己修正: 結果からファクター重みを更新 ===
                wrs = []
                motors_list = []
                sts_list = []
                weights_list = []
                for b in boats:
                    toban = b.get('toban', '')
                    wr, _, st = get_racer_wr(racer_db, toban)
                    wrs.append(wr if wr > 0 else (b.get('winRate', 0) or 3.5))
                    sts_list.append(st if st > 0 else (b.get('avgST', 0) or 0))
                    motors_list.append(b.get('motor2ren', 0) or 0)
                    weights_list.append(b.get('weight', 0) or 52)
                
                update_weights(fw, race, boats, pred, order, wrs, motors_list, sts_list, weather, weights_list)
            
            entry['boatNames'] = [b.get('name', '') for b in boats]
            predictions.append(entry)
        
        if (day_hit + day_miss) > 0:
            day_rate = round(day_hit / (day_hit + day_miss) * 100, 1)
            daily_stats[date] = {'hit': day_hit, 'miss': day_miss, 'rate': day_rate}
            d_str = f"{date[:4]}/{date[4:6]}/{date[6:]}"
            print(f"  {d_str}: {day_rate}% ({day_hit}/{day_hit+day_miss}) fw: motor={fw['motor']:.3f} st={fw['st']:.2f} wind={fw['wind_head']:.3f}")
    
    hit_rate = round(stats['hit'] / stats['withResult'] * 100, 1) if stats['withResult'] > 0 else 0
    ev_hit_rate = round(stats['ev_hit'] / (stats['ev_hit'] + stats['ev_miss']) * 100, 1) if (stats['ev_hit'] + stats['ev_miss']) > 0 else 0
    
    print(f"\nPick distribution (prob-based):")
    for pick in sorted(pick_dist.keys()):
        pd = pick_dist[pick]
        phr = pd['hit']/pd['total']*100 if pd['total'] > 0 else 0
        print(f"  Boat {pick}: {pd['total']} picks, {pd['hit']} hits ({phr:.1f}%)")
    
    print(f"\nEV-based pick distribution:")
    for pick in sorted(ev_pick_dist.keys()):
        pd = ev_pick_dist[pick]
        phr = pd['hit']/pd['total']*100 if pd['total'] > 0 else 0
        print(f"  Boat {pick}: {pd['total']} picks, {pd['hit']} hits ({phr:.1f}%)")
    
    print(f"\nFinal factor weights (after self-correction):")
    for k, v in fw.items():
        print(f"  {k}: {v:.4f}")
    
    daily_summary = [{'date': d, **daily_stats[d]} for d in sorted(daily_stats.keys(), reverse=True)]
    
    output = {
        'generated': datetime.now().strftime('%Y-%m-%d %H:%M'),
        'modelVersion': 'v8-self-correcting',
        'stats': {
            'totalPredicted': stats['total'],
            'withResult': stats['withResult'],
            'hit': stats['hit'], 'miss': stats['miss'], 'hitRate': hit_rate,
            'evHit': stats['ev_hit'], 'evMiss': stats['ev_miss'], 'evHitRate': ev_hit_rate,
        },
        'model': {
            'factors': ['コース×勝率帯テーブル', '力量差マッチアップ', 'モーター2連対率',
                       'ST力', '天候/風/波/水温', '体重差', '隣接コース力量関係'],
            'finalWeights': {k: round(v, 4) for k, v in fw.items()},
        },
        'dailySummary': daily_summary,
        'predictions': sorted(predictions, key=lambda x: (x['date'], x['venue'], x['raceNo']), reverse=True)
    }
    
    out_path = os.path.join(DIR, 'predictions.json')
    with open(out_path, 'w', encoding='utf-8') as f:
        json.dump(output, f, ensure_ascii=False, separators=(',', ':'))
    
    print(f"\n{'='*40}")
    print(f"Model: v8-self-correcting")
    print(f"Factors: 7 + self-correction loop")
    print(f"Predicted: {stats['total']}")
    print(f"Hit: {stats['hit']} / Miss: {stats['miss']}")
    print(f"Hit Rate: {hit_rate}%")
    print(f"EV-based Hit Rate: {ev_hit_rate}%")
    print(f"{'='*40}")


if __name__ == '__main__':
    process_all_races()

