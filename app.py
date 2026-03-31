import os
import pandas as pd
import streamlit as st
from dotenv import load_dotenv
from serpapi import GoogleSearch
from geopy.geocoders import Nominatim
from geopy.distance import geodesic
from geopy.exc import GeocoderTimedOut, GeocoderServiceError
import math
import time
import re

# ページ設定
st.set_page_config(
    page_title="店舗電話番号抽出アプリ",
    page_icon="📞",
    layout="wide"
)

# .envファイルから環境変数を読み込む
load_dotenv()

# APIキーをセッションステートで管理（初期値は環境変数から取得）
if 'api_key' not in st.session_state:
    st.session_state.api_key = os.getenv('SERPAPI_KEY') or os.getenv('SERP_API_KEY') or ""

api_key = st.session_state.api_key

# 地名から座標を取得する関数
@st.cache_data(ttl=3600)  # 1時間キャッシュ
def get_coordinates_from_address(address):
    """地名から緯度・経度を取得する関数"""
    try:
        geolocator = Nominatim(user_agent="phone_number_app")
        location = geolocator.geocode(address, timeout=10)
        if location:
            return {
                'latitude': location.latitude,
                'longitude': location.longitude,
                'address': location.address,
                'success': True
            }
        else:
            return {'success': False, 'error': '場所が見つかりませんでした'}
    except (GeocoderTimedOut, GeocoderServiceError) as e:
        return {'success': False, 'error': f'ジオコーディングエラー: {str(e)}'}
    except Exception as e:
        return {'success': False, 'error': f'エラー: {str(e)}'}

# 半径（メートル）から適切なズームレベルを計算する関数
def radius_to_zoom_level(radius_meters):
    """半径（メートル）から適切なズームレベルを計算"""
    if radius_meters <= 500:
        return 16
    elif radius_meters <= 1000:
        return 15
    elif radius_meters <= 2000:
        return 14
    elif radius_meters <= 5000:
        return 13
    elif radius_meters <= 10000:
        return 12
    elif radius_meters <= 20000:
        return 11
    else:
        return 10

# 半径内をカバーするために複数の座標点を生成する関数
def generate_search_points(center_lat, center_lon, radius_meters):
    """指定された半径をカバーするために複数の検索地点を生成"""
    lat_degree_per_meter = 1 / 111000
    lon_degree_per_meter = 1 / (111000 * math.cos(math.radians(center_lat)))
    
    grid_spacing = radius_meters * 0.4
    grid_size = int(radius_meters * 2 / grid_spacing) + 1
    
    search_points = []
    
    search_points.append({
        'lat': center_lat,
        'lon': center_lon,
        'zoom': radius_to_zoom_level(radius_meters)
    })
    
    for i in range(-grid_size, grid_size + 1):
        for j in range(-grid_size, grid_size + 1):
            if i == 0 and j == 0:
                continue
            
            lat_offset = i * grid_spacing * lat_degree_per_meter
            lon_offset = j * grid_spacing * lon_degree_per_meter
            
            new_lat = center_lat + lat_offset
            new_lon = center_lon + lon_offset
            
            distance = geodesic((center_lat, center_lon), (new_lat, new_lon)).meters
            
            if distance <= radius_meters * 1.2:
                search_points.append({
                    'lat': new_lat,
                    'lon': new_lon,
                    'zoom': radius_to_zoom_level(radius_meters)
                })
    
    return search_points

# 座標間の距離を計算する関数
def calculate_distance(lat1, lon1, lat2, lon2):
    """2点間の距離をメートルで返す"""
    return geodesic((lat1, lon1), (lat2, lon2)).meters


# ▼▼▼ 精度向上: score_place に店名一致度・閉店判定・レビュー数ボーナスを追加 ▼▼▼
def score_place(place, query=""):
    """
    店舗情報のスコアリング関数（精度向上版）

    Args:
        place: Google Mapsのlocal_resultsの1件
        query: 検索した屋号（店名）

    Returns:
        int: スコア（高いほど信頼性が高い）
    """
    score = 0
    title = place.get('title', '')

    # 電話番号がある → +50
    if place.get('phone') or place.get('formatted_phone_number'):
        score += 50
    # 住所がある → +20
    if place.get('address'):
        score += 20
    # 評価がある → +10
    if place.get('rating'):
        score += 10
    # レビュー数がある → +10（件数が多いほど追加ボーナス、最大+10）
    reviews = place.get('reviews', 0) or 0
    if reviews:
        score += 10
        try:
            score += min(int(int(reviews) / 100), 10)
        except (ValueError, TypeError):
            pass

    # 店名とクエリの一致度でスコアリング
    if query:
        query_clean = query.strip()
        if title == query_clean:
            score += 40        # 完全一致
        elif title.startswith(query_clean):
            score += 25        # 前方一致（例：「〇〇食堂 千葉店」）
        elif query_clean in title:
            score += 15        # 部分一致
        elif title in query_clean and len(title) >= 2:
            score += 10        # 略称対応
        else:
            score -= 20        # 全く一致しない → 大幅減点

    # 閉店・廃業ワードは大幅減点
    if any(x in title for x in ['閉店', '廃業', '跡地', '移転']):
        score -= 80

    # 支店・本店は若干減点（元の挙動を維持）
    if any(x in title for x in ['支店', '本店', '店']):
        score -= 5

    return score
