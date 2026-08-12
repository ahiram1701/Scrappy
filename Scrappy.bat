@echo off
rem ===========================================================================
rem  Scrappy -- lanzador de la interfaz de terminal
rem
rem  Doble clic para abrir la TUI.
rem
rem  Esta escrito para que un doble clic NUNCA acabe en una ventana que se
rem  cierra sin explicar nada: si falta algo, lo dice y espera a que pulses una
rem  tecla.
rem ===========================================================================

rem Situarse en la carpeta del script, no en la del acceso directo.
cd /d "%~dp0"

set "VENV_PY=%~dp0.venv\Scripts\python.exe"

if not exist "%VENV_PY%" goto :sin_venv

rem Windows Terminal da color real y Unicode correcto; la consola clasica de
rem Windows se queda corta con los bordes y los emoji que usa Textual. Si `wt`
rem esta disponible se prefiere, pero sin el tambien funciona.
where wt.exe >nul 2>&1
if %errorlevel% equ 0 (
    start "" wt.exe --title Scrappy "%VENV_PY%" -m scrappy tui
    exit /b 0
)

"%VENV_PY%" -m scrappy tui
if errorlevel 1 goto :fallo
exit /b 0


:sin_venv
echo.
echo  ===============================================================
echo   No se encuentra el entorno virtual de Scrappy.
echo  ===============================================================
echo.
echo   Se esperaba aqui:
echo     %VENV_PY%
echo.
echo   Para crearlo, abre una terminal en esta carpeta y ejecuta:
echo.
echo     python -m venv .venv
echo     .venv\Scripts\python -m pip install -e ".[dev]"
echo.
echo   O, si tienes make:  make setup
echo.
pause
exit /b 1


:fallo
echo.
echo  ===============================================================
echo   Scrappy termino con un error.
echo  ===============================================================
echo.
echo   Revisa el detalle completo en:
echo     %~dp0data\scrappy-tui.log
echo.
echo   Y comprueba la configuracion con:
echo     .venv\Scripts\scrappy health
echo.
pause
exit /b 1
