"""Server-side level readiness checks, separate from future gameplay rules."""

from cyberpunk_warehouse.services.modes import validate_mode_configuration


def level_publish_errors(level):
    errors = []
    if not isinstance(level.name, str) or not level.name.strip():
        errors.append("A level name is required.")
    if isinstance(level.level_number, bool) or not isinstance(level.level_number, int) or level.level_number < 1:
        errors.append("Level number must be a positive integer.")
    if level.game_mode is None or not level.game_mode.is_active:
        errors.append("An active game mode must be assigned.")
    else:
        errors.extend(validate_mode_configuration(
            level.game_mode.key,
            level.mode_configuration,
            time_limit_seconds=level.time_limit_seconds,
        ))

    if not isinstance(level.order_configuration, dict):
        errors.append("Order configuration must be an object.")
    else:
        order_count = level.order_configuration.get("order_count")
        if order_count is not None and (
            isinstance(order_count, bool) or not isinstance(order_count, int) or order_count < 1
        ):
            errors.append("order_count must be a positive integer when supplied.")

    assigned_product_ids = {product.id for product in level.products if product.id is not None}
    if not assigned_product_ids:
        errors.append("At least one active product must be assigned to the level before publishing.")
    if not level.orders:
        errors.append("At least one order with products is required before publishing.")
    for order in level.orders:
        if not order.items:
            errors.append(f"Order {order.display_order} must contain at least one product.")
        effective_customer = order.customer or level.customer
        if effective_customer is None or not effective_customer.is_active:
            errors.append(f"Order {order.display_order} requires an active customer.")
        for item in order.items:
            if item.product is None or not item.product.is_active:
                errors.append(f"Order {order.display_order} contains an inactive or missing product.")
            elif item.product.id not in assigned_product_ids:
                errors.append(f"Order {order.display_order} requests a product not assigned to the level.")
            if isinstance(item.quantity, bool) or not isinstance(item.quantity, int) or item.quantity <= 0 or item.quantity % 3:
                errors.append(f"Order {order.display_order} quantities must be positive multiples of three.")
    return errors


def validate_level_for_publish(level):
    errors = level_publish_errors(level)
    if errors:
        raise ValueError(" ".join(errors))
    return True
