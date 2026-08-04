# database/master_contract_db.py

import gzip
import os
import shutil
import time
from datetime import datetime

import pandas as pd
import requests
from sqlalchemy import Column, Float, Index, Integer, Sequence, String
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import scoped_session, sessionmaker

from database.engine_factory import create_db_engine
from extensions import socketio  # Import SocketIO
from utils.logging import get_logger

logger = get_logger(__name__)


DATABASE_URL = os.getenv("DATABASE_URL")  # Replace with your database path

engine = create_db_engine(DATABASE_URL)
db_session = scoped_session(sessionmaker(autocommit=False, autoflush=False, bind=engine))
Base = declarative_base()
Base.query = db_session.query_property()


class SymToken(Base):
    __tablename__ = "symtoken"
    id = Column(Integer, Sequence("symtoken_id_seq"), primary_key=True)
    symbol = Column(String, nullable=False, index=True)  # Single column index
    brsymbol = Column(String, nullable=False, index=True)  # Single column index
    name = Column(String)
    exchange = Column(String, index=True)  # Include this column in a composite index
    brexchange = Column(String, index=True)
    token = Column(String, index=True)  # Indexed for performance
    expiry = Column(String)
    strike = Column(Float)
    lotsize = Column(Integer)
    instrumenttype = Column(String)
    tick_size = Column(Float)
    contract_value = Column(Float)

    # Define a composite index on symbol and exchange columns
    __table_args__ = (Index("idx_symbol_exchange", "symbol", "exchange"),)


def init_db():
    logger.info("Initializing Master Contract DB")
    Base.metadata.create_all(bind=engine)


def drop_symtoken_indexes():
    logger.info("Dropping Symtoken Indexes for fast bulk operation")
    try:
        with engine.begin() as conn:
            conn.exec_driver_sql("DROP INDEX IF EXISTS ix_symtoken_symbol")
            conn.exec_driver_sql("DROP INDEX IF EXISTS ix_symtoken_brsymbol")
            conn.exec_driver_sql("DROP INDEX IF EXISTS ix_symtoken_exchange")
            conn.exec_driver_sql("DROP INDEX IF EXISTS ix_symtoken_brexchange")
            conn.exec_driver_sql("DROP INDEX IF EXISTS ix_symtoken_token")
            conn.exec_driver_sql("DROP INDEX IF EXISTS idx_symbol_exchange")
    except Exception as e:
        logger.warning(f"Error dropping indexes: {e}")


def create_symtoken_indexes():
    logger.info("Creating Symtoken Indexes")
    try:
        with engine.begin() as conn:
            conn.exec_driver_sql("CREATE INDEX IF NOT EXISTS ix_symtoken_symbol ON symtoken (symbol)")
            conn.exec_driver_sql("CREATE INDEX IF NOT EXISTS ix_symtoken_brsymbol ON symtoken (brsymbol)")
            conn.exec_driver_sql("CREATE INDEX IF NOT EXISTS ix_symtoken_exchange ON symtoken (exchange)")
            conn.exec_driver_sql("CREATE INDEX IF NOT EXISTS ix_symtoken_brexchange ON symtoken (brexchange)")
            conn.exec_driver_sql("CREATE INDEX IF NOT EXISTS ix_symtoken_token ON symtoken (token)")
            conn.exec_driver_sql("CREATE INDEX IF NOT EXISTS idx_symbol_exchange ON symtoken (symbol, exchange)")
        logger.info("Symtoken indexes created successfully")
    except Exception as e:
        logger.error(f"Error creating indexes: {e}")


def ensure_symtoken_schema(conn):
    """Ensure symtoken table schema has all required columns including contract_value."""
    try:
        cursor = conn.exec_driver_sql("PRAGMA table_info(symtoken)")
        existing_cols = [row[1] for row in cursor.fetchall()]
        if existing_cols and "contract_value" not in existing_cols:
            logger.info("Adding missing contract_value column to symtoken table")
            conn.exec_driver_sql("ALTER TABLE symtoken ADD COLUMN contract_value REAL")
    except Exception as e:
        logger.warning(f"Schema check exception: {e}")


def delete_symtoken_table():
    logger.info("Deleting Symtoken Table")
    try:
        db_session.remove()
        drop_symtoken_indexes()
        with engine.begin() as conn:
            ensure_symtoken_schema(conn)
            conn.exec_driver_sql("DELETE FROM symtoken")
        logger.info("Symtoken table deleted successfully")
    except Exception as e:
        logger.error(f"Error deleting symtoken table: {e}")
        db_session.rollback()
    finally:
        db_session.remove()


