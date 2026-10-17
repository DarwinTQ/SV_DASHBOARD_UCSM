@echo off
REM ==========================================================================
REM  Compila y ejecuta las pruebas de host de las reglas de deteccion.
REM  No necesita el ESP32-S3: solo un compilador de C++17 en el PATH.
REM
REM  Probado con:
REM    - g++  (MinGW-w64 / Strawberry Perl: C:\Strawberry\c\bin)
REM    - cl   (Visual Studio Build Tools, desde "x64 Native Tools Command Prompt")
REM
REM  Codigo de salida: 0 si todas las pruebas pasan, 1 si alguna falla.
REM ==========================================================================
setlocal

REM El punto final evita que la barra de %~dp0 "coma" la comilla de cierre.
cd /d "%~dp0."
set REGLAS=..\nodo_iot_esp32s3
set SALIDA=prueba_reglas.exe

if exist "%SALIDA%" del /q "%SALIDA%"

where g++ >nul 2>&1
if not errorlevel 1 goto compilar_gpp

where cl >nul 2>&1
if not errorlevel 1 goto compilar_msvc

echo [ERROR] No se encontro ni g++ ni cl en el PATH.
echo         Instala MinGW-w64 ^(o usa C:\Strawberry\c\bin^) o abre la consola
echo         "x64 Native Tools Command Prompt for VS" y vuelve a ejecutar este .bat.
exit /b 2

:compilar_gpp
echo [1/2] Compilando con g++ ...
g++ -std=c++17 -O2 -Wall -Wextra -I"%REGLAS%" prueba_reglas.cpp -o "%SALIDA%" -lm
if errorlevel 1 (
  echo [ERROR] Fallo la compilacion con g++.
  exit /b 1
)
goto ejecutar

:compilar_msvc
echo [1/2] Compilando con cl ...
cl /nologo /std:c++17 /O2 /EHsc /W3 /I"%REGLAS%" prueba_reglas.cpp /Fe:"%SALIDA%" >nul
if errorlevel 1 (
  echo [ERROR] Fallo la compilacion con cl.
  exit /b 1
)
if exist prueba_reglas.obj del /q prueba_reglas.obj
goto ejecutar

:ejecutar
echo [2/2] Ejecutando %SALIDA% ...
echo.
".\%SALIDA%"
set CODIGO=%ERRORLEVEL%
echo.
if "%CODIGO%"=="0" (
  echo [OK] Todas las pruebas de las reglas pasan.
) else (
  echo [ERROR] Hay pruebas que fallan ^(codigo %CODIGO%^).
)
exit /b %CODIGO%
