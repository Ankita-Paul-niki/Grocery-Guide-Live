# core/ai_services.py
from google import genai
from google.genai import types # type: ignore
from django.conf import settings
from core.models import Product, Cart, CartItem, Address, Order, OrderItem, Recipe, RecipeIngredient
import json
from decimal import Decimal, ROUND_CEILING
from core.utils import send_order_confirmation_email
from decimal import Decimal
client = genai.Client(api_key=settings.GEMINI_API_KEY)

def get_ai_shopping_assistant(user_prompt: str, chat_history: list = None, user=None) -> dict:
    
    # 1. CATALOG SEARCH TOOL
    def search_catalog(keywords: str, max_price: float = None) -> str:
        """Search available products in the store catalog by keyword and optional maximum price."""
        qs = Product.objects.filter(is_active=True)
        if max_price:
            qs = qs.filter(price__lte=max_price)

        matched = qs.filter(title__icontains=keywords) | qs.filter(description__icontains=keywords)
        matched = matched.distinct()[:5]

        if not matched.exists():
            return json.dumps({"status": "no_results", "items": []})

        results = [{"id": item.id, "name": item.title, "price": float(item.price)} for item in matched]
        return json.dumps({"status": "found", "items": results})
    def calculate_order_pricing(products):
        subtotal = sum([Decimal(str(p.price)) for p in products])
        discount = Decimal('0.00')
        shipping_fee = Decimal('0.00')

        if subtotal >= Decimal('999.00'):
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

        total_price = subtotal - discount + shipping_fee
        return subtotal, discount, shipping_fee, total_price
    # 2. CHECK SAVED ADDRESSES TOOL
    def check_user_addresses() -> str:
        """Check if the user has saved delivery addresses. Returns list of addresses or empty status."""
        if not user or not user.is_authenticated:
            return json.dumps({"status": "unauthenticated"})
        
        addresses = Address.objects.filter(user=user)
        if not addresses.exists():
            return json.dumps({"status": "no_address"})
        
        addrs = [{
            "id": a.id, 
            "street": a.street_address, 
            "city": a.city, 
            "phone": a.phone_number, 
            "is_default": a.is_default
        } for a in addresses]
        return json.dumps({"status": "found", "count": len(addrs), "addresses": addrs})

    # 3. PLACE ORDER TOOL (Handles Cash on Delivery)
    def place_order(product_ids: list[int], address_id: int, payment_method: str) -> str:
        """Place an order for given product IDs using a specific address ID and payment method ('cod')."""
        if not user or not user.is_authenticated:
            return "Error: User must be logged in to place an order."
        try:
            address = Address.objects.get(id=address_id, user=user)
            products = Product.objects.filter(id__in=product_ids, is_active=True)
            
            if not products.exists():
                return "Failed: Selected products are invalid or out of stock."

            subtotal, discount, shipping_fee, total_price = calculate_order_pricing(products)

            order = Order.objects.create(
                user=user,
                first_name=user.first_name or user.username,
                last_name=user.last_name or '',
                address=f"{address.street_address}, {address.city} - {address.pin_code}",
                phone=address.phone_number,
                email=user.email,
                total_price=total_price,
                status='Pending',
                payment_method='cod'
            )

            for product in products:
                OrderItem.objects.create(order=order, product=product, price=product.price, quantity=1)
            send_order_confirmation_email(order)
            return f"Success! Order #{order.id} placed successfully via Cash on Delivery. Total amount: ₹{total_price}."
        except Address.DoesNotExist:
            return "Failed: Invalid address selected."
        except Exception as e:
            return f"Failed to place order: {str(e)}"

    # 4. INITIATE ONLINE PAYMENT & OTP
    # 4. INITIATE ONLINE PAYMENT & OTP
    def initiate_online_payment(payment_type: str, payment_handle: str) -> str:
        """Simulate sending an OTP for online payment (upi, card, netbanking, wallet)."""
        return json.dumps({
            "status": "otp_required", 
            "message": f"An OTP has been sent to your registered phone for {payment_type.upper()} ({payment_handle}). Please enter the 4-digit OTP to complete payment."
        })

    # 5. VERIFY OTP AND PLACE ONLINE PAID ORDER
    def verify_otp_and_place_order(product_ids: list[int], address_id: int, payment_method: str, otp_code: str) -> str:
        """Verify the OTP code and finalize the online-paid order."""
        if otp_code != "123456":  # Test verification OTP
            return "Invalid OTP!"
        
        try:
            address = Address.objects.get(id=address_id, user=user)
            products = Product.objects.filter(id__in=product_ids, is_active=True)
            subtotal, discount, shipping_fee, total_price = calculate_order_pricing(products)

            order = Order.objects.create(
                user=user,
                first_name=user.first_name or user.username,
                last_name=user.last_name or '',
                address=f"{address.street_address}, {address.city} - {address.pin_code}",
                phone=address.phone_number,
                email=user.email,
                total_price=total_price,
                status='Paid',
                payment_method=payment_method
            )

            for product in products:
                OrderItem.objects.create(order=order, product=product, price=product.price, quantity=1)
            send_order_confirmation_email(order)
            return f"Payment verified successfully! Order #{order.id} placed and confirmed via {payment_method.upper()}. Subtotal: ₹{subtotal}, Discount: -₹{discount:.2f}, Shipping: ₹{shipping_fee}, Total: ₹{total_price:.2f}."
        except Exception as e:
            return f"Payment verification failed: {str(e)}"
    def _recipe_pack_multiplier(req_qty: Decimal, recipe_unit: str, product: Product) -> Decimal:
        pkg_qty = Decimal(str(product.package_quantity)) if product.package_quantity else Decimal('1.00')
        if pkg_qty <= 0:
            pkg_qty = Decimal('1.00')

        pkg_unit = (product.package_unit or '').upper()
        rec_unit = (recipe_unit or '').lower()

        if pkg_unit == 'KG':
            qty_in_pkg_unit = (req_qty / Decimal('1000')) if rec_unit == 'g' else (req_qty if rec_unit == 'kg' else Decimal('0'))
        elif pkg_unit == 'G':
            qty_in_pkg_unit = req_qty if rec_unit == 'g' else ((req_qty * Decimal('1000')) if rec_unit == 'kg' else Decimal('0'))
        elif pkg_unit == 'L':
            qty_in_pkg_unit = (req_qty / Decimal('1000')) if rec_unit == 'ml' else (req_qty if rec_unit in ['l', 'litre', 'liter'] else Decimal('0'))
        elif pkg_unit == 'ML':
            qty_in_pkg_unit = req_qty if rec_unit == 'ml' else ((req_qty * Decimal('1000')) if rec_unit in ['l', 'litre', 'liter'] else Decimal('0'))
        else:
            qty_in_pkg_unit = req_qty if rec_unit == 'pcs' else Decimal('0')

        if pkg_unit in ['KG', 'G', 'L', 'ML'] and rec_unit in ['pcs', 'cup', 'tsp', 'tbsp', 'pinch', 'taste']:
            return Decimal('1')

        if qty_in_pkg_unit <= Decimal('0'):
            return Decimal('1')

        raw_packs = qty_in_pkg_unit / pkg_qty
        return max(Decimal('1'), raw_packs.to_integral_value(rounding=ROUND_CEILING))

    # 6B. BUDGET RECIPE SEARCH TOOL
    def search_recipes_by_budget(max_budget: float, min_budget: float = 0.0, cuisine_name: str = "") -> str:
        """Find recipes whose calculated ingredient cost fits strictly within [min_budget, max_budget] in INR."""
        max_dec = Decimal(str(max_budget))
        min_dec = Decimal(str(min_budget))

        recipes_qs = Recipe.objects.prefetch_related('recipeingredient_set__product', 'cuisine')
        if cuisine_name:
            recipes_qs = recipes_qs.filter(cuisine__name__icontains=cuisine_name.strip())

        in_budget = []
        for r in recipes_qs:
            ing_qs = RecipeIngredient.objects.filter(recipe=r).select_related('product')
            if not ing_qs.exists():
                continue

            total = Decimal('0.00')
            ing_summary = []
            for ri in ing_qs:
                prod = ri.product
                mult = _recipe_pack_multiplier(Decimal(str(ri.required_quantity)), ri.unit_measure, prod)
                line_cost = (Decimal(str(prod.price)) * mult).quantize(Decimal('0.01'))
                total += line_cost
                ing_summary.append({
                    "id": prod.id,
                    "name": prod.title,
                    "packs_needed": int(mult),
                    "cost": float(line_cost),
                    "in_stock": prod.stock > 0,
                })

            total = total.quantize(Decimal('0.01'))
            if min_dec <= total <= max_dec:
                in_budget.append({
                    "id": r.id,
                    "title": r.title,
                    "cuisine": r.cuisine.name if r.cuisine else "",
                    "estimated_total": float(total),
                    "ingredients": ing_summary,
                })

        in_budget.sort(key=lambda x: x["estimated_total"])
        if not in_budget:
            return json.dumps({
                "status": "no_recipes_in_budget",
                "min_budget": float(min_dec),
                "max_budget": float(max_dec),
                "recipes": [],
            })

        return json.dumps({
            "status": "found",
            "min_budget": float(min_dec),
            "max_budget": float(max_dec),
            "recipes": in_budget,
        })
    # 6. RECIPE & INGREDIENT STOCK CHECK TOOL
    def check_recipe_ingredients(recipe_name: str) -> str:
        """Check recipe availability, matching ingredients against live database stock."""
        recipes = Recipe.objects.filter(title__icontains=recipe_name)
        if not recipes.exists():
            return json.dumps({"status": "not_found", "message": "Recipe not found."})
        
        recipe = recipes.first()
        ingredients = RecipeIngredient.objects.filter(recipe=recipe).select_related('product')
        
        available = []
        missing = []
        
        for ri in ingredients:
            prod = ri.product
            if prod.stock > 0:
                available.append({"id": prod.id, "name": prod.title, "price": float(prod.price), "qty": float(ri.required_quantity)})
            else:
                missing.append(prod.title)
        
        return json.dumps({
            "status": "success",
            "recipe_title": recipe.title,
            "description": recipe.description,
            "image_url": recipe.image.url if recipe.image else "",
            "available_ingredients": available,
            "missing_ingredients": missing
        })

    # 7. ADD ITEMS TO CART
    def add_items_to_cart(product_ids: list[int]) -> str:
        """Add specified product IDs to the user's shopping cart."""
        if not user or not user.is_authenticated:
            return "User must be logged in."
            
        cart, _ = Cart.objects.get_or_create(user=user)
        added = []
        for pid in product_ids:
            try:
                product = Product.objects.get(id=pid, is_active=True)
                cart_item, created = CartItem.objects.get_or_create(cart=cart, product=product, defaults={'quantity': 1})
                if not created:
                    cart_item.quantity += 1
                    cart_item.save()
                added.append(product.title)
            except Product.DoesNotExist:
                continue
        return f"Added {len(added)} items to cart: {', '.join(added)}"

    system_instruction = """
    You are an intelligent e-commerce shopping and recipe assistant named AI Guide. You must guide the user strictly step-by-step and NEVER skip steps.
    
    1. **Direct Ordering & Address Workflow (Mandatory Sequence)**:
       - **Step 1 (Search)**: When the user asks for items (like pizza), call `search_catalog`, display the matching items, and ask if they want to order.
       - **Step 2 (Address Check)**: If the user confirms or says yes, **STOP and call `check_user_addresses` immediately**. Do NOT place the order yet.
       - **Step 3 (Address Confirmation)**: Present the retrieved address details (street, city, phone) to the user. Ask: "Do you want to deliver to this address, or would you like to use a different one?" Wait for their confirmation.
       - **Step 4 (Payment & Execution)**: 
         - If the user specifies Cash on Delivery (COD), call `place_order` using the confirmed address ID and 'cod'.
         - If the user specifies UPI or another online payment method, call `initiate_online_payment`, ask them to enter the 4-digit OTP, and upon receiving it call `verify_otp_and_place_order`.

    2. **Recipe & Pantry Budget Workflow**:
       - Whenever the user asks for recipes within a budget (e.g., "under ₹50", "50 rupees budget", "1-50"), **ALWAYS call `search_recipes_by_budget`** first and ONLY show recipes returned by that tool whose `estimated_total` fits the budget. Never guess or include recipes above the budget.
       - When asked for a specific named recipe, call `check_recipe_ingredients`. Display details and image.
       - If ingredients are available, ask if they have any at home. Filter those out and ask to add the rest via `add_items_to_cart`."""

    # Model fallback rotation restricted to Gemini 3.8 Flash and Gemini 3.5 Flash-Lite
    models_to_try = ['gemini-3.8-flash', 'gemini-3.5-flash-lite']
    response = None
    last_error = None

    for model_name in models_to_try:
        try:
            chat = client.chats.create(
                model=model_name,
                history=chat_history or [],
                config=types.GenerateContentConfig(
                    system_instruction=system_instruction,
                    tools=[
                        search_catalog, 
                        check_user_addresses, 
                        place_order, 
                        initiate_online_payment, 
                        verify_otp_and_place_order, 
                        check_recipe_ingredients, 
                        search_recipes_by_budget,
                        add_items_to_cart
                    ],
                    temperature=0.4,
                )
            )
            response = chat.send_message(user_prompt)
            break
        except Exception as e:
            last_error = e
            continue

    if response is None:
        raise last_error

    return {"reply": response.text}