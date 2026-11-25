from functools import wraps
import os
import razorpay

import threading
from datetime import timedelta
from decimal import Decimal, ROUND_CEILING
from django.db import models
from django.conf import settings
from django.contrib import messages
from django.contrib.auth import login, logout
from django.contrib.auth.decorators import login_required, user_passes_test
from django.contrib.auth.forms import AuthenticationForm
from django.core.mail import send_mail
from django.core.paginator import Paginator
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from .models import ContactMessage
from .forms import RegisterForm
from django.db.models import Avg, Count, Q, F
from .models import Cuisine, Recipe, RecipeIngredient
import json
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from .ai_services import get_ai_shopping_assistant
from .models import (
    Cart,
    CartItem,
    Category,
    DeliveryProfile,
    Order,
    OrderItem,
    Product,
    ProductImage,
    ProductReview,
    UserPocket,
    Wishlist,
    RecipeReview,
    Address,
)


def get_cart_count(request):
    """Helper to get total item quantity in the current user's cart."""
    cart = None
    if request.user.is_authenticated:
        cart = Cart.objects.filter(user=request.user).first()
    elif request.session.session_key:
        cart = Cart.objects.filter(session_key=request.session.session_key, user=None).first()
    
    if cart:
        return sum(item.quantity for item in cart.items.all())
    return 0


def about(request):
    fruits = ['Apple', 'Banana', 'Mango', 'Orange', 'Grapes']
    payload = {
        'fruits': fruits
    }
    return render(request, 'about.html', payload)


def services(request):
    return render(request, "services.html")


@login_required(login_url='login')
def bootstrap(request):
    return render(request, 'bootstrap.html')


@login_required(login_url='login')
def shop_view(request):
    categories = Category.objects.all()
    products_qs = Product.objects.all()

    # 1. Category Filter
    category_slug = request.GET.get('category')
    if category_slug:
        products_qs = products_qs.filter(categories__Category_slug=category_slug)

    # 2. Keyword Search (e.g., "sugar")
    query = request.GET.get('keywords')
    if query:
        products_qs = products_qs.filter(title__icontains=query)

    # 3. Price Filter Slider (max price)
    max_price = request.GET.get('max_price')
    if max_price:
        try:
            products_qs = products_qs.filter(price__lte=Decimal(max_price))
        except (ValueError, TypeError):
            pass

    # 4. Additional Filters (Organic, Fresh, Sales, Discount, Expired)
    additional_filter = request.GET.get('additional')
    today = timezone.now().date()

    if additional_filter == 'organic':
        products_qs = products_qs.filter(
            Q(admin_badge='ORGANIC') | 
            Q(admin_badge='AUTO', is_organic=True)
        )
    elif additional_filter == 'fresh':
        products_qs = products_qs.filter(
            Q(admin_badge='FRESH') | 
            Q(admin_badge='AUTO', expiry_date__isnull=True) | 
            Q(admin_badge='AUTO', expiry_date__gte=today)
        )
    elif additional_filter in ['sales', 'discount']:
        products_qs = products_qs.filter(
            Q(admin_badge='DISCOUNT') | 
            Q(admin_badge='AUTO', original_price__isnull=False, original_price__gt=F('price'))
        )
    elif additional_filter == 'expired':
        products_qs = products_qs.filter(
            Q(admin_badge='EXPIRED') | 
            Q(admin_badge='AUTO', expiry_date__isnull=False, expiry_date__lt=today)
        )

    # 5. Sorting Logic (Popularity based on customer reviews)
    sort_option = request.GET.get('sort')
    
    # Annotate with average rating and total reviews for popularity sorting
    products_qs = products_qs.annotate(
        avg_rating=Avg('reviews__rating'),
        review_count=Count('reviews')
    )

    if sort_option == 'popularity':
        # Sort by highest average rating first, then by review count
        products_qs = products_qs.order_by('-avg_rating', '-review_count', '-created_at')
    elif sort_option == 'organic':
        products_qs = products_qs.filter(is_organic=True).order_by('-created_at')
    elif sort_option == 'fantastic':
        products_qs = products_qs.order_by('-price')
    else:
        products_qs = products_qs.order_by('-created_at')
    daily_needs_products = Product.objects.filter(
        is_active=True
    ).order_by('-created_at').distinct()

    discount_products = Product.objects.filter(
        is_active=True
    ).filter(
        Q(admin_badge='DISCOUNT') |
        Q(admin_badge='AUTO', original_price__isnull=False, original_price__gt=F('price'))
    ).distinct()
    # Cart quantity tracking
    cart = None
    if request.user.is_authenticated:
        cart = Cart.objects.filter(user=request.user).first()
    elif request.session.session_key:
        cart = Cart.objects.filter(session_key=request.session.session_key, user=None).first()

    cart_quantities = {}
    if cart:
        cart_quantities = {
            item.product_id: item.quantity 
            for item in CartItem.objects.filter(cart=cart)
        }

    # Wishlist IDs for favorited status tracking
    user_wishlist_ids = []
    if request.user.is_authenticated:
        user_wishlist_ids = list(Wishlist.objects.filter(user=request.user).values_list('product_id', flat=True))

    for product in products_qs:
        product.in_cart_qty = cart_quantities.get(product.id, 0)
        product.is_favorited = product.id in user_wishlist_ids

    # Pagination setup
    paginator = Paginator(products_qs, 9)
    page_number = request.GET.get('page')
    products = paginator.get_page(page_number)

    context = {
        'categories': categories,
        'products': products,
        'daily_needs_products': daily_needs_products,
        'discount_products': discount_products,
        'cart_count': get_cart_count(request),
        'selected_max_price': max_price,
        'selected_additional': additional_filter,
        'selected_sort': sort_option,
        'search_query': query,
    }
    return render(request, 'shop.html', context)