# ▲▲▲ 精度向上ここまで ▲▲▲


def calculate_confidence(result):
    """
    取得結果の信頼度を計算する関数

    Returns:
        str: 信頼度（Very High / High / Mid / Low）
    """
    has_phone = bool(result.get('電話番号'))
    has_address = bool(result.get('住所'))
    has_coords = bool(result.get('緯度') and result.get('経度'))
    
    if has_phone and has_address and has_coords:
        return 'Very High'
    elif has_phone and has_address:
        return 'High'
    elif has_phone:
        return 'Mid'
    else:
        return 'Low'


def search_phone_from_organic(store_name, api_key):
    """
    Google organic検索から電話番号を取得する関数（フォールバック用）
    """
    try:
        params = {
            "engine": "google",
            "q": f"{store_name} 公式 電話番号",
            "api_key": api_key,
            "num": 5
        }
        
        search = GoogleSearch(params)
        results = search.get_dict()
        
        # ▼ 精度向上: 日本の電話番号フォーマットを網羅した正規表現
        phone_patterns = [
            r'0120-\d{3}-\d{3}',            # フリーダイヤル 0120
            r'0800-\d{3}-\d{4}',             # フリーダイヤル 0800
            r'0\d{1,3}-\d{2,4}-\d{3,4}',    # 一般固定電話（市外局番付き）
            r'\(\d{2,4}\)\d{3,4}-\d{3,4}',  # (03)1234-5678 形式
        ]
        for r in results.get("organic_results", []):
            snippet = r.get("snippet", "")
            for pattern in phone_patterns:
                match = re.search(pattern, snippet)
                if match:
                    return match.group()
        # ▲ 精度向上ここまで

        return ""
    except Exception as e:
        return ""


def search_store_by_name(store_name, location_str=None, api_key=None, location_hint=None):
    """
    屋号（店名）から店舗情報を取得する関数（スコアリング・フォールバック対応）

    Args:
        store_name: 検索する店舗名（屋号のみ）
        location_str: 検索場所（例: "@35.6762,139.6503,14z" または None）
        api_key: SerpAPIキー
        location_hint: 地名のヒント（例: "東京都新宿区"）- llがない場合にクエリへ付加

    Returns:
        dict: 店舗情報（店舗名、電話番号、住所、緯度、経度、信頼度など）
    """
    if not api_key:
        return {
            'success': False,
            'error': 'APIキーが設定されていません',
            '店舗名': '',
            '電話番号': '',
            '住所': '',
            '緯度': None,
            '経度': None,
            '評価': '',
            'レビュー数': '',
            '信頼度': 'Low'
        }
    
    try:
        query = store_name.strip()

        # ▼ 精度向上: llパラメータ（座標）がない場合は地名をクエリに付加
        if location_hint and not location_str:
            query = f"{query} {location_hint.strip()}"

        # ▼ 精度向上: type/hl/gl を追加して日本向け検索を安定化
        params = {
            "engine": "google_maps",
            "q": query,
            "api_key": api_key,
            "type": "search",  # 検索モードを明示
            "hl": "ja",        # 日本語で結果を取得
            "gl": "jp",        # 日本の検索結果に絞る
        }
        # ▲ 精度向上ここまで

        # 地名・座標はllにのみ使用（元の挙動を維持）
        if location_str:
            params["ll"] = location_str
        
        search = GoogleSearch(params)
        results = search.get_dict()
        
        if results and 'local_results' in results:
            local_results = results.get('local_results', [])
            if local_results:
                # ▼ 精度向上: スコアリング呼び出しにqueryを渡す
                if len(local_results) > 1:
                    scored_places = [(place, score_place(place, query)) for place in local_results]
                    scored_places.sort(key=lambda x: x[1], reverse=True)
                    place = scored_places[0][0]
                else:
                    place = local_results[0]
                # ▲ 精度向上ここまで

                title = place.get('title', '')
                phone = place.get('phone') or place.get('formatted_phone_number') or place.get('電話', '')
                address = place.get('address') or place.get('住所', '')
                
                gps = place.get('gps_coordinates', {})
                latitude = gps.get('latitude') if gps else None
                longitude = gps.get('longitude') if gps else None
                
                result = {
                    'success': True,
                    '店舗名': title,
                    '電話番号': phone,
                    '住所': address,
                    '緯度': latitude,
                    '経度': longitude,
                    '評価': place.get('rating', ''),
                    'レビュー数': place.get('reviews', '')
                }
                
                result['信頼度'] = calculate_confidence(result)
                return result
        
        # Google Mapsで取得できなかった場合、organic検索にフォールバック
        phone_from_organic = search_phone_from_organic(store_name, api_key)
        
        if phone_from_organic:
            return {
                'success': True,
                '店舗名': store_name,
                '電話番号': phone_from_organic,
                '住所': '',
                '緯度': None,
                '経度': None,
                '評価': '',
                'レビュー数': '',
                '信頼度': 'Mid'
            }
        
        return {
            'success': False,
            'error': '店舗が見つかりませんでした',
            '店舗名': '',
            '電話番号': '',
            '住所': '',
            '緯度': None,
            '経度': None,
            '評価': '',
            'レビュー数': '',
            '信頼度': 'Low'
        }
        
    except Exception as e:
        return {
            'success': False,
            'error': f'エラー: {str(e)}',
            '店舗名': '',
            '電話番号': '',
            '住所': '',
            '緯度': None,
            '経度': None,
            '評価': '',
            'レビュー数': '',
            '信頼度': 'Low'
        }

