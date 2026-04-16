@echo off
chcp 65001 >nul
echo ============================================
echo  YouTube cookies eksport qilish
echo ============================================
echo.

set TARGET=C:\PROEKTS\1DAYGLOBAL\YOUTUBE\youtube_cookies.txt

REM 1. Agar fayl allaqachon mavjud bo'lsa
if exist "%TARGET%" (
    echo [OK] youtube_cookies.txt allaqachon mavjud!
    python -m yt_dlp --cookies "%TARGET%" --skip-download --quiet "https://www.youtube.com/watch?v=jNQXAC9IVRw"
    if %errorlevel%==0 (
        echo [OK] Cookies ishlayapti!
    ) else (
        echo [XATO] Cookies eski yoki yaroqsiz. Qayta eksport kerak.
        del "%TARGET%"
    )
)

REM 2. Agar yo'q bo'lsa — avval Chrome orqali urinib ko'rish
if not exist "%TARGET%" (
    echo Chrome yopilmoqda...
    taskkill /F /IM chrome.exe /T >nul 2>&1
    timeout /t 3 /nobreak >nul

    echo Chrome cookies eksport qilinmoqda...
    python -m yt_dlp --cookies-from-browser chrome --cookies "%TARGET%" --skip-download --quiet "https://www.youtube.com" 2>nul

    if exist "%TARGET%" (
        echo [OK] Chrome cookies muvaffaqiyatli eksport qilindi!
        goto :done
    )

    echo Chrome DPAPI xatosi. Downloads papkasida cookie fayl qidirilmoqda...
)

REM 3. Downloads papkasida Netscape format cookie faylini qidirish
if not exist "%TARGET%" (
    for /f "delims=" %%F in ('dir /b /o-d "%USERPROFILE%\Downloads\*.txt" 2^>nul') do (
        findstr /m "# Netscape HTTP Cookie File" "%USERPROFILE%\Downloads\%%F" >nul 2>&1
        if !errorlevel!==0 (
            echo Topildi: Downloads\%%F — ko'chirilmoqda...
            copy "%USERPROFILE%\Downloads\%%F" "%TARGET%" >nul
            goto :check
        )
    )
)

:check
if exist "%TARGET%" (
    echo [OK] youtube_cookies.txt yaratildi!
    goto :done
)

echo.
echo [XATO] Cookies fayli topilmadi!
echo.
echo QILISH KERAK:
echo   1. Chrome'da youtube.com ga kiring
echo   2. "Get cookies.txt LOCALLY" extension'ni oching
echo   3. "Export" tugmasini bosing — fayl Downloads ga tushadi
echo   4. O'sha faylni shu joyga ko'chiring:
echo      %TARGET%
echo   5. Qayta ushbu skriptni ishga tushiring
echo.
goto :restart

:done
echo.
:restart
echo Chrome qayta ishga tushirilmoqda...
start "" "C:\Program Files\Google\Chrome\Application\chrome.exe"
echo.
pause
