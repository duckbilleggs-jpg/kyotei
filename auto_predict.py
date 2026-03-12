"""
全レース自動予測スクリプト
race_db.json の全レースに対してAI予測を実行し、
結果がある場合は的中/ハズレを自動判定、predictions.json に出力
"""
import json, os
from datetime import datetime

DIR = os.path.dirname(os.path.abspath(__file__))

# コース補正（1号艇が有利）- JSと同じ値
COURSE_BONUS = [1.8, 1.1, 1.05, 1.0, 0.95, 0.85]

def predict_race(boats):
    """
    レースの予測を行う（JSのcalculateProbabilities と同一ロジック）
    boats: list of dict with 'winRate', 'odds' etc.
    """
    # 各艇のスコア計算
    scores = []
    for i, boat in enumerate(boats):
        wr = boat.get('winRate', 0) or 0
        if wr <= 0:
            wr = 3.0  # デフォルト
        score = wr * COURSE_BONUS[i]
        scores.append(score)
    
    # 正規化
    total = sum(scores)
    if total == 0:
        return None
    probs = [s / total for s in scores]
    
    # 期待値計算
    evs = []
    for i, boat in enumerate(boats):
        odds = boat.get('odds', 0) or 0
        if odds > 0:
            evs.append(probs[i] * odds)
        else:
            evs.append(None)
    
    # 結果
    results = []
    for i in range(len(boats)):
        results.append({
            'boat': i + 1,
            'prob': round(probs[i], 4),
            'ev': round(evs[i], 4) if evs[i] is not None else None,
            'odds': boats[i].get('odds', 0) or 0
        })
    
    # 予測1着（確率最高の艇）
    top = max(results, key=lambda x: x['prob'])
    
    return {
        'topPick': top['boat'],
        'topProb': top['prob'],
        'topEV': top['ev'],
        'allProbs': results
    }

def process_all_races():
    """race_db.jsonの全レースに対して予測を実行"""
    db_path = os.path.join(DIR, 'race_db.json')
    if not os.path.exists(db_path):
        print("❌ race_db.json が見つかりません")
        return
    
    with open(db_path, 'r', encoding='utf-8') as f:
        db = json.load(f)
    
    races = db.get('races', [])
    print(f"📊 全{len(races)}レースを処理中...")
    
    predictions = []
    stats = {'total': 0, 'withResult': 0, 'hit': 0, 'miss': 0}
    daily_stats = {}
    
    for race in races:
        boats = race.get('boats', [])
        if not boats or len(boats) < 6:
            continue
        
        # 勝率データがあるレースのみ
        has_wr = any(b.get('winRate', 0) for b in boats)
        if not has_wr:
            continue
        
        # 予測実行
        pred = predict_race(boats)
        if not pred:
            continue
        
        stats['total'] += 1
        
        # レース情報
        entry = {
            'date': race.get('date', ''),
            'venue': race.get('venue', ''),
            'venueName': race.get('venueName', ''),
            'raceNo': race.get('raceNo', 0),
            'topPick': pred['topPick'],
            'topProb': pred['topProb'],
            'topEV': pred['topEV'],
        }
        
        # 結果がある場合
        result = race.get('result', {})
        order = result.get('order', [])
        if order and len(order) >= 1:
            stats['withResult'] += 1
            
            # 1-2-3着
            sorted_order = sorted(order, key=lambda x: x.get('rank', 99))
            first = sorted_order[0] if len(sorted_order) >= 1 else None
            
            entry['result'] = {
                'order': sorted_order[:3],
                'winner': first['boat'] if first else None,
                'tansho_payout': result.get('tansho_payout', 0)
            }
            
            # 的中判定
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
        else:
            entry['result'] = None
            entry['hit'] = None
        
        # 各艇の名前も保持
        entry['boatNames'] = [b.get('name', '') for b in boats]
        
        predictions.append(entry)
    
    # 的中率計算
    hit_rate = 0
    if stats['withResult'] > 0:
        hit_rate = round(stats['hit'] / stats['withResult'] * 100, 1)
    
    # 日別サマリー
    daily_summary = []
    for date in sorted(daily_stats.keys(), reverse=True):
        d = daily_stats[date]
        rate = round(d['hit'] / d['total'] * 100, 1) if d['total'] > 0 else 0
        daily_summary.append({
            'date': date,
            'total': d['total'],
            'hit': d['hit'],
            'miss': d['miss'],
            'rate': rate
        })
    
    # 出力
    output = {
        'generated': datetime.now().strftime('%Y-%m-%d %H:%M'),
        'stats': {
            'totalPredicted': stats['total'],
            'withResult': stats['withResult'],
            'hit': stats['hit'],
            'miss': stats['miss'],
            'hitRate': hit_rate
        },
        'dailySummary': daily_summary,
        'predictions': sorted(predictions, key=lambda x: (x['date'], x['venue'], x['raceNo']), reverse=True)
    }
    
    out_path = os.path.join(DIR, 'predictions.json')
    with open(out_path, 'w', encoding='utf-8') as f:
        json.dump(output, f, ensure_ascii=False, separators=(',', ':'))
    
    print(f"\n✅ predictions.json 生成完了！")
    print(f"   予測レース数: {stats['total']}")
    print(f"   結果判明: {stats['withResult']}")
    print(f"   的中: {stats['hit']} / ハズレ: {stats['miss']}")
    print(f"   的中率: {hit_rate}%")
    print(f"   日数: {len(daily_summary)}日分")

if __name__ == '__main__':
    process_all_races()
