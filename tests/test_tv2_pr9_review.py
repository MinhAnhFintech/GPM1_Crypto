"""TV4 regressions for the received TV2/TV3 raw handoffs; offline only."""
import numpy as np
import pandas as pd
import pytest

from src.features.ohlcv_long import build_long
from src.features.ohlcv_quality import read_raw
from src.collectors.historical import binance_vision as vision

START = pd.Timestamp('2026-01-01T00:01Z')
END = START + pd.Timedelta(minutes=3)


def raw(tmp_path, venue='binance_spot', **changes):
    exchange, market = venue.rsplit('_', 1)
    frame = pd.DataFrame({
        'ts_open_utc': pd.date_range(START-pd.Timedelta(minutes=1), periods=3, freq='1min'),
        'exchange': exchange, 'market_type': market, 'venue': venue, 'symbol': 'BTC/USDT',
        'open': 100., 'high': 101., 'low': 99., 'close': 100.,
        'volume_base': 2., 'volume_quote': 201., 'data_kind': 'historical',
    })
    for column, value in changes.items():
        frame[column] = value
    path = tmp_path/f'{venue}_BTCUSDT_1m.parquet'
    frame.to_parquet(path, index=False)
    return frame, path


def test_original_binance_raw_and_observed_quote_volume_are_preserved(tmp_path):
    frame, path = raw(tmp_path)
    legacy = frame.rename(columns={'ts_open_utc':'open_time_utc', 'volume_base':'volume', 'volume_quote':'quote_volume'})
    legacy = legacy.drop(columns=['exchange','market_type','venue','symbol','data_kind'])
    legacy['close_time_utc'] = legacy.open_time_utc+pd.Timedelta(seconds=59, milliseconds=999)
    legacy.to_parquet(path,index=False)
    before = path.read_bytes()
    result = build_long(tmp_path,['binance_spot'],'BTC/USDT',START,END)
    assert len(result) == 3 and result.ts_available.iloc[0] == START
    assert result.volume_quote.eq(201.).all()  # never close * volume (200)
    assert path.read_bytes() == before


def test_unobserved_ccxt_quote_volume_stays_missing(tmp_path):
    raw(tmp_path,'okx_perp',volume_quote=np.nan)
    result=build_long(tmp_path,['okx_perp'],'BTC/USDT',START,END)
    assert result.volume_quote.isna().all()
    assert result.volume_base.eq(2.).all()


@pytest.mark.parametrize('case',['naive','off_grid','infinite','missing_base','wrong_venue','metadata_synthetic'])
def test_raw_contract_errors_cannot_be_silently_relabelled_or_accepted(tmp_path,case):
    frame,path=raw(tmp_path)
    if case=='naive': frame['ts_open_utc']=frame.ts_open_utc.dt.tz_localize(None)
    elif case=='off_grid': frame['ts_open_utc']+=pd.Timedelta(seconds=1)
    elif case=='infinite': frame['high']=np.inf
    elif case=='missing_base': frame['volume_base']=np.nan
    elif case=='wrong_venue': frame['venue']='bybit_spot'
    else: frame.attrs['data_kind']='synthetic'
    frame.to_parquet(path,index=False)
    with pytest.raises(ValueError):
        build_long(tmp_path,['binance_spot'],'BTC/USDT',START,END)


@pytest.mark.parametrize('all_empty',[False,True])
def test_every_configured_venue_must_have_data_inside_the_sample(tmp_path,all_empty):
    raw(tmp_path,'binance_spot')
    raw(tmp_path,'okx_spot',ts_open_utc=pd.date_range('2025-01-01',periods=3,freq='1min',tz='UTC'))
    start,end=(START+pd.Timedelta(days=30),END+pd.Timedelta(days=30)) if all_empty else (START,END)
    with pytest.raises(ValueError):
        build_long(tmp_path,['binance_spot','okx_spot'],'BTC/USDT',start,end)


@pytest.mark.parametrize('venues',[[],['binance_spot','binance_spot'],['../binance_spot']])
def test_configured_venue_ids_are_validated_before_reading(tmp_path,venues):
    raw(tmp_path)
    with pytest.raises(ValueError,match='venue'):
        build_long(tmp_path,venues,'BTC/USDT',START,END)


def test_quality_reader_accepts_coexisting_original_and_team_aliases(tmp_path):
    frame,path=raw(tmp_path)
    frame['open_time_utc']=frame.ts_open_utc
    frame['volume']=frame.volume_base
    frame.to_parquet(path,index=False)
    result=read_raw(path)
    assert len(result)==3 and result.columns.is_unique
    assert result.volume.eq(2.).all()


def test_vision_includes_previous_month_candle_available_at_sample_start(tmp_path,monkeypatch):
    called=[]
    def month(symbol,ym,cache):
        called.append(ym)
        opening=pd.Timestamp('2026-01-31T23:59Z') if ym=='2026-01' else pd.Timestamp('2026-02-01T00:00Z')
        values=[opening.value//1_000_000,100.,101.,99.,100.,2.,opening.value//1_000_000+59999,201.,3,1.,100.,0]
        return pd.DataFrame([values],columns=vision.COLS)
    monkeypatch.setattr(vision,'download_month',month)
    config=tmp_path/'config.yaml'
    config.write_text("sample:\n  start: '2026-02-01T00:00:00Z'\n  end: '2026-02-01T00:02:00Z'\n",encoding='utf-8')
    vision.main(['--config',str(config),'--out',str(tmp_path/'raw')])
    result=pd.read_parquet(tmp_path/'raw/binance_spot_BTCUSDT_1m.parquet')
    assert called==['2026-01','2026-02']
    assert result.ts_open_utc.min()==pd.Timestamp('2026-01-31T23:59Z')
