@echo off
echo ========================================
echo   Lancement MailHog + Contrat_Gen
echo ========================================
echo.
echo Demarrage de MailHog...
start "" MailHog
timeout /t 2 >nul
echo MailHog demarre sur http://localhost:8025  (SMTP 1025)
echo.
echo Demarrage de Flask (MAILS_MODE=smtp, instance.json non modifie)...
set MAILS_MODE=smtp
python app.py