# タイトルと説明
st.title("📞 店舗電話番号抽出アプリ")
st.markdown("SerpAPIを使用してGoogle Mapsから店舗を検索し、電話番号をリスト化します。")

# タブで機能を分ける
tab_normal, tab_csv = st.tabs(["🔍 通常検索", "📄 CSVから電話番号取得"])

# サイドバーに設定
with st.sidebar:
    st.header("⚙️ 設定")
    
    new_api_key = st.text_input(
        "SerpAPI キー",
        value=st.session_state.api_key,
        type="password",
        help="SerpAPIの管理画面から取得したAPIキーを入力してください。"
    )
    
    if new_api_key != st.session_state.api_key:
        st.session_state.api_key = new_api_key
        st.rerun()

    api_key = st.session_state.api_key

    if not api_key:
        st.error("⚠️ APIキーが設定されていません。サイドバーに入力するか、.envファイルに設定してください。")
    else:
        st.success("✅ APIキーが設定されています")
    
    st.markdown("---")
    st.markdown("### 使い方")
    st.markdown("""
    1. 検索キーワードを入力
    2. 場所を指定
       - 地名から検索（推奨）
       - 座標を直接入力
    3. 検索ボタンをクリック
    """)

# 通常検索タブ
with tab_normal:
    if 'lat' not in st.session_state:
        st.session_state.lat = 40.7455096
    if 'lon' not in st.session_state:
        st.session_state.lon = -74.0083012
    if 'zoom' not in st.session_state:
        st.session_state.zoom = 14

    st.markdown("### 📍 よく使われる場所のプリセット")
    preset_col1, preset_col2, preset_col3, preset_col4 = st.columns(4)

    with preset_col1:
        if st.button("🗽 ニューヨーク", use_container_width=True):
            st.session_state.lat = 40.7455096
            st.session_state.lon = -74.0083012
            st.session_state.zoom = 14
            st.rerun()

    with preset_col2:
        if st.button("🗼 東京", use_container_width=True):
            st.session_state.lat = 35.6762
            st.session_state.lon = 139.6503
            st.session_state.zoom = 14
            st.rerun()

    with preset_col3:
        if st.button("🌉 サンフランシスコ", use_container_width=True):
            st.session_state.lat = 37.7749
            st.session_state.lon = -122.4194
            st.session_state.zoom = 14
            st.rerun()

    with preset_col4:
        if st.button("🏙️ ロサンゼルス", use_container_width=True):
            st.session_state.lat = 34.0522
            st.session_state.lon = -118.2437
            st.session_state.zoom = 14
            st.rerun()

    st.markdown("---")

    st.markdown("### 📍 場所の設定")
location_input_method = st.radio(
    "場所の指定方法 *",
    ["地名から検索（推奨）", "座標を個別入力", "座標を一括入力"],
    horizontal=False,
    help="地名を入力すると自動で座標を取得します"
)

