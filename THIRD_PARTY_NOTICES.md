# Third-party notices and rebuilding

## MapleMate

Original MapleMate code and the maplemate skill are licensed under the MIT License in LICENSE. Copyright (c) 2026 nullpo0.

Third-party software retains its own copyright and license. Nexon API results, MapleStory artwork, trademarks, and linked third-party content are not relicensed by MapleMate. This is an independent project, not an official Nexon product or partnership. Data based on NEXON Open API. API use remains subject to https://openapi.nexon.com/ko/support/.

## Included software

The Windows release includes Python, Tcl/Tk, and Python libraries. The licenses directory contains their original license and copyright notices. licenses/manifest.json records the versions and SHA-256 hashes of the included notices and source archives. The package notices do not replace the original license terms. When Windows Python consolidates Tcl/Tk notices into its LICENSE.txt, that complete original file is preserved for the corresponding runtime.

## pystray (LGPLv3)

MapleMate uses pystray for its tray icon. pystray is licensed under GNU LGPL version 3 and is not relicensed under MIT. Both its GNU GPL and GNU LGPL license texts are included under licenses/pystray. Its installed Python source, original license texts, and package metadata are included in source/pystray-VERSION.zip.

Upstream: https://github.com/moses-palmer/pystray

Modification of pystray and reverse engineering of the combined program to debug those modifications are permitted as required by LGPLv3. The accompanying source and build materials allow you to rebuild MapleMate with an interface-compatible modified version of pystray.

## Rebuild with modified pystray

The source directory in the Windows ZIP contains the exact MapleMate source snapshot used for that build, pystray source, and requirements-build.txt with the installed build dependency versions. The manifest records the Python version and platform. For this release use Python 3.13 on Windows x64.

1. Extract MapleMate-VERSION-source.zip to a development directory.
2. In that directory create a virtual environment and install the recorded dependencies:

       py -3.13 -m venv .venv
       .\.venv\Scripts\python.exe -m pip install -r PATH_TO_RELEASE\source\requirements-build.txt
       .\.venv\Scripts\python.exe -m pip install --no-deps --no-build-isolation -e ".[build]"

3. Extract source/pystray-VERSION.zip. Copy its pystray directory over .venv/Lib/site-packages/pystray in the development environment, then make your changes there. These are the source files PyInstaller reads when it builds the application.
4. Run the tests and rebuild:

       .\.venv\Scripts\python.exe -m unittest discover -s tests -v
       .\.venv\Scripts\python.exe scripts/build_release.py --output-dir dist/rebuilt

The resulting dist/rebuilt/MapleMate contains the rebuilt application. The new package includes the modified installed pystray source. Preserve the required notices and identify your modifications when distributing that modified library. The required Python dependencies remain under their respective licenses.