def copy_from_dataframe(df):
    logger.info("Performing Bulk Insert")
    try:
        db_session.remove()
        # Filter DataFrame columns to match SymToken database schema
        db_cols = [c.name for c in SymToken.__table__.columns if c.name != "id"]
        valid_cols = [col for col in db_cols if col in df.columns]
        df_to_insert = df[valid_cols]

        total_records = len(df_to_insert)

        # Replace NaN with None so SQLite handles NULL correctly
        df_to_insert = df_to_insert.where(pd.notnull(df_to_insert), None)
        rows = list(df_to_insert.itertuples(index=False, name=None))

        col_names = ", ".join(valid_cols)
        placeholders = ", ".join(["?"] * len(valid_cols))
        query = f"INSERT INTO symtoken ({col_names}) VALUES ({placeholders})"

        # Apply ultra-fast in-memory PRAGMAs prior to starting the transaction
        try:
            with engine.connect() as conn:
                conn.exec_driver_sql("PRAGMA synchronous = OFF")
                conn.exec_driver_sql("PRAGMA temp_store = MEMORY")
                conn.commit()
        except Exception:
            pass

        CHUNK_SIZE = 10000
        # Single transaction write with fast C-level executemany
        with engine.begin() as conn:
            raw_conn = conn.connection
            for i in range(0, total_records, CHUNK_SIZE):
                chunk = rows[i : i + CHUNK_SIZE]
                raw_conn.executemany(query, chunk)
                time.sleep(0.0001)

        logger.info(f"Bulk insert completed successfully with {total_records} new records.")

        # Re-create all B-Tree indexes in a single optimized pass
        create_symtoken_indexes()

        # Restore standard SQLite PRAGMAs after bulk load
        try:
            with engine.connect() as conn:
                conn.exec_driver_sql("PRAGMA synchronous = NORMAL")
                conn.commit()
        except Exception:
            pass

    except Exception as e:
        logger.error(f"Error during bulk insert: {e}")
        db_session.rollback()
    finally:
        db_session.remove()


def download_json_angel_data(url, output_path):
    """
    Downloads a JSON file from the specified URL and saves it to the specified path using streaming.
    """
    logger.info("Downloading JSON data")
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    response = requests.get(url, stream=True, timeout=(15, 120))  # 15s connect, 120s read timeout
    if response.status_code == 200:  # Successful download
        with open(output_path, "wb") as f:
            for chunk in response.iter_content(chunk_size=65536):
                if chunk:
                    f.write(chunk)
        logger.info("Download complete")
    else:
        logger.error(f"Failed to download data. Status code: {response.status_code}")


def reformat_symbol(row):
    symbol = row["symbol"]
    instrument_type = row["instrumenttype"]

    if instrument_type == "FUT":
        # For FUT, remove the spaces and append 'FUT' at the end
        parts = symbol.split(" ")
        if len(parts) == 5:  # Make sure the symbol has the correct format
            symbol = parts[0] + parts[2] + parts[3] + parts[4] + parts[1]
    elif instrument_type in ["CE", "PE"]:
        # For CE/PE, rearrange the parts and remove spaces
        parts = symbol.split(" ")
        if len(parts) == 6:  # Make sure the symbol has the correct format
            symbol = parts[0] + parts[3] + parts[4] + parts[5] + parts[1] + parts[2]
    else:
        symbol = symbol  # No change for other instrument types

    return symbol


def convert_date(date_str):
    # Convert from '19MAR2024' to '19-MAR-24'
    try:
        return datetime.strptime(date_str, "%d%b%Y").strftime("%d-%b-%y")
    except ValueError:
        # Return the original date if it doesn't match the format
        return date_str