@login_required(login_url='login')
def wishlist_view(request):
    wishlist_items = Wishlist.objects.filter(user=request.user).select_related('product')
    
    cart = Cart.objects.filter(user=request.user).first()
    cart_quantities = {}
    if cart:
        cart_quantities = {item.product_id: item.quantity for item in CartItem.objects.filter(cart=cart)}

    for item in wishlist_items:
        item.product.in_cart_qty = cart_quantities.get(item.product.id, 0)
        item.product.is_favorited = True

    context = {
        'wishlist_items': wishlist_items,
        'cart_count': get_cart_count(request),
    }
    return render(request, 'wishlist.html', context)


@login_required(login_url='login')
def toggle_wishlist_view(request, product_id):
    product = get_object_or_404(Product, id=product_id)
    wishlist_item, created = Wishlist.objects.get_or_create(user=request.user, product=product)
    
    if not created:
        wishlist_item.delete()
        messages.info(request, f"Removed {product.title} from your wishlist.")
    else:
        messages.success(request, f"Added {product.title} to your wishlist!")
        
    return redirect(request.META.get('HTTP_REFERER', 'shop_page'))


@login_required(login_url='login')
def add_to_cart(request, product_id):
    product = get_object_or_404(Product, id=product_id)
    
    # Read the requested quantity from the URL query parameter ?qty= (defaults to 1)
    try:
        add_qty = int(request.GET.get('qty', 1))
        if add_qty < 1:
            add_qty = 1
    except ValueError:
        add_qty = 1
    
    if request.user.is_authenticated:
        cart, created = Cart.objects.get_or_create(user=request.user)
    else:
        if not request.session.session_key:
            request.session.create()
        session_key = request.session.session_key
        cart, created = Cart.objects.get_or_create(session_key=session_key, user=None)
    
    cart_item, created = CartItem.objects.get_or_create(cart=cart, product=product)
    if created:
        cart_item.quantity = add_qty
    else:
        cart_item.quantity += add_qty  # Adds up when you click + or add again
    cart_item.save()
        
    messages.success(request, f"Added {add_qty} x {product.title} to your cart!")
    return redirect(request.META.get('HTTP_REFERER', 'shop_page'))
@login_required(login_url='login')
def remove_from_cart(request, product_id):
    product = Product.objects.get(id=product_id)
    
    if request.user.is_authenticated:
        cart = Cart.objects.filter(user=request.user).first()
    else:
        session_key = request.session.session_key
        cart = Cart.objects.filter(session_key=session_key, user=None).first()
        
    if cart:
        cart_item = CartItem.objects.filter(cart=cart, product=product).first()
        if cart_item:
            if cart_item.quantity > 1:
                cart_item.quantity -= 1
                cart_item.save()
            else:
                cart_item.delete()
                
    messages.info(request, f"Updated cart for {product.title}")
    return redirect(request.META.get('HTTP_REFERER', 'shop_page'))


@login_required(login_url='login')
def get_user_cart(request):
    """Helper function to retrieve or create the active cart."""
    if request.user.is_authenticated:
        cart, _ = Cart.objects.get_or_create(user=request.user)
    else:
        if not request.session.session_key:
            request.session.create()
        session_key = request.session.session_key
        cart, _ = Cart.objects.get_or_create(session_key=session_key, user=None)
    return cart


@login_required(login_url='login')
def cart_view(request):
    cart = get_user_cart(request)
    cart_items = CartItem.objects.filter(cart=cart).select_related('product')
    discount = Decimal('0.00')
    shipping_fee = Decimal('0.00')
    subtotal = sum(item.total_price for item in cart_items)

    if not cart_items:
        shipping_fee = Decimal('0.00')
    elif subtotal >= Decimal('999.00'):
        shipping_fee = Decimal('0.00')
        discount = subtotal * Decimal('0.20')
    elif subtotal >= Decimal('499.00'):
        shipping_fee = Decimal('0.00')
        discount = subtotal * Decimal('0.10')
    elif subtotal >= Decimal('100.00'):
        shipping_fee = Decimal('0.00')
        discount = subtotal * Decimal('0.05')
    else:
        shipping_fee = Decimal('30.00')
        
    total = subtotal - discount + shipping_fee    

    context = {
        'cart': cart,
        'cart_items': cart_items,
        'subtotal': subtotal,
        'shipping_fee': shipping_fee,
        'discount': discount,
        'total': total,
        'cart_count': get_cart_count(request)
    }
    return render(request, 'cart.html', context)


@login_required(login_url='login')
def delete_cart_item(request, item_id):
    cart = get_user_cart(request)
    cart_item = CartItem.objects.filter(cart=cart, id=item_id).first()
    if cart_item:
        cart_item.delete()
        messages.info(request, "Item removed from cart.")
    return redirect('cart_page')