if location_input_method == "地名から検索（推奨）":
    col1, col2 = st.columns([4, 1])
    
    with col1:
        place_name = st.text_input(
            "地名または住所 *",
            value=st.session_state.get('last_place_name', ''),
            help="例: 東京, New York, 東京都渋谷区",
            placeholder="例: 東京、New York、東京都渋谷区",
            key="place_name_input"
        )
    
    with col2:
        st.markdown("<br>", unsafe_allow_html=True)
        geocode_button = st.button("📍 座標を取得", use_container_width=True, type="primary")
    
    if geocode_button:
        if place_name:
            with st.spinner(f"「{place_name}」の座標を取得しています..."):
                result = get_coordinates_from_address(place_name)
                
                if result['success']:
                    latitude = result['latitude']
                    longitude = result['longitude']
                    found_address = result['address']
                    
                    st.success(f"✅ 座標を取得しました: {found_address}")
                    st.info(f"緯度: {latitude:.7f}, 経度: {longitude:.7f}")
                    
                    st.session_state.lat = latitude
                    st.session_state.lon = longitude
                    st.session_state.last_place_name = place_name
                    st.session_state.found_address = found_address
                    st.rerun()
                else:
                    st.error(f"❌ {result.get('error', '座標を取得できませんでした')}")
    
    if 'found_address' in st.session_state:
        st.caption(f"📍 現在の場所: {st.session_state.found_address}")
        st.caption(f"緯度: {st.session_state.lat:.7f}, 経度: {st.session_state.lon:.7f}")

    st.markdown("---")

    with st.form("search_form"):
        st.subheader("🔍 検索条件")
    
        search_query = st.text_input(
            "検索キーワード *",
            value="Coffee",
            help="検索したい店舗のキーワードを入力してください（例: Coffee, ラーメン, 寿司）",
            placeholder="例: Coffee"
        )
        
        st.markdown("---")
        
        range_method = st.radio(
            "検索範囲の指定方法",
            ["半径（メートル）で指定", "ズームレベルで指定"],
            horizontal=True,
            index=0 if st.session_state.get('use_radius', True) else 1,
            help="半径を指定すると、指定した座標からその半径内の店舗を検索します"
        )
        
        use_radius = (range_method == "半径（メートル）で指定")
        st.session_state.use_radius = use_radius
        
        if location_input_method == "地名から検索（推奨）":
            if 'found_address' not in st.session_state:
                st.info("ℹ️ 上記で地名を入力して「📍 座標を取得」ボタンをクリックしてください。")
            
            if use_radius:
                radius_meters = st.number_input(
                    "検索半径（メートル）",
                    min_value=100,
                    max_value=50000,
                    value=st.session_state.get('radius_meters', 1000),
                    step=100,
                    help="中心座標から指定した半径（メートル）内の店舗を検索します（例: 500m, 1000m, 5000m）"
                )
                st.session_state.radius_meters = radius_meters
                zoom = radius_to_zoom_level(radius_meters)
                st.info(f"💡 **現在の設定**: 半径 {radius_meters}m - ズームレベル {zoom} で検索します")
                st.session_state.zoom = zoom
                location = f"@{st.session_state.lat},{st.session_state.lon},{zoom}z"
            else:
                zoom_preset = st.selectbox(
                    "検索範囲",
                    ["狭い範囲（ズーム15-16）", "標準範囲（ズーム13-14）", "広範囲（ズーム11-12）", "非常に広範囲（ズーム9-10）", "カスタム"],
                    index=1,
                    help="範囲を広げるには、より小さいズームレベルを選択してください"
                )
                if zoom_preset == "狭い範囲（ズーム15-16）":
                    zoom = 15
                elif zoom_preset == "標準範囲（ズーム13-14）":
                    zoom = 14
                elif zoom_preset == "広範囲（ズーム11-12）":
                    zoom = 12
                elif zoom_preset == "非常に広範囲（ズーム9-10）":
                    zoom = 10
                else:
                    zoom = st.number_input(
                        "ズームレベル（カスタム）",
                        min_value=1,
                        max_value=21,
                        value=int(st.session_state.zoom),
                        help="1-21の範囲。小さい値ほど広範囲で検索します（例: 10=広範囲、14=標準、16=狭範囲）"
                    )
                st.info(f"💡 **現在の設定**: ズームレベル {zoom} - {'広範囲' if zoom <= 12 else '標準範囲' if zoom <= 14 else '狭範囲'}で検索します")
                st.session_state.zoom = zoom
                location = f"@{st.session_state.lat},{st.session_state.lon},{zoom}z"
            
        elif location_input_method == "座標を個別入力":
            col1, col2 = st.columns(2)
            with col1:
                latitude = st.number_input(
                    "緯度（Latitude）",
                    value=float(st.session_state.lat),
                    format="%.7f",
                    help="例: 40.7455096（ニューヨーク）、35.6762（東京）"
                )
            with col2:
                longitude = st.number_input(
                    "経度（Longitude）",
                    value=float(st.session_state.lon),
                    format="%.7f",
                    help="例: -74.0083012（ニューヨーク）、139.6503（東京）"
                )
            
            if use_radius:
                radius_meters = st.number_input(
                    "検索半径（メートル）",
                    min_value=100,
                    max_value=50000,
                    value=st.session_state.get('radius_meters', 1000),
                    step=100,
                    help="中心座標から指定した半径（メートル）内の店舗を検索します（例: 500m, 1000m, 5000m）"
                )
                st.session_state.radius_meters = radius_meters
                zoom = radius_to_zoom_level(radius_meters)
                st.info(f"💡 **現在の設定**: 半径 {radius_meters}m - ズームレベル {zoom} で検索します")
            else:
                zoom_preset = st.selectbox(
                    "検索範囲",
                    ["狭い範囲（ズーム15-16）", "標準範囲（ズーム13-14）", "広範囲（ズーム11-12）", "非常に広範囲（ズーム9-10）", "カスタム"],
                    index=1,
                    help="範囲を広げるには、より小さいズームレベルを選択してください"
                )
                if zoom_preset == "狭い範囲（ズーム15-16）":
                    zoom = 15
                elif zoom_preset == "標準範囲（ズーム13-14）":
                    zoom = 14
                elif zoom_preset == "広範囲（ズーム11-12）":
                    zoom = 12
                elif zoom_preset == "非常に広範囲（ズーム9-10）":
                    zoom = 10
                else:
                    zoom = st.number_input(
                        "ズームレベル（カスタム）",
                        min_value=1,
                        max_value=21,
                        value=int(st.session_state.zoom),
                        help="1-21の範囲。小さい値ほど広範囲で検索します（例: 10=広範囲、14=標準、16=狭範囲）"
                    )
                st.caption(f"💡 ズームレベル {zoom}")
            
            st.session_state.lat = latitude
            st.session_state.lon = longitude
            st.session_state.zoom = zoom
            location = f"@{latitude},{longitude},{zoom}z"
            
        else:  # 座標を一括入力
            location = st.text_input(
                "場所（一括入力形式）",
                value=f"@{st.session_state.lat},{st.session_state.lon},{st.session_state.zoom}z",
                help="形式: @緯度,経度,ズームz（例: @40.7455096,-74.0083012,14z）",
                placeholder="@40.7455096,-74.0083012,14z"
            )
            
            try:
                if location.startswith('@'):
                    parts = location.replace('@', '').replace('z', '').split(',')
                    if len(parts) >= 2:
                        st.session_state.lat = float(parts[0])
                        st.session_state.lon = float(parts[1])
                        if len(parts) >= 3:
                            st.session_state.zoom = int(float(parts[2]))
            except:
                pass
            
            if use_radius:
                radius_meters = st.number_input(
                    "検索半径（メートル）",
                    min_value=100,
                    max_value=50000,
                    value=st.session_state.get('radius_meters', 1000),
                    step=100,
                    help="中心座標から指定した半径（メートル）内の店舗を検索します（例: 500m, 1000m, 5000m）"
                )
                st.session_state.radius_meters = radius_meters
                zoom = radius_to_zoom_level(radius_meters)
                st.info(f"💡 **現在の設定**: 半径 {radius_meters}m - ズームレベル {zoom} で検索します")
                st.session_state.zoom = zoom
        
        st.markdown("---")
        
        st.markdown("#### 🔍 検索オプション")
        
        col_filter1, col_filter2 = st.columns(2)
        
        with col_filter1:
            filter_takeout_only = st.checkbox(
                "📦 テイクアウト対応店舗のみ表示",
                value=st.session_state.get('filter_takeout', False),
                help="テイクアウトサービスを提供している店舗のみを表示します",
                key="filter_takeout_checkbox"
            )
            st.session_state.filter_takeout = filter_takeout_only
        
        with col_filter2:
            expand_search = st.checkbox(
                "🌐 複数地点から検索（範囲拡大）",
                value=st.session_state.get('expand_search', False),
                help="中心地点の周辺から複数の地点で検索して、より多くの結果を取得します",
                key="expand_search_checkbox"
            )
            st.session_state.expand_search = expand_search
            
        st.markdown("---")
        search_button = st.form_submit_button("🔍 検索開始", use_container_width=True, type="primary")
    
    # 検索実行
    if search_button:
        filter_takeout_only = st.session_state.get('filter_takeout', False)
        expand_search = st.session_state.get('expand_search', False)
        use_radius = st.session_state.get('use_radius', False)
        radius_meters = st.session_state.get('radius_meters', None)
        
        if not search_query:
            st.warning("⚠️ 検索キーワードを入力してください。")
        elif location_input_method == "地名から検索（推奨）" and 'found_address' not in st.session_state:
            st.warning("⚠️ 地名から座標を取得してください。")
        else:
            max_results = 100
            center_lat = st.session_state.lat
            center_lon = st.session_state.lon
            
            filter_text = "（テイクアウト対応のみ）" if filter_takeout_only else ""
            radius_text = f"（半径{radius_meters}m）" if use_radius and radius_meters else ""
            expand_text = "（複数地点検索）" if expand_search else ""
            with st.spinner(f"「{search_query}」を検索しています{filter_text}{radius_text}{expand_text}..."):
                try:
                    phone_numbers = []
                    all_places = []
                    page = 0
                    max_pages = 6
                    
                    progress_bar = st.progress(0)
                    status_text = st.empty()
                    
                    search_locations = []
                    if use_radius and radius_meters:
                        search_locations = generate_search_points(center_lat, center_lon, radius_meters)
                        status_text.text(f"半径{radius_meters}m内をカバーするために{len(search_locations)}地点から検索します...")
                    elif expand_search:
                        zoom_level = st.session_state.zoom
                        offsets = [
                            (0, 0),
                            (0.01, 0),
                            (-0.01, 0),
                            (0, 0.01),
                            (0, -0.01),
                            (0.007, 0.007),
                            (-0.007, 0.007),
                            (0.007, -0.007),
                            (-0.007, -0.007),
                        ]
                        for lat_offset, lon_offset in offsets:
                            search_locations.append({
                                'lat': center_lat + lat_offset,
                                'lon': center_lon + lon_offset,
                                'zoom': zoom_level
                            })
                    else:
                        search_locations.append({
                            'lat': st.session_state.lat,
                            'lon': st.session_state.lon,
                            'zoom': st.session_state.zoom
                        })
                    
                    total_locations = len(search_locations)
                    for loc_idx, loc in enumerate(search_locations):
                        location_str = f"@{loc['lat']},{loc['lon']},{loc['zoom']}z"
                        
                        if total_locations > 1:
                            status_text.text(f"地点 {loc_idx + 1}/{total_locations} を検索中... ({len(all_places)}件取得済み)")
                            progress_bar.progress(loc_idx / total_locations)
                        
                        params = {
                            "engine": "google_maps",
                            "q": search_query,
                            "ll": location_str,
                            "api_key": api_key
                        }
                        
                        search = GoogleSearch(params)
                        results = search.get_dict()
                    
                        page = 0
                        while page < max_pages:
                            page += 1
                            if total_locations > 1:
                                status_text.text(f"地点 {loc_idx + 1}/{total_locations} - ページ {page} を取得中... ({len(all_places)}件取得済み)")
                            else:
                                status_text.text(f"ページ {page} を取得中... ({len(all_places)}件取得済み)")
                            progress_bar.progress((loc_idx + page / max_pages) / total_locations)
                            
                            if not results or 'local_results' not in results:
                                break
                            
                            page_results = results.get('local_results', [])
                            
                            if not page_results:
                                break
                            
                            existing_places = {(p.get('title', ''), p.get('address', '')) for p in all_places}
                            for place in page_results:
                                place_key = (place.get('title', ''), place.get('address', ''))
                                if place_key not in existing_places:
                                    all_places.append(place)
                                    existing_places.add(place_key)
                            
                            if len(page_results) < 20:
                                break
                            
                            try:
                                search = search.get_next()
                                results = search.get_dict()
                            except Exception as e:
                                break
                        
                        if len(all_places) >= max_results * 2:
                            break
                    
                    progress_bar.progress(1.0)
                    status_text.text("結果をフィルタリング中...")
                    
                    for place in all_places:
                        if use_radius and radius_meters:
                            place_lat = None
                            place_lon = None
                            
                            gps = place.get('gps_coordinates', {})
                            if gps:
                                place_lat = gps.get('latitude')
                                place_lon = gps.get('longitude')
                            
                            if place_lat is None or place_lon is None:
                                address = place.get('address') or place.get('住所', '')
                                if address:
                                    try:
                                        geolocator = Nominatim(user_agent="phone_number_app")
                                        location = geolocator.geocode(address, timeout=5)
                                        if location:
                                            place_lat = location.latitude
                                            place_lon = location.longitude
                                    except:
                                        pass
                            
                            if place_lat is not None and place_lon is not None:
                                distance = calculate_distance(center_lat, center_lon, place_lat, place_lon)
                                if distance > radius_meters:
                                    continue
                        
                        if filter_takeout_only:
                            service_options = place.get('service_options', {})
                            takeout = service_options.get('takeout') or service_options.get('テイクアウト')
                            if not takeout:
                                continue
                        
                        if len(phone_numbers) >= max_results:
                            break
                            
                        title = place.get('title', 'タイトル不明')
                        phone = place.get('phone') or place.get('電話', '電話番号なし')
                        address = place.get('address') or place.get('住所', '住所不明')
                        rating = place.get('rating', '評価なし')
                        reviews = place.get('reviews', 'レビュー数なし')
                        
                        distance_info = {}
                        if use_radius and radius_meters:
                            gps = place.get('gps_coordinates', {})
                            if gps and gps.get('latitude') and gps.get('longitude'):
                                distance = calculate_distance(center_lat, center_lon, gps['latitude'], gps['longitude'])
                                distance_info['距離（m）'] = f"{distance:.0f}"
                        
                        phone_numbers.append({
                            '店舗名': title,
                            '電話番号': phone,
                            '住所': address,
                            '評価': rating,
                            'レビュー数': reviews,
                            **distance_info
                        })
                    
                    progress_bar.progress(1.0)
                    status_text.empty()
                    
                    if phone_numbers:
                        st.success(f"✅ {len(phone_numbers)}件の店舗が見つかりました！")
                        
                        df = pd.DataFrame(phone_numbers)
                        
                        tab1, tab2, tab3 = st.tabs(["📊 テーブル表示", "📋 リスト表示", "📥 CSVダウンロード"])
                        
                        with tab1:
                            st.dataframe(df, use_container_width=True, hide_index=True)
                        
                        with tab2:
                            for index, place in enumerate(phone_numbers, 1):
                                with st.container():
                                    col1, col2 = st.columns([3, 1])
                                    with col1:
                                        st.markdown(f"### {index}. {place['店舗名']}")
                                        st.markdown(f"📞 **電話番号:** {place['電話番号']}")
                                        st.markdown(f"📍 **住所:** {place['住所']}")
                                        if place['評価'] != '評価なし':
                                            st.markdown(f"⭐ **評価:** {place['評価']} ({place['レビュー数']}件)")
                                    st.divider()
                        
                        with tab3:
                            st.markdown("### CSVファイルをダウンロード")
                            csv = df.to_csv(index=False, encoding='utf-8-sig')
                            st.download_button(
                                label="📥 CSVファイルをダウンロード",
                                data=csv,
                                file_name=f"phone_numbers_{search_query}_{len(phone_numbers)}件.csv",
                                mime="text/csv",
                                use_container_width=True
                            )
                            st.markdown("#### プレビュー")
                            st.dataframe(df, use_container_width=True, hide_index=True)
                        
                        with st.sidebar:
                            st.markdown("---")
                            st.markdown(f"### 📞 電話番号リスト ({len(phone_numbers)}件)")
                            for index, place in enumerate(phone_numbers[:20], 1):
                                if place['電話番号'] != '電話番号なし':
                                    st.markdown(f"{index}. {place['電話番号']}")
                            if len(phone_numbers) > 20:
                                st.caption(f"他 {len(phone_numbers) - 20} 件...")
                    
                    else:
                        st.warning("⚠️ 電話番号が見つかりませんでした。")
                        if results:
                            st.json(results)
                            
                except Exception as e:
                    st.error(f"❌ エラーが発生しました: {str(e)}")
                    st.exception(e)

