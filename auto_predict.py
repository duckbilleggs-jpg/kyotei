"""
全レース自動予測スクリプト v4 - 競艇理論ベース

予測ファクター（競艇の考え方に基づく）:
1. 選手の実力: 勝率 + 2連率 + 当地勝率
2. モーター性能: モーター2連率（40%超=エースモーター）
3. コース有利: 1コースが有利だが、勝率差で覆る
4. スタート力: 平均ST（0.12以下=巧者）
5. 体重: 軽い方がスピード有利
6. 気象: 風・波で展開が変わる
7. 展示タイム: 直前のモーター調子

キーポイント:
- コース補正を弱め、選手実力差をより反映
- モーター2連率でモーター性能を加味
- 嚙み合わせ（実力高い選手がアウトコース=まくりチャンス）を考慮
"""
import json, os
from datetime import datetime
from collections import defaultdict

DIR = os.path.dirname(os.path.abspath(__file__))

# コース基本補正（現実の勝率を反映）
# 実データ: 1コース55%, 2コース15%, 3コース12%, 4コース11%, 5コース5%, 6コース2%
COURSE_BASE = [1.65, 1.08, 1.02, 0.97, 0.82, 0.72]

# 級別補正 (控えめに - コースを覆すには不十分なレベル)
GRADE_BONUS = {'A1': 1.08, 'A2': 1.03, 'B1': 0.97, 'B2': 0.92}

def learn_from_races(races):
    """過去レースからパラメータを学習"""
    venue_course_wins = defaultdict(lambda: defaultdict(int))
    venue_course_total = defaultdict(lambda: defaultdict(int))
    
    # 選手勝率帯ごとの実際の勝率（キャリブレーション用）
    wr_bins = defaultdict(lambda: {'win': 0, 'total': 0})
    
    for race in races:
        result = race.get('result', {})
        order = result.get('order', [])
        boats = race.get('boats', [])
        venue = race.get('venue', '')
        
        if not order or len(boats) < 6:
            continue
        
        first = next((o for o in order if o.get('rank') == 1), None)
        if not first:
            continue
        
        wi = first['boat'] - 1
        
        for i in range(6):
            venue_course_total[venue][i] += 1
        venue_course_wins[venue][wi] += 1
        
        # 勝率帯の学習
        for i, b in enumerate(boats):
            wr = b.get('winRate', 0) or 0
            wr_bin = int(wr)  # 3, 4, 5, 6, 7...
            wr_bins[wr_bin]['total'] += 1
            if i == wi:
                wr_bins[wr_bin]['win'] += 1
    
    # 会場別コース補正
    venue_corr = {}
    for venue in venue_course_total:
        total = venue_course_total[venue][0]
        if total < 10:
            continue
        corr = []
        for i in range(6):
            w = venue_course_wins[venue].get(i, 0)
            rate = w / total if total > 0 else 1/6
            corr.append(rate / (1/6))
        venue_corr[venue] = corr
    
    return {
        'venue_corr': venue_corr,
        'wr_bins': dict(wr_bins),
        'train_count': sum(venue_course_total[v][0] for v in venue_course_total)
    }