def get_ingredient_multiplier(req_qty: Decimal, recipe_unit: str, product: Product) -> Decimal:
   
    pkg_qty = Decimal(str(product.package_quantity)) if product.package_quantity else Decimal('1.00')
    if pkg_qty <= 0:
        pkg_qty = Decimal('1.00')

    pkg_unit = (product.package_unit or '').upper()
    rec_unit = (recipe_unit or '').lower()

    # Weight conversions (store in KG or G)
    if pkg_unit == 'KG':
        if rec_unit == 'kg':
            qty_in_pkg_unit = req_qty
        elif rec_unit == 'g':
            qty_in_pkg_unit = req_qty / Decimal('1000')
        else:
            return Decimal('1')
    elif pkg_unit == 'G':
        if rec_unit == 'g':
            qty_in_pkg_unit = req_qty
        elif rec_unit == 'kg':
            qty_in_pkg_unit = req_qty * Decimal('1000')
        else:
            return Decimal('1')

    # Volume conversions (store in L or ML)
    elif pkg_unit == 'L':
        if rec_unit in ['l', 'litre', 'liter']:
            qty_in_pkg_unit = req_qty
        elif rec_unit == 'ml':
            qty_in_pkg_unit = req_qty / Decimal('1000')
        else:
            return Decimal('1')
    elif pkg_unit == 'ML':
        if rec_unit == 'ml':
            qty_in_pkg_unit = req_qty
        elif rec_unit in ['l', 'litre', 'liter']:
            qty_in_pkg_unit = req_qty * Decimal('1000')
        else:
            return Decimal('1')

    # Piece / count-based store units (PCS, PKT, BOX, BTL, BND, DZN)
    else:
        if rec_unit == 'pcs':
            qty_in_pkg_unit = req_qty
        else:
            return Decimal('1')

    if qty_in_pkg_unit <= Decimal('0'):
        return Decimal('1')

    raw_packs = qty_in_pkg_unit / pkg_qty
    # Always buy at least 1 store pack, and round up to whole store packs when more is needed
    packs_needed = max(Decimal('1'), raw_packs.to_integral_value(rounding=ROUND_CEILING))
    return packs_needed
def home(request):
    categories = Category.objects.all()
    products_qs = Product.objects.filter(is_active=True)[:8]

    vegetable_products = Product.objects.filter(
        is_active=True, 
        categories__Category_name__icontains='Fresh Produce'
    ).distinct()

    cart = None
    if request.user.is_authenticated:
        cart = Cart.objects.filter(user=request.user).first()
    elif request.session.session_key:
        cart = Cart.objects.filter(session_key=request.session.session_key, user=None).first()

    cart_quantities = {}
    if cart:
        cart_quantities = {
            item.product_id: item.quantity 
            for item in CartItem.objects.filter(cart=cart)
        }

    for product in list(products_qs) + list(vegetable_products):
        product.in_cart_qty = cart_quantities.get(product.id, 0)

    context = {
        'categories': categories,
        'products': products_qs,
        'vegetable_products': vegetable_products,
        'cart_count': get_cart_count(request),
    }
    return render(request, 'bootstrap.html', context)


@login_required(login_url='login')
def orders_view(request):
    orders_list = Order.objects.filter(user=request.user).prefetch_related('items__product').order_by('-created_at')
    
    status_filter = request.GET.get('status')
    if status_filter:
        orders_list = orders_list.filter(status=status_filter)
        
    query = request.GET.get('q')
    if query:
        orders_list = orders_list.filter(items__product__title__icontains=query).distinct()

    # Calculate return eligibility (5-hour window) for each order
    now = timezone.now()
    for order in orders_list:
        if order.status == 'Delivered' and order.delivered_at:
            time_since_delivery = now - order.delivered_at
            order.can_return = time_since_delivery <= timedelta(hours=5)
        else:
            order.can_return = False

    context = {
        'orders': orders_list,
        'selected_status': status_filter,
        'cart_count': get_cart_count(request),
    }
    return render(request, 'orders.html', context)

def register_view(request):
    if request.user.is_authenticated:
        return redirect('home')

    if request.method == 'POST':
        form = RegisterForm(request.POST)
        if form.is_valid():
            user = form.save()
            UserPocket.objects.get_or_create(user=user)
            subject = 'Welcome to Grocery Guide!'
            message = (
                f"Hello {user.first_name or user.username},\n\n"
                f"Welcome to Grocery Guide!\n"
                f"Your account has been successfully created, and you are all set to explore our platform."
            )
            from_email = "groceryguideai@gmail.com"
            recipient_list = [user.email]

            try:
                send_mail(subject, message, from_email, recipient_list, fail_silently=False)
            except Exception as e:
                print(f"Error sending email: {e}")

            messages.success(request, f"Account created successfully for {user.username}! Please login below.")
            return redirect('login')
        else:
            messages.error(request, "Please fix the errors below.")
    else:
        form = RegisterForm()

    return render(request, 'register.html', {'form': form})


def login_view(request):
    if request.user.is_authenticated:
        return redirect('home')

    if request.method == 'POST':
        form = AuthenticationForm(request, data=request.POST)
        if form.is_valid():
            user = form.get_user()
            login(request, user)
            messages.success(request, f"Welcome back, {user.username}!")
            return redirect('home')
        else:
            messages.error(request, "Invalid username or password.")
    else:
        form = AuthenticationForm()

    return render(request, 'login.html', {'form': form})


