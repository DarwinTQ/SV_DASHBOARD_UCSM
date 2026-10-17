"""
Configuración de Django para el proyecto sv_ucsm.

Todos los valores se leen desde ``config.py`` (que a su vez lee ``.env``).
No definas aquí secretos ni rutas sueltas: agrégalas a config.py.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import config  # noqa: E402

BASE_DIR = config.BASE_DIR

SECRET_KEY = config.servidor.secret_key
DEBUG = config.servidor.debug
ALLOWED_HOSTS = config.servidor.allowed_hosts if config.servidor.allowed_hosts != ["*"] else ["*"]


INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "rest_framework",
    "channels",
    "monitoreo",
]

MIDDLEWARE = [
    "monitoreo.security.middleware.MarcaTiempoRecepcionMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

# WhiteNoise solo en producción: en desarrollo serviría la copia ya recolectada
# en staticfiles/ (su middleware corre antes del handler de staticfiles de
# Django) y mostraría CSS/JS desactualizado hasta volver a hacer collectstatic.
if not DEBUG:
    MIDDLEWARE.insert(2, "whitenoise.middleware.WhiteNoiseMiddleware")

ROOT_URLCONF = "sv_ucsm.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "monitoreo" / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "monitoreo.context_processors.contexto_global",
            ],
        },
    },
]

WSGI_APPLICATION = "sv_ucsm.wsgi.application"
ASGI_APPLICATION = "sv_ucsm.asgi.application"

CHANNEL_LAYERS = {
    "default": {
        "BACKEND": "channels.layers.InMemoryChannelLayer",
    },
}


# Base de datos
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": str(config.bd.ruta_sqlite),
    }
}


AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]


# Internacionalización
LANGUAGE_CODE = "es"
TIME_ZONE = config.servidor.zona_horaria
USE_I18N = True
USE_TZ = True


# Archivos estáticos y media
STATIC_URL = "static/"
STATICFILES_DIRS = [BASE_DIR / "static"]
STATIC_ROOT = BASE_DIR / "staticfiles"
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    # En desarrollo (DEBUG=True) se sirve directo desde static/ sin exigir
    # collectstatic; en producción WhiteNoise usa el manifiesto con hash + gzip.
    "staticfiles": {
        "BACKEND": (
            "django.contrib.staticfiles.storage.StaticFilesStorage" if DEBUG
            else "whitenoise.storage.CompressedManifestStaticFilesStorage"
        ),
    },
}

MEDIA_URL = "/media/"
MEDIA_ROOT = str(config.bd.media_root)

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

LOGIN_URL = "/login/"
LOGIN_REDIRECT_URL = "/"
LOGOUT_REDIRECT_URL = "/login/"

DATA_UPLOAD_MAX_MEMORY_SIZE = 10 * 1024 * 1024  # 10 MB (fotos del detalle/pruebas)

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [],
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.AllowAny"],
    "DEFAULT_RENDERER_CLASSES": ["rest_framework.renderers.JSONRenderer"],
    "EXCEPTION_HANDLER": "monitoreo.security.api_key.manejador_excepciones_api",
}

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "detallado": {
            "format": "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
            "datefmt": "%Y-%m-%d %H:%M:%S",
        },
    },
    "filters": {
        # Los cortes de conexión del navegador no son fallos: ver el docstring
        # de FiltroDesconexionCliente.
        "sin_desconexiones": {
            "()": "monitoreo.security.filtros_log.FiltroDesconexionCliente",
        },
    },
    "handlers": {
        "consola": {
            "class": "logging.StreamHandler",
            "formatter": "detallado",
        },
        "archivo": {
            "class": "logging.handlers.RotatingFileHandler",
            "filename": str(BASE_DIR / "logs" / "sistema.log"),
            "maxBytes": 5 * 1024 * 1024,
            "backupCount": 5,
            "formatter": "detallado",
            "encoding": "utf-8",
        },
    },
    "root": {
        "handlers": ["consola", "archivo"],
        "level": "INFO",
    },
    "loggers": {
        "django": {"handlers": ["consola", "archivo"], "level": "INFO", "propagate": False},
        "monitoreo": {"handlers": ["consola", "archivo"], "level": "DEBUG", "propagate": False},
        "asyncio": {
            "handlers": ["consola", "archivo"],
            "level": "WARNING",
            "propagate": False,
            "filters": ["sin_desconexiones"],
        },
    },
}
