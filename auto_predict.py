"""
全レース自動予測スクリプト（自己学習型 v2）
過去の全結果データから学習し、予測精度を向上させる

学習フロー:
1. race_db.json の結果付きレースを分析
2. 会場×コース別の1着率を統計計算（実データベース）
3. 体重差・気象条件の影響を分析
4. 学習済みモデルで全レースを予測
"""
import json, os, math
from datetime import datetime
from collections import defaultdict

DIR = os.path.dirname(os.path.abspath(__file__))

# --- 基本コース補正（初期値、データ不足時のフォールバック） ---
DEFAULT_COURSE_BONUS = [1.8, 1.1, 1.05, 1.0, 0.95, 0.85]

def learn_from_data(races):
    """
    過去の結果付きレースから統計を学習する
    Returns: 学習済みモデルパラメータ
    """
    # === 会場×コース別の1着率を集計 ===
    venue_course_wins = defaultdict(lambda: defaultdict(int))   # venue -> course -> wins
    venue_course_total = defaultdict(lambda: defaultdict(int))  # venue -> course -> total
    
    # === 体重と勝率の関係 ===
    weight_wins = {'light': 0, 'normal': 0, 'heavy': 0, 'light_total': 0, 'normal_total': 0, 'heavy_total': 0}
    
    # === 風向き×コースの勝率 ===
    # 風向き: 追い風(向かい風の反対) → 1号艇有利、向かい風 → 外枠チャンス
    wind_course_wins = {'headwind': defaultdict(int), 'tailwind': defaultdict(int), 'calm': defaultdict(int)}
    wind_course_total = {'headwind': defaultdict(int), 'tailwind': defaultdict(int), 'calm': defaultdict(int)}
    
    # === 波高とインコース勝率 ===
    wave_stats = {'high_in_win': 0, 'high_total': 0, 'low_in_win': 0, 'low_total': 0}
    
    analyzed = 0
    
    for race in races:
        result = race.get('result', {})
        order = result.get('order', [])
        boats = race.get('boats', [])
        venue = race.get('venue', '')
        weather = race.get('weather', {})
        
        if not order or len(boats) < 6:
            continue
        
        first = next((o for o in order if o.get('rank') == 1), None)
        if not first:
            continue
        
        winner_boat = first['boat']  # 1着の艇番 (1-6)
        winner_idx = winner_boat - 1
        analyzed += 1
        
        # 会場×コース集計
        for i in range(6):
            venue_course_total[venue][i] += 1
        venue_course_wins[venue][winner_idx] += 1
        
        # --- 体重分析 ---
        if boats and all(b.get('weight') for b in boats):
            weights = [b['weight'] for b in boats]
            avg_w = sum(weights) / len(weights)
            winner_w = weights[winner_idx]
            
            if winner_w < avg_w - 1:
                weight_wins['light'] += 1
            elif winner_w > avg_w + 1:
                weight_wins['heavy'] += 1
            else:
                weight_wins['normal'] += 1
            weight_wins['light_total'] += sum(1 for w in weights if w < avg_w - 1) or 1
            weight_wins['normal_total'] += sum(1 for w in weights if avg_w - 1 <= w <= avg_w + 1) or 1
            weight_wins['heavy_total'] += sum(1 for w in weights if w > avg_w + 1) or 1
        
        # --- 風向き分析 ---
        wind_speed = weather.get('windSpeed', 0)
        wind_dir = weather.get('windDir', 0)
        
        if wind_speed >= 3:
            # 風向き16方位: 追い風(南系 8-12) vs 向かい風(北系 0-4, 14-16)
            if 6 <= wind_dir <= 12:
                wind_type = 'tailwind'
            elif wind_dir <= 4 or wind_dir >= 14:
                wind_type = 'headwind'
            else:
                wind_type = 'calm'
        else:
            wind_type = 'calm'
        
        for i in range(6):
            wind_course_total[wind_type][i] += 1
        wind_course_wins[wind_type][winner_idx] += 1
        
        # --- 波高分析 ---
        wave = weather.get('waveHeight', 0)
        if wave >= 5:
            wave_stats['high_total'] += 1
            if winner_boat <= 2:
                wave_stats['high_in_win'] += 1
        else:
            wave_stats['low_total'] += 1
            if winner_boat <= 2:
                wave_stats['low_in_win'] += 1
    
    # === コース別補正値を計算 ===
    venue_corrections = {}
    for venue in venue_course_total:
        corrections = []
        total = venue_course_total[venue][0]  # 各コースの母数は同じ
        if total < 5:  # データ少なすぎ
            continue
        for i in range(6):
            wins = venue_course_wins[venue].get(i, 0)
            rate = wins / total if total > 0 else 1/6
            # 期待値(1/6)との比率を補正係数に
            corrections.append(rate / (1/6))
        venue_corrections[venue] = corrections
    
    # === 体重補正係数 ===
    weight_factor = 1.0
    if weight_wins['light_total'] > 0 and weight_wins['heavy_total'] > 0:
        light_rate = weight_wins['light'] / max(weight_wins['light_total'], 1)
        heavy_rate = weight_wins['heavy'] / max(weight_wins['heavy_total'], 1)
        if light_rate > 0 and heavy_rate > 0:
            weight_factor = light_rate / heavy_rate
    
    # === 風向き補正 ===
    wind_corrections = {}
    for wt in ['headwind', 'tailwind', 'calm']:
        corr = []
        for i in range(6):
            total = wind_course_total[wt].get(i, 0)
            wins = wind_course_wins[wt].get(i, 0)
            if total >= 10:
                corr.append((wins / total) / (1/6))
            else:
                corr.append(1.0)
        wind_corrections[wt] = corr
    
    model = {
        'venue_corrections': venue_corrections,
        'weight_factor': round(weight_factor, 3),
        'wind_corrections': wind_corrections,
        'wave_stats': wave_stats,
        'analyzed_races': analyzed
    }
    
    print(f"📚 学習完了: {analyzed}レースから統計を抽出")
    print(f"   会場別データ: {len(venue_corrections)}会場")
    print(f"   体重優位性: 軽量{weight_factor:.2f}倍")
    
    return model


