from unittest.mock import patch, MagicMock
import pytest
from marshmallow import ValidationError

from restx_api.schemas import OptionsOrderSchema, OptionsMultiOrderSchema, OptionsMultiOrderLegSchema
from restx_api.data_schemas import OptionSymbolSchema
from services.place_options_order_service import place_options_order


def test_options_order_schema_accepts_underlying_ltp():
    """Verify OptionsOrderSchema accepts underlying_ltp without validation error."""
    payload = {
        "apikey": "test_api_key",
        "strategy": "MCX_Quant_Engine_V3",
        "underlying": "CRUDEOILM",
        "exchange": "MCX",
        "offset": "ATM",
        "option_type": "CE",
        "action": "BUY",
        "quantity": 6,
        "underlying_ltp": 7921.00,
    }
    schema = OptionsOrderSchema()
    loaded = schema.load(payload)
    assert loaded["underlying_ltp"] == 7921.00
    assert loaded["underlying"] == "CRUDEOILM"
    assert loaded["quantity"] == 6


def test_options_order_schema_without_underlying_ltp():
    """Verify OptionsOrderSchema defaults underlying_ltp to None when omitted."""
    payload = {
        "apikey": "test_api_key",
        "strategy": "MCX_Quant_Engine_V3",
        "underlying": "CRUDEOILM",
        "exchange": "MCX",
        "offset": "ATM",
        "option_type": "CE",
        "action": "BUY",
        "quantity": 6,
    }
    schema = OptionsOrderSchema()
    loaded = schema.load(payload)
    assert loaded.get("underlying_ltp") is None


def test_option_symbol_schema_accepts_underlying_ltp():
    """Verify OptionSymbolSchema accepts underlying_ltp."""
    payload = {
        "apikey": "test_api_key",
        "underlying": "CRUDEOILM",
        "exchange": "MCX",
        "offset": "ATM",
        "option_type": "CE",
        "underlying_ltp": 7921.00,
    }
    schema = OptionSymbolSchema()
    loaded = schema.load(payload)
    assert loaded["underlying_ltp"] == 7921.00


def test_options_multiorder_schema_accepts_underlying_ltp():
    """Verify OptionsMultiOrderSchema accepts underlying_ltp at multiorder and leg levels."""
    payload = {
        "apikey": "test_api_key",
        "strategy": "STRADDLE",
        "underlying": "CRUDEOILM",
        "exchange": "MCX",
        "underlying_ltp": 7921.00,
        "legs": [
            {
                "offset": "ATM",
                "option_type": "CE",
                "action": "BUY",
                "quantity": 6,
                "underlying_ltp": 7921.00,
            },
            {
                "offset": "ATM",
                "option_type": "PE",
                "action": "BUY",
                "quantity": 6,
            },
        ],
    }
    schema = OptionsMultiOrderSchema()
    loaded = schema.load(payload)
    assert loaded["underlying_ltp"] == 7921.00
    assert loaded["legs"][0]["underlying_ltp"] == 7921.00
    assert loaded["legs"][1].get("underlying_ltp") is None


@patch("services.place_options_order_service.get_option_symbol")
@patch("services.place_options_order_service.place_order")
def test_place_options_order_passes_underlying_ltp(mock_place_order, mock_get_option_symbol):
    """Verify place_options_order passes underlying_ltp to get_option_symbol and place_order."""
    mock_get_option_symbol.return_value = (
        True,
        {
            "symbol": "CRUDEOILM26AUG7900CE",
            "exchange": "MCX",
            "underlying_ltp": 7921.00,
        },
        200,
    )
    mock_place_order.return_value = (
        True,
        {"status": "success", "orderid": "123456", "mode": "live"},
        200,
    )

    options_data = {
        "apikey": "test_api_key",
        "strategy": "MCX_Quant_Engine_V3",
        "underlying": "CRUDEOILM",
        "exchange": "MCX",
        "offset": "ATM",
        "option_type": "CE",
        "action": "BUY",
        "quantity": 6,
        "underlying_ltp": 7921.00,
    }

    success, response, status_code = place_options_order(options_data, api_key="test_api_key")

    assert success is True
    assert status_code == 200
    assert response["underlying_ltp"] == 7921.00
    assert response["symbol"] == "CRUDEOILM26AUG7900CE"

    # Verify get_option_symbol received underlying_ltp
    mock_get_option_symbol.assert_called_once_with(
        underlying="CRUDEOILM",
        exchange="MCX",
        expiry_date=None,
        strike_int=None,
        offset="ATM",
        option_type="CE",
        api_key="test_api_key",
        underlying_ltp=7921.00,
    )

    # Verify place_order received underlying_ltp in order_data
    call_args = mock_place_order.call_args
    assert call_args.kwargs["order_data"]["underlying_ltp"] == 7921.00
    assert call_args.kwargs["order_data"]["symbol"] == "CRUDEOILM26AUG7900CE"