def logout_view(request):
    logout(request)
    messages.info(request, "You have been logged out.")
    return redirect('home')


def credit_reward_after_delay(order_id):
    """Wait 2 seconds after delivery, check if returned, then credit pockets."""
    import time
    time.sleep(2)
    try:
        order = Order.objects.get(id=order_id)
        if order.status == 'Delivered' and not order.is_returned and not order.return_requested and not order.is_reward_credited:
            total_reward = sum(item.product.reward_pocket_amount * item.quantity for item in order.items.all())
            
            if total_reward > 0:
                pocket, created = UserPocket.objects.get_or_create(user=order.user)
                pocket.balance += total_reward
                pocket.save()
                
                order.is_reward_credited = True
                order.save()
    except Order.DoesNotExist:
        pass


@login_required(login_url='login')
def cancel_order_view(request, order_id):
    order = get_object_or_404(Order, id=order_id, user=request.user)
    time_diff = timezone.now() - order.created_at
    
    if time_diff <= timedelta(minutes=1) and order.status == 'Pending':
        order.status = 'Cancelled'
        order.save()
        messages.success(request, "Order successfully cancelled within the 1-minute window.")
    else:
        messages.error(request, "Cancellation window of 1 minute has expired.")
    return redirect('orders_page')


@login_required(login_url='login')
def request_return_view(request, order_id):
    order = get_object_or_404(Order, id=order_id, user=request.user)
    
    if order.status == 'Delivered' and order.delivered_at:
        delivery_time_diff = timezone.now() - order.delivered_at
        if delivery_time_diff <= timedelta(hours=5):
            order.return_requested = True
            order.save()
            messages.success(request, "Return request registered successfully within the 5-hour window.")
        else:
            messages.error(request, "The 5-hour return window after delivery has expired.")
    else:
        messages.error(request, "Return is only available for successfully delivered items.")
    return redirect('orders_page')


def mark_order_delivered(order):
    if order.status != 'Delivered':
        order.status = 'Delivered'
        order.delivered_at = timezone.now()

    # Credit user pocket if rewards haven't been credited yet
    if not order.is_reward_credited and not order.return_requested:
        total_reward = sum(item.product.reward_pocket_amount * item.quantity for item in order.items.all())
        
        if total_reward > 0:
            pocket, created = UserPocket.objects.get_or_create(user=order.user)
            pocket.balance += total_reward
            pocket.save()
            
            order.is_reward_credited = True

    order.save()

def product_detail_view(request, slug):
    product = get_object_or_404(Product, slug=slug)
    reviews = product.reviews.all().order_by('-created_at')
    
    if request.method == 'POST':
        if not request.user.is_authenticated:
            messages.error(request, "You must be logged in to leave a review.")
            return redirect('login')
            
        rating = request.POST.get('rating', 5)
        comment = request.POST.get('comment')
        if comment:
            ProductReview.objects.create(
                product=product,
                user=request.user,
                rating=rating,
                comment=comment
            )
            messages.success(request, "Your review has been submitted successfully!")
            return redirect('product_detail', slug=product.slug)

    context = {
        'product': product,
        'reviews': reviews,
        'cart_count': get_cart_count(request)
    }
    return render(request, 'product_detail.html', context)


@login_required(login_url='login')
def pocket_view(request):
    pocket, created = UserPocket.objects.get_or_create(user=request.user)
    context = {
        'pocket': pocket,
        'cart_count': get_cart_count(request),
    }
    return render(request, 'pocket.html', context)


def delivery_required(view_func):
    """Restricts view execution exclusively to users with a valid DeliveryProfile."""
    @wraps(view_func)
    def _wrapped_view(request, *args, **kwargs):
        if not request.user.is_authenticated:
            messages.error(request, "Please log in to access this page.")
            return redirect('delivery_login')
            
        if not hasattr(request.user, 'delivery_profile'):
            messages.error(request, "Access denied. This area is strictly reserved for delivery personnel.")
            return redirect('home')
            
        return view_func(request, *args, **kwargs)
    return _wrapped_view


def assign_delivery_person(order):
    """Automatically assigns the first available delivery agent who is marked present today."""
    available_agent = DeliveryProfile.objects.filter(
        is_attended_today=True, 
        is_available=True
    ).first()

    if available_agent:
        order.delivery_person = available_agent.user
        order.status = 'On the way'
        order.save()

        available_agent.is_available = False
        available_agent.save()
        return True
    return False


def delivery_login_view(request):
    if request.user.is_authenticated:
        if hasattr(request.user, 'delivery_profile'):
            return redirect('delivery_dashboard')
        else:
            logout(request)

    if request.method == 'POST':
        form = AuthenticationForm(request, data=request.POST)
        if form.is_valid():
            user = form.get_user()
            
            if hasattr(user, 'delivery_profile'):
                login(request, user)
                messages.success(request, f"Welcome back, Delivery Partner {user.username}!")
                return redirect('delivery_dashboard')
            else:
                messages.error(request, "Access Denied: This login portal is strictly for delivery personnel.")
        else:
            messages.error(request, "Invalid username or password.")
    else:
        form = AuthenticationForm()

    return render(request, 'delivery_login.html', {'form': form})


