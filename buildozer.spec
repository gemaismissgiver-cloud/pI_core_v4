[app]
title = PI v4
package.name = piv4
package.domain = de.pi
source.dir = .
source.include_exts = py,json,png,jpg,ttf,kv
version = 1.0
requirements = python3,kivy==2.3.0,pyjnius,pillow
orientation = portrait
fullscreen = 0
android.permissions = INTERNET
android.api = 33
android.minapi = 26
android.archs = arm64-v8a

[buildozer]
log_level = 2
warn_on_root = 1
