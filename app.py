import os
from dotenv import load_dotenv
load_dotenv()
import hmac
import hashlib
import time
import requests as rq
from flask import Flask, render_template, request, jsonify
from flask_sqlalchemy import SQLAlchemy
from datetime import datetime
from zoneinfo import ZoneInfo

TZ = ZoneInfo('Asia/Taipei')

def now_tw():
    return datetime.now(TZ)

BINGX_API_KEY = os.environ.get('BINGX_API_KEY', '')
BINGX_SECRET = os.environ.get('BINGX_SECRET', '')
BINGX_BASE = 'https://open-api.bingx.com'

app = Flask(__name__)

db_url = 'postgresql://postgres:EHDvFFYYQFljNZvUhVeaJJkVaEulBIuk@zephyr.proxy.rlwy.net:49839/railway'

app.config['SQLALCHEMY_DATABASE_URI'] = db_url
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)


class Trade(db.Model):
    __tablename__ = 'trades'
    id = db.Column(db.Integer, primary_key=True)
    trader = db.Column(db.String(20), default='我')
    date = db.Column(db.String(20), nullable=False)
    coin = db.Column(db.String(20), nullable=False)
    direction = db.Column(db.String(10), nullable=False)
    entry_price = db.Column(db.Float, nullable=False)
    take_profit = db.Column(db.Float, nullable=False)
    stop_loss = db.Column(db.Float, nullable=False)
    rr_ratio = db.Column(db.Float)
    trade_time = db.Column(db.String(10))
    risk_amount = db.Column(db.Float)
    condition = db.Column(db.Text)
    pnl = db.Column(db.Float)
    status = db.Column(db.String(20), default='進行中')
    notes = db.Column(db.Text)
    image_data = db.Column(db.Text)
    image_data2 = db.Column(db.Text)
    fee = db.Column(db.Float)
    created_at = db.Column(db.String(30), default=lambda: datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S'))

    def to_dict(self, include_images=True):
        d = {c.name: getattr(self, c.name) for c in self.__table__.columns}
        if not include_images:
            d.pop('image_data', None)
            d.pop('image_data2', None)
            d['has_image'] = bool(self.image_data)
            d['has_image2'] = bool(self.image_data2)
        return d


def calc_rr(entry, tp, sl, direction):
    reward = (tp - entry) if direction == 'LONG' else (entry - tp)
    risk = (entry - sl) if direction == 'LONG' else (sl - entry)
    return round(reward / risk, 2) if risk != 0 else None


@app.route('/')
def index():
    return render_template('index.html')


@app.route('/api/debug')
def debug_db():
    keys = [k for k in os.environ if not k.startswith('PATH') and 'SECRET' not in k and 'PASSWORD' not in k]
    return jsonify({'computed': db_url[:40], 'env_keys': sorted(keys)})


@app.route('/api/trades', methods=['GET'])
def get_trades():
    trades = Trade.query.order_by(Trade.date.desc(), Trade.id.desc()).all()
    return jsonify([t.to_dict(include_images=False) for t in trades])


@app.route('/api/trades', methods=['POST'])
def add_trade():
    try:
        d = request.json
        entry, tp, sl = float(d['entry_price']), float(d['take_profit']), float(d['stop_loss'])
        trade = Trade(
            trader=d.get('trader', '我'),
            date=d.get('date', datetime.now().strftime('%Y-%m-%d')),
            coin=d['coin'].upper(),
            direction=d['direction'],
            entry_price=entry,
            take_profit=tp,
            stop_loss=sl,
            rr_ratio=calc_rr(entry, tp, sl, d['direction']),
            trade_time=d.get('trade_time', ''),
            risk_amount=d.get('risk_amount'),
            condition=d.get('condition', ''),
            pnl=d.get('pnl'),
            status=d.get('status', '進行中'),
            notes=d.get('notes', ''),
            image_data=d.get('image_data'),
            image_data2=d.get('image_data2'),
        )
        db.session.add(trade)
        db.session.commit()
        return jsonify(trade.to_dict()), 201
    except Exception as e:
        db.session.rollback()
        return str(e), 500


@app.route('/api/trades/<int:trade_id>', methods=['GET'])
def get_trade(trade_id):
    trade = Trade.query.get_or_404(trade_id)
    return jsonify(trade.to_dict())


@app.route('/api/trades/<int:trade_id>', methods=['PUT'])
def update_trade(trade_id):
    try:
        trade = Trade.query.get_or_404(trade_id)
        d = request.json
        float_fields = {'entry_price', 'take_profit', 'stop_loss', 'risk_amount', 'pnl'}
        for field in ['trader', 'date', 'coin', 'direction', 'entry_price', 'take_profit',
                      'stop_loss', 'trade_time', 'risk_amount', 'condition', 'pnl', 'status', 'notes', 'image_data', 'image_data2', 'fee']:
            if field in d:
                val = d[field]
                if field in float_fields and val is not None:
                    val = float(val)
                setattr(trade, field, val)
        trade.rr_ratio = calc_rr(trade.entry_price, trade.take_profit, trade.stop_loss, trade.direction)
        db.session.commit()
        return jsonify(trade.to_dict())
    except Exception as e:
        db.session.rollback()
        return str(e), 500


@app.route('/api/trades/<int:trade_id>', methods=['DELETE'])
def delete_trade(trade_id):
    trade = Trade.query.get_or_404(trade_id)
    db.session.delete(trade)
    db.session.commit()
    return jsonify({'ok': True})


def bingx_get(path, params={}):
    ts = str(int(time.time() * 1000))
    p = dict(params)
    p['timestamp'] = ts
    query = '&'.join(f'{k}={v}' for k, v in sorted(p.items()))
    sig = hmac.new(BINGX_SECRET.encode(), query.encode(), hashlib.sha256).hexdigest()
    url = f'{BINGX_BASE}{path}?{query}&signature={sig}'
    resp = rq.get(url, headers={'X-BX-APIKEY': BINGX_API_KEY}, timeout=10)
    return resp.json()


@app.route('/api/sync-bingx', methods=['POST'])
def sync_bingx():
    if not BINGX_API_KEY or not BINGX_SECRET:
        return jsonify({'error': '請先設定 BINGX_API_KEY 和 BINGX_SECRET 環境變數'}), 400
    try:
        # 取得持倉
        pos_data = bingx_get('/openApi/swap/v2/user/positions')
        if pos_data.get('code') != 0:
            return jsonify({'error': pos_data.get('msg', 'BingX API 錯誤')}), 400

        # 取得未成交訂單（含止盈止損掛單）
        order_data = bingx_get('/openApi/swap/v2/trade/openOrders')
        open_orders = order_data.get('data', {}).get('orders', []) if order_data.get('code') == 0 else []

        # 建立 {symbol_side: {tp, sl}} 對照表
        tpsl_map = {}
        for o in open_orders:
            sym = o.get('symbol', '')
            o_side = o.get('positionSide', '')
            key = f"{sym}_{o_side}"
            o_type = o.get('type', '')
            stop_price = float(o.get('stopPrice') or 0)
            if o_type in ('TAKE_PROFIT_MARKET', 'TAKE_PROFIT') and stop_price:
                tpsl_map.setdefault(key, {})['tp'] = stop_price
            elif o_type in ('STOP_MARKET', 'STOP') and stop_price:
                tpsl_map.setdefault(key, {})['sl'] = stop_price

        positions = pos_data.get('data', [])
        # 建立目前 BingX 有倉位的 key 集合
        active_keys = set()
        for pos in positions:
            if float(pos.get('positionAmt') or 0) != 0:
                active_keys.add(f"{pos.get('symbol', '')}_{pos.get('positionSide', '')}")

        added, skipped, auto_closed = [], [], []

        # 檢查平倉：DB 進行中但 BingX 已無倉位
        active_trades = Trade.query.filter_by(status='進行中').all()
        for trade in active_trades:
            symbol = f"{trade.coin}-USDT"
            key = f"{symbol}_{trade.direction}"
            if key in active_keys:
                continue
            # 抓實現盈虧
            start_ts = int(datetime.strptime(trade.date, '%Y-%m-%d').replace(tzinfo=TZ).timestamp() * 1000)
            pnl_resp = bingx_get('/openApi/swap/v2/user/income', {'symbol': symbol, 'incomeType': 'REALIZED_PNL', 'startTime': start_ts, 'limit': 50})
            fee_resp = bingx_get('/openApi/swap/v2/user/income', {'symbol': symbol, 'incomeType': 'COMMISSION', 'startTime': start_ts, 'limit': 50})
            pnl_list = pnl_resp.get('data', {}).get('incomes', []) if pnl_resp.get('code') == 0 else []
            fee_list = fee_resp.get('data', {}).get('incomes', []) if fee_resp.get('code') == 0 else []
            pnl = round(sum(float(i.get('income', 0)) for i in pnl_list), 4)
            fee = round(abs(sum(float(i.get('income', 0)) for i in fee_list)), 4)
            trade.pnl = pnl
            trade.fee = fee
            trade.status = '止盈' if pnl > 0 else '止損'
            auto_closed.append(trade.coin)

        for pos in positions:
            size = float(pos.get('positionAmt') or 0)
            entry = float(pos.get('avgPrice') or 0)
            if size == 0 or entry == 0:
                continue
            symbol = pos.get('symbol', '')
            side = pos.get('positionSide', '')
            coin = symbol.replace('-USDT', '').replace('-USDC', '').replace('-BUSD', '')
            key = f"{symbol}_{side}"
            tp = tpsl_map.get(key, {}).get('tp', 0)
            sl = tpsl_map.get(key, {}).get('sl', 0)
            existing = Trade.query.filter_by(coin=coin, direction=side, entry_price=entry, status='進行中').first()
            if existing:
                if tp and existing.take_profit != tp:
                    existing.take_profit = tp
                    existing.rr_ratio = calc_rr(entry, tp, sl, side) if tp and sl else existing.rr_ratio
                if sl and existing.stop_loss != sl:
                    existing.stop_loss = sl
                    existing.rr_ratio = calc_rr(entry, tp, sl, side) if tp and sl else existing.rr_ratio
                skipped.append(coin)
                continue
            update_ts = pos.get('updateTime')
            if update_ts:
                open_dt = datetime.fromtimestamp(int(update_ts) / 1000, tz=TZ)
            else:
                open_dt = now_tw()
            trade = Trade(
                coin=coin,
                direction=side,
                entry_price=entry,
                take_profit=tp,
                stop_loss=sl,
                rr_ratio=calc_rr(entry, tp, sl, side) if tp and sl else None,
                date=open_dt.strftime('%Y-%m-%d'),
                trade_time=open_dt.strftime('%H:%M'),
                status='進行中',
            )
            db.session.add(trade)
            added.append(coin)
        db.session.commit()
        return jsonify({'added': added, 'skipped': skipped, 'closed': auto_closed})
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 500


@app.route('/api/stats', methods=['GET'])
def get_stats():
    trader = request.args.get('trader')
    q = Trade.query
    if trader:
        q = q.filter_by(trader=trader)
    trades = q.all()
    closed = [t for t in trades if t.status in ('止盈', '止損', '已平倉')]
    wins = [t for t in trades if t.status == '止盈']
    losses = [t for t in trades if t.status == '止損']
    manual = [t for t in trades if t.status == '已平倉']
    total_pnl = sum(t.pnl for t in closed if t.pnl)
    total_fees = sum(t.fee for t in trades if t.fee)
    win_rate = round(len(wins) / len(closed) * 100, 1) if closed else 0
    rr_vals = [t.rr_ratio for t in trades if t.rr_ratio]
    avg_rr = round(sum(rr_vals) / len(rr_vals), 2) if rr_vals else 0
    return jsonify({
        'total': len(trades),
        'closed': len(closed),
        'active': len([t for t in trades if t.status == '進行中']),
        'wins': len(wins),
        'losses': len(losses),
        'manual': len(manual),
        'win_rate': win_rate,
        'total_pnl': round(total_pnl, 2),
        'total_fees': round(total_fees, 2),
        'net_pnl': round(total_pnl - total_fees, 2),
        'avg_rr': avg_rr,
    })


with app.app_context():
    try:
        db.create_all()
        with db.engine.connect() as conn:
            if db_url.startswith('postgresql'):
                existing = {row[0] for row in conn.execute(
                    db.text("SELECT column_name FROM information_schema.columns WHERE table_name='trades'")
                )}
            else:
                existing = {row[1] for row in conn.execute(db.text("PRAGMA table_info(trades)"))}
            model_cols = {c.name: c for c in Trade.__table__.columns if c.name != 'id'}
            for col_name, col in model_cols.items():
                if col_name not in existing:
                    col_type = str(col.type.compile(db.engine.dialect))
                    conn.execute(db.text(f'ALTER TABLE trades ADD COLUMN {col_name} {col_type}'))
                    conn.commit()
    except Exception as e:
        print(f'DB init error: {e}')

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 8080)))