@delivery_required
def delivery_dashboard_view(request):
    profile = request.user.delivery_profile
    
    # CHANGED: Allow delivery persons to see orders across all active progression steps
    active_orders = Order.objects.filter(
        delivery_person=request.user, 
        status__in=['Pending', 'Picking', 'On the way', 'Arrived']
    ).order_by('-created_at')
    
    completed_orders = Order.objects.filter(
        delivery_person=request.user, 
        status='Delivered'
    ).order_by('-delivered_at')[:10]
    
    context = {
        'profile': profile,
        'active_orders': active_orders,
        'completed_orders': completed_orders,
        'cart_count': get_cart_count(request),
    }
    return render(request, 'delivery_dashboard.html', context)

@delivery_required
def confirm_delivery_view(request, order_id):
    order = get_object_or_404(Order, id=order_id, delivery_person=request.user)
    
    if request.method == 'POST':
        proof_file = request.FILES.get('delivery_proof')
        if not proof_file:
            messages.error(request, "Please upload a photo proof of delivery!")
            return redirect('delivery_dashboard')
            
        order.delivery_proof = proof_file
        order.save()
        
        mark_order_delivered(order)

        profile = request.user.delivery_profile
        profile.is_available = True
        profile.save()

        next_order = Order.objects.filter(status='Pending', delivery_person__isnull=True).first()
        if next_order:
            assign_delivery_person(next_order)

        messages.success(request, f"Order #{order.id} delivered and rewards credited!")
        return redirect('delivery_dashboard')
        
    return redirect('delivery_dashboard')
@delivery_required
def update_order_status_view(request, order_id, status_name):
    """Allows delivery partner to manually update order status step-by-step."""
    order = get_object_or_404(Order, id=order_id, delivery_person=request.user)
    
    valid_statuses = ['Picking', 'On the way', 'Arrived']
    if status_name in valid_statuses:
        order.status = status_name
        order.save()
        messages.success(request, f"Order #{order.id} status updated to: {status_name}")
        
    return redirect('delivery_dashboard')
@login_required(login_url='login')
def contact_view(request):
    if request.method == 'POST':
        name = request.POST.get('name')
        email = request.POST.get('email')
        message = request.POST.get('message')
        
        if name and email and message:
            ContactMessage.objects.create(name=name, email=email, message=message)
            messages.success(request, "Your message has been sent successfully!")
        else:
            messages.error(request, "Please fill out all fields.")
            
        return redirect('contact')
        
    context = {
        'cart_count': get_cart_count(request),
    }
    return render(request, 'contact.html', context)

@login_required(login_url='login')
def meal_guide_view(request):
    cuisines = Cuisine.objects.all()
    recipes = []

    selected_cuisine_id = request.GET.get('cuisine')
    selected_recipe_id = request.GET.get('recipe')
    selected_budget = request.GET.get('budget')

    try:
        servings = int(request.GET.get('servings', 1))
        if servings < 1:
            servings = 1
    except ValueError:
        servings = 1

    # Handle POST submission for Recipe Reviews
    if request.method == 'POST' and request.POST.get('submit_recipe_review') == '1':
        recipe_id = request.POST.get('recipe') or selected_recipe_id
        if recipe_id:
            try:
                recipe_obj = get_object_or_404(Recipe, id=int(recipe_id))
                rating = request.POST.get('rating', 5)
                comment = request.POST.get('comment')
                image = request.FILES.get('image')
                video = request.FILES.get('video')

                if comment:
                    RecipeReview.objects.create(
                        recipe=recipe_obj,
                        user=request.user,
                        rating=rating,
                        comment=comment,
                        image=image,
                        video=video
                    )
                    messages.success(request, "Your recipe review has been posted successfully!")
                else:
                    messages.error(request, "Review comment cannot be empty.")
            except Exception as e:
                messages.error(request, f"Error saving review: {e}")
        else:
            messages.error(request, "Could not identify the recipe for this review.")

        return redirect(request.META.get('HTTP_REFERER', 'guide'))

    selected_recipe = None
    filtered_products = []
    total_cost = Decimal('0.00')
    recipe_in_budget = True

    if selected_cuisine_id:
        try:
            recipes = Recipe.objects.filter(cuisine_id=int(selected_cuisine_id))
        except ValueError:
            recipes = []

    cart = None
    if request.user.is_authenticated:
        cart = Cart.objects.filter(user=request.user).first()
    elif request.session.session_key:
        cart = Cart.objects.filter(session_key=request.session.session_key, user=None).first()

    cart_quantities = {}
    if cart:
        cart_quantities = {
            item.product_id: item.quantity
            for item in CartItem.objects.filter(cart=cart)
        }

    if selected_recipe_id:
        try:
            selected_recipe = get_object_or_404(Recipe, id=int(selected_recipe_id))
            recipe_ingredients = RecipeIngredient.objects.filter(recipe=selected_recipe).select_related('product')

            product_list = []
            for ri in recipe_ingredients:
                prod = ri.product
                base_req_qty = Decimal(str(ri.required_quantity))
                req_qty = base_req_qty * Decimal(str(servings))
                unit = ri.unit_measure

                multiplier = get_ingredient_multiplier(req_qty, unit, prod)
                item_total_price = (prod.price * multiplier).quantize(Decimal('0.01'))
                cart_multiplier = max(1, int(multiplier.to_integral_value(rounding=ROUND_CEILING)))

                product_list.append({
                    'product': prod,
                    'required_qty': req_qty,
                    'unit_measure': ri.get_unit_measure_display(),
                    'store_measurement': prod.full_measurement,
                    'price': prod.price,
                    'item_total': item_total_price,
                    'cart_multiplier': cart_multiplier,
                    'in_cart_qty': cart_quantities.get(prod.id, 0)
                })

            filtered_products = product_list
            total_cost = sum((item['item_total'] for item in filtered_products), Decimal('0.00'))

            if selected_budget:
                try:
                    if '-' in selected_budget:
                        min_val, max_val = map(float, selected_budget.split('-'))
                        recipe_in_budget = min_val <= float(total_cost) <= max_val
                    elif '+' in selected_budget:
                        min_val = float(selected_budget.replace('+', ''))
                        recipe_in_budget = float(total_cost) >= min_val
                except ValueError:
                    recipe_in_budget = True

        except (ValueError, Recipe.DoesNotExist):
            selected_recipe = None

    recipe_reviews = selected_recipe.reviews.all().order_by('-created_at') if selected_recipe else []

    if selected_recipe and recipe_reviews.exists():
        raw_avg = selected_recipe.reviews.aggregate(Avg('rating'))['rating__avg']
        avg_rating = round(raw_avg, 1) if raw_avg else 0.0
    else:
        avg_rating = 0.0

    context = {
        'cuisines': cuisines,
        'recipes': recipes,
        'selected_cuisine_id': int(selected_cuisine_id) if selected_cuisine_id and selected_cuisine_id.isdigit() else None,
        'selected_recipe_id': int(selected_recipe_id) if selected_recipe_id and selected_recipe_id.isdigit() else None,
        'selected_recipe': selected_recipe,
        'filtered_products': filtered_products,
        'selected_budget': selected_budget,
        'servings': servings,
        'total_cost': total_cost,
        'recipe_in_budget': recipe_in_budget,
        'recipe_reviews': recipe_reviews,
        'avg_rating': avg_rating,
        'cart_count': get_cart_count(request),
    }
    return render(request, 'recipe_planner.html', context)
