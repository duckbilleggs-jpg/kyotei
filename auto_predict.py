"""
全レース自動予測スクリプト（自己学習型 v3 - ウォークフォワード）

正しい学習フロー:
1. レースを日付順にソート
2. 各日のレースを予測する時は「その日より前のデータだけ」で学習
3. これにより "未来を見てカンニング" を防ぎ、真の予測精度を計測

学習ファクター:
- 会場×コース別の1着率（実データベース）
- 体重差の影響（軽い方が有利）
- 風向き×コースの影響（追い風=イン有利、向かい風=アウト有利）
"""
import json, os, math
from datetime import datetime
from collections import defaultdict

DIR = os.path.dirname(os.path.abspath(__file__))

# 基本コース補正（学習データがない初期状態で使用）
DEFAULT_COURSE_BONUS = [1.8, 1.1, 1.05, 1.0, 0.95, 0.85]

def learn_from_races(races):
    """
    与えられたレース群から統計モデルを構築する
    ※ウォークフォワードでは「予測対象日より前のレース」のみを渡す
    """
    venue_course_wins = defaultdict(lambda: defaultdict(int))
    venue_course_total = defaultdict(lambda: defaultdict(int))
    
    # 体重分析用
    weight_light_win = 0
    weight_heavy_win = 0
    weight_total = 0
    
    # 風向き×コース
    wind_course = {
        'headwind': defaultdict(lambda: {'win': 0, 'total': 0}),
        'tailwind': defaultdict(lambda: {'win': 0, 'total': 0}),
        'calm':     defaultdict(lambda: {'win': 0, 'total': 0})
    }
    
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
        
        winner = first['boat']  # 1-6
        wi = winner - 1
        
        # ---- 会場×コース集計 ----
        for i in range(6):
            venue_course_total[venue][i] += 1
        venue_course_wins[venue][wi] += 1
        
        # ---- 体重分析 ----
        weights = [b.get('weight', 0) for b in boats]
        if all(w > 0 for w in weights):
            avg_w = sum(weights) / len(weights)
            winner_w = weights[wi]
            weight_total += 1
            if winner_w < avg_w - 0.5:
                weight_light_win += 1
            elif winner_w > avg_w + 0.5:
                weight_heavy_win += 1
        
        # ---- 風向き×コース ----
        ws = weather.get('windSpeed', 0)
        wd = weather.get('windDir', 0)
        if ws >= 3:
            if 6 <= wd <= 12:
                wt = 'tailwind'
            elif wd <= 4 or wd >= 14:
                wt = 'headwind'
            else:
                wt = 'calm'
        else:
            wt = 'calm'
        
        for i in range(6):
            wind_course[wt][i]['total'] += 1
        wind_course[wt][wi]['win'] += 1
    
    # ---- コース補正値を計算 ----
    venue_corr = {}
    for venue in venue_course_total:
        total = venue_course_total[venue][0]
        if total < 5:
            continue
        corr = []
        for i in range(6):
            w = venue_course_wins[venue].get(i, 0)
            rate = w / total if total > 0 else 1/6
            corr.append(rate / (1/6))  # 期待値との比率
        venue_corr[venue] = corr
    
    # ---- 体重ファクター ----
    wf = 1.0
    if weight_total >= 10:
        lr = weight_light_win / max(weight_total, 1)
        hr = weight_heavy_win / max(weight_total, 1)
        if hr > 0:
            wf = lr / hr
        elif lr > 0:
            wf = 1.5
    
    # ---- 風補正 ----
    wind_corr = {}
    for wt in ['headwind', 'tailwind', 'calm']:
        c = []
        for i in range(6):
            t = wind_course[wt][i]['total']
            w = wind_course[wt][i]['win']
            if t >= 10:
                c.append((w / t) / (1/6))
            else:
                c.append(1.0)
        wind_corr[wt] = c
    
    return {
        'venue_corr': venue_corr,
        'weight_factor': round(wf, 3),
        'wind_corr': wind_corr,
        'train_count': len(races)
    }


