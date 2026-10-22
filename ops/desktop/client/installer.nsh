!macro customInstall
  ; Offline rollback baseline: an interactive (non-update) install keeps a copy of its own installer in the
  ; per-user baseline folder the app reads (main.cjs baselineRoot). The first rollback then needs no download;
  ; the app re-verifies the hash and the publisher before using it. Nothing elevated; any failure is ignored.
  ; Update trust is not set here: updates no longer depend on the release certificate being in the Trusted Root store.
  ; Per-machine (all users) installs skip it: the app reads the signed-in user's %LOCALAPPDATA%, which may not be the
  ; (elevated) installing account's, so their first rollback falls back to a network download.
  ${IfNot} ${isUpdated}
  ${AndIfNot} ${Silent}
  ${AndIf} $installMode == "CurrentUser"
    ClearErrors
    CreateDirectory "$LOCALAPPDATA\${APP_ID}\baseline\${VERSION}"
    CopyFiles /SILENT "$EXEPATH" "$LOCALAPPDATA\${APP_ID}\baseline\${VERSION}\$EXEFILE"
    ClearErrors
  ${EndIf}
!macroend
!macro customUnInstall
  ${IfNot} ${isUpdated}
    ; Remove only this application's login task and its cached installers (rollback baseline and updater cache;
    ; update state lives in the profile). Keep user data and credentials. The release certificate stays trusted:
    ; all three apps share it.
    DeleteRegValue HKCU "Software\Microsoft\Windows\CurrentVersion\Run" "${APP_ID}"
    DeleteRegValue HKCU "Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved\Run" "${APP_ID}"
    RMDir /r "$LOCALAPPDATA\${APP_ID}\baseline"
    RMDir /r "$LOCALAPPDATA\${APP_ID}\updater"
    RMDir "$LOCALAPPDATA\${APP_ID}"
    ; Terrier's privately downloaded district data and its district state (district-packs.cjs storeRoot()). Only a real uninstall removes it;
    ; an update runs the old uninstaller, which must keep it (hence the isUpdated guard around this whole block).
    ${If} "${APP_ID}" == "org.melange.desktop.terrier"
      RMDir /r "$LOCALAPPDATA\MelangeData\terrier"
      RMDir "$LOCALAPPDATA\MelangeData"
    ${EndIf}
  ${EndIf}
!macroend