@login_required(login_url='login')
def add_all_recipe_to_cart(request, recipe_id):
    recipe = get_object_or_404(Recipe, id=recipe_id)
    recipe_ingredients = RecipeIngredient.objects.filter(recipe=recipe).select_related('product')

    if request.user.is_authenticated:
        cart, created = Cart.objects.get_or_create(user=request.user)
    else:
        if not request.session.session_key:
            request.session.create()
        session_key = request.session.session_key
        cart, created = Cart.objects.get_or_create(session_key=session_key, user=None)

    for ri in recipe_ingredients:
        prod = ri.product
        req_qty = Decimal(str(ri.required_quantity))
        unit = ri.unit_measure

        multiplier = get_ingredient_multiplier(req_qty, unit, prod)
        cart_multiplier = max(1, int(multiplier.to_integral_value(rounding=ROUND_CEILING)))

        cart_item, created = CartItem.objects.get_or_create(cart=cart, product=prod)
        if created:
            cart_item.quantity = cart_multiplier
        else:
            cart_item.quantity += cart_multiplier
        cart_item.save()

    messages.success(request, f"Successfully added all items for {recipe.title} to your cart!")
    return redirect(request.META.get('HTTP_REFERER', 'guide'))
from django.core.mail import send_mail
from django.conf import settings

def send_order_confirmation_email(order_id):
    try:
        order = Order.objects.prefetch_related('items__product').filter(id=order_id).first()
        if not order:
            return

        subject = f"Order Confirmation - #{order.id} | Grocery Guide"
        items_list_str = ""
        for item in order.items.all():
            items_list_str += f"- {item.product.title} (Qty: {item.quantity}) @ ₹{item.price} each\n"

        message = (
            f"Hello {order.first_name},\n\n"
            f"Thank you for shopping with Grocery Guide!\n"
            f"Your order has been successfully placed.\n\n"
            f"--- Order Details ---\n"
            f"Order ID: #{order.id}\n"
            f"Payment Method: {order.get_payment_method_display()}\n"
            f"Status: {order.status}\n\n"
            f"Items Ordered:\n{items_list_str}\n"
            f"Total Bill: ₹{order.total_price}\n\n"
            f"Delivery Address:\n{order.address}\n\n"
            f"We will notify you once your order is out for delivery.\n\n"
            f"Best regards,\n"
            f"Grocery Guide Team"
        )

        send_mail(
            subject=subject,
            message=message,
            from_email=settings.DEFAULT_FROM_EMAIL or "groceryguideai@gmail.com",
            recipient_list=[order.email],
            fail_silently=True,
        )
    except Exception as e:
        print(f"Error sending order confirmation email: {e}")