def predict_race(boats, venue, model, weather=None):
    """学習済みモデルでレースを予測"""
    vc = model.get('venue_corr', {})
    wc = model.get('wind_corr', {})
    wf = model.get('weight_factor', 1.0)
    
    course_bonus = vc.get(venue, DEFAULT_COURSE_BONUS)
    
    scores = []
    all_weights = [b.get('weight', 0) for b in boats]
    has_weight = all(w > 0 for w in all_weights)
    avg_weight = sum(all_weights) / len(all_weights) if has_weight else 52.0
    
    for i, boat in enumerate(boats):
        wr = boat.get('winRate', 0) or 0
        wr2 = boat.get('winRate2', 0) or 0  # 当地勝率
        if wr <= 0:
            wr = 3.0
        
        # ベーススコア: 全国勝率と当地勝率の加重平均
        # 当地勝率がある場合、会場慣れを反映（当地を重視）
        if wr2 > 0:
            base_wr = wr * 0.6 + wr2 * 0.4  # 当地勝率を40%反映
        else:
            base_wr = wr
        
        # ① コース補正（会場データがあれば実データ、なければデフォルト）
        score = base_wr * course_bonus[i]
        
        # ② 体重補正（軽い=有利）
        if has_weight:
            diff = avg_weight - all_weights[i]  # 軽い方がプラス
            adj = 1.0 + diff * 0.015 * max(wf - 0.5, 0.5)
            score *= max(min(adj, 1.3), 0.7)
        
        # ③ 風向き補正
        if weather and weather.get('windSpeed', 0) >= 3:
            wd = weather.get('windDir', 0)
            if 6 <= wd <= 12:
                wt = 'tailwind'
            elif wd <= 4 or wd >= 14:
                wt = 'headwind'
            else:
                wt = 'calm'
            if wt in wc and len(wc[wt]) > i:
                score *= wc[wt][i]
        
        scores.append(max(score, 0.01))
    
    total = sum(scores)
    if total == 0:
        return None
    probs = [s / total for s in scores]
    
    results = []
    for i in range(len(boats)):
        odds = boats[i].get('odds', 0) or 0
        ev = round(probs[i] * odds, 4) if odds > 0 else None
        results.append({'boat': i+1, 'prob': round(probs[i], 4), 'ev': ev, 'odds': odds})
    
    top = max(results, key=lambda x: x['prob'])
    return {'topPick': top['boat'], 'topProb': top['prob'], 'topEV': top['ev'], 'allProbs': results}


