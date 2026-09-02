from decimal import Decimal

from src.models import PaymentLineItem


def create_line_items_for_payment(payment):
    """
    Builds the PaymentLineItem row(s) for a just-created Payment, so it can
    be filtered/reported on by item type. Must be called inside the same
    transaction as the Payment.objects.create() that produced `payment`, so
    a payment can never end up with no line items (or vice versa).

    - fee_structure payments (bundled school fees): one line item per
      FeeComponent on that structure, with amount_paid allocated across them
      proportionally to each component's share of the components' total.
      Marked is_estimated=True since this is a computed split, not something
      the payer chose per item.
    - other_fee payments: exactly one line item mirroring it exactly.
      Marked is_estimated=False - it's a direct record, not an allocation.

    Safe to call more than once for the same payment (e.g. a retried
    webhook) - existing line items for this payment are cleared first.
    """
    payment.line_items.all().delete()

    if payment.fee_structure_id:
        _create_school_fee_line_items(payment)
    elif payment.other_fee_id:
        _create_other_fee_line_item(payment)
    # A Payment with neither set (shouldn't happen in practice) simply gets
    # no line items - it stays outside the item-type filter rather than
    # raising, since filtering is additive functionality, not something
    # payment creation should ever fail over.


def _create_school_fee_line_items(payment):
    components = list(payment.fee_structure.components.all().order_by("id"))
    if not components:
        return

    total_component_amount = sum((c.amount for c in components), Decimal("0.00"))
    if total_component_amount <= 0:
        return

    # Some callers (e.g. the manual payment_create/payment_update admin forms)
    # pass amount_paid straight from request.POST without converting it, so
    # payment.amount_paid can still be a plain str in memory at this point -
    # coerce defensively rather than assume every caller already normalized it.
    amount_paid = Decimal(str(payment.amount_paid))
    line_items = []
    allocated = Decimal("0.00")

    for index, component in enumerate(components):
        is_last = index == len(components) - 1
        if is_last:
            # The last component absorbs whatever's left, so the line items
            # always sum to exactly amount_paid - covers both rounding and
            # any drift between total_amount and the sum of components.
            share = amount_paid - allocated
        else:
            ratio = component.amount / total_component_amount
            share = (amount_paid * ratio).quantize(Decimal("0.01"))
            allocated += share

        line_items.append(PaymentLineItem(
            payment=payment,
            fee_component=component,
            other_fee=None,
            category=component.category,
            label=component.name,
            amount=share,
            is_estimated=True,
        ))

    PaymentLineItem.objects.bulk_create(line_items)


def _create_other_fee_line_item(payment):
    other_fee = payment.other_fee
    PaymentLineItem.objects.create(
        payment=payment,
        fee_component=None,
        other_fee=other_fee,
        category=other_fee.category,
        label=other_fee.name,
        amount=payment.amount_paid,
        is_estimated=False,
    )
