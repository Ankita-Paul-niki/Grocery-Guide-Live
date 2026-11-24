from django.conf import settings
from django.core.mail import send_mail


def send_order_confirmation_email(order):
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

    try:
        send_mail(
            subject=subject,
            message=message,
            from_email=settings.DEFAULT_FROM_EMAIL or "groceryguideai@gmail.com",
            recipient_list=[order.email],
            fail_silently=True,
        )
    except Exception as e:
        print(f"Error sending order confirmation email: {e}")