def predict_race(boats, venue, model, weather=None):
    """
    学習済みモデルを使ってレースを予測する
    """
    vc = model.get('venue_corrections', {})
    wc = model.get('wind_corrections', {})
    wf = model.get('weight_factor', 1.0)
    
    # コース別基本補正（会場データがあれば使う、なければデフォルト）
    if venue in vc:
        course_bonus = vc[venue]
    else:
        course_bonus = DEFAULT_COURSE_BONUS
    
    scores = []
    for i, boat in enumerate(boats):
        wr = boat.get('winRate', 0) or 0
        if wr <= 0:
            wr = 3.0
        
        # ① コース補正（学習済み会場データ）
        score = wr * course_bonus[i]
        
        # ② 体重補正
        weight = boat.get('weight', 0)
        if weight > 0:
            avg_weight = 52.0  # 競艇の標準体重目安
            all_weights = [b.get('weight', 52) for b in boats]
            if all(w > 0 for w in all_weights):
                avg_weight = sum(all_weights) / len(all_weights)
            
            diff = avg_weight - weight  # 軽い方がプラス
            # 1kg軽いと約2%有利（学習済みweight_factorで調整）
            weight_adj = 1.0 + diff * 0.02 * (wf - 1) if wf != 1 else 1.0 + diff * 0.015
            score *= max(weight_adj, 0.7)  # 極端な補正を制限
        
        # ③ 風向き補正
        if weather:
            wind_speed = weather.get('windSpeed', 0)
            wind_dir = weather.get('windDir', 0)
            
            if wind_speed >= 3:
                if 6 <= wind_dir <= 12:
                    wt = 'tailwind'
                elif wind_dir <= 4 or wind_dir >= 14:
                    wt = 'headwind'
                else:
                    wt = 'calm'
                
                if wt in wc and len(wc[wt]) > i:
                    score *= wc[wt][i]
        
        scores.append(max(score, 0.1))
    
    # 正規化
    total = sum(scores)
    if total == 0:
        return None
    probs = [s / total for s in scores]
    
    # 期待値
    results = []
    for i in range(len(boats)):
        odds = boats[i].get('odds', 0) or 0
        ev = round(probs[i] * odds, 4) if odds > 0 else None
        results.append({
            'boat': i + 1,
            'prob': round(probs[i], 4),
            'ev': ev,
            'odds': odds
        })
    
    top = max(results, key=lambda x: x['prob'])
    
    return {
        'topPick': top['boat'],
        'topProb': top['prob'],
        'topEV': top['ev'],
        'allProbs': results
    }


