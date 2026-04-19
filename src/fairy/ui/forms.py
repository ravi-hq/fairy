from django import forms
from django.contrib.auth.forms import UserCreationForm
from django.contrib.auth.models import User


class RegisterForm(UserCreationForm):
    email = forms.EmailField(required=False)

    class Meta:
        model = User
        fields = ("username", "email")


class SpritesKeyForm(forms.Form):
    api_key = forms.CharField(
        widget=forms.PasswordInput(render_value=False),
        label="Sprites API token",
        help_text="Stored encrypted. Submitting replaces any existing token.",
    )


class APIKeyCreateForm(forms.Form):
    name = forms.CharField(max_length=100, label="Label")
    expires_at = forms.DateTimeField(
        required=False,
        label="Expires at (optional)",
        widget=forms.DateTimeInput(attrs={"type": "datetime-local"}),
    )
