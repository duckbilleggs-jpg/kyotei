import requests
from bs4 import BeautifulSoup
import re

HDR = {"User-Agent": "Mozilla/5.0"}

# 出走表
r = requests.get("https://www.boatrace.jp/owpc/pc/race/racelist?rno=1&jcd=24&hd=20260311", headers=HDR, timeout=15)
r.encoding = "utf-8"
soup = BeautifulSoup(r.text, "html.parser")

print("=== 出走表 ===")
for i, tbody in enumerate(soup.find_all("tbody", class_="is-fs12")[:2]):
    print(f"\n--- 艇{i+1} ---")
    for j, td in enumerate(tbody.find_all("td")):
        t = td.get_text(strip=True)
        cls = td.get("class", [])
        if t:
            print(f"  td[{j}] cls={cls} => {t[:50]}")
    for a in tbody.find_all("a", href=True):
        txt = a.get_text(strip=True)
        href = a["href"][:60]
        print(f"  a: {txt} => {href}")

# 結果
r2 = requests.get("https://www.boatrace.jp/owpc/pc/race/raceresult?rno=1&jcd=24&hd=20260311", headers=HDR, timeout=15)
r2.encoding = "utf-8"
soup2 = BeautifulSoup(r2.text, "html.parser")

print("\n\n=== 結果 ===")
for i, tbody in enumerate(soup2.find_all("tbody", class_="is-fs14")[:3]):
    print(f"\n--- 着{i+1} ---")
    for j, td in enumerate(tbody.find_all("td")):
        t = td.get_text(strip=True)
        cls = td.get("class", [])
        if t:
            print(f"  td[{j}] cls={cls} => {t[:50]}")

print("\n\n=== 払戻 ===")
for table in soup2.find_all("table"):
    text = table.get_text()
    if "単勝" in text:
        for row in table.find_all("tr"):
            cells = [c.get_text(strip=True) for c in row.find_all(["th","td"])]
            if any(c for c in cells):
                print(f"  {cells}")
        break