def process_all_races():
    """race_db.jsonの全レースに対して学習→予測を実行"""
    db_path = os.path.join(DIR, 'race_db.json')
    if not os.path.exists(db_path):
        print("❌ race_db.json が見つかりません")
        return
    
    with open(db_path, 'r', encoding='utf-8') as f:
        db = json.load(f)
    
    races = db.get('races', [])
    print(f"📊 全{len(races)}レースを処理中...")
    
    # ===== Phase 1: 過去データから学習 =====
    print("\n🧠 Phase 1: 過去データから学習中...")
    model = learn_from_data(races)
    
    # ===== Phase 2: 学習済みモデルで全レース予測 =====
    print("\n🎯 Phase 2: 学習済みモデルで予測中...")
    
    predictions = []
    stats = {'total': 0, 'withResult': 0, 'hit': 0, 'miss': 0}
    daily_stats = {}
    venue_stats = defaultdict(lambda: {'total': 0, 'hit': 0})
    
    for race in races:
        boats = race.get('boats', [])
        if not boats or len(boats) < 6:
            continue
        
        has_wr = any(b.get('winRate', 0) for b in boats)
        if not has_wr:
            continue
        
        weather = race.get('weather', None)
        venue = race.get('venue', '')
        
        pred = predict_race(boats, venue, model, weather)
        if not pred:
            continue
        
        stats['total'] += 1
        
        entry = {
            'date': race.get('date', ''),
            'venue': venue,
            'venueName': race.get('venueName', ''),
            'raceNo': race.get('raceNo', 0),
            'topPick': pred['topPick'],
            'topProb': pred['topProb'],
            'topEV': pred['topEV'],
        }
        
        # 気象情報も保持
        if weather:
            entry['weather'] = weather
        
        # 結果判定
        result = race.get('result', {})
        order = result.get('order', [])
        if order and len(order) >= 1:
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
            else:
                entry['hit'] = False
                stats['miss'] += 1
            
            # 日別集計
            date = race.get('date', '')
            if date not in daily_stats:
                daily_stats[date] = {'total': 0, 'hit': 0, 'miss': 0}
            daily_stats[date]['total'] += 1
            if entry['hit']:
                daily_stats[date]['hit'] += 1
            else:
                daily_stats[date]['miss'] += 1
            
            # 会場別集計
            venue_stats[venue]['total'] += 1
            if entry['hit']:
                venue_stats[venue]['hit'] += 1
        else:
            entry['result'] = None
            entry['hit'] = None
        
        entry['boatNames'] = [b.get('name', '') for b in boats]
        predictions.append(entry)
    
    # 的中率
    hit_rate = round(stats['hit'] / stats['withResult'] * 100, 1) if stats['withResult'] > 0 else 0
    
    # 日別サマリー
    daily_summary = []
    for date in sorted(daily_stats.keys(), reverse=True):
        d = daily_stats[date]
        rate = round(d['hit'] / d['total'] * 100, 1) if d['total'] > 0 else 0
        daily_summary.append({
            'date': date, 'total': d['total'],
            'hit': d['hit'], 'miss': d['miss'], 'rate': rate
        })
    
    # 会場別サマリー
    venue_summary = []
    for v in sorted(venue_stats.keys()):
        vs = venue_stats[v]
        rate = round(vs['hit'] / vs['total'] * 100, 1) if vs['total'] > 0 else 0
        venue_summary.append({
            'venue': v, 'total': vs['total'], 'hit': vs['hit'],
            'rate': rate
        })
    
    # 出力
    output = {
        'generated': datetime.now().strftime('%Y-%m-%d %H:%M'),
        'modelVersion': 'v2-learning',
        'stats': {
            'totalPredicted': stats['total'],
            'withResult': stats['withResult'],
            'hit': stats['hit'],
            'miss': stats['miss'],
            'hitRate': hit_rate,
            'analyzedForLearning': model['analyzed_races']
        },
        'model': {
            'weightFactor': model['weight_factor'],
            'venueCount': len(model['venue_corrections']),
        },
        'dailySummary': daily_summary,
        'venueSummary': venue_summary,
        'predictions': sorted(predictions, key=lambda x: (x['date'], x['venue'], x['raceNo']), reverse=True)
    }
    
    out_path = os.path.join(DIR, 'predictions.json')
    with open(out_path, 'w', encoding='utf-8') as f:
        json.dump(output, f, ensure_ascii=False, separators=(',', ':'))
    
    print(f"\n✅ predictions.json 生成完了！")
    print(f"   学習レース数: {model['analyzed_races']}")
    print(f"   予測レース数: {stats['total']}")
    print(f"   結果判明: {stats['withResult']}")
    print(f"   的中: {stats['hit']} / ハズレ: {stats['miss']}")
    print(f"   的中率: {hit_rate}%")


if __name__ == '__main__':
    process_all_races()
