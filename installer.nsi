; Installateur d'AGOA DATA (construit par GitHub Actions : makensis /DVERSION=x.y.z installer.nsi)
; Le lanceur AGOA l'exécute en silencieux : AGOA-DATA-Setup-x.y.z.exe /S /D=C:\AGOA\data
Unicode true
!include "MUI2.nsh"

!define APPNAME "AGOA DATA"
!define EXENAME "AGOA DATA.exe"
!define REGKEY "Software\Microsoft\Windows\CurrentVersion\Uninstall\AGOADATA"
!ifndef VERSION
  !define VERSION "0.0.0"
!endif

Name "${APPNAME}"
OutFile "dist\AGOA-DATA-Setup-${VERSION}.exe"
InstallDir "C:\AGOA\data"
RequestExecutionLevel user
SetCompressor /SOLID lzma
BrandingText "AGOA - Agence Rémi Thollet"

VIProductVersion "${VERSION}.0"
VIAddVersionKey /LANG=1036 "ProductName" "${APPNAME}"
VIAddVersionKey /LANG=1036 "FileDescription" "Installateur ${APPNAME}"
VIAddVersionKey /LANG=1036 "CompanyName" "Agence Rémi Thollet Architecte"
VIAddVersionKey /LANG=1036 "FileVersion" "${VERSION}"
VIAddVersionKey /LANG=1036 "LegalCopyright" "Agence Rémi Thollet Architecte"

!define MUI_ICON "icon.ico"
!define MUI_UNICON "icon.ico"
!define MUI_ABORTWARNING
!define MUI_WELCOMEPAGE_TITLE "Installation d'${APPNAME}"
!define MUI_WELCOMEPAGE_TEXT "Ce programme installe ${APPNAME} : volumétrie d'un bâtiment à partir du nuage de points LiDAR HD de l'IGN."
!define MUI_FINISHPAGE_RUN "$INSTDIR\${EXENAME}"
!define MUI_FINISHPAGE_RUN_TEXT "Lancer ${APPNAME}"

!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_INSTFILES
!insertmacro MUI_PAGE_FINISH
!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES
!insertmacro MUI_LANGUAGE "French"

Section "Install"
  SetOutPath "$INSTDIR"
  File "dist\${EXENAME}"
  WriteUninstaller "$INSTDIR\Uninstall.exe"

  CreateShortcut "$SMPROGRAMS\${APPNAME}.lnk" "$INSTDIR\${EXENAME}"

  WriteRegStr HKCU "${REGKEY}" "DisplayName" "${APPNAME}"
  WriteRegStr HKCU "${REGKEY}" "DisplayVersion" "${VERSION}"
  WriteRegStr HKCU "${REGKEY}" "Publisher" "Agence Rémi Thollet Architecte"
  WriteRegStr HKCU "${REGKEY}" "InstallLocation" "$INSTDIR"
  WriteRegStr HKCU "${REGKEY}" "DisplayIcon" "$INSTDIR\${EXENAME}"
  WriteRegStr HKCU "${REGKEY}" "UninstallString" '"$INSTDIR\Uninstall.exe"'
  WriteRegDWORD HKCU "${REGKEY}" "NoModify" 1
  WriteRegDWORD HKCU "${REGKEY}" "NoRepair" 1
SectionEnd

Section "Uninstall"
  ; les résultats (dossiers d'analyse) ne sont jamais supprimés : ils sont ailleurs que dans C:\AGOA\data
  Delete "$INSTDIR\${EXENAME}"
  Delete "$INSTDIR\Uninstall.exe"
  RMDir "$INSTDIR"
  Delete "$SMPROGRAMS\${APPNAME}.lnk"
  DeleteRegKey HKCU "${REGKEY}"
SectionEnd
