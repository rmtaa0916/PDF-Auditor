IMMUNE Audit APK Bundle
=======================

What this bundle does
---------------------
- Lets you pick a PDF on Android
- Runs a full-page audit using an Android-safe image-analysis backend
- Flags likely blank answer lines, unanswered radio groups, and unanswered checkbox groups
- Saves an audited PDF with direct page overlays plus a consolidated summary page

Why this version is lean
------------------------
The original desktop snippet relied on PyMuPDF (fitz). For Android APK packaging through Buildozer/python-for-android,
that is risky because PyMuPDF is not a standard Android recipe dependency. This bundle avoids that dependency for the
Android runtime path by using:
- Android PdfRenderer (Java helper) for page rasterization
- OpenCV + NumPy for detection
- ReportLab + pypdf for overlay output and summary-page generation

Included files
--------------
- main.py
- buildozer.spec
- android_src/org/formalchemist/formalchemist/UriCopyHelper.java
- android_src/org/formalchemist/formalchemist/PdfRenderHelper.java
- assets/icon.png
- PATCH_CHANGE_LOG.txt

Build notes
-----------
1. Put these files in your project root.
2. In Linux/WSL with Buildozer installed, run:
   buildozer android debug
3. Install the generated APK from bin/ after the build completes.

Important limitation
--------------------
This bundle is adapted for Android packaging. It does not preserve the exact original PyMuPDF vector-drawing audit logic.
Instead, it reproduces the same high-level workflow using an Android-safe raster detection pipeline.
