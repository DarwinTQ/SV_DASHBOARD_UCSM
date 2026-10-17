@echo off
REM ==========================================================================
REM  Arranca el broker Mosquitto con la configuracion del proyecto (UCSM).
REM  Ejecutar como ADMINISTRADOR la primera vez (para la regla de firewall).
REM
REM  Requisito previo: instalar Mosquitto desde https://mosquitto.org/download/
REM  (instalador de Windows x64). Por defecto queda en:
REM     C:\Program Files\mosquitto\
REM ==========================================================================
setlocal

set MOSQUITTO_DIR=C:\Program Files\mosquitto
set PROYECTO=D:\Tesis_2026\SV_INTERFAZ_UCSM
set CONF=%PROYECTO%\infra\mosquitto\mosquitto.conf
set PASSWD=%PROYECTO%\infra\mosquitto\passwd

if not exist "%MOSQUITTO_DIR%\mosquitto.exe" (
  echo [ERROR] No se encontro mosquitto.exe en "%MOSQUITTO_DIR%".
  echo         Instalalo desde https://mosquitto.org/download/ o edita MOSQUITTO_DIR en este .bat.
  pause
  exit /b 1
)

REM ----- 1) Crear el archivo de contrasenas la primera vez -------------------
if not exist "%PASSWD%" (
  echo [AVISO] No existe "%PASSWD%". Creando los tres usuarios del sistema...
  echo         Las claves por defecto son de DESARROLLO: cambialas en produccion
  echo         y refleja el cambio en .env ^(MQTT_CLAVE^) y en el bloque cfg del firmware.
  "%MOSQUITTO_DIR%\mosquitto_passwd.exe" -c -b "%PASSWD%" servidor ucsm2026servidor
  "%MOSQUITTO_DIR%\mosquitto_passwd.exe" -b    "%PASSWD%" nodo01   ucsm2026nodo
  "%MOSQUITTO_DIR%\mosquitto_passwd.exe" -b    "%PASSWD%" cam01    ucsm2026cam
  echo [OK] Usuarios creados: servidor, nodo01, cam01.
)

REM ----- 2) Regla de firewall para el puerto 1883 (una sola vez) -------------
netsh advfirewall firewall show rule name="MQTT 1883 UCSM" >nul 2>&1
if errorlevel 1 (
  echo [FIREWALL] Abriendo el puerto 1883/TCP en la red privada...
  netsh advfirewall firewall add rule name="MQTT 1883 UCSM" dir=in action=allow protocol=TCP localport=1883 profile=private >nul
  if errorlevel 1 (
    echo [AVISO] No se pudo crear la regla de firewall: ejecuta este .bat como Administrador.
  ) else (
    echo [OK] Regla de firewall creada.
  )
)

REM ----- 3) Detener el servicio de Windows si estuviera corriendo ------------
REM  (ocuparia el puerto 1883 con su propia configuracion por defecto)
sc query mosquitto | find "RUNNING" >nul 2>&1
if not errorlevel 1 (
  echo [AVISO] El servicio de Windows "mosquitto" esta corriendo; se detiene para
  echo         usar la configuracion del proyecto.
  net stop mosquitto >nul 2>&1
)

REM ----- 4) Arrancar en primer plano con log detallado ----------------------
echo.
echo ==========================================================================
echo  Mosquitto  ^|  configuracion: %CONF%
echo  Toppicos  : ucsm/v1/^<CODIGO^>/...        Puerto: 1883
echo  Prueba    : mosquitto_sub -h 127.0.0.1 -u servidor -P ucsm2026servidor -t "ucsm/v1/#" -v
echo  Ctrl+C para detener.
echo ==========================================================================
echo.
"%MOSQUITTO_DIR%\mosquitto.exe" -c "%CONF%" -v

endlocal
