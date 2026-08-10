@echo off
title Warren Buffett Jr
echo ==========================================
echo    WARREN BUFFETT JR - Iniciando...
echo ==========================================
echo.
echo  La app se abrira sola en tu navegador
echo  en unos segundos.
echo.
echo  Para APAGARLA: cierra esta ventana.
echo ==========================================
echo.
start "" cmd /c "timeout /t 3 /nobreak >nul & start http://localhost:8765"
"C:\Users\hugoh\Documents\warren-buffett-jr\engine\.venv\Scripts\python.exe" "C:\Users\hugoh\Documents\warren-buffett-jr\engine\scripts\webapp.py"