def process_all_races():
    db_path = os.path.join(DIR, 'race_db.json')
    if not os.path.exists(db_path):
        print("❌ race_db.json が見つかりません")
        return
    
    with open(db_path, 'r', encoding='utf-8') as f:
        db = json.load(f)
    
    races = db.get('races', [])
    print(f"📊 全{len(races)}レースを処理中...")
    
    # ===== 日付ごとにグループ化 =====
    by_date = defaultdict(list)
    for race in races:
        d = race.get('date', '')
        if d:
            by_date[d].append(race)
    
    dates = sorted(by_date.keys())
    print(f"📅 {len(dates)}日分のデータ: {dates[0]}〜{dates[-1]}")
    
    # ===== ウォークフォワード学習 =====
    print("\n🧠 ウォークフォワード学習開始...")
    print("   （各日のレースを予測する時、その日より前のデータだけで学習）\n")
    
    all_past_races = []  # 過去のレースを蓄積
    predictions = []
    stats = {'total': 0, 'withResult': 0, 'hit': 0, 'miss': 0}
    daily_stats = {}
    venue_stats = defaultdict(lambda: {'total': 0, 'hit': 0})
    
    for di, date in enumerate(dates):
        day_races = by_date[date]
        
        # その日より前のデータで学習
        if all_past_races:
            model = learn_from_races(all_past_races)
            train_info = f"学習:{len(all_past_races)}レース"
        else:
            # 初日はデフォルトモデル
            model = {
                'venue_corr': {},
                'weight_factor': 1.0,
                'wind_corr': {},
                'train_count': 0
            }
            train_info = "学習:初期モデル"
        
        day_hit = 0
        day_miss = 0
        day_total = 0
        
        for race in day_races:
            boats = race.get('boats', [])
            if not boats or len(boats) < 6:
                continue
            if not any(b.get('winRate', 0) for b in boats):
                continue
            
            weather = race.get('weather', None)
            venue = race.get('venue', '')
            
            pred = predict_race(boats, venue, model, weather)
            if not pred:
                continue
            
            stats['total'] += 1
            day_total += 1
            
            entry = {
                'date': date,
                'venue': venue,
                'venueName': race.get('venueName', ''),
                'raceNo': race.get('raceNo', 0),
                'topPick': pred['topPick'],
                'topProb': pred['topProb'],
                'topEV': pred['topEV'],
            }
            if weather:
                entry['weather'] = weather
            
            # 結果判定
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
                else:
                    entry['hit'] = False
                    stats['miss'] += 1
                    day_miss += 1
                
                venue_stats[venue]['total'] += 1
                if entry['hit']:
                    venue_stats[venue]['hit'] += 1
            else:
                entry['result'] = None
                entry['hit'] = None
            
            entry['boatNames'] = [b.get('name', '') for b in boats]
            predictions.append(entry)
        
        # 日別集計
        if day_total > 0:
            day_rate = round(day_hit / (day_hit + day_miss) * 100, 1) if (day_hit + day_miss) > 0 else 0
            daily_stats[date] = {
                'total': day_total, 'hit': day_hit, 'miss': day_miss, 'rate': day_rate
            }
            d_str = f"{date[:4]}/{date[4:6]}/{date[6:]}"
            print(f"  {d_str}: {day_rate}% ({day_hit}勝{day_miss}敗) ← {train_info}")
        
        # この日のレースを過去データに追加（次の日の学習用）
        all_past_races.extend(day_races)
    
    # ===== 最終集計 =====
    hit_rate = round(stats['hit'] / stats['withResult'] * 100, 1) if stats['withResult'] > 0 else 0
    
    daily_summary = []
    for date in sorted(daily_stats.keys(), reverse=True):
        d = daily_stats[date]
        daily_summary.append({
            'date': date, 'total': d['total'],
            'hit': d['hit'], 'miss': d['miss'], 'rate': d['rate']
        })
    
    venue_summary = []
    for v in sorted(venue_stats.keys()):
        vs = venue_stats[v]
        rate = round(vs['hit'] / vs['total'] * 100, 1) if vs['total'] > 0 else 0
        venue_summary.append({'venue': v, 'total': vs['total'], 'hit': vs['hit'], 'rate': rate})
    
    # フロントエンド用のJSでも使えるように、学習モデルも保存
    # （最新の全データで学習した最終モデル = 今後の予測用）
    final_model = learn_from_races(all_past_races)
    
    output = {
        'generated': datetime.now().strftime('%Y-%m-%d %H:%M'),
        'modelVersion': 'v3-walkforward',
        'stats': {
            'totalPredicted': stats['total'],
            'withResult': stats['withResult'],
            'hit': stats['hit'],
            'miss': stats['miss'],
            'hitRate': hit_rate,
            'totalTrainingRaces': len(all_past_races)
        },
        'model': {
            'weightFactor': final_model['weight_factor'],
            'venueCount': len(final_model['venue_corr']),
            'method': 'walk-forward (past data only)'
        },
        'dailySummary': daily_summary,
        'venueSummary': venue_summary,
        'predictions': sorted(predictions,
            key=lambda x: (x['date'], x['venue'], x['raceNo']), reverse=True)
    }
    
    out_path = os.path.join(DIR, 'predictions.json')
    with open(out_path, 'w', encoding='utf-8') as f:
        json.dump(output, f, ensure_ascii=False, separators=(',', ':'))
    
    print(f"\n{'='*40}")
    print(f"✅ predictions.json 生成完了！")
    print(f"   方式: ウォークフォワード（過去データのみで学習→未来を予測）")
    print(f"   予測レース数: {stats['total']}")
    print(f"   結果判明: {stats['withResult']}")
    print(f"   的中: {stats['hit']} / ハズレ: {stats['miss']}")
    print(f"   的中率: {hit_rate}%")
    print(f"{'='*40}")


if __name__ == '__main__':
    process_all_races()
