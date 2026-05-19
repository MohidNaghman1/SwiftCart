# SwiftCart 🛒

SwiftCart is a modern, high-performance e-commerce web application built using **Django** and **Django REST Framework (DRF)** on the backend, with a clean, responsive, and interactive Vanilla JS & CSS frontend. 

It features secure JWT-based user authentication, product catalog management, a dynamic shopping cart, a Stripe payment gateway checkout integration with webhook verification, and a comprehensive Admin Dashboard to process orders and manage delivery states.

---

## 🌟 Key Features

### 1. User Authentication & Security
- **Registration & Login**: Secure user enrollment and authentication.
- **JWT Authentication**: Powered by `djangorestframework-simplejwt` to secure API requests.
- **Role-Based Access**: Admins/staff get access to a full control dashboard, while customers can browse products and manage their own orders.

### 2. Product Catalog Management
- **Interactive Browsing**: Responsive grids displaying products, categories, search, and live stock tracking.
- **Stock Integrity**: Real-time validation checks stock availability prior to initiating checkout and restores stock dynamically if orders are cancelled.

### 3. Stripe Checkout Integration
- **Pre-filled Checkout**: Automatically pre-fills the customer's email address on the secure, Stripe-hosted payment form.
- **Flexible Payments**: Supports card payments and automatically integrates Google Pay.
- **Success Polling**: An interactive polling system tracking payments securely from the client side.

### 4. Stripe Webhooks (Transaction Safety)
- Uses secure Stripe webhooks to track payment completion signals asynchronously.
- Updates order status to `pending` upon successful payment verification.
- Implements fallback safety handlers to create the order dynamically even if the customer's browser window is closed during redirection.

### 5. Admin Dashboard
- **Comprehensive Control**: Live listing and detail views of all orders placed.
- **Manual Delivery Flow**: Admins can mark orders in the `pending` state as `deliverd` directly from the dashboard using a single-click delivery button.
- **Filter and Search**: View orders categorized by status (`Pending`, `Paid`, `Deliverd`, `Cancelled`).

### 6. Logging & Debugging
- Structured application-wide logging configured under `settings.py`.
- Custom `orders` app logging at the `INFO` level to monitor and log checkout flow events, webhook completions, status polling, and admin updates.

---

## 🏗️ Project Architecture

```
SwiftCart/
│
├── Swiftcart/                 # Core Django configuration
│   ├── settings.py            # Global settings & custom logging config
│   ├── urls.py                # Root routing
│   └── views.py               # Main page loaders
│
├── orders/                    # Orders & checkout app
│   ├── models.py              # Order & OrderItem models (including statuses)
│   ├── serializers.py         # DRF Serializers for items & orders
│   ├── views.py               # CheckoutInit, Success, Cancel, Webhooks, and API Views
│   └── urls.py                # Orders API routing
│
├── products/                  # Products & categories app
│   ├── models.py              # Product & Category models
│   └── views.py               # Product list & details API
│
├── users/                     # JWT User profiles app
│   └── models.py              # Custom user profiles
│
├── templates/                 # Frontend pages
│   ├── base.html              # Global premium styling wrapper & UI utilities
│   ├── products.html          # Product listing, cart drawer, and user orders
│   ├── success.html           # Stripe redirect & status verification polling
│   └── admin/
│       ├── orders.html        # Admin Dashboard order view & Delivery action button
│       └── products.html      # Admin Product catalog manager
│
└── manage.py                  # Django CLI manager
```

---

## 🛠️ Tech Stack

- **Backend**: Python 3.x, Django 5.x, Django REST Framework
- **Authentication**: Simple JWT
- **Payment Processing**: Stripe API (Python client library)
- **Database**: SQLite3 / PostgreSQL compatible
- **Frontend**: HTML5, Vanilla JavaScript, Premium Custom CSS (Glassmorphism, custom status pills, CSS Variables)

---

## 🚀 Setup & Installation

### 1. Clone the repository and navigate to the project directory
```bash
git clone <repository-url>
cd SwiftCart
```

### 2. Create and activate a virtual environment
```powershell
# Windows
python -m venv .venv
.venv\Scripts\activate
```

### 3. Install dependencies
```bash
pip install -r requirements.txt
```

### 4. Configure Environment Variables
Create a `.env` file in the root directory:
```env
DEBUG=True
SECRET_KEY=your-django-secret-key
STRIPE_SECRET_KEY=sk_test_...
STRIPE_PUBLISHABLE_KEY=pk_test_...
STRIPE_WEBHOOK_SECRET=whsec_...
```

### 5. Apply Migrations
```bash
python manage.py makemigrations
python manage.py migrate
```

### 6. Run the Development Server
```bash
python manage.py runserver
```

---

## 🚦 Order Status Transitions

The application manages order lifecycles through the following states:

1. **`processing`**: Order is initiated but payment confirmation is pending.
2. **`pending`**: Payment was successfully processed and confirmed via Stripe webhooks. The order is waiting to be packaged/delivered.
3. **`deliverd`**: Admin marked the order as delivered from the dashboard.
4. **`cancelled`**: Payment failed, checkout session expired, or the order was manually cancelled. (Stock is restored to products automatically).
