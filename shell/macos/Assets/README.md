# ScribeLoop icon

`ScribeLoop.svg` is the editable vector design: a warm ivory speech-loop with a
three-bar waveform on a dark green tile. `ScribeLoop.png` is the 1024 px master;
`ScribeLoop.icns` contains the native 16–1024 px representations used by the app.
The artwork is drawn directly as vector geometry, with no external image API.

The matching AppKit renderer lives at `packaging/macos/render_icon.swift`. Keep its
geometry/palette aligned with the SVG when revising the icon. Regenerate from the
repository root using Xcode:

```sh
DEVELOPER_DIR=/Applications/Xcode.app/Contents/Developer xcrun swift \
  -module-cache-path build/macos/swift-cache packaging/macos/render_icon.swift \
  build/macos/ScribeLoop.iconset
iconutil -c icns build/macos/ScribeLoop.iconset -o shell/macos/Assets/ScribeLoop.icns
cp build/macos/ScribeLoop.iconset/icon_512x512@2x.png shell/macos/Assets/ScribeLoop.png
```

The desktop builder copies the ICNS resource and sets `CFBundleIconFile`. The
preview bundle inherits the same icon. UI branding uses the application's icon,
so the Dock, Finder and notebook share one mark.
