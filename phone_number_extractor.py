import os
import sys
from dotenv import load_dotenv
from serpapi import GoogleSearch

# .envファイルから環境変数を読み込む
load_dotenv()

# APIキーを取得
api_key = os.getenv('SERPAPI_KEY') or os.getenv('SERP_API_KEY')

if not api_key:
    print("エラー: SERPAPI_KEYまたはSERP_API_KEYが.envファイルに設定されていません。")
    sys.exit(1)

# コマンドライン引数から検索パラメータを取得（デフォルト値あり）
search_query = sys.argv[1] if len(sys.argv) > 1 else "Coffee"
location = sys.argv[2] if len(sys.argv) > 2 else "@40.7455096,-74.0083012,14z"

print(f"検索クエリ: {search_query}")
print(f"場所: {location}")
print("\n店舗を検索しています...")

# SerpAPIクライアントの設定
params = {
    "engine": "google_maps",
    "q": search_query,
    "ll": location,
    "api_key": api_key
}

search = GoogleSearch(params)
results = search.get_dict()

# 電話番号を抽出してリスト化
phone_numbers = []

if results and 'local_results' in results:
    for place in results['local_results']:
        title = place.get('title', 'タイトル不明')
        phone = place.get('phone') or place.get('電話', '電話番号なし')
        address = place.get('address') or place.get('住所', '住所不明')
        
        phone_numbers.append({
            'title': title,
            'phone': phone,
            'address': address
        })

# 結果を表示
print("\n=== 検索結果: 電話番号リスト ===\n")
print(f"{len(phone_numbers)}件の店舗が見つかりました\n")

if not phone_numbers:
    print("電話番号が見つかりませんでした。")
    print("\nデバッグ情報:")
    if results:
        print(f"検索結果のキー: {list(results.keys())}")
else:
    for index, place in enumerate(phone_numbers, 1):
        print(f"{index}. {place['title']}")
        print(f"   電話番号: {place['phone']}")
        print(f"   住所: {place['address']}")
        print("")
    
    # 電話番号のみのリストも表示
    print("\n=== 電話番号のみのリスト ===\n")
    for index, place in enumerate(phone_numbers, 1):
        print(f"{index}. {place['phone']}")

# CSV形式でも出力（オプション）
if len(sys.argv) > 3 and (sys.argv[3] == '--csv' or sys.argv[3] == '-c'):
    print("\n=== CSV形式 ===\n")
    print("店舗名,電話番号,住所")
    for place in phone_numbers:
        print(f"\"{place['title']}\",\"{place['phone']}\",\"{place['address']}\"")



