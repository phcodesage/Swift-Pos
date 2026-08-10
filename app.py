import os
import csv
from io import StringIO
from datetime import datetime, timedelta
from flask import Flask, render_template, request, redirect, url_for, flash, jsonify, Response
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__)
app.config['SECRET_KEY'] = 'swiftpos_philippines_secret_key_2026'

# Use absolute path for SQLite database to ensure consistency across reloads
basedir = os.path.abspath(os.path.dirname(os.path.realpath(__file__)))
db_dir = os.path.join(basedir, 'instance')
os.makedirs(db_dir, exist_ok=True)
app.config['SQLALCHEMY_DATABASE_URI'] = f'sqlite:///{os.path.join(db_dir, "swiftpos.db")}'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)
login_manager = LoginManager(app)
login_manager.login_view = 'login'

# Self-healing database check: Auto-recreates tables if SQLite is empty or missing tables
@app.before_request
def ensure_database_is_healthy():
    try:
        # Check if the user table exists and query runs
        db.session.query(User).first()
    except Exception:
        with app.app_context():
            db.create_all()
            seed_database()

# Database Models
class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(150), unique=True, nullable=False)
    password = db.Column(db.String(150), nullable=False)
    role = db.Column(db.String(50), nullable=False, default='cashier') # 'admin' or 'cashier'
    full_name = db.Column(db.String(150), nullable=True)

class Category(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), unique=True, nullable=False)

class Product(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(150), nullable=False)
    price = db.Column(db.Float, nullable=False)
    capital = db.Column(db.Float, nullable=False, default=0.0)
    stock = db.Column(db.Float, nullable=False, default=0.0)
    category = db.Column(db.String(100), nullable=False)
    unit = db.Column(db.String(20), nullable=False, default='pcs') # 'Kg' or 'pcs'
    image_url = db.Column(db.String(500), nullable=True)

