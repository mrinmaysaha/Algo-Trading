import os
import sys
import time
import requests
import pandas as pd
import duckdb
from datetime import datetime, timezone, timedelta

# Configuration
DATA_DIR = "data/crypto"
os.makedirs(DATA_DIR, exist_ok=True)
HISTORIFY_DB = "db/historify.duckdb"

DELTA_API_URL = "https://api.delta.exchange/v2/history/candles"

SYMBOLS = ["BTCUSD", "ETHUSD"]
RESOLUTIONS = [
    ("1h", 3600, 90),     # resolution, seconds per bar, chunk days
    ("15m", 900, 20)
]

def fetch_candles_range(symbol: str, resolution: str, start_ts: int, end_ts: int) -> list:
    """Fetch candles in chunks from Delta Exchange public API."""
    all_candles = []
    curr_start = start_ts
    
    # Delta returns up to ~2,000 candles per call
    step_seconds = 1800 * (3600 if resolution == "1h" else 900)
    
    headers = {"Accept": "application/json"}
    
    while curr_start < end_ts:
        curr_end = min(curr_start + step_seconds, end_ts)
        params = {
            "symbol": symbol,
            "resolution": resolution,
            "start": curr_start,
            "end": curr_end
        }
        
        try:
            resp = requests.get(DELTA_API_URL, params=params, headers=headers, timeout=15)
            if resp.status_code == 200:
                data = resp.json()
                candles = data.get("result", [])
                if candles:
                    all_candles.extend(candles)
            elif resp.status_code == 429:
                print("Rate limit encountered, sleeping 2s...")
                time.sleep(2)
                continue
            else:
                print(f"Failed to fetch {symbol} {resolution} ({curr_start} - {curr_end}): {resp.status_code}")
        except Exception as e:
            print(f"Error fetching {symbol} {resolution}: {e}")
            time.sleep(1)
            
        curr_start = curr_end
        time.sleep(0.1) # polite delay
        
    return all_candles

def main():
    now = int(time.time())
    one_year_ago = now - (365 * 86400)
    
    print(f"Fetching 1 year of crypto market data from Delta Exchange ({datetime.fromtimestamp(one_year_ago, tz=timezone.utc).strftime('%Y-%m-%d')} to {datetime.fromtimestamp(now, tz=timezone.utc).strftime('%Y-%m-%d')})...")
    
    for symbol in SYMBOLS:
        for res_name, bar_sec, chunk_days in RESOLUTIONS:
            print(f"\nDownloading {symbol} [{res_name}]...")
            raw_candles = fetch_candles_range(symbol, res_name, one_year_ago, now)
            
            if not raw_candles:
                print(f"No candles returned for {symbol} {res_name}!")
                continue
                
            df = pd.DataFrame(raw_candles)
            if 'time' in df.columns:
                df.rename(columns={'time': 'timestamp'}, inplace=True)
                
            # Deduplicate & Sort
            df.drop_duplicates(subset=['timestamp'], inplace=True)
            df.sort_values(by='timestamp', inplace=True)
            df.reset_index(drop=True, inplace=True)
            
            # Format datetime
            df['datetime'] = pd.to_datetime(df['timestamp'], unit='s', utc=True)
            
            # Save parquet & csv
            parquet_path = os.path.join(DATA_DIR, f"{symbol}_{res_name}_1year.parquet")
            csv_path = os.path.join(DATA_DIR, f"{symbol}_{res_name}_1year.csv")
            df.to_parquet(parquet_path, index=False)
            df.to_csv(csv_path, index=False)
            print(f"Saved {len(df)} bars to {parquet_path} ({df['datetime'].min()} to {df['datetime'].max()})")
            
            # Ingest into DuckDB if exists
            if os.path.exists(HISTORIFY_DB):
                try:
                    con = duckdb.connect(HISTORIFY_DB)
                    # Prepare for market_data table
                    # Columns: symbol, exchange, interval, timestamp, open, high, low, close, volume, oi, created_at
                    # OpenAlgo canonical symbol: BTCUSDFUT / ETHUSDFUT
                    canonical_symbol = f"{symbol}FUT" if not symbol.endswith("FUT") else symbol
                    
                    df_ingest = df.copy()
                    df_ingest['symbol'] = canonical_symbol
                    df_ingest['exchange'] = 'CRYPTO'
                    df_ingest['interval'] = res_name
                    df_ingest['oi'] = 0.0
                    df_ingest['created_at'] = datetime.now()
                    
                    # Select matching columns
                    cols = ['symbol', 'exchange', 'interval', 'timestamp', 'open', 'high', 'low', 'close', 'volume', 'oi', 'created_at']
                    df_ingest = df_ingest[cols]
                    
                    # Delete existing slice to prevent duplicates
                    con.execute(f"DELETE FROM market_data WHERE symbol = '{canonical_symbol}' AND exchange = 'CRYPTO' AND interval = '{res_name}'")
                    con.register('df_view', df_ingest)
                    con.execute("INSERT INTO market_data SELECT * FROM df_view")
                    
                    # Update data_catalog
                    con.execute(f"DELETE FROM data_catalog WHERE symbol = '{canonical_symbol}' AND exchange = 'CRYPTO' AND interval = '{res_name}'")
                    con.execute(f"""
                        INSERT INTO data_catalog (symbol, exchange, interval, first_timestamp, last_timestamp, record_count, last_download_at)
                        VALUES ('{canonical_symbol}', 'CRYPTO', '{res_name}', {int(df['timestamp'].min())}, {int(df['timestamp'].max())}, {len(df)}, CURRENT_TIMESTAMP)
                    """)
                    con.close()
                    print(f"Successfully ingested {canonical_symbol} [{res_name}] into Historify DuckDB!")
                except Exception as db_err:
                    print(f"Historify DuckDB ingestion note: {db_err}")

if __name__ == "__main__":
    main()
