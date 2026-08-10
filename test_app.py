import unittest
from app import app, db, User, Product, Category, Transaction, TransactionItem, Shift, Expense, UtangPayment, seed_database
from werkzeug.security import generate_password_hash

class SwiftPOSTestCase(unittest.TestCase):
    def setUp(self):
        app.config['TESTING'] = True
        app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///:memory:'
        app.config['WTF_CSRF_ENABLED'] = False
        self.client = app.test_client()

        with app.app_context():
            db.create_all()
            
            # Ensure base users exist
            if not User.query.filter_by(username='admin').first():
                admin = User(username='admin', password=generate_password_hash('admin123', method='pbkdf2:sha256'), role='admin', full_name='Admin Test')
                db.session.add(admin)

            if not User.query.filter_by(username='cashier').first():
                cashier = User(username='cashier', password=generate_password_hash('cashier123', method='pbkdf2:sha256'), role='cashier', full_name='Cashier Test')
                db.session.add(cashier)

            # Ensure categories exist
            cat = Category.query.filter_by(name='Beverages').first()
            if not cat:
                cat = Category(name='Beverages')
                db.session.add(cat)

            # Create test products if not present
            if not Product.query.filter_by(name='Coca-Cola 1.5L').first():
                p1 = Product(name='Coca-Cola 1.5L', price=75.0, capital=60.0, stock=45.0, category='Beverages', unit='pcs')
                db.session.add(p1)

            if not Product.query.filter_by(name='Rice (Sinandomeng)').first():
                p2 = Product(name='Rice (Sinandomeng)', price=50.0, capital=40.0, stock=100.0, category='Staples', unit='Kg')
                db.session.add(p2)

            db.session.commit()

    def tearDown(self):
        with app.app_context():
            db.session.remove()
            db.drop_all()

    def login(self, username, password):
        return self.client.post('/login', data=dict(username=username, password=password), follow_redirects=True)

    def logout(self):
        return self.client.get('/logout', follow_redirects=True)

    # 1. Test Authentication & Roles
    def test_login_logout(self):
        res = self.login('admin', 'admin123')
        self.assertIn(b'Dashboard', res.data)

        res = self.logout()
        self.assertIn(b'Sign in', res.data)

    def test_login_failure(self):
        res = self.login('admin', 'wrongpass')
        self.assertIn(b'Invalid username or password', res.data)

    def test_role_permissions(self):
        # Cashier logged in
        self.login('cashier', 'cashier123')
        
        # Cashier trying to access admin dashboard -> redirected to POS
        res = self.client.get('/dashboard', follow_redirects=True)
        self.assertIn(b'Current Sales Cart', res.data)
        
        # Cashier trying to access stocks -> redirected to POS with flash message
        res = self.client.get('/stocks', follow_redirects=True)
        self.assertIn(b'Access restricted', res.data)

    # 2. Test Product API
    def test_product_api(self):
        self.login('cashier', 'cashier123')
        res = self.client.get('/api/products?category=All&search=')
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertGreaterEqual(len(data), 2)

        # Filter by search
        res = self.client.get('/api/products?category=All&search=Coca')
        data = res.get_json()
        self.assertEqual(len(data), 1)
        self.assertEqual(data[0]['name'], 'Coca-Cola 1.5L')

    # 3. Test POS Cash Checkout
    def test_cash_checkout(self):
        self.login('cashier', 'cashier123')
        
        with app.app_context():
            prod = Product.query.filter_by(name='Coca-Cola 1.5L').first()
            p_id = prod.id
            initial_stock = prod.stock

        payload = {
            'cart': [{'id': p_id, 'qty': 2}], # 2 x 75.0 = 150.0
            'payment_type': 'cash',
            'subtotal': 150.0,
            'discount': 10.0,
            'total': 140.0,
            'cash': 200.0
        }
        res = self.client.post('/api/checkout', json=payload)
        data = res.get_json()
        self.assertTrue(data['success'])
        self.assertEqual(data['change'], 60.0)

        # Verify stock auto-deduction (initial_stock - 2)
        with app.app_context():
            updated_prod = db.session.get(Product, p_id)
            self.assertEqual(updated_prod.stock, initial_stock - 2.0)

            # Verify transaction record
            tx = db.session.get(Transaction, data['transaction_id'])
            self.assertEqual(tx.total_amount, 140.0)
            self.assertTrue(tx.is_paid)

    # 4. Test Utang / Credit Checkout & Lending Payment
    def test_utang_checkout_and_payment(self):
        self.login('cashier', 'cashier123')
        with app.app_context():
            prod = Product.query.filter_by(name='Rice (Sinandomeng)').first()
            p_id = prod.id

        payload = {
            'cart': [{'id': p_id, 'qty': 0.5}], # 0.5kg x 50.0 = 25.0
            'payment_type': 'utang',
            'borrower_name': 'Aling Nena',
            'subtotal': 25.0,
            'discount': 0.0,
            'total': 25.0,
            'cash': 0.0
        }
        res = self.client.post('/api/checkout', json=payload)
        data = res.get_json()
        self.assertTrue(data['success'])

        with app.app_context():
            tx = db.session.get(Transaction, data['transaction_id'])
            self.assertFalse(tx.is_paid)
            self.assertEqual(tx.borrower_name, 'Aling Nena')
            self.assertEqual(tx.paid_amount, 0.0)
            tx_id = tx.id

        # Record partial payment of 10.0
        self.login('admin', 'admin123')
        res = self.client.post('/lending', data={
            'action': 'pay',
            'transaction_id': tx_id,
            'amount': '10.0',
            'notes': 'Partial cash'
        }, follow_redirects=True)
        self.assertIn(b'Payment of', res.data)

        with app.app_context():
            tx = db.session.get(Transaction, tx_id)
            self.assertEqual(tx.paid_amount, 10.0)
            self.assertFalse(tx.is_paid)

        # Record remaining payment of 15.0 -> Fully Paid
        res = self.client.post('/lending', data={
            'action': 'pay',
            'transaction_id': tx_id,
            'amount': '15.0',
            'notes': 'Full clear'
        }, follow_redirects=True)

        with app.app_context():
            tx = db.session.get(Transaction, tx_id)
            self.assertEqual(tx.paid_amount, 25.0)
            self.assertTrue(tx.is_paid)

    # 5. Test Shift System
    def test_shift_management(self):
        self.login('cashier', 'cashier123')
        
        # Start shift
        res = self.client.post('/shifts', data={'action': 'start', 'starting_cash': '500.0'}, follow_redirects=True)
        self.assertIn(b'Shift started successfully', res.data)

        with app.app_context():
            c_user = User.query.filter_by(username='cashier').first()
            shift = Shift.query.filter_by(user_id=c_user.id, status='active').first()
            self.assertIsNotNone(shift)
            self.assertEqual(shift.starting_cash, 500.0)

        # End shift
        res = self.client.post('/shifts', data={'action': 'end'}, follow_redirects=True)
        self.assertIn(b'Shift ended and closed', res.data)

        with app.app_context():
            shift = db.session.get(Shift, shift.id)
            self.assertEqual(shift.status, 'closed')

    # 6. Test Stocks CRUD
    def test_stocks_crud(self):
        self.login('admin', 'admin123')

        # Add Product
        res = self.client.post('/stocks', data={
            'action': 'add',
            'name': 'Piattos Cheese',
            'category': 'Snacks',
            'unit': 'pcs',
            'capital': '30.0',
            'price': '40.0',
            'stock': '50.0'
        }, follow_redirects=True)
        self.assertIn(b'created successfully', res.data)

        with app.app_context():
            p = Product.query.filter_by(name='Piattos Cheese').first()
            self.assertIsNotNone(p)
            self.assertEqual(p.price, 40.0)
            p_id = p.id

        # Edit Product
        res = self.client.post('/stocks', data={
            'action': 'edit',
            'id': p_id,
            'name': 'Piattos Cheese 85g',
            'category': 'Snacks',
            'unit': 'pcs',
            'capital': '32.0',
            'price': '42.0',
            'stock': '45.0'
        }, follow_redirects=True)
        self.assertIn(b'updated successfully', res.data)

        with app.app_context():
            p = db.session.get(Product, p_id)
            self.assertEqual(p.name, 'Piattos Cheese 85g')
            self.assertEqual(p.price, 42.0)

        # Delete Product
        res = self.client.post('/stocks', data={
            'action': 'delete',
            'id': p_id
        }, follow_redirects=True)
        self.assertIn(b'removed', res.data)

        with app.app_context():
            p = db.session.get(Product, p_id)
            self.assertIsNone(p)

    # 7. Test Finance Expenses & Profit Logic
    def test_finance_expenses(self):
        self.login('admin', 'admin123')

        # Add expense
        res = self.client.post('/finance', data={
            'action': 'add_expense',
            'description': 'Store Electricity',
            'category': 'Utilities',
            'amount': '150.0'
        }, follow_redirects=True)
        self.assertIn(b'Expense record added', res.data)

        with app.app_context():
            e = Expense.query.filter_by(description='Store Electricity').first()
            self.assertIsNotNone(e)
            self.assertEqual(e.amount, 150.0)

    # 8. Test CSV Export
    def test_csv_export(self):
        self.login('admin', 'admin123')
        res = self.client.get('/receipts/export')
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.mimetype, 'text/csv')
        self.assertIn(b'Receipt ID,Date,Cashier', res.data)

    # 9. Test Change Password Flow
    def test_change_password(self):
        self.login('cashier', 'cashier123')
        res = self.client.post('/change-password', data={
            'current_password': 'cashier123',
            'new_password': 'newpassword123',
            'confirm_password': 'newpassword123'
        }, follow_redirects=True)
        self.assertIn(b'Password updated successfully', res.data)

        # Verify old password no longer works
        self.logout()
        res = self.login('cashier', 'cashier123')
        self.assertIn(b'Invalid username or password', res.data)

        # Verify new password works
        res = self.login('cashier', 'newpassword123')
        self.assertIn(b'Current Sales Cart', res.data)

    # 10. Test Forgot Password Recovery Flow
    def test_forgot_password(self):
        # Trigger forgot password with incorrect store recovery pin -> failure
        res = self.client.post('/forgot-password', data={
            'username': 'admin',
            'recovery_pin': 'wrong_pin',
            'new_password': 'recovered123',
            'confirm_password': 'recovered123'
        }, follow_redirects=True)
        self.assertIn(b'Invalid Store Recovery PIN', res.data)

        # Trigger reset with correct recovery pin (9999) -> success
        res = self.client.post('/forgot-password', data={
            'username': 'admin',
            'recovery_pin': '9999',
            'new_password': 'recovered123',
            'confirm_password': 'recovered123'
        }, follow_redirects=True)
        self.assertIn(b'Password reset successfully', res.data)

        # Verify recovered password works on login
        res = self.login('admin', 'recovered123')
        self.assertIn(b'Dashboard', res.data)

if __name__ == '__main__':
    unittest.main()