class Shift(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    starting_cash = db.Column(db.Float, nullable=False, default=0.0)
    start_time = db.Column(db.DateTime, default=datetime.now)
    end_time = db.Column(db.DateTime, nullable=True)
    total_income = db.Column(db.Float, default=0.0)
    status = db.Column(db.String(20), default='active') # 'active' or 'closed'
    
    user = db.relationship('User', backref='shifts')

class Transaction(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    shift_id = db.Column(db.Integer, db.ForeignKey('shift.id'), nullable=True)
    subtotal = db.Column(db.Float, nullable=False)
    discount = db.Column(db.Float, nullable=False, default=0.0)
    total_amount = db.Column(db.Float, nullable=False)
    cash_received = db.Column(db.Float, nullable=False, default=0.0)
    change = db.Column(db.Float, nullable=False, default=0.0)
    payment_type = db.Column(db.String(20), nullable=False, default='cash') # 'cash' or 'utang'
    borrower_name = db.Column(db.String(150), nullable=True)
    is_paid = db.Column(db.Boolean, default=True)
    paid_amount = db.Column(db.Float, default=0.0)
    date_created = db.Column(db.DateTime, default=datetime.now)
    
    user = db.relationship('User', backref='transactions')
    items = db.relationship('TransactionItem', backref='transaction', cascade="all, delete-orphan", lazy=True)

class TransactionItem(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    transaction_id = db.Column(db.Integer, db.ForeignKey('transaction.id'), nullable=False)
    product_id = db.Column(db.Integer, db.ForeignKey('product.id'), nullable=False)
    quantity = db.Column(db.Float, nullable=False)
    price_at_sale = db.Column(db.Float, nullable=False)
    capital_at_sale = db.Column(db.Float, nullable=False, default=0.0)
    
    product = db.relationship('Product')

class UtangPayment(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    transaction_id = db.Column(db.Integer, db.ForeignKey('transaction.id'), nullable=False)
    amount_paid = db.Column(db.Float, nullable=False)
    date_paid = db.Column(db.DateTime, default=datetime.now)
    notes = db.Column(db.String(255), nullable=True)
    
    transaction = db.relationship('Transaction', backref='payments')

class Expense(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    description = db.Column(db.String(255), nullable=False)
    category = db.Column(db.String(100), nullable=False, default='General')
    amount = db.Column(db.Float, nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    date_created = db.Column(db.DateTime, default=datetime.now)
    
    user = db.relationship('User')

@login_manager.user_loader
def load_user(user_id):
    try:
        return db.session.get(User, int(user_id))
    except Exception:
        return None

def seed_database():
    db.create_all()
    
    # Create default accounts if not present
    if not User.query.filter_by(username='admin').first():
        admin = User(
            username='admin',
            password=generate_password_hash('admin123', method='pbkdf2:sha256'),
            role='admin',
            full_name='Store Manager Admin'
        )
        db.session.add(admin)
        
    if not User.query.filter_by(username='cashier').first():
        cashier = User(
            username='cashier',
            password=generate_password_hash('cashier123', method='pbkdf2:sha256'),
            role='cashier',
            full_name='Ate Nena (Cashier)'
        )
        db.session.add(cashier)
        
    db.session.commit()

    # Seed popular Philippine sari-sari store products with images
    if Product.query.count() == 0:
        ph_products = [
            # Beverages & Sodas
            {"name": "Coca-Cola 1.5L", "price": 75.0, "capital": 62.0, "stock": 45.0, "category": "Beverages", "unit": "pcs", "image_url": "/static/images/coca_cola.jpg"},
            {"name": "Royal Tru Orange 1.5L", "price": 75.0, "capital": 62.0, "stock": 40.0, "category": "Beverages", "unit": "pcs", "image_url": "/static/images/royal_orange.jpg"},
            {"name": "Sprite 1.5L", "price": 75.0, "capital": 62.0, "stock": 35.0, "category": "Beverages", "unit": "pcs", "image_url": "/static/images/sprite.jpg"},
            {"name": "C2 Green Tea Apple 500ml", "price": 32.0, "capital": 25.0, "stock": 60.0, "category": "Beverages", "unit": "pcs", "image_url": "/static/images/c2_green_tea.jpg"},
            {"name": "San Miguel Pale Pilsen 330ml Can", "price": 55.0, "capital": 44.0, "stock": 50.0, "category": "Beverages", "unit": "pcs", "image_url": "/static/images/san_miguel.jpg"},
            {"name": "Red Horse Beer 500ml", "price": 70.0, "capital": 58.0, "stock": 48.0, "category": "Beverages", "unit": "pcs", "image_url": "/static/images/red_horse.jpg"},
            {"name": "Sting Energy Drink Strawberry 300ml", "price": 22.0, "capital": 17.0, "stock": 70.0, "category": "Beverages", "unit": "pcs", "image_url": "/static/images/sting_energy.jpg"},
            {"name": "Gatorade Cool Blue 500ml", "price": 48.0, "capital": 39.0, "stock": 30.0, "category": "Beverages", "unit": "pcs", "image_url": "/static/images/gatorade.jpg"},

            # Snacks & Biscuits
            {"name": "Piattos Cheese 85g", "price": 42.0, "capital": 34.0, "stock": 50.0, "category": "Snacks", "unit": "pcs", "image_url": "/static/images/piattos.jpg"},
            {"name": "Nova Multigrain Country Cheddar 78g", "price": 40.0, "capital": 33.0, "stock": 40.0, "category": "Snacks", "unit": "pcs", "image_url": "/static/images/nova.jpg"},
            {"name": "Vcut Potato Chips BBQ 60g", "price": 38.0, "capital": 30.0, "stock": 35.0, "category": "Snacks", "unit": "pcs", "image_url": "/static/images/vcut.jpg"},
            {"name": "Chippy Barbecue 110g", "price": 36.0, "capital": 28.0, "stock": 45.0, "category": "Snacks", "unit": "pcs", "image_url": "/static/images/chippy.jpg"},
            {"name": "Boy Bawang Garlic 100g", "price": 28.0, "capital": 21.0, "stock": 60.0, "category": "Snacks", "unit": "pcs", "image_url": "/static/images/boy_bawang.jpg"},
            {"name": "Hansel Mocha Biscuit Sandwich 31g", "price": 10.0, "capital": 7.5, "stock": 120.0, "category": "Snacks", "unit": "pcs", "image_url": "/static/images/hansel.jpg"},
            {"name": "Fudgee Barr Chocolate 10s", "price": 95.0, "capital": 78.0, "stock": 25.0, "category": "Snacks", "unit": "pcs", "image_url": "/static/images/fudgee_barr.jpg"},

            # Instant Noodles & Canned Goods
            {"name": "Lucky Me! Instant Pancit Canton Extra Hot 80g", "price": 16.0, "capital": 12.5, "stock": 150.0, "category": "Instant Foods", "unit": "pcs", "image_url": "/static/images/pancit_canton_extra_hot.jpg"},
            {"name": "Lucky Me! Instant Pancit Canton Chilimansi 80g", "price": 16.0, "capital": 12.5, "stock": 150.0, "category": "Instant Foods", "unit": "pcs", "image_url": "/static/images/pancit_canton_chilimansi.jpg"},
            {"name": "Lucky Me! Supreme Beef Noodle Soup 55g", "price": 28.0, "capital": 22.0, "stock": 80.0, "category": "Instant Foods", "unit": "pcs", "image_url": "/static/images/lucky_me_beef.jpg"},
            {"name": "Century Tuna Flakes in Vegetable Oil 180g", "price": 46.0, "capital": 38.0, "stock": 65.0, "category": "Canned Goods", "unit": "pcs", "image_url": "/static/images/century_tuna.jpg"},
            {"name": "555 Sardines in Tomato Sauce 155g", "price": 24.0, "capital": 19.0, "stock": 90.0, "category": "Canned Goods", "unit": "pcs", "image_url": "/static/images/sardines_555.jpg"},
            {"name": "Argentina Corned Beef 175g", "price": 42.0, "capital": 34.0, "stock": 70.0, "category": "Canned Goods", "unit": "pcs", "image_url": "/static/images/argentina_corned_beef.jpg"},
            {"name": "Spam Classic 340g", "price": 220.0, "capital": 185.0, "stock": 20.0, "category": "Canned Goods", "unit": "pcs", "image_url": "/static/images/spam.jpg"},

            # Rice, Sugar & Staples (Kg items)
            {"name": "Sinandomeng Rice", "price": 54.0, "capital": 46.0, "stock": 250.0, "category": "Staples & Grain", "unit": "Kg", "image_url": "/static/images/sinandomeng_rice.jpg"},
            {"name": "Dinorado Premium Rice", "price": 62.0, "capital": 52.0, "stock": 180.0, "category": "Staples & Grain", "unit": "Kg", "image_url": "/static/images/dinorado_rice.jpg"},
            {"name": "Washed Sugar (Brown)", "price": 72.0, "capital": 58.0, "stock": 80.0, "category": "Staples & Grain", "unit": "Kg", "image_url": "/static/images/washed_sugar.jpg"},
            {"name": "Refined White Sugar", "price": 86.0, "capital": 72.0, "stock": 65.0, "category": "Staples & Grain", "unit": "Kg", "image_url": "/static/images/refined_sugar.jpg"},
            {"name": "Refined Cooking Oil (Liters)", "price": 90.0, "capital": 75.0, "stock": 50.0, "category": "Condiments", "unit": "Kg", "image_url": "/static/images/cooking_oil.jpg"},

            # Condiments & Spices
            {"name": "Silver Swan Soy Sauce 1L bottle", "price": 52.0, "capital": 42.0, "stock": 30.0, "category": "Condiments", "unit": "pcs", "image_url": "/static/images/silver_swan.jpg"},
            {"name": "Datu Puti Vinegar 1L bottle", "price": 45.0, "capital": 35.0, "stock": 30.0, "category": "Condiments", "unit": "pcs", "image_url": "/static/images/datu_puti.jpg"},
            {"name": "Magic Sarap All-in-One Seasoning 8g", "price": 6.0, "capital": 4.2, "stock": 300.0, "category": "Condiments", "unit": "pcs", "image_url": "/static/images/magic_sarap.jpg"},
            {"name": "Knorr Liquid Seasoning 250ml", "price": 68.0, "capital": 55.0, "stock": 25.0, "category": "Condiments", "unit": "pcs", "image_url": "/static/images/knorr_seasoning.jpg"},

            # Coffee, Milk & Breakfast
            {"name": "Nescafé Original 3-in-1 Twin Pack 56g", "price": 15.0, "capital": 11.8, "stock": 200.0, "category": "Coffee & Milk", "unit": "pcs", "image_url": "/static/images/nescafe_3in1.jpg"},
            {"name": "Bear Brand Fortified Powdered Milk 33g", "price": 18.0, "capital": 14.5, "stock": 140.0, "category": "Coffee & Milk", "unit": "pcs", "image_url": "/static/images/bear_brand.jpg"},
            {"name": "Milo Chocolate Malt Drink 24g", "price": 14.0, "capital": 11.0, "stock": 160.0, "category": "Coffee & Milk", "unit": "pcs", "image_url": "/static/images/milo.jpg"},

            # Household & Personal Care
            {"name": "Surf Powder Sun Fresh Laundry Detergent 65g", "price": 12.0, "capital": 9.2, "stock": 180.0, "category": "Household", "unit": "pcs", "image_url": "/static/images/surf_powder.jpg"},
            {"name": "Safeguard Pure White Bar Soap 130g", "price": 58.0, "capital": 47.0, "stock": 40.0, "category": "Personal Care", "unit": "pcs", "image_url": "/static/images/safeguard_soap.jpg"},
            {"name": "Palmolive Coconut Cream Shampoo 12ml Sachet", "price": 8.0, "capital": 6.0, "stock": 200.0, "category": "Personal Care", "unit": "pcs", "image_url": "/static/images/palmolive_shampoo.jpg"},
            {"name": "Cream Silk Standout Straight Conditioner 12ml", "price": 9.0, "capital": 6.8, "stock": 180.0, "category": "Personal Care", "unit": "pcs", "image_url": "/static/images/cream_silk.jpg"}
        ]

        for p_data in ph_products:
            p = Product(**p_data)
            db.session.add(p)
        db.session.commit()

        # Seed categories table
        cat_names = set(p["category"] for p in ph_products)
        for c_name in cat_names:
            if not Category.query.filter_by(name=c_name).first():
                db.session.add(Category(name=c_name))
        db.session.commit()

# Unconditionally initialize DB tables on app context load
with app.app_context():
    seed_database()

# Application Routes
@app.route('/')
@login_required
def index():
    if current_user.role == 'admin':
        return redirect(url_for('dashboard'))
    return redirect(url_for('pos'))

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        remember = True if request.form.get('remember_me') else False
        
        user = User.query.filter_by(username=username).first()
        if user and check_password_hash(user.password, password):
            login_user(user, remember=remember)
            return redirect(url_for('index'))
        else:
            flash('Invalid username or password credentials.', 'error')
            
    return render_template('login.html')

@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('login'))

@app.route('/dashboard')
@login_required
def dashboard():
    if current_user.role != 'admin':
        return redirect(url_for('pos'))
    
    total_sales = db.session.query(db.func.sum(Transaction.total_amount)).filter(Transaction.is_paid == True).scalar() or 0.0
    total_transactions = Transaction.query.filter_by(is_paid=True).count()
    
    # Calculate total profit
    total_cost = db.session.query(db.func.sum(TransactionItem.quantity * TransactionItem.capital_at_sale))\
                           .join(Transaction)\
                           .filter(Transaction.is_paid == True).scalar() or 0.0
    total_expenses = db.session.query(db.func.sum(Expense.amount)).scalar() or 0.0
    net_profit = total_sales - total_cost - total_expenses
    
    # Low stock items: Kg <= 5kg or pcs <= 10pcs
    low_stock_items = Product.query.filter(
        ((Product.unit == 'Kg') & (Product.stock <= 5.0)) | 
        ((Product.unit == 'pcs') & (Product.stock <= 10.0))
    ).all()

    # Total unpaid utang
    unpaid_utang = db.session.query(db.func.sum(Transaction.total_amount - Transaction.paid_amount))\
                            .filter(Transaction.payment_type == 'utang', Transaction.is_paid == False).scalar() or 0.0

    # Get daily sales for the last 7 days
    from datetime import datetime as dt, timedelta
    today = dt.now().date()
    sales_trend = []
    for i in range(6, -1, -1):
        d = today - timedelta(days=i)
        day_start = dt.combine(d, dt.min.time())
        day_end = dt.combine(d, dt.max.time())
        day_total = db.session.query(db.func.sum(Transaction.total_amount))\
                              .filter(Transaction.is_paid == True, 
                                      Transaction.date_created >= day_start,
                                      Transaction.date_created <= day_end).scalar() or 0.0
        sales_trend.append({
            'day': d.strftime('%a'),
            'total': float(day_total)
        })

    return render_template('dashboard.html', 
                           total_sales=total_sales, 
                           total_transactions=total_transactions, 
                           net_profit=net_profit,
                           total_expenses=total_expenses,
                           unpaid_utang=unpaid_utang,
                           low_stock_items=low_stock_items,
                           sales_trend=sales_trend)


@app.route('/pos')
@login_required
def pos():
    categories = [c.name for c in Category.query.order_by(Category.name).all()]
    if not categories:
        categories = [c[0] for c in db.session.query(Product.category).distinct().all()]
        
    active_shift = Shift.query.filter_by(user_id=current_user.id, status='active').first()
    return render_template('pos.html', categories=categories, active_shift=active_shift)

@app.route('/api/products')
@login_required
def api_products():
    category = request.args.get('category')
    search = request.args.get('search')
    
    query = Product.query
    if category and category != 'All':
        query = query.filter_by(category=category)
    if search:
        query = query.filter(Product.name.ilike(f'%{search}%'))
        
    products = query.order_by(Product.name).all()
    return jsonify([{
        'id': p.id,
        'name': p.name,
        'price': p.price,
        'capital': p.capital,
        'stock': p.stock,
        'category': p.category,
        'unit': p.unit,
        'image_url': p.image_url or '/static/images/coca_cola.jpg'
    } for p in products])

@app.route('/api/media')
@login_required
def api_media():
    images_dir = os.path.join(app.root_path, 'static', 'images')
    if not os.path.exists(images_dir):
        return jsonify([])
    
    allowed_extensions = {'.jpg', '.jpeg', '.png', '.webp', '.gif'}
    files = [
        f'/static/images/{f}'
        for f in os.listdir(images_dir)
        if os.path.splitext(f.lower())[1] in allowed_extensions
    ]
    return jsonify(sorted(files))

@app.route('/api/media/upload', methods=['POST'])
@login_required
def api_upload_media():
    if 'file' not in request.files:
        return jsonify({'success': False, 'message': 'No file uploaded'})
    file = request.files['file']
    if file.filename == '':
        return jsonify({'success': False, 'message': 'Empty filename'})
    
    ext = os.path.splitext(file.filename.lower())[1]
    if ext not in {'.jpg', '.jpeg', '.png', '.webp', '.gif'}:
        return jsonify({'success': False, 'message': 'Invalid image format'})
    
    from werkzeug.utils import secure_filename
    filename = secure_filename(file.filename)
    images_dir = os.path.join(app.root_path, 'static', 'images')
    os.makedirs(images_dir, exist_ok=True)
    
    save_path = os.path.join(images_dir, filename)
    file.save(save_path)
    
    return jsonify({'success': True, 'url': f'/static/images/{filename}'})

@app.route('/api/media/delete', methods=['POST'])
@login_required
def api_delete_media():
    if current_user.role != 'admin':
        return jsonify({'success': False, 'message': 'Admin privilege required'}), 403
    data = request.json or {}
    src = data.get('src', '')
    if not src or not src.startswith('/static/images/'):
        return jsonify({'success': False, 'message': 'Invalid file path'})
    
    filename = os.path.basename(src)
    file_path = os.path.join(app.root_path, 'static', 'images', filename)
    
    if os.path.exists(file_path):
        try:
            os.remove(file_path)
            return jsonify({'success': True})
        except Exception as e:
            return jsonify({'success': False, 'message': str(e)})
    return jsonify({'success': False, 'message': 'File not found'})

@app.route('/media')
@login_required
def media():
    if current_user.role != 'admin':
        flash('Access restricted to Admin users.', 'error')
        return redirect(url_for('pos'))
    return render_template('media.html')

@app.route('/api/checkout', methods=['POST'])
@login_required
def checkout():
    data = request.json or {}
    cart = data.get('cart', [])
    payment_type = data.get('payment_type', 'cash') # 'cash' or 'utang'
    borrower_name = data.get('borrower_name', '').strip()
    discount = float(data.get('discount', 0.0))
    cash_received = float(data.get('cash', 0.0))
    subtotal = float(data.get('subtotal', 0.0))
    total_amount = float(data.get('total', 0.0))
    
    if not cart:
        return jsonify({'success': False, 'message': 'Cart is empty'})
        
    if payment_type == 'cash' and cash_received < total_amount:
        return jsonify({'success': False, 'message': 'Insufficient cash amount'})
        
    if payment_type == 'utang' and not borrower_name:
        return jsonify({'success': False, 'message': 'Borrower name is required for Utang / Credit'})

    active_shift = Shift.query.filter_by(user_id=current_user.id, status='active').first()

    change = cash_received - total_amount if payment_type == 'cash' else 0.0
    is_paid = True if payment_type == 'cash' else False
    paid_amount = total_amount if is_paid else 0.0

    try:
        transaction = Transaction(
            user_id=current_user.id,
            shift_id=active_shift.id if active_shift else None,
            subtotal=subtotal,
            discount=discount,
            total_amount=total_amount,
            cash_received=cash_received if payment_type == 'cash' else 0.0,
            change=change,
            payment_type=payment_type,
            borrower_name=borrower_name if payment_type == 'utang' else None,
            is_paid=is_paid,
            paid_amount=paid_amount
        )
        db.session.add(transaction)
        db.session.flush()
        
        for item in cart:
            product = db.session.get(Product, item['id'])
            if not product:
                continue
            if product.stock < item['qty']:
                return jsonify({'success': False, 'message': f'Not enough stock for {product.name} (Stock left: {product.stock})'})
                
            product.stock -= float(item['qty'])
            
            t_item = TransactionItem(
                transaction_id=transaction.id,
                product_id=product.id,
                quantity=float(item['qty']),
                price_at_sale=product.price,
                capital_at_sale=product.capital
            )
            db.session.add(t_item)
            
        if active_shift and is_paid:
            active_shift.total_income += total_amount

        db.session.commit()
        return jsonify({
            'success': True, 
            'transaction_id': transaction.id, 
            'change': change,
            'payment_type': payment_type
        })
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'message': str(e)})

# Stocks CRUD Page
@app.route('/stocks', methods=['GET', 'POST'])
@login_required
def stocks():
    if current_user.role != 'admin':
        flash('Access restricted to Admin users.', 'error')
        return redirect(url_for('pos'))
        
    if request.method == 'POST':
        action = request.form.get('action')
        if action == 'add':
            cat_name = request.form.get('category').strip()
            if cat_name and not Category.query.filter_by(name=cat_name).first():
                db.session.add(Category(name=cat_name))

            new_prod = Product(
                name=request.form.get('name').strip(),
                price=float(request.form.get('price')),
                capital=float(request.form.get('capital')),
                stock=float(request.form.get('stock')),
                category=cat_name,
                unit=request.form.get('unit'),
                image_url=request.form.get('image_url', '').strip() or None
            )
            db.session.add(new_prod)
            db.session.commit()
            flash(f'Product "{new_prod.name}" created successfully.', 'success')

        elif action == 'edit':
            prod = db.session.get(Product, request.form.get('id'))
            if prod:
                cat_name = request.form.get('category').strip()
                if cat_name and not Category.query.filter_by(name=cat_name).first():
                    db.session.add(Category(name=cat_name))

                prod.name = request.form.get('name').strip()
                prod.price = float(request.form.get('price'))
                prod.capital = float(request.form.get('capital'))
                prod.stock = float(request.form.get('stock'))
                prod.category = cat_name
                prod.unit = request.form.get('unit')
                prod.image_url = request.form.get('image_url', '').strip() or prod.image_url
                db.session.commit()
                flash(f'Product "{prod.name}" updated successfully.', 'success')

        elif action == 'delete':
            prod = db.session.get(Product, request.form.get('id'))
            if prod:
                name = prod.name
                db.session.delete(prod)
                db.session.commit()
                flash(f'Product "{name}" removed.', 'success')
                
        return redirect(url_for('stocks'))
        
    products = Product.query.order_by(Product.category, Product.name).all()
    categories = [c.name for c in Category.query.order_by(Category.name).all()]
    return render_template('stocks.html', products=products, categories=categories)

# Receipts & Export
@app.route('/receipts')
@login_required
def receipts():
    query = Transaction.query
    if current_user.role != 'admin':
        query = query.filter_by(user_id=current_user.id)
        
    transactions = query.order_by(Transaction.date_created.desc()).all()
    cashiers = User.query.all() if current_user.role == 'admin' else []
    return render_template('receipts.html', transactions=transactions, cashiers=cashiers)

@app.route('/receipts/delete/<int:id>', methods=['POST'])
@login_required
def delete_receipt(id):
    if current_user.role != 'admin':
        return jsonify({'success': False, 'message': 'Admin privilege required'}), 403
    t = db.session.get(Transaction, id)
    if t:
        db.session.delete(t)
        db.session.commit()
        flash(f'Receipt #{id} deleted successfully.', 'success')
    return redirect(url_for('receipts'))

@app.route('/receipts/export')
@login_required
def export_csv():
    if current_user.role != 'admin':
        flash('Admin privilege required to export CSV', 'error')
        return redirect(url_for('receipts'))
        
    transactions = Transaction.query.order_by(Transaction.date_created.desc()).all()
    
    si = StringIO()
    cw = csv.writer(si)
    cw.writerow(['Receipt ID', 'Date', 'Cashier', 'Payment Type', 'Borrower', 'Subtotal', 'Discount', 'Total Amount', 'Status'])
    
    for t in transactions:
        cw.writerow([
            t.id,
            t.date_created.strftime('%Y-%m-%d %H:%M:%S'),
            t.user.username,
            t.payment_type,
            t.borrower_name or 'N/A',
            f"{t.subtotal:.2f}",
            f"{t.discount:.2f}",
            f"{t.total_amount:.2f}",
            'Paid' if t.is_paid else 'Unpaid (Utang)'
        ])
        
    output = si.getvalue()
    return Response(
        output,
        mimetype="text/csv",
        headers={"Content-disposition": f"attachment; filename=swiftpos_sales_{datetime.now().strftime('%Y%m%d')}.csv"}
    )

# Lending / Credit (Utang) Management
@app.route('/lending', methods=['GET', 'POST'])
@login_required
def lending():
    if request.method == 'POST':
        action = request.form.get('action')
        if action == 'pay':
            t_id = request.form.get('transaction_id')
            amount = float(request.form.get('amount', 0.0))
            notes = request.form.get('notes', '').strip()
            
            t = db.session.get(Transaction, t_id)
            if t and amount > 0:
                t.paid_amount += amount
                payment = UtangPayment(transaction_id=t.id, amount_paid=amount, notes=notes)
                db.session.add(payment)
                
                if t.paid_amount >= t.total_amount:
                    t.is_paid = True
                    
                db.session.commit()
                flash(f'Payment of ₱{amount:.2f} recorded for borrower {t.borrower_name}', 'success')
        return redirect(url_for('lending'))

    # Group or list unpaid transactions
    utang_list = Transaction.query.filter_by(payment_type='utang').order_by(Transaction.is_paid.asc(), Transaction.date_created.desc()).all()
    return render_template('lending.html', utang_list=utang_list)

# Finance Page
@app.route('/finance', methods=['GET', 'POST'])
@login_required
def finance():
    if current_user.role != 'admin':
        flash('Access restricted to Admin users.', 'error')
        return redirect(url_for('pos'))

    if request.method == 'POST':
        action = request.form.get('action')
        if action == 'add_expense':
            exp = Expense(
                description=request.form.get('description'),
                category=request.form.get('category', 'General'),
                amount=float(request.form.get('amount')),
                user_id=current_user.id
            )
            db.session.add(exp)
            db.session.commit()
            flash('Expense record added.', 'success')
        elif action == 'delete_expense':
            exp = db.session.get(Expense, request.form.get('id'))
            if exp:
                db.session.delete(exp)
                db.session.commit()
                flash('Expense record deleted.', 'success')
        return redirect(url_for('finance'))

    period = request.args.get('period', 'all')
    now = datetime.now()
    
    query_t = Transaction.query.filter_by(is_paid=True)
    query_e = Expense.query
    
    if period == 'today':
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        query_t = query_t.filter(Transaction.date_created >= start)
        query_e = query_e.filter(Expense.date_created >= start)
    elif period == 'week':
        start = now - timedelta(days=now.weekday())
        start = start.replace(hour=0, minute=0, second=0, microsecond=0)
        query_t = query_t.filter(Transaction.date_created >= start)
        query_e = query_e.filter(Expense.date_created >= start)
    elif period == 'month':
        start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        query_t = query_t.filter(Transaction.date_created >= start)
        query_e = query_e.filter(Expense.date_created >= start)
    elif period == 'year':
        start = now.replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0)
        query_t = query_t.filter(Transaction.date_created >= start)
        query_e = query_e.filter(Expense.date_created >= start)

    paid_transactions = query_t.all()
    expenses = query_e.order_by(Expense.date_created.desc()).all()

    total_income = sum(t.total_amount for t in paid_transactions)
    
    # Capital of sold items
    total_capital = sum(
        sum(item.quantity * item.capital_at_sale for item in t.items) 
        for t in paid_transactions
    )
    
    total_expenses = sum(e.amount for e in expenses)
    gross_profit = total_income - total_capital
    net_profit = gross_profit - total_expenses

    return render_template('finance.html',
                           period=period,
                           total_income=total_income,
                           total_capital=total_capital,
                           gross_profit=gross_profit,
                           total_expenses=total_expenses,
                           net_profit=net_profit,
                           expenses=expenses)

# Shifts Management
@app.route('/shifts', methods=['GET', 'POST'])
@login_required
def shifts():
    if request.method == 'POST':
        action = request.form.get('action')
        if action == 'start':
            active = Shift.query.filter_by(user_id=current_user.id, status='active').first()
            if active:
                flash('You already have an active shift.', 'error')
            else:
                starting_cash = float(request.form.get('starting_cash', 0.0))
                shift = Shift(user_id=current_user.id, starting_cash=starting_cash)
                db.session.add(shift)
                db.session.commit()
                flash('Shift started successfully! Good luck on sales.', 'success')

        elif action == 'end':
            active = Shift.query.filter_by(user_id=current_user.id, status='active').first()
            if active:
                active.end_time = datetime.now()
                active.status = 'closed'
                db.session.commit()
                flash('Shift ended and closed.', 'success')

        return redirect(url_for('shifts'))

    active_shift = Shift.query.filter_by(user_id=current_user.id, status='active').first()
    
    if current_user.role == 'admin':
        all_shifts = Shift.query.order_by(Shift.start_time.desc()).all()
    else:
        all_shifts = Shift.query.filter_by(user_id=current_user.id).order_by(Shift.start_time.desc()).all()

    return render_template('shifts.html', active_shift=active_shift, shifts=all_shifts)

# User Management (Admin Only)
@app.route('/users', methods=['GET', 'POST'])
@login_required
def users():
    if current_user.role != 'admin':
        flash('Admin privilege required.', 'error')
        return redirect(url_for('pos'))

    if request.method == 'POST':
        action = request.form.get('action')
        if action == 'add':
            username = request.form.get('username').strip()
            password = request.form.get('password').strip()
            role = request.form.get('role', 'cashier')
            full_name = request.form.get('full_name', '').strip()

            if User.query.filter_by(username=username).first():
                flash('Username already exists.', 'error')
            else:
                new_user = User(
                    username=username,
                    password=generate_password_hash(password, method='pbkdf2:sha256'),
                    role=role,
                    full_name=full_name
                )
                db.session.add(new_user)
                db.session.commit()
                flash(f'Account created for {username} ({role}).', 'success')
        elif action == 'delete':
            u_id = request.form.get('id')
            if int(u_id) == current_user.id:
                flash('You cannot delete your own logged-in admin account.', 'error')
            else:
                u = db.session.get(User, u_id)
                if u:
                    db.session.delete(u)
                    db.session.commit()
                    flash('User deleted.', 'success')
        return redirect(url_for('users'))

    all_users = User.query.all()
    return render_template('users.html', users=all_users)

if __name__ == '__main__':
    with app.app_context():
        seed_database()
    app.run(debug=True, host='0.0.0.0', port=5002)
