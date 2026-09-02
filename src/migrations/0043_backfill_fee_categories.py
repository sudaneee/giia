# Data migration: assigns a best-guess category to every existing
# FeeComponent/OtherFeeStructure row based on its name, so the new item-type
# filter has something sensible to work with immediately instead of every
# row sitting under "Other". Purely a classification pass - no amounts,
# students, or payments are touched. Anything misclassified is a one-field
# fix in Django admin, not a data-integrity issue, since PaymentLineItem
# snapshots the category at payment time rather than joining live.
#
# Logic is intentionally duplicated here (not imported from wallet/src code)
# per Django's migration guidance: migrations must keep working even if the
# real implementation changes or is removed later.
from django.db import migrations


def _category_for_component_name(name):
    name = (name or "").strip().lower()
    if "tuition" in name:
        return "tuition"
    if "learning material" in name:
        return "learning_materials"
    if "feeding" in name:
        return "feeding"
    if "uniform" in name:
        return "uniform"
    if "ta fee" in name:
        return "ta_fees"
    if "transport" in name:
        return "transportation"
    return "other"


def _category_for_other_fee_name(name):
    name = (name or "").strip().lower()
    has_transport = "transport" in name
    has_tahfeez = "tahfeez" in name
    if has_transport and has_tahfeez:
        return "transportation"
    if has_tahfeez:
        return "tahfeez"
    if has_transport:
        return "transportation"
    if "cardigan" in name or "uniform" in name:
        return "uniform"
    return "other"


def backfill_categories(apps, schema_editor):
    FeeComponent = apps.get_model("src", "FeeComponent")
    OtherFeeStructure = apps.get_model("src", "OtherFeeStructure")

    for component in FeeComponent.objects.all():
        category = _category_for_component_name(component.name)
        if category != component.category:
            component.category = category
            component.save(update_fields=["category"])

    for other_fee in OtherFeeStructure.objects.all():
        category = _category_for_other_fee_name(other_fee.name)
        if category != other_fee.category:
            other_fee.category = category
            other_fee.save(update_fields=["category"])


def noop_reverse(apps, schema_editor):
    # Intentionally not reverting categories back to "other" - reversing this
    # migration shouldn't destroy a classification an admin may have since
    # hand-corrected in Django admin.
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('src', '0042_payment_line_item_and_categories'),
    ]

    operations = [
        migrations.RunPython(backfill_categories, noop_reverse),
    ]
