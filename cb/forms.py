from django import forms
from django.contrib.auth.models import User
from django.conf import settings
import requests

class UsernameUpdateForm(forms.ModelForm):
    class Meta:
        model = User
        fields = ['username']

    def clean_username(self):
        username = self.cleaned_data['username']
        if User.objects.filter(username=username).exists():
            raise forms.ValidationError("Username already taken")
        return username

    def clean(self):
        cleaned_data = super().clean()

        token = self.data.get("g-recaptcha-response")
        if not token:
            raise forms.ValidationError("Please complete the reCAPTCHA")

        response = requests.post(
            "https://www.google.com/recaptcha/api/siteverify",
            data={
                "secret": settings.RECAPTCHA_SECRET_KEY,
                "response": token
            }
        )

        if not response.json().get("success"):
            raise forms.ValidationError("Invalid reCAPTCHA")

        return cleaned_data
