from decimal import Decimal, ROUND_HALF_UP
from rest_framework.response import Response

# Convert PKR amount to USD cents for Stripe integration.
def convert_pkr_to_usd_cents(pkr_amount, exchange_rate=278.0):
    if pkr_amount is None:
        return 0
    usd_amount = Decimal(str(pkr_amount)) / Decimal(str(exchange_rate))
    return int((usd_amount * Decimal("100")).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


# Convert USD cents back to PKR for local storage and reporting.
def convert_usd_cents_to_pkr(usd_cents, exchange_rate=278.0):
    if usd_cents is None:
        return Decimal("0.00")
    usd_amount = Decimal(str(usd_cents)) / Decimal("100")
    pkr_amount = usd_amount * Decimal(str(exchange_rate))
    return pkr_amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

# Build a standardized API response payload.
def api_response(status, msg, data=None, http_status=200):
    response_body = {
        'status': status,
        'msg': msg,
        'data': data or {},
    }
    return Response(response_body, status=http_status)


# Return a fully qualified URL for a stored media path.
def build_absolute_uri(request, path):
    if not path:
        return None
    return request.build_absolute_uri(path)