# CSVインポートタブ
with tab_csv:
    st.markdown("### 📄 CSVから電話番号取得")
    st.markdown("CSVまたはExcelファイルに含まれる屋号（店名）から、Google Mapsで電話番号を取得します。")
    
    uploaded_file = st.file_uploader(
        "CSVまたはExcelファイルをアップロード",
        type=['csv', 'xlsx', 'xls'],
        help="屋号（店名）が含まれるCSVまたはExcelファイルをアップロードしてください"
    )
    
    if uploaded_file is not None:
        try:
            if uploaded_file.name.endswith('.csv'):
                df_uploaded = pd.read_csv(uploaded_file)
            else:
                df_uploaded = pd.read_excel(uploaded_file)
            
            st.success(f"✅ ファイルを読み込みました（{len(df_uploaded)}行）")
            
            st.markdown("#### 📋 データプレビュー")
            st.dataframe(df_uploaded.head(10), use_container_width=True)
            
            st.markdown("#### 🔍 列の選択")
            columns = df_uploaded.columns.tolist()
            
            auto_detected_col = None
            for col in columns:
                col_lower = col.lower()
                if any(keyword in col_lower for keyword in ['店名', '屋号', '名前', 'name', 'title', '店舗名', '名称']):
                    auto_detected_col = col
                    break
            
            store_name_col = st.selectbox(
                "屋号（店名）の列を選択 *",
                columns,
                index=columns.index(auto_detected_col) if auto_detected_col else 0,
                help="屋号（店名）が含まれる列を選択してください"
            )
            
            # 共通検索条件（元の挙動を維持）
            st.markdown("#### 📍 共通検索条件（任意）")
            
            col_cond1, col_cond2 = st.columns(2)
            
            with col_cond1:
                location_name = st.text_input(
                    "地名（任意）",
                    placeholder="例: 東京都渋谷区",
                    help="地名を指定すると、その地域で検索します"
                )
            
            with col_cond2:
                use_radius_csv = st.checkbox("検索半径を指定", help="指定した半径内の店舗のみを取得します")
                radius_meters_csv = None
                if use_radius_csv:
                    radius_meters_csv = st.number_input(
                        "検索半径（メートル）",
                        min_value=100,
                        max_value=50000,
                        value=1000,
                        step=100
                    )
            
            center_lat_csv = None
            center_lon_csv = None
            
            if use_radius_csv and not location_name:
                st.markdown("##### 中心座標の指定（半径指定時は必須）")
                col_coord1, col_coord2 = st.columns(2)
                with col_coord1:
                    center_lat_csv = st.number_input(
                        "緯度",
                        value=35.6762,
                        format="%.7f"
                    )
                with col_coord2:
                    center_lon_csv = st.number_input(
                        "経度",
                        value=139.6503,
                        format="%.7f"
                    )
            
            if st.button("🔍 電話番号を取得", type="primary", use_container_width=True):
                if store_name_col not in df_uploaded.columns:
                    st.error("❌ 選択した列が存在しません")
                else:
                    store_names = df_uploaded[store_name_col].dropna().astype(str).tolist()
                    
                    if not store_names:
                        st.warning("⚠️ 屋号が含まれていません")
                    else:
                        # 検索場所を設定（元の挙動を維持）
                        location_str_csv = None
                        if location_name:
                            with st.spinner(f"「{location_name}」の座標を取得しています..."):
                                result = get_coordinates_from_address(location_name)
                                if result['success']:
                                    center_lat_csv = result['latitude']
                                    center_lon_csv = result['longitude']
                                    zoom_csv = radius_to_zoom_level(radius_meters_csv) if radius_meters_csv else 14
                                    location_str_csv = f"@{center_lat_csv},{center_lon_csv},{zoom_csv}z"
                                    st.success(f"✅ 座標を取得しました: {result['address']}")
                                else:
                                    st.warning(f"⚠️ 座標を取得できませんでした: {result.get('error', '')}")
                        elif use_radius_csv and center_lat_csv and center_lon_csv:
                            zoom_csv = radius_to_zoom_level(radius_meters_csv) if radius_meters_csv else 14
                            location_str_csv = f"@{center_lat_csv},{center_lon_csv},{zoom_csv}z"
                        
                        results_list = []
                        progress_bar = st.progress(0)
                        status_text = st.empty()
                        
                        for idx, store_name in enumerate(store_names):
                            status_text.text(f"検索中: {idx + 1}/{len(store_names)} - {store_name}")
                            progress_bar.progress((idx + 1) / len(store_names))
                            
                            # ▼ 精度向上: location_hint（地名）を渡す
                            result = search_store_by_name(store_name, location_str_csv, api_key, location_hint=location_name)
                            # ▲ 精度向上ここまで
                            
                            row_result = {
                                '屋号（入力値）': store_name,
                                '取得店舗名': result.get('店舗名', ''),
                                '電話番号': result.get('電話番号', ''),
                                '住所': result.get('住所', ''),
                                '緯度': result.get('緯度', ''),
                                '経度': result.get('経度', ''),
                                '評価': result.get('評価', ''),
                                'レビュー数': result.get('レビュー数', ''),
                                '信頼度': result.get('信頼度', 'Low'),
                                'エラー': result.get('error', '') if not result.get('success', False) else ''
                            }
                            
                            if use_radius_csv and radius_meters_csv and center_lat_csv and center_lon_csv:
                                lat = result.get('緯度')
                                lon = result.get('経度')
                                if lat and lon:
                                    distance = calculate_distance(center_lat_csv, center_lon_csv, lat, lon)
                                    row_result['距離（m）'] = f"{distance:.0f}"
                                    if distance > radius_meters_csv:
                                        row_result['取得店舗名'] = ''
                                        row_result['電話番号'] = ''
                                        row_result['住所'] = ''
                                        row_result['エラー'] = f'半径{radius_meters_csv}mを超えています'
                                else:
                                    row_result['距離（m）'] = ''
                            else:
                                row_result['距離（m）'] = ''
                            
                            results_list.append(row_result)
                            time.sleep(0.5)
                        
                        progress_bar.progress(1.0)
                        status_text.empty()
                        
                        if results_list:
                            df_results = pd.DataFrame(results_list)
                            
                            st.success(f"✅ {len(results_list)}件の検索が完了しました！")
                            
                            tab_result1, tab_result2, tab_result3 = st.tabs(["📊 テーブル表示", "📋 リスト表示", "📥 CSVダウンロード"])
                            
                            with tab_result1:
                                st.dataframe(df_results, use_container_width=True, hide_index=True)
                            
                            with tab_result2:
                                for index, row in enumerate(results_list, 1):
                                    with st.container():
                                        st.markdown(f"### {index}. {row['屋号（入力値）']}")
                                        if row['取得店舗名']:
                                            st.markdown(f"**取得店舗名:** {row['取得店舗名']}")
                                        if row['電話番号']:
                                            st.markdown(f"📞 **電話番号:** {row['電話番号']}")
                                        if row['住所']:
                                            st.markdown(f"📍 **住所:** {row['住所']}")
                                        if row.get('距離（m）'):
                                            st.markdown(f"📏 **距離:** {row['距離（m）']}m")
                                        confidence = row.get('信頼度', 'Low')
                                        confidence_emoji = {
                                            'Very High': '🟢',
                                            'High': '🟡',
                                            'Mid': '🟠',
                                            'Low': '🔴'
                                        }.get(confidence, '⚪')
                                        st.markdown(f"{confidence_emoji} **信頼度:** {confidence}")
                                        if row['エラー']:
                                            st.warning(f"⚠️ {row['エラー']}")
                                        st.divider()
                            
                            with tab_result3:
                                st.markdown("### CSVファイルをダウンロード")
                                csv_output = df_results.to_csv(index=False, encoding='utf-8-sig')
                                st.download_button(
                                    label="📥 CSVファイルをダウンロード",
                                    data=csv_output,
                                    file_name=f"phone_numbers_from_csv_{len(results_list)}件.csv",
                                    mime="text/csv",
                                    use_container_width=True
                                )
                                st.markdown("#### プレビュー")
                                st.dataframe(df_results, use_container_width=True, hide_index=True)
        
        except Exception as e:
            st.error(f"❌ エラーが発生しました: {str(e)}")
            st.exception(e)
    else:
        st.info("ℹ️ CSVまたはExcelファイルをアップロードしてください")

# フッター
st.markdown("---")
st.caption("Made with ❤️ using Streamlit and SerpAPI")