def predict_race(boats, venue, model, weather=None):
    """
    競艇理論に基づいた予測
    
    スコア = 選手実力 × コース補正 × モーター補正 × ST補正 × 体重補正 × 気象補正
    """
    vc = model.get('venue_corr', {})
    
    # 会場別コース補正（あれば使う、なければデフォルト）
    # ただしデフォルト補正は弱めに
    course_bonus = vc.get(venue, COURSE_BASE)
    
    # 各ボートの情報を整理
    all_wr = [b.get('winRate', 0) or 3.0 for b in boats]
    all_weights = [b.get('weight', 0) for b in boats]
    has_weight = all(w > 0 for w in all_weights)
    avg_weight = sum(all_weights) / len(all_weights) if has_weight else 52.0
    
    all_motor = [b.get('motor2ren', 0) for b in boats]
    has_motor = any(m > 0 for m in all_motor)
    avg_motor = sum(m for m in all_motor if m > 0) / max(sum(1 for m in all_motor if m > 0), 1) if has_motor else 30.0
    
    all_st = [b.get('avgST', 0) for b in boats]
    has_st = any(s > 0 for s in all_st)
    
    all_exhibit = [b.get('exhibitTime', 0) for b in boats]
    has_exhibit = any(e > 0 for e in all_exhibit)
    
    scores = []
    
    for i, boat in enumerate(boats):
        # === 1. 選手実力（ベーススコア）===
        wr = boat.get('winRate', 0) or 3.0
        wr2 = boat.get('winRate2', 0) or 0  # 当地勝率
        wr2ren = boat.get('winRate2ren', 0) or 0  # 2連率
        
        # 全国勝率ベース + 当地勝率を加味
        if wr2 > 0:
            base = wr * 0.65 + wr2 * 0.35
        else:
            base = wr
        
        # 2連率も少し加味（安定感）
        if wr2ren > 0:
            base = base * 0.85 + (wr2ren / 10) * 0.15
        
        # === 2. コース補正 ===
        # 1号艇の選手が明らかに弱い場合のみコース補正を少し弱める
        boat1_wr = all_wr[0]
        this_wr = all_wr[i]
        
        if i > 0 and this_wr - boat1_wr > 3.0:
            # この選手が1号艇より勝率3.0以上高い → コース補正少し緩和
            course_factor = 1.0 + (course_bonus[i] - 1.0) * 0.6
        elif i > 0 and this_wr - boat1_wr > 2.0:
            course_factor = 1.0 + (course_bonus[i] - 1.0) * 0.8
        else:
            course_factor = course_bonus[i]
        
        score = base * course_factor
        
        # === 3. モーター補正 ===
        if has_motor:
            motor = boat.get('motor2ren', 0) or avg_motor
            # 40%超=エースモーター、平均は30%前後（微調整レベル）
            motor_factor = 1.0 + (motor - avg_motor) * 0.004
            score *= max(min(motor_factor, 1.08), 0.92)
        
        # === 4. スタート力補正 ===
        if has_st:
            st = boat.get('avgST', 0)
            if st > 0:
                # 0.12以下=巧者、0.20以上=遅い（微調整レベル）
                if i >= 3:  # アウトコース（4-6号艇）
                    st_factor = 1.0 + (0.16 - st) * 1.5
                else:  # インコース（1-3号艇）
                    st_factor = 1.0 + (0.16 - st) * 0.8
                score *= max(min(st_factor, 1.06), 0.94)
        
        # === 5. 級別補正 ===
        grade = boat.get('grade', '')
        if grade in GRADE_BONUS:
            score *= GRADE_BONUS[grade]
        
        # === 6. 体重補正 ===
        if has_weight:
            w = all_weights[i]
            diff = avg_weight - w
            score *= 1.0 + diff * 0.008
        
        # === 7. 気象補正 ===
        if weather:
            ws = weather.get('windSpeed', 0)
            wd = weather.get('windDir', 0)
            wave = weather.get('waveHeight', 0)
            
            if ws >= 3:
                # 追い風(南系) → イン有利
                if 6 <= wd <= 12:
                    if i <= 1:
                        score *= 1.03
                    elif i >= 4:
                        score *= 0.97
                # 向かい風(北系) → アウト有利（まくりが決まりやすい）
                elif wd <= 4 or wd >= 14:
                    if i <= 1:
                        score *= 0.97
                    elif i >= 4:
                        score *= 1.03
            
            # 波高 → 高波はインが不利
            if wave >= 5:
                if i == 0:
                    score *= 0.95
                elif i >= 3:
                    score *= 1.02
        
        # === 8. 展示タイム補正 ===
        if has_exhibit:
            et = boat.get('exhibitTime', 0)
            if et > 0:
                avg_et = sum(e for e in all_exhibit if e > 0) / max(sum(1 for e in all_exhibit if e > 0), 1)
                et_diff = avg_et - et  # タイムが小さい=速い
                score *= 1.0 + et_diff * 5.0  # 0.1秒差で50%変動
                score = max(score, 0.1)
        
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
        print("race_db.json not found")
        return
    
    with open(db_path, 'r', encoding='utf-8') as f:
        db = json.load(f)
    
    races = db.get('races', [])
    print(f"Total {len(races)} races")
    
    by_date = defaultdict(list)
    for race in races:
        d = race.get('date', '')
        if d:
            by_date[d].append(race)
    
    dates = sorted(by_date.keys())
    if not dates:
        print("No data")
        return
    print(f"{len(dates)} days: {dates[0]} - {dates[-1]}")
    
    # ウォークフォワード
    all_past = []
    predictions = []
    stats = {'total': 0, 'withResult': 0, 'hit': 0, 'miss': 0}
    daily_stats = {}
    venue_stats = defaultdict(lambda: {'total': 0, 'hit': 0})
    pick_dist = defaultdict(lambda: {'total': 0, 'hit': 0})
    
    for di, date in enumerate(dates):
        day_races = by_date[date]
        
        if all_past:
            model = learn_from_races(all_past)
        else:
            model = {'venue_corr': {}, 'wr_bins': {}, 'train_count': 0}
        
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
            pick_dist[pred['topPick']]['total'] += 1
            
            entry = {
                'date': date, 'venue': venue,
                'venueName': race.get('venueName', ''),
                'raceNo': race.get('raceNo', 0),
                'topPick': pred['topPick'],
                'topProb': pred['topProb'],
                'topEV': pred['topEV'],
            }
            if weather:
                entry['weather'] = weather
            
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
                
                venue_stats[venue]['total'] += 1
                if entry['hit']:
                    venue_stats[venue]['hit'] += 1
            else:
                entry['result'] = None
                entry['hit'] = None
            
            entry['boatNames'] = [b.get('name', '') for b in boats]
            predictions.append(entry)
        
        if day_total > 0:
            day_rate = round(day_hit / (day_hit + day_miss) * 100, 1) if (day_hit + day_miss) > 0 else 0
            daily_stats[date] = {'total': day_total, 'hit': day_hit, 'miss': day_miss, 'rate': day_rate}
            d_str = f"{date[:4]}/{date[4:6]}/{date[6:]}"
            train_n = len(all_past)
            print(f"  {d_str}: {day_rate}% ({day_hit}/{day_hit+day_miss}) [train:{train_n}]")
        
        all_past.extend(day_races)
    
    hit_rate = round(stats['hit'] / stats['withResult'] * 100, 1) if stats['withResult'] > 0 else 0
    
    # 予測先分布
    print(f"\nPick distribution:")
    for pick in sorted(pick_dist.keys()):
        pd = pick_dist[pick]
        phr = pd['hit']/pd['total']*100 if pd['total'] > 0 else 0
        print(f"  Boat {pick}: {pd['total']} picks, {pd['hit']} hits ({phr:.1f}%)")
    
    daily_summary = [{'date': d, **daily_stats[d]} for d in sorted(daily_stats.keys(), reverse=True)]
    venue_summary = [{'venue': v, **venue_stats[v], 'rate': round(venue_stats[v]['hit']/venue_stats[v]['total']*100,1) if venue_stats[v]['total']>0 else 0} for v in sorted(venue_stats.keys())]
    
    final_model = learn_from_races(all_past)
    
    output = {
        'generated': datetime.now().strftime('%Y-%m-%d %H:%M'),
        'modelVersion': 'v4-boatrace-theory',
        'stats': {
            'totalPredicted': stats['total'],
            'withResult': stats['withResult'],
            'hit': stats['hit'], 'miss': stats['miss'],
            'hitRate': hit_rate,
            'totalTrainingRaces': len(all_past)
        },
        'model': {
            'venueCount': len(final_model['venue_corr']),
            'method': 'walk-forward + boat-race-theory (motor/ST/weight/weather/grade)',
            'factors': 'winRate, winRate2, motor2ren, avgST, grade, weight, weather, exhibitTime'
        },
        'dailySummary': daily_summary,
        'venueSummary': venue_summary,
        'predictions': sorted(predictions, key=lambda x: (x['date'], x['venue'], x['raceNo']), reverse=True)
    }
    
    out_path = os.path.join(DIR, 'predictions.json')
    with open(out_path, 'w', encoding='utf-8') as f:
        json.dump(output, f, ensure_ascii=False, separators=(',', ':'))
    
    print(f"\n{'='*40}")
    print(f"Model: v4-boatrace-theory")
    print(f"Predicted: {stats['total']}")
    print(f"Hit: {stats['hit']} / Miss: {stats['miss']}")
    print(f"Hit Rate: {hit_rate}%")
    print(f"{'='*40}")


if __name__ == '__main__':
    process_all_races()
