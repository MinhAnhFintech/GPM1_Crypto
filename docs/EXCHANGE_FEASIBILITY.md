# Bao cao kha thi du lieu cac san (C3.01)

- Thoi gian chay (UTC): `2026-10-17 08:29:21`
- ccxt: `4.5.88`  |  Timeframe: `1m`  |  Nguong do sau yeu cau: **90 ngay**
- Ket qua phu thuoc IP/vung cua may chay. Cac so 'chinh thuc' chi dung de doi chieu, xem muc 6.

## 1. Spot: gioi han goi, phan trang, do sau lich su

| San | Symbol | Quote | Official* | Max thuc te | since dung? | Lo hong | Trung | s/trang | ETA 1 nam (phut) | Lich su OK toi da |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Binance | `BTC/USDT` | USDT | 1000 | 1000 | co | 0 | 0 | 0.26 | 2.2 | 365d |
| OKX | `BTC/USDT` | USDT | 300 | 300 | co | 0 | 0 | 0.21 | 6.1 | 365d |
| Bybit | `BTC/USDT` | USDT | 1000 | 1000 | co | 0 | 0 | 0.14 | 1.2 | 365d |
| Kraken | `BTC/USDT` | USDT | 720 | 721 | khong | 0 | 0 | 2.39 | 29.0 | 0d |
| Coinbase Exchange | `BTC/USDT` | USDT | 300 | 350 | co | 157 | 0 | 0.36 | 8.9 | 365d |

*Official = so tu tai lieu, CHUA xac nhan het, xem muc 6.

## 2. Chi tiet do sau lich su spot (nen 1 phut)

| San | 7d | 30d | 90d | 180d | 365d |
| --- | --- | --- | --- | --- | --- |
| Binance | OK | OK | OK | OK | OK |
| OKX | OK | OK | OK | OK | OK |
| Bybit | OK | OK | OK | OK | OK |
| Kraken | KHONG DU (nen dau cach moc +156h) | KHONG DU (nen dau cach moc +708h) | KHONG DU (nen dau cach moc +2148h) | KHONG DU (nen dau cach moc +4308h) | KHONG DU (nen dau cach moc +8748h) |
| Coinbase Exchange | RONG | OK | OK | OK | OK |

## 3. Order book (cho collector live, muc tieu phu +-10 bps)

| San | Limit khuyen nghi | Phu duoc 10bps? | Span bid (bps) | Span ask (bps) | Limit hop le |
| --- | --- | --- | --- | --- | --- |
| Binance | 500 | khong | 11.7 | 9.1 | 20, 50, 100, 200, 500 |
| OKX | 500 | co | 31.0 | 13.7 | 20, 50, 100, 200, 500 |
| Bybit | 200 | co | 15.8 | 12.0 | 20, 50, 100, 200, 500 |
| Kraken | 100 | co | 36.4 | 31.5 | 20, 50, 100, 200, 500 |
| Coinbase Exchange | 20 | co | 9999.9 | 112362.1 | 20, 50, 100, 200, 500 |

Neu 'Phu duoc 10bps' = khong, cot `depth_censored` trong collector se =True o san do. Ghi vao muc Han che.

## 4. Perpetual va phai sinh (nang cao, RQ3)

| San | Perp symbol | Quote | Nen perp OK toi da | Funding history (has / OK) | OI history (has / OK) |
| --- | --- | --- | --- | --- | --- |
| Binance | `BTC/USDT:USDT` | USDT | 365d | co / 365d | co / 0d |
| OKX | `BTC/USDT:USDT` | USDT | 365d | co / 90d | co / 30d |
| Bybit | `BTC/USDT:USDT` | USDT | 365d | co / 365d | co / 365d |
| Kraken | - | - | 0d | n/a / 0d | n/a / 0d |
| Coinbase Exchange | - | - | 0d | n/a / 0d | n/a / 0d |

## 5. Trang thai, rate limit, loi

| San | Ma ccxt | Trang thai | Rate limit (ms) | Loi/ghi chu |
| --- | --- | --- | --- | --- |
| Binance | `binance` | OK | 50 | - |
| OKX | `okx` | OK | 110 | - |
| Bybit | `bybit` | OK | 20 | - |
| Kraken | `kraken` | OK | 1000 | Khong tim thay perpetual tuyen tinh BTC |
| Coinbase Exchange | `coinbaseexchange` | OK | 100 | Khong tim thay perpetual tuyen tinh BTC |

## 6. Nguon so lieu chinh thuc can xac nhan

| Ma ccxt | Limit tai lieu | URL |
| --- | --- | --- |
| binance | 1000 | https://developers.binance.com/docs/binance-spot-api-docs/rest-api/market-data-endpoints |
| okx | 300 | https://www.okx.com/docs-v5/en/#order-book-trading-market-data-get-candlesticks |
| bybit | 1000 | https://bybit-exchange.github.io/docs/v5/market/kline |
| kraken | 720 | https://docs.kraken.com/exchange/guides/general/historical-data |
| coinbaseexchange | 300 | https://docs.cdp.coinbase.com/api-reference/exchange-api/rest-api/products/get-product-candles |

## 7. Ket luan sinh tu du lieu

**Du dieu kien lam Design A (spot, quote USDT, lich su >= 90d, phan trang sach):** Binance, OKX, Bybit

**Chua dat, kem ly do:**
- Kraken: phan trang khong dat: gaps=0, dups=0, since_ok=False; lich su 1 phut toi da 0d < yeu cau 90d
- Coinbase Exchange: phan trang khong dat: gaps=157, dups=0, since_ok=True

**Du dieu kien perp cho RQ3 (nang cao):** Binance, OKX, Bybit

**Quyet dinh chot san (dien tay sau khi ca nhom doc bang):** ...