@login_required(login_url='login')
def checkout(request):
    user = request.user
    cart = get_user_cart(request)
    cart_items = CartItem.objects.filter(cart=cart).select_related('product')
    
    if not cart_items.exists():
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return JsonResponse({'status': 'error', 'message': 'Your cart is empty.'}, status=400)
        messages.warning(request, "Your cart is empty.")
        return redirect('cart_page')

    subtotal = sum((item.total_price for item in cart_items), Decimal('0.00'))
    discount = Decimal('0.00')
    shipping_fee = Decimal('0.00')

    # Exactly the pricing and shipping logic requested
    if not cart_items:
        shipping_fee = Decimal('0.00')
    elif subtotal >= Decimal('999.00'):
        shipping_fee = Decimal('0.00')
        discount = (subtotal * Decimal('0.20')).quantize(Decimal('0.01'))
    elif subtotal >= Decimal('499.00'):
        shipping_fee = Decimal('0.00')
        discount = (subtotal * Decimal('0.10')).quantize(Decimal('0.01'))
    elif subtotal >= Decimal('100.00'):
        shipping_fee = Decimal('0.00')
        discount = (subtotal * Decimal('0.05')).quantize(Decimal('0.01'))
    else:
        shipping_fee = Decimal('30.00')
        
    total = (subtotal - discount + shipping_fee).quantize(Decimal('0.01'))

    saved_addresses = Address.objects.filter(user=user).order_by('-is_default', '-id')
    default_address = saved_addresses.first()

    if request.method == 'POST':
        first_name = request.POST.get('first_name', user.first_name).strip()
        last_name = request.POST.get('last_name', user.last_name).strip()
        email = request.POST.get('email', user.email).strip()
        phone = request.POST.get('phone', '').strip()
        street_address = request.POST.get('address', '').strip()
        landmark = request.POST.get('landmark', '').strip()
        city = request.POST.get('city', '').strip()
        state = request.POST.get('state', '').strip()
        pincode = request.POST.get('pincode', '').strip()
        country = request.POST.get('country', 'India').strip()
        payment_method = request.POST.get('payment_method', 'COD').strip()

        full_address_str = f"{street_address}{', ' + landmark if landmark else ''}, {city}, {state} - {pincode}"
        
        if street_address and city and state and pincode:
            Address.objects.update_or_create(
                user=user,
                email=email,
                defaults={
                    'first_name': first_name,
                    'last_name': last_name,
                    'phone_number': phone,
                    'street_address': street_address,
                    'landmark': landmark,
                    'city': city,
                    'state': state,
                    'pin_code': pincode,
                    'country': country,
                }
            )
        items_snapshot = list(cart_items)

        if payment_method.upper() == 'RAZORPAY':
            client = razorpay.Client(auth=(settings.RAZORPAY_KEY_ID, settings.RAZORPAY_KEY_SECRET))
            amount_in_paise = int(total * 100)

            razorpay_order = client.order.create({
                'amount': amount_in_paise,
                'currency': 'INR',
                'payment_capture': 1,
            })

            order = Order.objects.create(
                user=user,
                first_name=first_name,
                last_name=last_name,
                address=full_address_str,
                phone=phone,
                email=email,
                total_price=total,
                status='Awaiting Payment',
                payment_method='RAZORPAY',
                razorpay_order_id=razorpay_order['id'],
            )
            

            for item in cart_items:
                OrderItem.objects.create(
                    order=order,
                    product=item.product,
                    price=item.product.price,
                    quantity=item.quantity,
                )
            threading.Thread(target=send_order_confirmation_email, args=(order.id,), daemon=True).start()
            return JsonResponse({
                'status': 'created',
                'razorpay_key_id': settings.RAZORPAY_KEY_ID,
                'razorpay_order_id': razorpay_order['id'],
                'amount': amount_in_paise,
                'currency': 'INR',
                'order_id': order.id,
                'customer_name': f"{first_name} {last_name}".strip(),
                'customer_email': email,
                'customer_phone': phone,
            })

        order = Order.objects.create(
            user=user,
            first_name=first_name,
            last_name=last_name,
            address=full_address_str,
            phone=phone,
            email=email,
            total_price=total,
            status='Pending',
            payment_method='COD'
        )
        
        assigned = assign_delivery_person(order)
        if assigned:
            messages.info(request, "Delivery partner automatically assigned to your bundled order!")

        for item in cart_items:
            OrderItem.objects.create(
                order=order,
                product=item.product,
                price=item.product.price,
                quantity=item.quantity
            )

        cart_items.delete()
        threading.Thread(target=send_order_confirmation_email, args=(order.id,), daemon=True).start()
        messages.success(request, f"Bundled Order #{order.id} placed successfully with Cash on Delivery!")
        return JsonResponse({'status': 'success', 'redirect_url': '/orders/'})

    context = {
        'cart_items': cart_items,
        'subtotal': subtotal.quantize(Decimal('0.01')),
        'discount': discount,
        'shipping_fee': shipping_fee,
        'delivery_charge': shipping_fee,
        'total': total,
        'grand_total': total,
        'cart_count': get_cart_count(request),
        'saved_addresses': saved_addresses,
        'default_address': default_address,
        'state_choices': getattr(Address, 'STATE_CHOICES', []),
    }
    return render(request, 'checkout.html', context)
@delivery_required
def toggle_attendance_view(request):
    profile = request.user.delivery_profile
    profile.is_attended_today = not profile.is_attended_today
    profile.save()
    
    if profile.is_attended_today:
        unassigned_order = Order.objects.filter(status='Pending', delivery_person__isnull=True).first()
        if unassigned_order:
            assign_delivery_person(unassigned_order)

    status_label = "Present & Active" if profile.is_attended_today else "Off Duty"
    messages.info(request, f"Attendance status updated: {status_label}")
    return redirect('delivery_dashboard')
