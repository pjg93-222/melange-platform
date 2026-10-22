@echo off
rem Melange Systems one-click update trust. Double-click this file; no administrator rights needed.
rem It adds ONLY the public release certificate embedded below to your own Windows trusted roots
rem (Windows asks Yes/No first). That certificate is code-signing only and not a CA (CA:false),
rem so it cannot vouch for websites or other certificates. It refuses to run if the embedded
rem certificate is not the pinned one (SHA-1 thumbprint 0EBC9AD04E929CDD731EDB5D278A4143BC3D3D30).
setlocal
set "MELANGE_TRUST_SELF=%~f0"
"%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -NonInteractive -ExecutionPolicy Bypass -WindowStyle Hidden -Command "$ErrorActionPreference='Stop';$pin='0EBC9AD04E929CDD731EDB5D278A4143BC3D3D30';$root='Cert:\CurrentUser\Root\'+$pin;Add-Type -AssemblyName PresentationFramework;$say={param($m,$i) [void][Windows.MessageBox]::Show($m,'Melange Systems updates','OK',$i)};try{$text=[IO.File]::ReadAllText($env:MELANGE_TRUST_SELF);$cert=[Security.Cryptography.X509Certificates.X509Certificate2]::new([Convert]::FromBase64String(((($text -split '-----(?:BEGIN|END) CERTIFICATE-----')[1]) -replace '\s','')));if($cert.Thumbprint -ne $pin){& $say 'This file was altered: its certificate is not the pinned Melange Systems release certificate. Nothing was changed.' 'Error';exit 2};if(Test-Path $root){& $say 'Updates are already trusted on this PC. Nothing was changed.' 'Information';exit 0};$store=[Security.Cryptography.X509Certificates.X509Store]::new('Root','CurrentUser');$store.Open('ReadWrite');try{$store.Add($cert)}catch{}finally{$store.Close()};if(Test-Path $root){& $say 'Done. Melange Systems apps on this PC will now install verified updates automatically.' 'Information';exit 0};& $say 'Not trusted: the Windows security prompt was declined. Run this file again and choose Yes to turn updates on.' 'Warning';exit 1}catch{& $say ('Trust was not changed: '+$_.Exception.Message) 'Error';exit 3}"
exit /b %errorlevel%
-----BEGIN CERTIFICATE-----
MIIDTjCCAjagAwIBAgIQEUIe7YLPIapKm5npT3oAHTANBgkqhkiG9w0BAQsFADA4
MTYwNAYDVQQDDC1NZWxhbmdlIFN5c3RlbXMgRGVza3RvcCBSZWxlYXNlIChzZWxm
LXNpZ25lZCkwHhcNMjYxMDE3MDQwMjA0WhcNMzExMDE3MDQxMjAxWjA4MTYwNAYD
VQQDDC1NZWxhbmdlIFN5c3RlbXMgRGVza3RvcCBSZWxlYXNlIChzZWxmLXNpZ25l
ZCkwggEiMA0GCSqGSIb3DQEBAQUAA4IBDwAwggEKAoIBAQC/Ysuw8SerVkKNoubj
EXVb9B52jGsz/YpNGs6fKPuyibht0FgThnLly0lQRlrjq1yHkxlXdctiFIPWTdFO
fho+kknP4aWN/mtK3zzVBdvvYcEb87q8vB80nBS8DvknpFb8RjP7wUCzlh95ucfx
F1ITOz6rP/72AVsdNGHCd2V+I4WMAbBRfyZYOPf/1RcinvWKrD8ZchywZXIw425q
BFNvJ9zEfopAz5dRRJGMyuKW+l+yi1+dTJ0V2kuRB3pdXfYB2jVjF7Od/ukTr09z
hYE/27AoaU/kldLD6j+p/TZ1hkSwz0lsx+KShwWFYd1Vu0mkTUt2U+hGJkXCG5gQ
+b85AgMBAAGjVDBSMA4GA1UdDwEB/wQEAwIHgDATBgNVHSUEDDAKBggrBgEFBQcD
AzAMBgNVHRMBAf8EAjAAMB0GA1UdDgQWBBTH7jHJgtZ3HELtypQuWJe5tahQczAN
BgkqhkiG9w0BAQsFAAOCAQEAA1KK9SL97ngJyYnhjWy7nTnEN9Ozq2Xd6+tPjGvu
ChkiI8xwKtBNvfTjnRSqwGVfx1l4cGeLpQ/jM0bv3tww9O1Q/8Z+VMKG5todODNx
G1B92Z2vE2yyJL/azmpQKdpt1DM0H7cQ63vMi994aIuI1tPJ/hvFNF5CuhuXYFGG
pN2R+V3tkbWgJYU9GRy6tA6QKaxkDkrrj19nMVpoDBZ1TRGwwA9oH8PDnxehOnyd
IF1fccRDbSaxuuUnfR0mvYTiy6wQ4brHTBApUwlo30GNOFCdD7Yv8fGv8ZI7ERAp
ZRBrM4iY6S6g9eGHnltWXarjHzOlgE0lWMFC8771zZmUCQ==
-----END CERTIFICATE-----
