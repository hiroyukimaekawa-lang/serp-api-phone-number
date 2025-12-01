require 'serpapi'
require 'dotenv/load'

# .envファイルからAPIキーを読み込む
api_key = ENV['SERPAPI_KEY'] || ENV['SERP_API_KEY']

unless api_key
  puts "エラー: SERPAPI_KEYまたはSERP_API_KEYが.envファイルに設定されていません。"
  exit 1
end

# コマンドライン引数から検索パラメータを取得（デフォルト値あり）
search_query = ARGV[0] || "Coffee"
location = ARGV[1] || "@40.7455096,-74.0083012,14z"

puts "検索クエリ: #{search_query}"
puts "場所: #{location}"
puts "\n店舗を検索しています..."

# SerpAPIクライアントの設定
client = SerpApi::Client.new(
  engine: "google_maps",
  q: search_query,
  ll: location,
  api_key: api_key
)

# 検索を実行
results = client.search

# 電話番号を抽出してリスト化
phone_numbers = []

if results && results['local_results']
  results['local_results'].each do |place|
    title = place['title'] || 'タイトル不明'
    phone = place['phone'] || place['電話'] || '電話番号なし'
    address = place['address'] || place['住所'] || '住所不明'
    
    phone_numbers << {
      title: title,
      phone: phone,
      address: address
    }
  end
end

# 結果を表示
puts "\n=== 検索結果: 電話番号リスト ===\n"
puts "#{phone_numbers.length}件の店舗が見つかりました\n\n"

if phone_numbers.empty?
  puts "電話番号が見つかりませんでした。"
  puts "\nデバッグ情報:"
  puts "検索結果のキー: #{results.keys.inspect}" if results
else
  phone_numbers.each_with_index do |place, index|
    puts "#{index + 1}. #{place[:title]}"
    puts "   電話番号: #{place[:phone]}"
    puts "   住所: #{place[:address]}"
    puts ""
  end
  
  # 電話番号のみのリストも表示
  puts "\n=== 電話番号のみのリスト ===\n"
  phone_numbers.each_with_index do |place, index|
    puts "#{index + 1}. #{place[:phone]}"
  end
end

# CSV形式でも出力（オプション）
if ARGV[2] == '--csv' || ARGV[2] == '-c'
  puts "\n=== CSV形式 ===\n"
  puts "店舗名,電話番号,住所"
  phone_numbers.each do |place|
    puts "\"#{place[:title]}\",\"#{place[:phone]}\",\"#{place[:address]}\""
  end
end
