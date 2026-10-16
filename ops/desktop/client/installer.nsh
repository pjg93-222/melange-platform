!macro customUnInstall
  ${IfNot} ${isUpdated}
    ; Remove only this application's login task. Keep user data and credentials.
    DeleteRegValue HKCU "Software\Microsoft\Windows\CurrentVersion\Run" "${APP_ID}"
    DeleteRegValue HKCU "Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved\Run" "${APP_ID}"
  ${EndIf}
!macroend