def process_angel_json(path):
    """
    Processes the Angel JSON file to fit the existing database schema.
    Args:
    path (str): The file path of the downloaded JSON data.

    Returns:
    DataFrame: The processed DataFrame ready to be inserted into the database.
    """
    # Read JSON data into a DataFrame
    df = pd.read_json(path)

    # Rename the columns based on the database schema
    # Assuming that the JSON structure matches the sample response provided
    df = df.rename(
        columns={
            "exch_seg": "exchange",
            "instrumenttype": "instrumenttype",
            "lotsize": "lotsize",
            "strike": "strike",
            "symbol": "symbol",
            "token": "token",
            "name": "name",
            "tick_size": "tick_size",
        }
    )

    # Reformat 'symbol' column if needed (based on the given reformat_symbol function)
    # df['symbol'] = df.apply(lambda row: reformat_symbol(row), axis=1)

    # Assuming 'brsymbol' and 'brexchange' are not present in the JSON and are the same as 'symbol' and 'exchange'
    df["brsymbol"] = df["symbol"]
    df["brexchange"] = df["exchange"]

    # Update exchange names based on the instrument type
    df.loc[(df["instrumenttype"] == "AMXIDX") & (df["exchange"] == "NSE"), "exchange"] = "NSE_INDEX"
    df.loc[(df["instrumenttype"] == "AMXIDX") & (df["exchange"] == "BSE"), "exchange"] = "BSE_INDEX"
    df.loc[(df["instrumenttype"] == "AMXIDX") & (df["exchange"] == "MCX"), "exchange"] = "MCX_INDEX"

    # Reformat 'symbol' based on 'brsymbol'
    df["symbol"] = df["symbol"].str.replace("-EQ|-BE|-MF|-SG", "", regex=True)

    # Assuming the 'expiry' field in the JSON is in the format '19MAR2024'
    df["expiry"] = df["expiry"].apply(lambda x: convert_date(x) if pd.notnull(x) else x)
    df["expiry"] = df["expiry"].str.upper()

    # Convert 'strike' to float, 'lotsize' to int, and 'tick_size' to float as per the database schema
    df["strike"] = df["strike"].astype(float) / 100
    df.loc[(df["instrumenttype"] == "OPTCUR") & (df["exchange"] == "CDS"), "strike"] = (
        df["strike"].astype(float) / 100000
    )
    df.loc[(df["instrumenttype"] == "OPTIRC") & (df["exchange"] == "CDS"), "strike"] = (
        df["strike"].astype(float) / 100000
    )

    df["lotsize"] = df["lotsize"].astype(int)
    df["tick_size"] = df["tick_size"].astype(float) / 100  # Divide tick_size by 100

    # Futures Symbol Update in CDS and MCX Exchanges
    df.loc[(df["instrumenttype"] == "FUTCUR") & (df["exchange"] == "CDS"), "symbol"] = (
        df["name"] + df["expiry"].str.replace("-", "", regex=False) + "FUT"
    )
    df.loc[(df["instrumenttype"] == "FUTIRC") & (df["exchange"] == "CDS"), "symbol"] = (
        df["name"] + df["expiry"].str.replace("-", "", regex=False) + "FUT"
    )
    df.loc[(df["instrumenttype"] == "FUTCOM") & (df["exchange"] == "MCX"), "symbol"] = (
        df["name"] + df["expiry"].str.replace("-", "", regex=False) + "FUT"
    )
    # Options Symbol Update in CDS and MCX Exchanges
    df.loc[(df["instrumenttype"] == "OPTCUR") & (df["exchange"] == "CDS"), "symbol"] = (
        df["name"]
        + df["expiry"].str.replace("-", "", regex=False)
        + df["strike"].astype(str).str.replace(r"\.0", "", regex=True)
        + df["symbol"].str[-2:]
    )
    df.loc[(df["instrumenttype"] == "OPTIRC") & (df["exchange"] == "CDS"), "symbol"] = (
        df["name"]
        + df["expiry"].str.replace("-", "", regex=False)
        + df["strike"].astype(str).str.replace(r"\.0", "", regex=True)
        + df["symbol"].str[-2:]
    )
    df.loc[(df["instrumenttype"] == "OPTFUT") & (df["exchange"] == "MCX"), "symbol"] = (
        df["name"]
        + df["expiry"].str.replace("-", "", regex=False)
        + df["strike"].astype(str).str.replace(r"\.0", "", regex=True)
        + df["symbol"].str[-2:]
    )

    # BFO Index Futures Symbol Update (SENSEX, BANKEX, etc.)
    # Format: SYMBOL[DDMMMYY]FUT
    # Example: SENSEX28MAR24FUT
    df.loc[(df["instrumenttype"] == "FUTIDX") & (df["exchange"] == "BFO"), "symbol"] = (
        df["name"] + df["expiry"].str.replace("-", "", regex=False) + "FUT"
    )

    # BFO Stock Futures Symbol Update (RELIANCE, TCS, etc.)
    # Format: SYMBOL[DDMMMYY]FUT
    # Example: RELIANCE30OCT25FUT
    df.loc[(df["instrumenttype"] == "FUTSTK") & (df["exchange"] == "BFO"), "symbol"] = (
        df["name"] + df["expiry"].str.replace("-", "", regex=False) + "FUT"
    )

    # BFO Index Options Symbol Update (SENSEX, BANKEX, etc.)
    # Format: SYMBOL[DDMMMYY][StrikePrice][CE/PE]
    # Example: SENSEX28MAR2475000CE
    df.loc[
        (df["instrumenttype"] == "OPTIDX")
        & (df["exchange"] == "BFO")
        & (df["symbol"].str.endswith("CE", na=False)),
        "symbol",
    ] = (
        df["name"]
        + df["expiry"].str.replace("-", "", regex=False)
        + df["strike"].astype(str).str.replace(r"\.0", "", regex=True)
        + "CE"
    )
    df.loc[
        (df["instrumenttype"] == "OPTIDX")
        & (df["exchange"] == "BFO")
        & (df["symbol"].str.endswith("PE", na=False)),
        "symbol",
    ] = (
        df["name"]
        + df["expiry"].str.replace("-", "", regex=False)
        + df["strike"].astype(str).str.replace(r"\.0", "", regex=True)
        + "PE"
    )

    # BFO Stock Options Symbol Update (RELIANCE, TCS, etc.)
    # Format: SYMBOL[DDMMMYY][StrikePrice][CE/PE]
    # Example: RELIANCE30OCT251330PE
    df.loc[
        (df["instrumenttype"] == "OPTSTK")
        & (df["exchange"] == "BFO")
        & (df["symbol"].str.endswith("CE", na=False)),
        "symbol",
    ] = (
        df["name"]
        + df["expiry"].str.replace("-", "", regex=False)
        + df["strike"].astype(str).str.replace(r"\.0", "", regex=True)
        + "CE"
    )
    df.loc[
        (df["instrumenttype"] == "OPTSTK")
        & (df["exchange"] == "BFO")
        & (df["symbol"].str.endswith("PE", na=False)),
        "symbol",
    ] = (
        df["name"]
        + df["expiry"].str.replace("-", "", regex=False)
        + df["strike"].astype(str).str.replace(r"\.0", "", regex=True)
        + "PE"
    )

    # Common Index Symbol Formats
    # For NSE_INDEX, derive symbol from 'name' column
    # and normalize to OpenAlgo common format (uppercase, no spaces/hyphens)
    idx_mask = df["exchange"] == "NSE_INDEX"
    df.loc[idx_mask, "symbol"] = (
        df.loc[idx_mask, "name"]
        .str.upper()
        .str.replace(" ", "", regex=False)
        .str.replace("-", "", regex=False)
    )

    # For BSE_INDEX, derive symbol from 'name' column
    # and normalize to OpenAlgo common format (uppercase, no spaces/hyphens, remove S&P prefix)
    bse_idx_mask = df["exchange"] == "BSE_INDEX"
    df.loc[bse_idx_mask, "symbol"] = (
        df.loc[bse_idx_mask, "name"]
        .str.upper()
        .str.replace("S&P ", "", regex=False)
        .str.replace(" ", "", regex=False)
        .str.replace("-", "", regex=False)
    )

    # Override for major indices where normalized name differs from OpenAlgo standard
    df["symbol"] = df["symbol"].replace(
        {
            "NIFTY50": "NIFTY",
            "NIFTYBANK": "BANKNIFTY",
            "NIFTYFINSERVICE": "FINNIFTY",
            "NIFTYNEXT50": "NIFTYNXT50",
            "NIFTYMIDSELECT": "MIDCPNIFTY",
            "NIFTYMIDCAPSELECT": "MIDCPNIFTY",
            "SNSX50": "SENSEX50",
        }
    )

    # Convert instrumenttype from OPTIDX/OPTSTK to CE/PE (to match Zerodha format)
    # This ensures consistency across brokers for option chain queries
    df.loc[
        (df["instrumenttype"] == "OPTIDX") & (df["symbol"].str.endswith("CE", na=False)),
        "instrumenttype",
    ] = "CE"
    df.loc[
        (df["instrumenttype"] == "OPTIDX") & (df["symbol"].str.endswith("PE", na=False)),
        "instrumenttype",
    ] = "PE"
    df.loc[
        (df["instrumenttype"] == "OPTSTK") & (df["symbol"].str.endswith("CE", na=False)),
        "instrumenttype",
    ] = "CE"
    df.loc[
        (df["instrumenttype"] == "OPTSTK") & (df["symbol"].str.endswith("PE", na=False)),
        "instrumenttype",
    ] = "PE"

    # Convert MCX OPTFUT to CE/PE (to match NFO format)
    df.loc[
        (df["instrumenttype"] == "OPTFUT") & (df["symbol"].str.endswith("CE", na=False)),
        "instrumenttype",
    ] = "CE"
    df.loc[
        (df["instrumenttype"] == "OPTFUT") & (df["symbol"].str.endswith("PE", na=False)),
        "instrumenttype",
    ] = "PE"

    # Convert CDS OPTCUR/OPTIRC to CE/PE (to match NFO format)
    df.loc[
        (df["instrumenttype"] == "OPTCUR") & (df["symbol"].str.endswith("CE", na=False)),
        "instrumenttype",
    ] = "CE"
    df.loc[
        (df["instrumenttype"] == "OPTCUR") & (df["symbol"].str.endswith("PE", na=False)),
        "instrumenttype",
    ] = "PE"
    df.loc[
        (df["instrumenttype"] == "OPTIRC") & (df["symbol"].str.endswith("CE", na=False)),
        "instrumenttype",
    ] = "CE"
    df.loc[
        (df["instrumenttype"] == "OPTIRC") & (df["symbol"].str.endswith("PE", na=False)),
        "instrumenttype",
    ] = "PE"

    # Convert all futures instrument types to 'FUT' for consistency
    # FUTIDX (Index Futures), FUTSTK (Stock Futures) - NFO/BFO
    # FUTCOM (Commodity Futures) - MCX
    # FUTCUR, FUTIRC, FUTIRT (Currency/Interest Rate Futures) - CDS
    df.loc[df["instrumenttype"] == "FUTIDX", "instrumenttype"] = "FUT"
    df.loc[df["instrumenttype"] == "FUTSTK", "instrumenttype"] = "FUT"
    df.loc[df["instrumenttype"] == "FUTCOM", "instrumenttype"] = "FUT"
    df.loc[df["instrumenttype"] == "FUTCUR", "instrumenttype"] = "FUT"
    df.loc[df["instrumenttype"] == "FUTIRC", "instrumenttype"] = "FUT"
    df.loc[df["instrumenttype"] == "FUTIRT", "instrumenttype"] = "FUT"

    # Return the processed DataFrame
    return df


