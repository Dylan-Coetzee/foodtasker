import json

from django.http import JsonResponse
from oauth2_provider.models import AccessToken
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt

from foodtaskerapp.models import Restaurant, Meal, Order, OrderDetail, Driver
from foodtaskerapp.serializers import RestaurantSerializer, MealSerializer, OrderSerializer

import stripe
from foodtasker.settings import STRIPE_API_KEY

##################
# Customer
##################

def customer_get_restaurants(request):
    restaurants = RestaurantSerializer(
        Restaurant.objects.all().order_by("-id"),
        many = True,
        context = {"request": request}
    ).data

    return JsonResponse({"restaurants": restaurants})

def customer_get_meals(request, restaurant_id):
    meals = MealSerializer(
        Meal.objects.filter(restaurant_id = restaurant_id).order_by("-id"),
        many = True,
        context = {"request": request}
    ).data

    return JsonResponse({"meals": meals})

@csrf_exempt
def customer_add_order(request):

    if request.method == "POST":
        #Get Token
        access_token = AccessToken.objects.get(token = request.POST.get("access_token"),
            expires__gt = timezone.now())

        #Get Profile
        customer = access_token.user.customer

        #Get Stripe token
        stripe_token = request.POST['stripe_token']

        #Check whether the customer has any other order that is not DELIVERED
        ## TODO: probably will change with split
        if Order.objects.filter(customer = customer).exclude(status = Order.DELIVERED):
            return JsonResponse({"status": "fail", "error": "Your last order must be completed."})

        # Check Address
        if not request.POST["address"]:
            return JsonResponse({"status": "failed", "error": "Address is required."})

        order_details = json.loads(request.POST["order_details"])

        order_total = 0

        for meal in order_details:
            order_total += Meal.objects.get(id = meal["meal_id"]).price * meal["quantity"]

        if len(order_details) > 0:

            stripe.api_key = STRIPE_API_KEY
            stripe.verify_ssl_certs = False

            #Create a charge: this will charge the customers card
            charge =  stripe.Charge.create(
                amount = order_total * 100, # Ammount in cents
                currency = 'usd',
                source = stripe_token,
                description = "FoodTasker Order"
            )

            if charge.status != "failed":
                #Step 1 - Create Orders
                order = Order.objects.create(
                    customer = customer,
                    restaurant_id = request.POST["restaurant_id"],
                    total = order_total,
                    status = Order.COOKING,
                    address = request.POST["address"]
                )

                #Step 2 - Create Order order_details
                for meal in order_details:
                    OrderDetail.objects.create(
                        order = order,
                        meal_id = meal["meal_id"],
                        quantity = meal["quantity"],
                        sub_total = Meal.objects.get(id = meal["meal_id"]).price * meal["quantity"]
                    )

                return JsonResponse({"status": "success"})
            else:
                return JsonResponse({"status": "failed", "error": "Could not connect to stripe"})


def customer_get_latest_order(request):
    #Get Token
    access_token = AccessToken.objects.get(token = request.GET.get("access_token"),
        expires__gt = timezone.now())

    customer = access_token.user.customer
    order = OrderSerializer(Order.objects.filter(customer = customer).last()).data
    
    return JsonResponse({ "order": order })

def customer_driver_location(request):
    #Get Token
    access_token = AccessToken.objects.get(token = request.GET.get("access_token"),
        expires__gt = timezone.now())

    #Get Customer
    customer = access_token.user.customer

    #Driver location relate to customer order
    current_order = Order.objects.filter(customer = customer, status = Order.ONTHEWAY).last()
    location = current_order.driver.location

    return JsonResponse({"location": location})

##################
# Restaurant
##################

def restaurant_order_notification(request, last_request_time):
    notification = Order.objects.filter(restaurant = request.user.restaurant,
        created_at__gt = last_request_time).count()
    return JsonResponse({ "notification": notification })

##################
# Drivers
##################

def driver_get_ready_orders(request):
    orders = OrderSerializer(
        Order.objects.filter(status = Order.READY, driver = None).order_by("-id"),
        many = True
    ).data

    return JsonResponse({"orders": orders})

@csrf_exempt
def driver_pick_order(request):

    if request.method == "POST":
        #Get Token
        access_token = AccessToken.objects.get(token = request.POST.get("access_token"),
            expires__gt = timezone.now())

        #Get Driver
        driver = access_token.user.driver

        #Check if driver can only up one order at the same timezone
        if Order.objects.filter(driver = driver).exclude(status = Order.ONTHEWAY):
            return JsonResponse({"status": "failed", "error": "You can only pick order at the same time."})

        try:
            order = Order.objects.get(
                id = request.POST["order_id"],
                driver = None,
                status = Order.READY
            )
            order.driver = driver
            order.status = Order.ONTHEWAY
            order.picked_at = timezone.now()
            order.save()

            return JsonResponse({"status": "success"})

        except Order.DoesNotExist:
            return JsonResponse({"status": "failed", "error": "This order has been picked up by another driver."})

    return JsonResponse({ })

#Get Request
#Params: access_token
def driver_get_latest_order(request):
    #Get Token
    access_token = AccessToken.objects.get(token = request.GET.get("access_token"),
        expires__gt = timezone.now())

    #Get Driver
    driver = access_token.user.driver
    order = OrderSerializer(
        Order.objects.filter(driver = driver).order_by("picked_at").last(),
    ).data

    return JsonResponse({ "order": order })

#POST
@csrf_exempt
def driver_complete_order(request):
    access_token = AccessToken.objects.get(token = request.POST.get("access_token"),
        expires__gt = timezone.now())

    #Get Driver
    driver = access_token.user.driver
    order = Order.objects.get(id = request.POST["order_id"], driver = driver)
    order.status = Order.DELIVERED
    order.save()

    return JsonResponse({"status": "success"})

# Get params: access_token
def driver_get_revenue(request):
    access_token = AccessToken.objects.get(token = request.GET.get("access_token"),
        expires__gt = timezone.now())

    #Get Driver
    driver = access_token.user.driver

    from datetime import timedelta

    revenue = {}
    today = timezone.now()
    current_weekdays = [today + timedelta(days = i) for i in range(0 - today.weekday(), 7 - today.weekday())]

    for day in current_weekdays:
        orders = Order.objects.filter(
            driver = driver,
            status = Order.DELIVERED,
            created_at__year = day.year,
            created_at__month = day.month,
            created_at__day = day.day,
        )

        revenue[day.strftime("%a")] = sum(order.total for order in orders)

    return JsonResponse({"revenue": revenue})

#POST params access_token, lat, lng
@csrf_exempt
def driver_update_location(request):
    if request.method == "POST":
        #Get Token
        access_token = AccessToken.objects.get(token = request.POST.get("access_token"),
            expires__gt = timezone.now())

        #Get Driver
        driver = access_token.user.driver

        #Set location string => database
        driver.location = request.POST["location"]
        driver.save()

        return JsonResponse({"status": "success"})
