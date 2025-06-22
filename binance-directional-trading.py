import pandas as pd
import numpy as np
import concurrent.futures
import requests
import os
from binance.client import Client
from dotenv import load_dotenv

# ========== [환경 변수 로딩] ==========
load_dotenv()
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

# ========== [Binance API 클라이언트] ==========
api_key = 'YOUR_API_KEY'  # 필요 시 환경 변수 처리 가능
api_secret = 'YOUR_API_SECRET'
client = Client(api_key, api_secret)

# ========== [텔레그램 전송 함수] ==========
def send_telegram(message):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    data = {"chat_id": CHAT_ID, "text": message}
    try:
        response = requests.post(url, data=data)
        if response.status_code != 200:
            print(f"[텔레그램 오류] 코드: {response.status_code}, 응답: {response.text}")
    except Exception as e:
        print(f"[텔레그램 예외] {e}")

# ========== [USDT 선물 심볼 리스트 가져오기] ==========
def get_all_futures_symbols(client):
    info = client.futures_exchange_info()
    return [item['symbol'] for item in info['symbols'] if item['status'] == 'TRADING' and item['symbol'].endswith('USDT')]

# ========== [기술적 지표 계산] ==========
def calculate_indicators(df):
    df['close'] = pd.to_numeric(df['close'])
    df['high'] = pd.to_numeric(df['high'])
    df['low'] = pd.to_numeric(df['low'])
    df['volume'] = pd.to_numeric(df['volume'])

    df['sma5'] = df['close'].rolling(window=5).mean()
    df['sma20'] = df['close'].rolling(window=20).mean()
    std = df['close'].rolling(window=20).std()
    df['upper_band'] = df['sma20'] + (2 * std)
    df['lower_band'] = df['sma20'] - (2 * std)
    df['sma_volume20'] = df['volume'].rolling(window=20).mean()
    df['return'] = df['close'].pct_change()

    return df

# ========== [롱/숏 조건 감지] ==========
def detect_signals(symbol):
    try:
        klines = client.futures_klines(symbol=symbol, interval='4h', limit=50)
        df = pd.DataFrame(klines, columns=[
            'timestamp', 'open', 'high', 'low', 'close', 'volume',
            'close_time', 'quote_asset_volume', 'number_of_trades',
            'taker_buy_base_asset_volume', 'taker_buy_quote_asset_volume', 'ignore'
        ])
        df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
        df.set_index('timestamp', inplace=True)
        df.index = df.index.tz_localize('UTC').tz_convert('Asia/Seoul')

        if len(df) < 30:
            return None, None

        df = calculate_indicators(df)

        row = df.iloc[-1]
        prev = df.iloc[-2]
        recent_20 = df['close'].iloc[-20:]
        upper_now, upper_prev = row['upper_band'], prev['upper_band']
        lower_now, lower_prev = row['lower_band'], prev['lower_band']
        sma5_now = row['sma5']

        last10 = df.iloc[-11:-1]
        broke_above_sma5 = (last10['close'] < last10['sma5']).any()
        broke_below_sma5 = (last10['close'] > last10['sma5']).any()

        is_long = (
            row['close'] > upper_now and
            row['close'] == recent_20.max() and
            upper_now > upper_prev and
            lower_now < lower_prev and
            row['return'] >= 0.05 and
            row['volume'] > row['sma_volume20'] and
            broke_above_sma5
        )

        is_short = (
            row['close'] < lower_now and
            row['close'] == recent_20.min() and
            lower_now < lower_prev and
            row['return'] <= -0.05 and
            row['volume'] > row['sma_volume20'] and
            broke_below_sma5
        )

        long_signal = (symbol, row.name.strftime('%Y-%m-%d %H:%M'), round(row['return'] * 100, 2)) if is_long else None
        short_signal = (symbol, row.name.strftime('%Y-%m-%d %H:%M'), round(row['return'] * 100, 2)) if is_short else None

        return long_signal, short_signal

    except Exception as e:
        print(f"[오류] {symbol}: {e}")
        return None, None

# ========== [병렬 처리 전체 분석] ==========
def analyze_all_symbols():
    symbols = get_all_futures_symbols(client)
    all_longs = []
    all_shorts = []

    def process(symbol):
        return detect_signals(symbol)

    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
        results = executor.map(process, symbols)

    for long_sig, short_sig in results:
        if long_sig:
            all_longs.append(long_sig)
        if short_sig:
            all_shorts.append(short_sig)

    return all_longs, all_shorts

# ========== [실행 및 텔레그램 전송] ==========
if __name__ == "__main__":
    long_signals, short_signals = analyze_all_symbols()

    message = "⏰ [4시간봉 자동 시그널 리포트]\n\n"

    if long_signals:
        message += "📈 롱 조건 충족:\n"
        for s in long_signals:
            message += f"  - {s[0]} | 시점: {s[1]} | 수익률: {s[2]}%\n"
    else:
        message += "📈 롱 조건 충족: 없음\n"

    if short_signals:
        message += "\n📉 숏 조건 충족:\n"
        for s in short_signals:
            message += f"  - {s[0]} | 시점: {s[1]} | 수익률: {s[2]}%\n"
    else:
        message += "\n📉 숏 조건 충족: 없음\n"

    print(message)
    send_telegram(message)