@csrf_exempt
def ai_chat_api_endpoint(request):
    """API endpoint for AI Guide assistant."""
    if request.method != 'POST':
        return JsonResponse({'error': 'Invalid request method'}, status=400)
    
    try:
        data = json.loads(request.body)
        user_message = data.get('message', '').strip()
        
        if not user_message:
            return JsonResponse({'reply': 'Please type a message.'})

        # Get history from session
        history = request.session.get('ai_chat_history', [])

        response_data = get_ai_shopping_assistant(
            user_prompt=user_message, 
            chat_history=history, 
            user=request.user
        )
        reply_text = response_data.get('reply')

        # Append turn to history
        history.append({"role": "user", "parts": [{"text": user_message}]})
        history.append({"role": "model", "parts": [{"text": reply_text}]})
        request.session['ai_chat_history'] = history[-10:]

        return JsonResponse({'reply': reply_text})
        
    except Exception as e:
        return JsonResponse({'reply': f'An error occurred: {str(e)}'}, status=500)
@csrf_exempt
def payment_verify(request):
    if request.method != 'POST':
        return JsonResponse({'status': 'invalid_method'}, status=405)

    try:
        data = json.loads(request.body)
        razorpay_order_id = data.get('razorpay_order_id')
        razorpay_payment_id = data.get('razorpay_payment_id')
        razorpay_signature = data.get('razorpay_signature')

        if not razorpay_order_id or not razorpay_payment_id:
            return JsonResponse({'status': 'failed', 'message': 'Missing payment credentials.'}, status=400)

        client = razorpay.Client(auth=(settings.RAZORPAY_KEY_ID, settings.RAZORPAY_KEY_SECRET))

        client.utility.verify_payment_signature({
            'razorpay_order_id': razorpay_order_id,
            'razorpay_payment_id': razorpay_payment_id,
            'razorpay_signature': razorpay_signature,
        })

        order = Order.objects.get(razorpay_order_id=razorpay_order_id)
        
        # 1. Update payment details and set status to 'Pending' so it looks like a confirmed COD order
        order.payment_status = 'Paid'
        order.status = 'Pending'
        order.razorpay_payment_id = razorpay_payment_id
        order.razorpay_signature = razorpay_signature
        order.save()

        # 2. Automatically assign a delivery partner (just like COD orders)
        assigned = assign_delivery_person(order)
        if assigned:
            print(f"Delivery partner automatically assigned to online Order #{order.id}!")

        # 3. Clear user cart items securely upon successful payment
        cart = Cart.objects.filter(user=order.user).first()
        if cart:
            CartItem.objects.filter(cart=cart).delete()

        messages.success(request, f"Payment successful! Order #{order.id} confirmed.")
        return JsonResponse({'status': 'success', 'redirect_url': '/orders/'})

    except Exception as e:
        Order.objects.filter(razorpay_order_id=razorpay_order_id).update(payment_status='Failed')
        return JsonResponse({'status': 'failed', 'message': str(e)}, status=400)


@login_required(login_url='login')
def profile(request):
    if not request.user.is_authenticated:
        messages.error(request, "Please log in to access your profile.")
        return redirect('login')

    user = request.user
    pocket, _ = UserPocket.objects.get_or_create(user=user)

    if request.method == 'POST':
        form_type = request.POST.get('form_type')

        if form_type == 'profile':
            user.first_name = request.POST.get('first_name', '').strip()
            user.last_name = request.POST.get('last_name', '').strip()
            new_username = request.POST.get('username', '').strip()
            user.email = request.POST.get('email', '').strip()

            if new_username and new_username != user.username:
                user.username = new_username

            user.save()
            messages.success(request, "Profile updated successfully.")
            return redirect('profile')

        elif form_type == 'address':
            full_name = request.POST.get('full_name', '').strip()
            name_parts = full_name.split(' ', 1)
            first_name = name_parts[0] if name_parts else ''
            last_name = name_parts[1] if len(name_parts) > 1 else ''

            has_existing = Address.objects.filter(user=user).exists()

            Address.objects.create(
                user=user,
                first_name=first_name,
                last_name=last_name,
                email=user.email or None,
                phone_number=request.POST.get('phone', '').strip(),
                street_address=request.POST.get('address', '').strip(),
                landmark=request.POST.get('landmark', '').strip() or None,
                city=request.POST.get('city', '').strip(),
                state=request.POST.get('state', '').strip(),
                pin_code=request.POST.get('pincode', '').strip(),
                country=request.POST.get('country', 'India').strip() or 'India',
                is_default=not has_existing,
            )
            messages.success(request, "New address added successfully.")
            return redirect('profile')

    addresses = Address.objects.filter(user=user).order_by('-is_default', '-id')
    
    # Calculate live counts for the profile card
    total_orders = Order.objects.filter(user=user).count()
    wishlist_count = Wishlist.objects.filter(user=user).count()

    context = {
        'user': user,
        'addresses': addresses,
        'pocket': pocket,
        'total_orders': total_orders,        
        'wishlist_count': wishlist_count,    
        'cart_count': get_cart_count(request),
        'state_choices': Address.STATE_CHOICES,
    }
    return render(request, 'profile.html', context)

def checkout_process(request):
    pass
