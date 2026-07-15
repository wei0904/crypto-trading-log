import os
from dotenv import load_dotenv
load_dotenv()
import hmac
import hashlib
import time
import requests as rq
from functools import wraps
from flask import Flask, render_template, request, jsonify, session
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime
from zoneinfo import ZoneInfo

TZ = ZoneInfo('Asia/Taipei')

def now_tw():
    return datetime.now(TZ)

BINGX_API_KEY = os.environ.get('BINGX_API_KEY', '')
BINGX_SECRET = os.environ.get('BINGX_SECRET', '')
BINGX_BASE = 'https://open-api.bingx.com'

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'please-change-this-secret-key')

db_url = os.environ.get('DATABASE_URL', 'postgresql://postgres:EHDvFFYYQFljNZvUhVeaJJkVaEulBIuk@zephyr.proxy.rlwy.net:49839/railway')
if db_url.startswith('postgres://'):
    db_url = db_url.replace('postgres://', 'postgresql://', 1)

app.config['SQLALCHEMY_DATABASE_URI'] = db_url
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)


class User(db.Model):
    __tablename__ = 'users'
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(50), unique=True, nullable=False)
    password_hash = db.Column(db.String(256), nullable=False)
    display_name = db.Column(db.String(50))
    bingx_api_key = db.Column(db.String(200))
    bingx_secret = db.Column(db.String(200))
    initial_capital = db.Column(db.Float)
    created_at = db.Column(db.String(30), default=lambda: datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S'))


class Trade(db.Model):
    __tablename__ = 'trades'
    id = db.Column(db.Integer, primary_key=True)
    trader = db.Column(db.String(50), default='我')
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


def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'username' not in session:
            return jsonify({'error': 'unauthorized'}), 401
        return f(*args, **kwargs)
    return decorated


def calc_rr(entry, tp, sl, direction):
    reward = (tp - entry) if direction == 'LONG' else (entry - tp)
    risk = (entry - sl) if direction == 'LONG' else (sl - entry)
    return round(reward / risk, 2) if risk != 0 else None


# ── Auth Routes ────────────────────────────────────────────────────────────────

@app.route('/auth/register', methods=['POST'])
def register():
    d = request.json
    username = (d.get('username') or '').strip().lower()
    password = d.get('password', '')
    display_name = (d.get('display_name') or username).strip()
    if not username or not password:
        return jsonify({'error': '帳號和密碼為必填'}), 400
    if len(username) < 3:
        return jsonify({'error': '帳號至少 3 個字元'}), 400
    if len(password) < 6:
        return jsonify({'error': '密碼至少 6 個字元'}), 400
    if User.query.filter_by(username=username).first():
        return jsonify({'error': '帳號已存在'}), 400
    user = User(
        username=username,
        password_hash=generate_password_hash(password),
        display_name=display_name,
    )
    db.session.add(user)
    db.session.commit()
    session['username'] = username
    session['display_name'] = display_name
    return jsonify({'username': username, 'display_name': display_name}), 201


@app.route('/auth/login', methods=['POST'])
def login():
    d = request.json
    username = (d.get('username') or '').strip().lower()
    password = d.get('password', '')
    user = User.query.filter_by(username=username).first()
    if not user or not check_password_hash(user.password_hash, password):
        return jsonify({'error': '帳號或密碼錯誤'}), 401
    session['username'] = username
    session['display_name'] = user.display_name or username
    return jsonify({'username': username, 'display_name': user.display_name or username})


@app.route('/auth/logout', methods=['POST'])
def logout():
    session.clear()
    return jsonify({'ok': True})


@app.route('/auth/me')
def me():
    if 'username' not in session:
        return jsonify({'error': 'unauthorized'}), 401
    return jsonify({
        'username': session['username'],
        'display_name': session.get('display_name', session['username']),
    })


# ── App Routes ─────────────────────────────────────────────────────────────────

@app.route('/')
def index():
    return render_template('index.html')


@app.route('/api/debug')
def debug_db():
    keys = [k for k in os.environ if not k.startswith('PATH') and 'SECRET' not in k and 'PASSWORD' not in k]
    return jsonify({'computed': db_url[:40], 'env_keys': sorted(keys)})


@app.route('/api/debug-sync')
@login_required
def debug_sync():
    api_key, secret = get_user_bingx_keys()
    pos_data = bingx_get('/openApi/swap/v2/user/positions', api_key=api_key, secret=secret)
    order_data = bingx_get('/openApi/swap/v2/trade/openOrders', api_key=api_key, secret=secret)
    active_trades = [{'coin': t.coin, 'direction': t.direction, 'entry': t.entry_price}
                     for t in Trade.query.filter_by(status='進行中', trader=session['username']).all()]
    return jsonify({'positions': pos_data, 'orders': order_data, 'active_db_trades': active_trades})


@app.route('/api/trades', methods=['GET'])
@login_required
def get_trades():
    trades = (Trade.query
              .filter_by(trader=session['username'])
              .order_by(Trade.date.desc(), Trade.id.desc())
              .all())
    return jsonify([t.to_dict(include_images=False) for t in trades])


@app.route('/api/trades', methods=['POST'])
@login_required
def add_trade():
    try:
        d = request.json
        entry, tp, sl = float(d['entry_price']), float(d['take_profit']), float(d['stop_loss'])
        trade = Trade(
            trader=session['username'],
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
@login_required
def get_trade(trade_id):
    trade = Trade.query.filter_by(id=trade_id, trader=session['username']).first_or_404()
    return jsonify(trade.to_dict())


@app.route('/api/trades/<int:trade_id>', methods=['PUT'])
@login_required
def update_trade(trade_id):
    try:
        trade = Trade.query.filter_by(id=trade_id, trader=session['username']).first_or_404()
        d = request.json
        float_fields = {'entry_price', 'take_profit', 'stop_loss', 'risk_amount', 'pnl'}
        for field in ['date', 'coin', 'direction', 'entry_price', 'take_profit',
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
@login_required
def delete_trade(trade_id):
    trade = Trade.query.filter_by(id=trade_id, trader=session['username']).first_or_404()
    db.session.delete(trade)
    db.session.commit()
    return jsonify({'ok': True})


def get_user_bingx_keys():
    user = User.query.filter_by(username=session['username']).first()
    api_key = (user.bingx_api_key or '').strip() if user else ''
    secret = (user.bingx_secret or '').strip() if user else ''
    return api_key or BINGX_API_KEY, secret or BINGX_SECRET


def bingx_get(path, params={}, api_key=None, secret=None):
    _key = api_key or BINGX_API_KEY
    _secret = secret or BINGX_SECRET
    ts = str(int(time.time() * 1000))
    p = dict(params)
    p['timestamp'] = ts
    query = '&'.join(f'{k}={v}' for k, v in sorted(p.items()))
    sig = hmac.new(_secret.encode(), query.encode(), hashlib.sha256).hexdigest()
    url = f'{BINGX_BASE}{path}?{query}&signature={sig}'
    resp = rq.get(url, headers={'X-BX-APIKEY': _key}, timeout=10)
    return resp.json()


@app.route('/api/settings', methods=['GET'])
@login_required
def get_settings():
    user = User.query.filter_by(username=session['username']).first()
    has_key = bool(user and user.bingx_api_key and user.bingx_api_key.strip())
    masked = ''
    if has_key:
        k = user.bingx_api_key.strip()
        masked = k[:4] + '*' * (len(k) - 8) + k[-4:]
    return jsonify({
        'has_bingx': has_key,
        'masked_key': masked,
        'initial_capital': user.initial_capital if user else None,
    })


@app.route('/api/settings', methods=['POST'])
@login_required
def save_settings():
    user = User.query.filter_by(username=session['username']).first()
    if not user:
        return jsonify({'error': '使用者不存在'}), 404
    d = request.json
    if 'bingx_api_key' in d:
        user.bingx_api_key = d['bingx_api_key'].strip() or None
    if 'bingx_secret' in d:
        user.bingx_secret = d['bingx_secret'].strip() or None
    if 'initial_capital' in d:
        v = d['initial_capital']
        user.initial_capital = float(v) if v not in (None, '', 0) else None
    db.session.commit()
    return jsonify({'ok': True})


@app.route('/api/balance', methods=['GET'])
@login_required
def get_balance():
    user = User.query.filter_by(username=session['username']).first()
    api_key, secret = get_user_bingx_keys()
    result = {'initial_capital': user.initial_capital if user else None, 'equity': None, 'available': None}
    if api_key and secret:
        try:
            data = bingx_get('/openApi/swap/v2/user/balance', api_key=api_key, secret=secret)
            if data.get('code') == 0:
                bal = data.get('data', {}).get('balance', {})
                result['equity'] = float(bal.get('equity') or bal.get('balance') or 0)
                result['available'] = float(bal.get('availableMargin') or bal.get('available') or 0)
        except Exception:
            pass
    return jsonify(result)


@app.route('/api/sync-bingx', methods=['POST'])
@login_required
def sync_bingx():
    api_key, secret = get_user_bingx_keys()
    if not api_key or not secret:
        return jsonify({'error': '請先在「BingX 設定」中填入你的 API Key 和 Secret'}), 400
    try:
        pos_data = bingx_get('/openApi/swap/v2/user/positions', api_key=api_key, secret=secret)
        if pos_data.get('code') != 0:
            return jsonify({'error': pos_data.get('msg', 'BingX API 錯誤')}), 400

        order_data = bingx_get('/openApi/swap/v2/trade/openOrders', api_key=api_key, secret=secret)
        open_orders = order_data.get('data', {}).get('orders', []) if order_data.get('code') == 0 else []

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
        active_keys = set()
        for pos in positions:
            if float(pos.get('positionAmt') or 0) != 0:
                active_keys.add(f"{pos.get('symbol', '')}_{pos.get('positionSide', '')}")

        added, skipped, auto_closed = [], [], []

        active_trades = Trade.query.filter_by(status='進行中', trader=session['username']).all()
        for trade in active_trades:
            try:
                symbol = f"{trade.coin}-USDT"
                key = f"{symbol}_{trade.direction}"
                if key in active_keys:
                    continue
                start_ts = int(datetime.strptime(trade.date, '%Y-%m-%d').replace(tzinfo=TZ).timestamp() * 1000)
                pnl_resp = bingx_get('/openApi/swap/v2/user/income', {'symbol': symbol, 'incomeType': 'REALIZED_PNL', 'startTime': start_ts, 'limit': 50}, api_key=api_key, secret=secret)
                fee_resp = bingx_get('/openApi/swap/v2/user/income', {'symbol': symbol, 'incomeType': 'COMMISSION', 'startTime': start_ts, 'limit': 50}, api_key=api_key, secret=secret)
                raw_pnl = pnl_resp.get('data') if pnl_resp.get('code') == 0 else None
                raw_fee = fee_resp.get('data') if fee_resp.get('code') == 0 else None
                pnl_list = (raw_pnl.get('incomes', []) if isinstance(raw_pnl, dict) else raw_pnl if isinstance(raw_pnl, list) else [])
                fee_list = (raw_fee.get('incomes', []) if isinstance(raw_fee, dict) else raw_fee if isinstance(raw_fee, list) else [])
                pnl = round(sum(float(i.get('income', 0)) for i in pnl_list if isinstance(i, dict)), 4)
                fee = round(abs(sum(float(i.get('income', 0)) for i in fee_list if isinstance(i, dict))), 4)
                trade.pnl = pnl if pnl != 0 else None
                trade.fee = fee if fee != 0 else None
                trade.status = '止盈' if pnl > 0 else '止損' if pnl < 0 else '已平倉'
                auto_closed.append(trade.coin)
            except Exception:
                # 單筆平倉失敗不影響其他同步
                auto_closed.append(trade.coin)
                trade.status = '已平倉'

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
            existing = Trade.query.filter_by(coin=coin, direction=side, entry_price=entry, status='進行中', trader=session['username']).first()
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
                trader=session['username'],
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
@login_required
def get_stats():
    trades = Trade.query.filter_by(trader=session['username']).all()
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


@app.route('/api/ai-analyze', methods=['POST'])
@login_required
def ai_analyze():
    api_key = os.environ.get('GEMINI_API_KEY', '')
    if not api_key:
        return jsonify({'error': '尚未設定 GEMINI_API_KEY，請聯絡管理員'}), 400

    trades = (Trade.query
              .filter_by(trader=session['username'])
              .filter(Trade.status.in_(['止盈', '止損', '已平倉']))
              .order_by(Trade.date.desc(), Trade.id.desc())
              .limit(30).all())

    if len(trades) < 3:
        return jsonify({'error': '需要至少 3 筆已平倉交易才能進行分析'}), 400

    lines = []
    for i, t in enumerate(reversed(trades), 1):
        pnl_str = f"{t.pnl:+.2f}" if t.pnl is not None else '未知'
        rr_str = f"{t.rr_ratio:.2f}" if t.rr_ratio else '未知'
        lines.append(
            f"{i}. [{t.date} {t.trade_time or ''}] {t.coin} {t.direction} "
            f"結果:{t.status} 損益:{pnl_str}U 風報比:{rr_str} "
            f"進場條件:{t.condition or '無'} 備註:{t.notes or '無'}"
        )

    trade_text = '\n'.join(lines)
    prompt = f"""你是一位專業的加密貨幣交易心理教練，請根據以下 {len(trades)} 筆交易紀錄進行深入分析。

交易紀錄（由舊到新）：
{trade_text}

請從以下幾個面向進行分析，並用繁體中文回答：

1. **整體交易模式判斷**：這些交易整體偏向「策略交易」、「情緒交易」還是「FOMO追漲」？給出百分比估算。

2. **情緒交易特徵**：有哪些交易可能是情緒驅動的？請列出具體例子（用交易編號）。

3. **FOMO 跡象**：有沒有看起來像是看到行情動了才追進的交易？

4. **策略一致性**：進場條件是否一致？有沒有規律可循？

5. **優點與需要改進之處**：各列出 2-3 點。

6. **給交易者的具體建議**：3 條實用的改善建議。

請保持客觀、直接，不要過度安慰。如果有問題就直說。"""

    try:
        url = f'https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={api_key}'
        body = {'contents': [{'parts': [{'text': prompt}]}]}
        resp = rq.post(url, json=body, timeout=30)
        resp.raise_for_status()
        result = resp.json()['candidates'][0]['content']['parts'][0]['text']
        return jsonify({'analysis': result, 'trade_count': len(trades)})
    except Exception as e:
        return jsonify({'error': f'AI 分析失敗：{str(e)}'}), 500


def migrate_table(conn, table_name, model_cols, is_pg):
    if is_pg:
        existing = {row[0] for row in conn.execute(
            db.text(f"SELECT column_name FROM information_schema.columns WHERE table_name='{table_name}'")
        )}
    else:
        existing = {row[1] for row in conn.execute(db.text(f"PRAGMA table_info({table_name})"))}
    for col_name, col in model_cols.items():
        if col_name not in existing:
            col_type = str(col.type.compile(db.engine.dialect))
            conn.execute(db.text(f'ALTER TABLE {table_name} ADD COLUMN {col_name} {col_type}'))
            conn.commit()


with app.app_context():
    try:
        db.create_all()
        is_pg = db_url.startswith('postgresql')
        with db.engine.connect() as conn:
            migrate_table(conn, 'trades', {c.name: c for c in Trade.__table__.columns if c.name != 'id'}, is_pg)
            migrate_table(conn, 'users', {c.name: c for c in User.__table__.columns if c.name != 'id'}, is_pg)
    except Exception as e:
        print(f'DB init error: {e}')

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 8080)))