def delete_angel_temp_data(output_path):
    try:
        # Check if the file exists
        if os.path.exists(output_path):
            # Delete the file
            os.remove(output_path)
            logger.info(f"The temporary file {output_path} has been deleted.")
        else:
            logger.info(f"The temporary file {output_path} does not exist.")
    except Exception as e:
        logger.error(f"An error occurred while deleting the file: {e}")


def master_contract_download():
    logger.info("Downloading Master Contract")
    url = "https://margincalculator.angelbroking.com/OpenAPI_File/files/OpenAPIScripMaster.json"
    output_path = "tmp/angel.json"
    try:
        download_json_angel_data(url, output_path)
        token_df = process_angel_json(output_path)
        delete_angel_temp_data(output_path)
        # token_df['token'] = pd.to_numeric(token_df['token'], errors='coerce').fillna(-1).astype(int)

        # token_df = token_df.drop_duplicates(subset='symbol', keep='first')

        delete_symtoken_table()  # Consider the implications of this action
        copy_from_dataframe(token_df)

        try:
            socketio.emit(
                "master_contract_download", {"status": "success", "message": "Successfully Downloaded"}
            )
        except Exception as se:
            logger.debug(f"SocketIO emit ignored: {se}")

        return {"status": "success", "message": "Successfully Downloaded"}

    except Exception as e:
        logger.info(f"{str(e)}")
        try:
            socketio.emit("master_contract_download", {"status": "error", "message": str(e)})
        except Exception:
            pass
        return {"status": "error", "message": str(e)}


def search_symbols(symbol, exchange):
    return SymToken.query.filter(
        SymToken.symbol.like(f"%{symbol}%"), SymToken.exchange == exchange
    ).all()
