from django import forms
from django.contrib.auth.models import User
from django.contrib.auth.password_validation import validate_password

from .models import ParentAccount


class ParentRegistrationForm(forms.Form):
    first_name = forms.CharField(
        max_length=150,
        widget=forms.TextInput(attrs={'class': 'form-control'}),
    )
    last_name = forms.CharField(
        max_length=150,
        widget=forms.TextInput(attrs={'class': 'form-control'}),
    )
    email = forms.EmailField(
        widget=forms.EmailInput(attrs={'class': 'form-control'}),
    )
    phone_number = forms.CharField(
        max_length=20,
        widget=forms.TextInput(attrs={'class': 'form-control'}),
    )
    title = forms.ChoiceField(
        choices=ParentAccount.TITLE_CHOICES,
        widget=forms.Select(attrs={'class': 'form-control'}),
    )
    gender = forms.ChoiceField(
        choices=ParentAccount.GENDER_CHOICES,
        widget=forms.Select(attrs={'class': 'form-control'}),
    )
    date_of_birth = forms.DateField(
        widget=forms.DateInput(attrs={'class': 'form-control', 'type': 'date'}),
    )
    bvn = forms.CharField(
        max_length=11,
        min_length=11,
        widget=forms.TextInput(attrs={'class': 'form-control', 'inputmode': 'numeric'}),
        help_text='Your 11-digit Bank Verification Number.',
    )
    address = forms.CharField(
        max_length=255,
        widget=forms.TextInput(attrs={'class': 'form-control'}),
    )
    state = forms.CharField(
        max_length=50,
        widget=forms.TextInput(attrs={'class': 'form-control'}),
    )
    password1 = forms.CharField(
        label='Password',
        widget=forms.PasswordInput(attrs={'class': 'form-control'}),
    )
    password2 = forms.CharField(
        label='Confirm Password',
        widget=forms.PasswordInput(attrs={'class': 'form-control'}),
    )

    def clean_email(self):
        email = self.cleaned_data['email'].strip().lower()
        if User.objects.filter(email__iexact=email).exists():
            raise forms.ValidationError('An account with this email already exists.')
        return email

    def clean_bvn(self):
        bvn = self.cleaned_data['bvn'].strip()
        if not bvn.isdigit() or len(bvn) != 11:
            raise forms.ValidationError('BVN must be exactly 11 digits.')
        return bvn

    def clean_password1(self):
        password1 = self.cleaned_data.get('password1')
        if password1:
            validate_password(password1)
        return password1

    def clean(self):
        cleaned_data = super().clean()
        password1 = cleaned_data.get('password1')
        password2 = cleaned_data.get('password2')
        if password1 and password2 and password1 != password2:
            self.add_error('password2', 'Passwords do not match.')
        return cleaned_data


class AddChildForm(forms.Form):
    admission_number = forms.CharField(
        max_length=20,
        widget=forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'e.g. GIIA/2024/0123'}),
        label="Student's Admission Number",
    )
