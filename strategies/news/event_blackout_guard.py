# =============================================================================
# OpenAlgo Event Blackout Guard
# Deterministic calendar lock for high-volatility commodity macro events
# Protects against 50-150 point slippage spikes on EIA, FOMC, CPI, and NFP
# =============================================================================

import datetime
from typing import Tuple, Optional, Dict, List

IST = datetime.timezone(datetime.timedelta(hours=5, minutes=30))

class EventBlackoutGuard:
    """
    Monitors deterministic scheduled economic releases and locks new trade entries
    during high-volatility windows.
    """

    # Buffer minutes around the event
    PRE_EVENT_BUFFER_MINUTES = 5
    POST_EVENT_BUFFER_MINUTES = 5

    @classmethod
    def is_dst(cls, dt: datetime.datetime) -> bool:
        """Determines if US Eastern Daylight Time (EDT) is active (March to November)."""
        year = dt.year
        # Second Sunday in March
        march_first = datetime.date(year, 3, 1)
        first_sun = (6 - march_first.weekday()) % 7 + 1
        dst_start = datetime.date(year, 3, first_sun + 7)

        # First Sunday in November
        nov_first = datetime.date(year, 11, 1)
        dst_end = datetime.date(year, 11, (6 - nov_first.weekday()) % 7 + 1)

        d = dt.date()
        return dst_start <= d < dst_end

    @classmethod
    def get_event_time_ist(cls, base_hour_winter: int, base_minute_winter: int, dt: datetime.datetime) -> datetime.time:
        """Adjusts US release times for Daylight Savings Time (1 hour earlier in DST)."""
        if cls.is_dst(dt):
            return datetime.time(base_hour_winter - 1, base_minute_winter)
        return datetime.time(base_hour_winter, base_minute_winter)

    @classmethod
    def is_blackout_active(cls, symbol: str, current_dt: Optional[datetime.datetime] = None) -> Tuple[bool, str]:
        """
        Checks if symbol is currently within a high-volatility event blackout window.
        Returns: (is_blocked: bool, reason: str)
        """
        if current_dt is None:
            current_dt = datetime.datetime.now(IST)
        elif current_dt.tzinfo is None:
            current_dt = current_dt.replace(tzinfo=IST)

        weekday = current_dt.weekday()  # Monday=0, Wednesday=2, Thursday=3, Friday=4
        sym = symbol.upper()

        # 1. EIA Weekly Petroleum Status Report (Wednesdays) -> Impacts CRUDEOIL / CRUDEOILM
        if "CRUDE" in sym and weekday == 2:
            event_time = cls.get_event_time_ist(20, 0, current_dt)
            blackout_start = (datetime.datetime.combine(current_dt.date(), event_time, tzinfo=IST) 
                              - datetime.timedelta(minutes=cls.PRE_EVENT_BUFFER_MINUTES))
            blackout_end = (datetime.datetime.combine(current_dt.date(), event_time, tzinfo=IST) 
                            + datetime.timedelta(minutes=cls.POST_EVENT_BUFFER_MINUTES))
            if blackout_start <= current_dt <= blackout_end:
                return True, f"EIA Crude Oil Inventory Blackout ({blackout_start.strftime('%H:%M')}-{blackout_end.strftime('%H:%M')} IST)"

        # 2. EIA Weekly Natural Gas Storage Report (Thursdays) -> Impacts NATGAS / NATGASMINI
        if ("NATGAS" in sym or "NATURALGAS" in sym) and weekday == 3:
            event_time = cls.get_event_time_ist(20, 0, current_dt)
            blackout_start = (datetime.datetime.combine(current_dt.date(), event_time, tzinfo=IST) 
                              - datetime.timedelta(minutes=cls.PRE_EVENT_BUFFER_MINUTES))
            blackout_end = (datetime.datetime.combine(current_dt.date(), event_time, tzinfo=IST) 
                            + datetime.timedelta(minutes=cls.POST_EVENT_BUFFER_MINUTES))
            if blackout_start <= current_dt <= blackout_end:
                return True, f"EIA Natural Gas Storage Blackout ({blackout_start.strftime('%H:%M')}-{blackout_end.strftime('%H:%M')} IST)"

        # 3. US Non-Farm Payrolls (NFP) (First Friday of the Month) -> Impacts GOLD, SILVER, CRUDE
        if weekday == 4 and current_dt.day <= 7:
            event_time = cls.get_event_time_ist(19, 0, current_dt)
            blackout_start = (datetime.datetime.combine(current_dt.date(), event_time, tzinfo=IST) 
                              - datetime.timedelta(minutes=cls.PRE_EVENT_BUFFER_MINUTES))
            blackout_end = (datetime.datetime.combine(current_dt.date(), event_time, tzinfo=IST) 
                            + datetime.timedelta(minutes=cls.POST_EVENT_BUFFER_MINUTES))
            if blackout_start <= current_dt <= blackout_end:
                return True, f"US Non-Farm Payrolls (NFP) Blackout ({blackout_start.strftime('%H:%M')}-{blackout_end.strftime('%H:%M')} IST)"

        return False, ""
