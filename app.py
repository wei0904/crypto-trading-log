import os
from flask import Flask, render_template, request, jsonify
from flask_sqlalchemy import SQLAlchemy
from datetime import datetime

app = Flask(__name__)

db_url = os.environ.get('DATABASE_URL', f"sqlite:///{os.path.join(os.path.dirname(__file__), 'trades.db')}")
# Railway PostgreSQL URL starts with postgres://, SQLAlchemy needs postgresql://
if db_url.startswith('postgres://'):
    db_url = db_url.replace('postgres://', 'postgresql://', 1)

app.config['SQLALCHEMY_DATABASE_URI'] = db_url
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)


class Trade(db.Model):
    __tablename__ = 'trades'
    id = db.Column(db.Integer, primary_key=True)
    date = db.Column(db.String(20), nullable=False)
    coin = db.Column(db.String(20), nullable=False)
    direction = db.Column(db.String(10), nullable=False)
    entry_price = db.Column(db.Float, nullable=False)
    take_profit = db.Column(db.Float, nullable=False)
    stop_loss = db.Column(db.Float, nullable=False)
    rr_ratio = db.Column(db.Float)
    condition = db.Column(db.Text)
    pnl = db.Column(db.Float)
    status = db.Column(db.String(20), default='進行中')
    notes = db.Column(db.Text)
    created_at = db.Column(db.String(30), default=lambda: datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S'))

    def to_dict(self):
        return {c.name: getattr(self, c.name) for c in self.__table__.columns}


def calc_rr(entry, tp, sl, direction):
    reward = (tp - entry) if direction == 'LONG' else (entry - tp)
    risk = (entry - sl) if direction == 'LONG' else (sl - entry)
    return round(reward / risk, 2) if risk != 0 else None


@app.route('/')
def index():
    return render_template('index.html')


@app.route('/api/trades', methods=['GET'])
def get_trades():
    trades = Trade.query.order_by(Trade.date.desc(), Trade.id.desc()).all()
    return jsonify([t.to_dict() for t in trades])


@app.route('/api/trades', methods=['POST'])
def add_trade():
    d = request.json
    entry, tp, sl = float(d['entry_price']), float(d['take_profit']), float(d['stop_loss'])
    trade = Trade(
        date=d.get('date', datetime.now().strftime('%Y-%m-%d')),
        coin=d['coin'].upper(),
        direction=d['direction'],
        entry_price=entry,
        take_profit=tp,
        stop_loss=sl,
        rr_ratio=calc_rr(entry, tp, sl, d['direction']),
        condition=d.get('condition', ''),
        pnl=d.get('pnl'),
        status=d.get('status', '進行中'),
        notes=d.get('notes', ''),
    )
    db.session.add(trade)
    db.session.commit()
    return jsonify(trade.to_dict()), 201


@app.route('/api/trades/<int:trade_id>', methods=['PUT'])
def update_trade(trade_id):
    trade = Trade.query.get_or_404(trade_id)
    d = request.json
    for field in ['date', 'coin', 'direction', 'entry_price', 'take_profit',
                  'stop_loss', 'condition', 'pnl', 'status', 'notes']:
        if field in d:
            setattr(trade, field, d[field])
    trade.rr_ratio = calc_rr(trade.entry_price, trade.take_profit, trade.stop_loss, trade.direction)
    db.session.commit()
    return jsonify(trade.to_dict())


@app.route('/api/trades/<int:trade_id>', methods=['DELETE'])
def delete_trade(trade_id):
    trade = Trade.query.get_or_404(trade_id)
    db.session.delete(trade)
    db.session.commit()
    return jsonify({'ok': True})


@app.route('/api/stats', methods=['GET'])
def get_stats():
    trades = Trade.query.all()
    closed = [t for t in trades if t.status in ('止盈', '止損', '已平倉')]
    wins = [t for t in closed if t.pnl and t.pnl > 0]
    losses = [t for t in closed if t.pnl and t.pnl < 0]
    total_pnl = sum(t.pnl for t in closed if t.pnl)
    win_rate = round(len(wins) / len(closed) * 100, 1) if closed else 0
    rr_vals = [t.rr_ratio for t in trades if t.rr_ratio]
    avg_rr = round(sum(rr_vals) / len(rr_vals), 2) if rr_vals else 0
    return jsonify({
        'total': len(trades),
        'closed': len(closed),
        'active': len([t for t in trades if t.status == '進行中']),
        'wins': len(wins),
        'losses': len(losses),
        'win_rate': win_rate,
        'total_pnl': round(total_pnl, 2),
        'avg_rr': avg_rr,
    })


with app.app_context():
    db.create_all()

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 8080)))
