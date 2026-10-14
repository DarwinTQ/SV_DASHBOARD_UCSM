from django.contrib.auth.forms import AuthenticationForm
from django.contrib.auth.views import LoginView, LogoutView

from monitoreo.forms.base import FormularioBootstrapMixin


class FormularioLogin(FormularioBootstrapMixin, AuthenticationForm):
    pass


class AuthLoginController(LoginView):
    template_name = "monitoreo/auth/login.html"
    authentication_form = FormularioLogin
    redirect_authenticated_user = True


class AuthLogoutController(LogoutView):
    next_page = "monitoreo:login"
