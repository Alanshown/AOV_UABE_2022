#!/bin/sh
set -eu
cd "$(dirname "$0")/../.."
test -x dist/AOV_UABE_2022/AOV_UABE_2022
stage=$(mktemp -d ./deb-root.XXXXXX)
trap 'rm -rf -- "$stage"' EXIT
install -d "$stage/DEBIAN" "$stage/opt/aov-uabe-2022" "$stage/usr/bin" "$stage/usr/share/applications" "$stage/usr/share/icons/hicolor/64x64/apps" "$stage/usr/share/doc/aov-uabe-2022" ./artifacts
cp -a dist/AOV_UABE_2022/. "$stage/opt/aov-uabe-2022/"
install -m644 LICENSE "$stage/usr/share/doc/aov-uabe-2022/copyright"
python - "$stage" <<'PY'
from PIL import Image
from pathlib import Path
import sys
dest=Path(sys.argv[1])/'usr/share/icons/hicolor/64x64/apps/aov-uabe-2022.png'
Image.open('icon.ico').convert('RGBA').resize((64,64), Image.Resampling.LANCZOS).save(dest)
PY
cat > "$stage/DEBIAN/control" <<'EOF'
Package: aov-uabe-2022
Version: 2.4-2
Section: utils
Priority: optional
Architecture: amd64
Maintainer: ShownAlan
Depends: libc6 (>= 2.41), libgl1, libegl1, libegl-mesa0, libgl1-mesa-dri, libx11-6, libxext6, libxrender1, libxft2, libfontconfig1, libfreetype6, libxcb1, fonts-noto-cjk, fonts-dejavu-core
Homepage: https://github.com/Alanshown/AOV_UABE_2022
Description: AssetBundle desktop workshop for Unity 2022
 Open, inspect, export, save and rebuild AssetBundle assets.
EOF
cat > "$stage/usr/bin/aov-uabe-2022" <<'EOF'
#!/bin/sh
exec /opt/aov-uabe-2022/AOV_UABE_2022 "$@"
EOF
chmod 755 "$stage/usr/bin/aov-uabe-2022"
cat > "$stage/usr/share/applications/aov-uabe-2022.desktop" <<'EOF'
[Desktop Entry]
Type=Application
Name=AOV_UABE_2022 2.4
Comment=AssetBundle workshop for Unity 2022
Exec=aov-uabe-2022
Icon=aov-uabe-2022
Terminal=false
Categories=Development;Utility;
EOF
dpkg-deb --root-owner-group -Zxz -z6 --build "$stage" ./artifacts/AOV_UABE_2022-2.4-Linux-x64.deb
dpkg-deb --info ./artifacts/AOV_UABE_2022-2.4-Linux-x64.deb
