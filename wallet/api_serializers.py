from rest_framework import serializers

from src.models import OtherFeeStructure, Payment, Session, Term

from .models import ParentStudentLink, Wallet, WalletPayment, WalletTransaction


class SchoolClassSerializer(serializers.Serializer):
    name = serializers.CharField()
    arm = serializers.CharField()


class StudentSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    admission_number = serializers.CharField()
    first_name = serializers.CharField()
    last_name = serializers.CharField()
    enrolled_class = serializers.SerializerMethodField()

    def get_enrolled_class(self, student):
        school_class = student.enrolled_class
        if not school_class:
            return None
        return {'name': school_class.name, 'arm': school_class.arm}


class ParentStudentLinkSerializer(serializers.ModelSerializer):
    student = StudentSerializer()

    class Meta:
        model = ParentStudentLink
        fields = ['id', 'student', 'added_at']


class WalletTransactionSerializer(serializers.ModelSerializer):
    class Meta:
        model = WalletTransaction
        fields = [
            'id', 'transaction_type', 'amount', 'balance_before',
            'balance_after', 'reference', 'narration', 'source',
            'status', 'created_at',
        ]


class WalletSerializer(serializers.ModelSerializer):
    class Meta:
        model = Wallet
        fields = ['id', 'balance', 'is_active']


class TermSerializer(serializers.ModelSerializer):
    class Meta:
        model = Term
        fields = ['id', 'name']


class SessionSerializer(serializers.ModelSerializer):
    terms = TermSerializer(source='term_set', many=True, read_only=True)

    class Meta:
        model = Session
        fields = ['id', 'name', 'current', 'terms']


class OtherFeeStructureSerializer(serializers.ModelSerializer):
    class Meta:
        model = OtherFeeStructure
        fields = [
            'id', 'name', 'amount', 'description',
            'multi_child_discount_percent', 'staff_discount_percent',
        ]


class FeeSelectionSerializer(serializers.Serializer):
    """
    Not model-backed - mirrors the dicts _build_school_fee_selections()/
    _build_other_fee_selections() build in wallet/views.py, which the API
    reuses as-is rather than recomputing this shape a second way.
    """
    student_id = serializers.IntegerField()
    student_name = serializers.CharField()
    amount = serializers.DecimalField(max_digits=12, decimal_places=2)
    label = serializers.CharField()

    @classmethod
    def from_selection(cls, item):
        student = item['student']
        if item.get('fee_structure') is not None:
            label = f"School Fees ({item['fee_structure'].get_student_type_display()})"
        elif item.get('other_fee') is not None:
            label = item['other_fee'].name
        else:
            label = 'Fee'
        return {
            'student_id': student.id,
            'student_name': f"{student.first_name} {student.last_name}",
            'amount': item['amount'],
            'label': label,
        }


class PaymentLineSerializer(serializers.ModelSerializer):
    student_name = serializers.SerializerMethodField()
    fee_label = serializers.SerializerMethodField()

    class Meta:
        model = Payment
        fields = ['id', 'student_name', 'fee_label', 'amount_paid', 'payment_date']

    def get_student_name(self, payment):
        return f"{payment.student.first_name} {payment.student.last_name}"

    def get_fee_label(self, payment):
        if payment.fee_structure_id:
            return str(payment.fee_structure)
        if payment.other_fee_id:
            return payment.other_fee.name
        return 'Fee'


class WalletPaymentSerializer(serializers.ModelSerializer):
    reference = serializers.CharField(source='wallet_transaction.reference')
    amount = serializers.DecimalField(source='wallet_transaction.amount', max_digits=12, decimal_places=2)
    created_at = serializers.DateTimeField(source='wallet_transaction.created_at')
    session = serializers.CharField(source='session.name')
    term = serializers.CharField(source='term.name')

    class Meta:
        model = WalletPayment
        fields = ['id', 'reference', 'amount', 'created_at', 'session', 'term']


class WalletPaymentDetailSerializer(WalletPaymentSerializer):
    payments = PaymentLineSerializer(many=True, read_only=True)

    class Meta(WalletPaymentSerializer.Meta):
        fields = WalletPaymentSerializer.Meta.fields + ['payments']
