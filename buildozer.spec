[app]
title = IMMUNE Audit APK
package.name = formalchemist
package.domain = org.formalchemist

source.dir = .
source.include_exts = py,png,jpg,jpeg,kv,json,csv,java,txt
version = 0.1.0

requirements = python3==3.10.11,hostpython3==3.10.11,kivy,pyjnius,androidssystemfilechooser,numpy,opencv,pypdf,reportlab,pillow

orientation = portrait
fullscreen = 0
icon.filename = assets/icon.png

android.archs = arm64-v8a
android.api = 33
android.minapi = 24
android.ndk = 25b
android.accept_sdk_license = True
android.allow_backup = True

android.permissions = INTERNET
android.add_src = android_src

[buildozer]
log_level = 2
warn_on_root = 1
